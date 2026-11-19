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

Daisy chain (option A): relay nodes between master and peer. A relay has no UE: it answers the polls of the link
above it like a peer and polls the link below it like a master, passing items through. Each hop is a link with its own
id (carried in the air header) and its own stop-and-wait ARQ; all boards may share one channel, since every bridge
ignores other links' packets. The relay polls the hop below right after it has answered a poll from above, so the
chain carries one exchange at a time; the master's polls wait for a quiet channel (--sense, longer than the peer's
turnaround) so they do not land in the lower hop's exchange.

  master (UE1)  --link 0-->  relay  --link 1-->  peer (UE2)
  sl_bridge.py --role master --ue-port 5600 --board A --link 0 --sense 0.015
  sl_bridge.py --role relay  --board B --link 0                  (up: link 0, down: link 1, same board)
  sl_bridge.py --role peer   --ue-port 5601 --board C --link 1
  relay with its lower hop over UDP: --down-air udp --down-air-port 5710 --down-peer 127.0.0.1:5711
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
air_max = AIR_MAX                   # set by --air-packets
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


class AirDev:
    """A board (fragmented, 255 B packets) or a UDP socket (no boards). Messages carry a link id (the ue_id byte of the
    air header) and go to that link's queue; one board can serve two links (a relay)."""

    def __init__(self, mode, board=None, channel=0x5A, power=0x1F, port=None, peer=None):
        self.mode = mode
        self.queues = {}
        self.tx_pkts = self.rx_pkts = 0
        self.last_rx = 0.0       # time of the last radio packet heard, any link (a reply may still be coming in)
        self.txlock = threading.Lock()
        if mode == "board":
            self.board = nrlink.Board(board)
            log("board", board, self.board.ping())
            self.board.configure(channel, power)
            self.reasm = Reassembler()
            threading.Thread(target=self._board_rx, daemon=True).start()
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("0.0.0.0", port))
            host, p = peer.rsplit(":", 1)
            self.peer = (host, int(p))
            threading.Thread(target=self._udp_rx, daemon=True).start()

    def queue(self, link):
        return self.queues.setdefault(link, queue.Queue())

    debug = False

    def send(self, kind, link, seq, payload, sense=0.0):
        if AirDev.debug:
            log(f"tx kind {kind} link {link} seq {seq & 0xFFFF} {len(payload)} B")
        if sense and self.mode == "board":
            # carrier sense: wait (bounded) until nothing has been heard for `sense` seconds
            limit = time.monotonic() + 0.1
            while time.monotonic() < limit and time.monotonic() - self.last_rx < sense:
                time.sleep(0.0005)
        with self.txlock:
            if self.mode == "board":
                for p in fragments(kind, link, seq & 0xFFFF, 0, payload):
                    self.board.tx(p)
                    self.tx_pkts += 1
            else:
                self.sock.sendto(struct.pack("<BBH", kind, link, seq & 0xFFFF) + payload, self.peer)
                self.tx_pkts += 1

    def _board_rx(self):
        while True:
            _rssi, _ts, pkt = self.board.rx.get()
            self.rx_pkts += 1
            self.last_rx = time.monotonic()
            if AirDev.debug:
                h = nrlink.AIR_HDR.unpack_from(pkt)
                log(f"  rx pkt kind {h[0]} link {h[1]} seq {h[2]} frag {h[4]}/{h[5]}")
            got = self.reasm.add(pkt)
            if got:
                (kind, link, seq, _x), msg = got
                if kind in (AIR_SLM, AIR_SLP) and link in self.queues:
                    self.queues[link].put((kind, seq, msg))

    def _udp_rx(self):
        while True:
            data, _ = self.sock.recvfrom(65535)
            self.rx_pkts += 1
            kind, link, seq = struct.unpack_from("<BBH", data)
            if link in self.queues:
                self.queues[link].put((kind, seq, data[4:]))


class Air:
    """One link (hop) on an AirDev: what master() and peer() talk through."""

    def __init__(self, dev, link, sense):
        self.dev, self.link, self.sense = dev, link, sense
        self.q = dev.queue(link)

    @property
    def last_rx(self):
        return self.dev.last_rx

    def send(self, kind, seq, payload):
        # polls wait for a quiet channel; replies go at once (the poller is listening)
        self.dev.send(kind, self.link, seq, payload, self.sense if kind == AIR_SLM else 0.0)


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
            out, self.outbox = take(keep, air_max)
        return out

    def give(self, items):
        with self.lock:
            self.inbox += items


