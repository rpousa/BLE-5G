#!/usr/bin/env python3
"""sl_bridge.py: NR sidelink (mode 2) between two OAI nrUEs over the nrlink boards.

Each sidelink nrUE runs with NRLINK_SL=<bridge ip>:<port> (executables/nrlink_sl.c in the sidelink tree), which
replaces its PHY. Its bridge paces it in real time (one RUN about every millisecond, up to the slot the clock has
reached), collects what its MAC transmits (PSBCH, or SCI 1-A + SCI 2 + the SL-SCH transport block) and hands it
what the other UE transmitted. The UE holds received items until the first slot whose RX action fits them.

Air: the bridge next to the sync-reference UE (master) polls: AIR_SLM carries its outgoing items (or none), the
peer bridge answers at once with AIR_SLP and its own; only one board talks at a time. The peer bridge sets its
slot clock from the first PSBCH it receives (the nearby UE synchronises on it).

  sl_bridge.py --role master --ue-port 5600 --board 2HOKMMVEX1WMP --channel 0x5A
  sl_bridge.py --role peer   --ue-port 5601 --board GBAMNH4BQ3CSB --channel 0x5A
  sl_bridge.py --role master --ue-port 5600 --air udp --air-port 5700 --peer 127.0.0.1:5701   (no boards)
"""
import argparse
import queue
import socket
import struct
import threading
import time

import nrlink
from nrlink import Reassembler, fragments

SLM_HDR = struct.Struct("<BBHIB")   # magic 'S', type, n_items, abs_slot, flags
SLM_MAGIC, SLM_HELLO, SLM_RUN, SLM_DONE = 0x53, 1, 2, 3
ITEM_HDR = struct.Struct("<BIH")    # type, slot, len
SLI_PSBCH = 1
AIR_SLM, AIR_SLP = 0x04, 0x05       # next to nrlink's AIR_DL 0x01, AIR_POLL 0x02, AIR_UL 0x03
ITEMS = struct.Struct("<H")         # air payload: n_items, then the items as the UE wrote them
SLOT_S = 0.0005                     # 30 kHz: 0.5 ms slots
RUN_MAX = 48000                     # bytes of items per RUN (the UE reads up to 60000)
AIR_MAX = 20 * 248 - 2              # bytes of items per air message (20 radio packets)
BACKLOG_MAX = 64000                 # bytes of items waiting for the air; beyond, the oldest are dropped
MAX_AGE = 200                       # slots an item may wait for the air (the receiving UE drops them at 800)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def split_items(n, data):
    out, off = [], 0
    for _ in range(n):
        t, s, ln = ITEM_HDR.unpack_from(data, off)
        out.append((t, s, data[off:off + ITEM_HDR.size + ln]))
        off += ITEM_HDR.size + ln
    return out


class Air:
    """One message per (kind, seq) over the boards (fragmented, 255 B packets) or over UDP (no boards)."""

    def __init__(self, a):
        self.q = queue.Queue()
        self.mode = a.air
        self.tx_pkts = self.rx_pkts = 0
        self.last_rx = 0.0       # time of the last radio packet received (a reply may still be coming in)
        if a.air == "board":
            self.board = nrlink.Board(a.board)
            log("board", a.board, self.board.ping())
            self.board.configure(a.channel, a.power)
            self.reasm = Reassembler()
            threading.Thread(target=self._board_rx, daemon=True).start()
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("0.0.0.0", a.air_port))
            host, port = a.peer.rsplit(":", 1)
            self.peer = (host, int(port))
            threading.Thread(target=self._udp_rx, daemon=True).start()

    def send(self, kind, seq, payload):
        if self.mode == "board":
            for p in fragments(kind, 0, seq & 0xFFFF, 0, payload):
                self.board.tx(p)
                self.tx_pkts += 1
        else:
            self.sock.sendto(struct.pack("<BH", kind, seq & 0xFFFF) + payload, self.peer)
            self.tx_pkts += 1

    def _board_rx(self):
        while True:
            _rssi, _ts, pkt = self.board.rx.get()
            self.rx_pkts += 1
            self.last_rx = time.monotonic()
            got = self.reasm.add(pkt)
            if got:
                (kind, _uid, seq, _x), msg = got
                if kind in (AIR_SLM, AIR_SLP):
                    self.q.put((kind, seq, msg))

    def _udp_rx(self):
        while True:
            data, _ = self.sock.recvfrom(65535)
            self.rx_pkts += 1
            kind, seq = struct.unpack_from("<BH", data)
            self.q.put((kind, seq, data[3:]))


class UE:
    """The local sidelink nrUE (nrlink_sl.c), paced in real time by a thread of its own."""

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self.addr = None
        self.lock = threading.Lock()
        self.inbox = []          # raw items received over the air, for the UE
        self.outbox = []         # raw items from the UE, for the air
        self.t0 = None           # wall time of slot 0 (None: clock not set yet)
        self.slot = -1           # last slot the UE reached
        self.st = dict(runs=0, slots=0, tx=0, rx=0)
        self.dropped = 0         # items dropped from a full outbox

    def wait_hello(self):
        self.sock.settimeout(None)
        while True:
            data, addr = self.sock.recvfrom(65535)
            if len(data) >= SLM_HDR.size and data[0] == SLM_MAGIC and data[1] == SLM_HELLO:
                self.addr = addr
                log(f"UE at {addr[0]}:{addr[1]}, {'sync reference' if data[8] & 1 else 'nearby UE'}")
                return

    def run(self, slot, items, timeout=1.0):
        """RUN up to slot with items; returns (flags, items) of the DONE, or None."""
        msg = SLM_HDR.pack(SLM_MAGIC, SLM_RUN, len(items), slot, 0) + b"".join(items)
        for _ in range(3):
            self.sock.sendto(msg, self.addr)
            self.sock.settimeout(timeout)
            try:
                while True:
                    data, addr = self.sock.recvfrom(65535)
                    if len(data) < SLM_HDR.size or data[0] != SLM_MAGIC:
                        continue
                    _m, typ, n, s, flags = SLM_HDR.unpack_from(data)
                    if typ == SLM_HELLO:          # the UE (re)started: send it the RUN at its new address
                        if addr != self.addr:
                            log(f"UE now at {addr[0]}:{addr[1]}")
                            self.addr = addr
                        self.sock.sendto(msg, self.addr)
                        continue
                    if typ == SLM_DONE and s >= slot:
                        return flags, [r for _t, _s, r in split_items(n, data[SLM_HDR.size:])]
            except socket.timeout:
                log(f"UE did not answer RUN {slot}, again")
        return None

    def set_clock(self, slot):
        old = None if self.t0 is None else int((time.monotonic() - self.t0) / SLOT_S)
        self.t0 = time.monotonic() - slot * SLOT_S
        log(f"slot clock set: now slot {slot}" + ("" if old is None else f" (was {old})"))

    def pace(self, period):
        """Thread: RUN the UE up to the clock's slot about every period seconds."""
        nxt = time.monotonic()
        while True:
            nxt += period
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.monotonic()
            if self.t0 is None:
                continue
            target = int((time.monotonic() - self.t0) / SLOT_S)
            if target <= self.slot:
                continue
            with self.lock:
                items, self.inbox = take(self.inbox, RUN_MAX)
            r = self.run(target, items)
            if r is None:                 # (a restarted UE says HELLO, which run() follows)
                with self.lock:
                    self.inbox = items + self.inbox
                continue
            _flags, out = r
            with self.lock:
                self.outbox += out
                n = sum(len(x) for x in self.outbox)
                while n > BACKLOG_MAX:
                    n -= len(self.outbox.pop(0))
                    self.dropped += 1
            self.st["runs"] += 1
            self.st["slots"] += target - self.slot if self.slot >= 0 else 1
            self.st["tx"] += len(out)
            self.st["rx"] += len(items)
            self.slot = target

    def take_outbox(self):
        with self.lock:
            # what waited too long would arrive too late to be delivered: drop it here, before it takes airtime
            keep = [x for x in self.outbox if self.slot - ITEM_HDR.unpack_from(x)[1] <= MAX_AGE]
            self.dropped += len(self.outbox) - len(keep)
            out, self.outbox = take(keep, AIR_MAX)
        return out

    def give(self, items):
        with self.lock:
            self.inbox += items


def take(items, max_bytes):
    """Splits off the leading items that fit in max_bytes (at least one): (taken, rest)."""
    n, size = 0, 0
    while n < len(items) and (n == 0 or size + len(items[n]) <= max_bytes):
        size += len(items[n])
        n += 1
    return items[:n], items[n:]


def pack_items(items):
    return ITEMS.pack(len(items)) + b"".join(items)


def unpack_items(msg):
    (n,) = ITEMS.unpack_from(msg)
    return split_items(n, msg[ITEMS.size:])