class RelaySide:
    """One side of a relay node, standing in for the UE that master() / peer() expect: items given to it (received
    on this side) go out on the other side; take_outbox() returns what the other side received."""

    def __init__(self):
        self.lock = threading.Lock()
        self.outbox = []
        self.other = None
        self.t0 = 0.0            # never None: received items are always accepted
        self.slot = 0
        self.st = dict(runs=0, slots=0, tx=0, rx=0)
        self.dropped = 0

    def set_clock(self, slot):
        pass

    def give(self, items):
        o, now = self.other, time.monotonic()
        with o.lock:
            o.outbox += [(now, x) for x in items]
            n = sum(len(x) for _t, x in o.outbox)
            while n > BACKLOG_MAX:
                n -= len(o.outbox.pop(0)[1])
                o.dropped += 1

    def take_outbox(self):
        # as at the ends: what waited too long here would arrive too late to be delivered, so it gets no airtime
        limit = time.monotonic() - MAX_AGE * SLOT_S
        with self.lock:
            keep = [x for t, x in self.outbox if t >= limit]
            self.dropped += len(self.outbox) - len(keep)
            out, rest = take(keep, air_max)
            self.outbox = self.outbox[len(self.outbox) - len(rest):] if rest else []
        return out


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


def master(a, ue, air, trigger=None):
    """Polls the peer. Stop and wait: a poll keeps its sequence number and payload until its reply arrives (late
    replies count), so items lost with a radio packet are sent again rather than lost.
    trigger (relay): poll only after the hop above has been served (or after relay_idle), so the chain carries one
    exchange at a time instead of the lower hop's idle polls filling the channel."""
    ue.set_clock(0)
    seq, payload, n_out, tries = 0, None, 0, 0
    st = dict(polls=0, replies=0, timeouts=0, resends=0, up=0, down=0, bytes=0)
    t_stat = time.monotonic()
    while True:
        if payload is None:
            if trigger is not None:
                trigger.wait(a.relay_idle)
                trigger.clear()
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
        if idle and trigger is None:
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


def peer(a, ue, air, on_reply=None):
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
            if on_reply:
                on_reply()
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
    ap.add_argument("--role", choices=["master", "peer", "relay"], required=True)
    ap.add_argument("--ue-port", type=int, help="UDP port the local nrUE's NRLINK_SL points at (master, peer)")
    ap.add_argument("--link", type=int, default=0, help="link id of this bridge's hop (relay: the hop above it)")
    ap.add_argument("--sense", type=float, default=0.0,
                    help="board, master: quiet time before each poll (in a chain, 0.015)")
    ap.add_argument("--down-sense", type=float, default=0.001, help="relay: quiet time before a poll of the hop below")
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
    ap.add_argument("--air-packets", type=int, default=20,
                    help="radio packets per air message at most (in a chain, about 6: one round of the chain must "
                         "stay well under the 100 ms an item may wait)")
    ap.add_argument("--relay-idle", type=float, default=0.05,
                    help="relay: poll the hop below at least this often when nothing comes from above")
    ap.add_argument("--pace", type=float, default=0.001, help="seconds between RUNs")
    ap.add_argument("--stats", type=float, default=5.0)
    ap.add_argument("--down-air", choices=["same", "board", "udp"], default="same",
                    help="relay: the hop below on the same board (default), another board, or UDP")
    ap.add_argument("--down-board", help="relay, --down-air board: its serial")
    ap.add_argument("--down-air-port", type=int, default=5710)
    ap.add_argument("--down-peer", default="127.0.0.1:5711")
    ap.add_argument("--debug-air", action="store_true", help="log every air message and radio packet")
    a = ap.parse_args()
    AirDev.debug = a.debug_air
    global air_max
    air_max = a.air_packets * 248 - 2
    dev = AirDev(a.air, a.board, a.channel, a.power, a.air_port, a.peer)
    if a.role == "relay":
        down_dev = {"same": lambda: dev,
                    "board": lambda: AirDev("board", a.down_board, a.channel, a.power),
                    "udp": lambda: AirDev("udp", port=a.down_air_port, peer=a.down_peer)}[a.down_air]()
        up_air, down_air = Air(dev, a.link, 0.0), Air(down_dev, a.link + 1, a.down_sense)
        up, down = RelaySide(), RelaySide()
        up.other, down.other = down, up
        log(f"relay: link {a.link} above ({a.air}), link {a.link + 1} below ({a.down_air})")
        served = threading.Event()
        threading.Thread(target=peer, args=(a, up, up_air, served.set), daemon=True).start()
        master(a, down, down_air, served)
        return
    air = Air(dev, a.link, a.sense)
    ue = UE(a.ue_port)
    ue.wait_hello()
    threading.Thread(target=ue.pace, args=(a.pace,), daemon=True).start()
    (master if a.role == "master" else peer)(a, ue, air)


if __name__ == "__main__":
    main()