def master(a, ue, air):
    """Polls the peer. Stop and wait: a poll keeps its sequence number and payload until its reply arrives (late
    replies count), so items lost with a radio packet are sent again rather than lost."""
    ue.set_clock(0)
    seq, payload, n_out, tries = 0, None, 0, 0
    st = dict(polls=0, replies=0, timeouts=0, resends=0, up=0, down=0, bytes=0)
    t_stat = time.monotonic()
    while True:
        if payload is None:
            out = ue.take_outbox()
            payload, n_out, tries = pack_items(out), len(out), 0
            seq = (seq + 1) & 0xFFFF
        else:
            st["resends"] += 1
        air.send(AIR_SLM, seq, payload)
        tries += 1
        st["polls"] += 1
        st["bytes"] += len(payload)
        deadline = time.monotonic() + a.reply_timeout + 0.004 * (len(payload) // 248)
        got = None
        while True:
            try:
                kind, s, msg = air.q.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                if time.monotonic() - air.last_rx < a.frag_gap:   # fragments still coming in: don't talk over them
                    deadline = air.last_rx + a.frag_gap
                    continue
                st["timeouts"] += 1
                break
            if kind == AIR_SLP and s == seq:
                got = unpack_items(msg)
                st["bytes"] += len(msg)
                break
        if got is None and tries < a.retries:
            continue                       # resend the same poll; a late reply to it still counts
        if got is not None:
            ue.give([r for _t, _s, r in got])
            st["replies"] += 1
            st["up"] += len(got)
        st["down"] += n_out
        idle = n_out == 0 and not got
        payload = None
        if idle:
            time.sleep(a.idle_poll)
        now = time.monotonic()
        if now - t_stat >= a.stats:
            dt = now - t_stat
            u = ue.st
            log(f"UE slot {ue.slot}: {u['slots'] / dt:.0f} slots/s in {u['runs'] / dt:.0f} RUNs/s; "
                f"air {st['polls'] / dt:.0f} polls/s, {st['replies']} replies, {st['timeouts']} timeouts, "
                f"{st['resends']} resends, items out {st['down']} in {st['up']}, {st['bytes'] * 8 / dt / 1000:.1f} kbit/s, "
                f"backlog {len(ue.outbox)}, dropped {ue.dropped}")
            st = dict.fromkeys(st, 0)
            ue.st = dict.fromkeys(ue.st, 0)
            t_stat = now


def peer(a, ue, air):
    """Answers each poll at once; a repeated poll (its reply was lost) gets the same reply again."""
    st = dict(polls=0, repeats=0, up=0, down=0)
    last_seq, last_reply = None, b""
    t_stat = time.monotonic()
    while True:
        try:
            kind, seq, msg = air.q.get(timeout=1.0)
        except queue.Empty:
            kind = None
        if kind == AIR_SLM and seq == last_seq:
            air.send(AIR_SLP, seq, last_reply)
            st["repeats"] += 1
        elif kind == AIR_SLM:
            its = unpack_items(msg)
            psbch = [s for t, s, _r in its if t == SLI_PSBCH]
            if psbch:
                # a PSBCH is sent for slot k + 4 while the master is at k: keep this clock no later than that
                # (and jump forward when behind: the first PSBCH may have waited in the queue)
                ref = psbch[-1] - 4
                now = None if ue.t0 is None else int((time.monotonic() - ue.t0) / SLOT_S)
                if now is None or now < ref - 2:
                    ue.set_clock(ref)
            if ue.t0 is not None:
                ue.give([r for _t, _s, r in its])
            out = ue.take_outbox()
            last_seq, last_reply = seq, pack_items(out)
            air.send(AIR_SLP, seq, last_reply)
            st["polls"] += 1
            st["down"] += len(its)
            st["up"] += len(out)
        now = time.monotonic()
        if now - t_stat >= a.stats:
            dt = now - t_stat
            u = ue.st
            log(f"UE slot {ue.slot}: {u['slots'] / dt:.0f} slots/s in {u['runs'] / dt:.0f} RUNs/s; "
                f"air {st['polls'] / dt:.0f} polls/s, {st['repeats']} repeats, items in {st['down']} out {st['up']}, "
                f"backlog {len(ue.outbox)}, dropped {ue.dropped}")
            st = dict.fromkeys(st, 0)
            ue.st = dict.fromkeys(ue.st, 0)
            t_stat = now


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", choices=["master", "peer"], required=True)
    ap.add_argument("--ue-port", type=int, required=True, help="UDP port the local nrUE's NRLINK_SL points at")
    ap.add_argument("--air", choices=["board", "udp"], default="board")
    ap.add_argument("--board", help="board serial (air board)")
    ap.add_argument("--channel", type=lambda x: int(x, 0), default=0x5A, help="GenFSK channel (0x2A = 2402 MHz)")
    ap.add_argument("--power", type=lambda x: int(x, 0), default=0x1F)
    ap.add_argument("--air-port", type=int, default=5700, help="air udp: local port")
    ap.add_argument("--peer", default="127.0.0.1:5701", help="air udp: the other bridge")
    ap.add_argument("--reply-timeout", type=float, default=0.03, help="master: seconds to wait for the peer's reply")
    ap.add_argument("--frag-gap", type=float, default=0.012, help="master: keep waiting while packets arrive this often")
    ap.add_argument("--retries", type=int, default=4, help="master: sends of one poll before giving up on it")
    ap.add_argument("--idle-poll", type=float, default=0.002, help="master: pause after an empty exchange")
    ap.add_argument("--pace", type=float, default=0.001, help="seconds between RUNs")
    ap.add_argument("--stats", type=float, default=5.0)
    a = ap.parse_args()
    air = Air(a)
    ue = UE(a.ue_port)
    ue.wait_hello()
    threading.Thread(target=ue.pace, args=(a.pace,), daemon=True).start()
    (master if a.role == "master" else peer)(a, ue, air)


if __name__ == "__main__":
    main()
