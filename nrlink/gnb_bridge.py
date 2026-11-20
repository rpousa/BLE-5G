#!/usr/bin/env python3
"""gNB bridge: OAI PNF (nrlink_gnb.c) <-UDP-> this <-serial-> gNB board ~~2.4 GHz~~ UE boards.

Per slot: broadcast the DL_SLOT message over the air, and when the slot has UL PDUs, poll each UE board
for what its UE transmits in that slot. The collected UL items go back to the PNF as UL_RESULT, which
also releases the PNF to the next slot.

LEDs on the gNB board follow the slot scheme: green while the NR slots run (normal communication), blue during each
sidelink window (--sl-window). Every phase is one flash with a short dark gap before the next, so each period shows as
a green flash then a blue flash. Without sidelink windows it flashes green every 0.4 s while slots flow.

--sl-mode1: the gNB schedules the sidelink (NR sidelink mode 1, TS 38.321 5.22.1.1). Each in-coverage UE reports
its sidelink buffer in its uplink (IT_SLBSR, as the SL-BSR MAC CE); at the end of every TDD period this bridge grants
each reporting UE a slice of the sidelink window sized to its buffer (offset and length, as DCI 3_0's time resource),
and the rest of the window to the pool that UEs out of coverage use (mode 2, an exceptional pool). The window is
only as long as the grants plus the pool, up to --sl-window ms: a period with little sidelink traffic hands the air
back to the NR slots. The pool's size follows its measured load (the channel busy ratio the gNB board hears in it).
UE boards with nrlink v7 hold their staged sidelink packets until their grant.
"""
import argparse
import queue
import socket
import struct
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import config  # noqa: E402
from nrlink import (SL_GRANT, SL_POOL_ID, IT_SLBSR, GREEN, BLUE, AIR_DL, AIR_POLL, AIR_UL, AIR_SLWIN, AIR_HDR, Board, Reassembler, fragments, items, MSG_HDR, NRLINK_MAGIC,  # noqa: E402
                    NRLINK_DL_SLOT, NRLINK_UL_RESULT, IT_TB, IT_ULGRANT, IT_PUSCH, OFF, telemetry)


class GnbBridge:
    def __init__(self, args):
        self.ues = [int(u) for u in args.ues.split(",") if u] or list(config.UES)
        self.args = args
        self.board = Board(config.GNB_BOARD)
        print("gNB board:", self.board.ping(), flush=True)
        self.board.configure(config.RADIO_CHANNEL, config.RADIO_POWER)
        self.board.led_solid(OFF, False)   # clear LED1 left on by an older bridge
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind(("0.0.0.0", config.GNB_UDP_PORT))
        self.reasm = Reassembler()
        self.rnti_ue = {}            # C-RNTI -> ue_id, learnt from the UEs' UL messages
        self.led = (OFF, False)
        self.led_t = 0.0
        self.recent = {}      # ue_id -> last time it was served
        self.rr = 0
        self.stats = dict(slots=0, dl_bytes=0, ul_bytes=0, polls=0, poll_timeouts=0, air_pkts=0)
        self.slot_ms = {0: [0.0, 0], 1: [0.0, 0]}   # time in handle_slot, DL-only slots (0) and slots with UL (1)
        self.t_stats = time.time()
        self.tele = self.new_tele()
        self.t_tele = self.t_led = time.time()
        self.nr_t0 = None     # start of the current NR stretch (end of the last sidelink window)
        self.nr_ms = 40.0     # length of the last NR stretch, for the green flash
        self.bsr = {}         # mode 1: ue_id -> (time, bytes, packets, flags) from its last SL-BSR
        self.pool_ms = args.sl_pool_min
        self.cbr = 0.0
        self.m1 = self.new_m1()
        self.sl_rr = 0

    @staticmethod
    def new_m1():
        return dict(win=0, win_ms=0.0, pool_ms=0.0, grant_ms={}, bsr_n=0, pool_air_ms=0.0, cut=0, ooc=0)

    @staticmethod
    def new_tele():
        return dict(slots=0, ul_slots=0, air_pkts=0, dl_bytes=0, ul_bytes=0, polls=0, timeouts=0,
                    ue={u: dict(dl_slots=0, ul_slots=0, dl_bytes=0, ul_bytes=0, rssi=None) for u in config.UES})

    def send_tele(self):
        now = time.time()
        if now - self.t_tele < 1.0:
            return
        dt, t = now - self.t_tele, self.tele
        telemetry(k="gnb", slots_s=round(t["slots"] / dt, 1), ul_slots_s=round(t["ul_slots"] / dt, 1),
                  air_pps=round(t["air_pkts"] / dt), dl_kbps=round(t["dl_bytes"] * 8 / dt / 1e3, 2),
                  ul_kbps=round(t["ul_bytes"] * 8 / dt / 1e3, 2), polls=t["polls"], timeouts=t["timeouts"],
                  ue={str(u): dict(v, dl_kbps=round(v["dl_bytes"] * 8 / dt / 1e3, 2),
                                   ul_kbps=round(v["ul_bytes"] * 8 / dt / 1e3, 2)) for u, v in t["ue"].items()},
                  rnti={str(u): f"{r:04x}" for r, u in self.rnti_ue.items()})
        self.tele, self.t_tele = self.new_tele(), now

    def air_send(self, pkts):
        for p in pkts:
            self.board.tx(p)
            self.stats["air_pkts"] += 1
            self.tele["air_pkts"] += 1

    def poll(self, ue_id, sfn, slot):
        """Ask one UE board for its UL transmission of (sfn, slot); returns its UL_SLOT message or None."""
        self.stats["polls"] += 1
        self.air_send(fragments(AIR_POLL, ue_id, sfn, slot, b""))
        deadline = time.time() + self.args.poll_timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                self.stats["poll_timeouts"] += 1
                return None
            try:
                rssi, _ts, pkt = self.board.rx.get(timeout=left)
            except queue.Empty:
                continue
            self.tele["air_pkts"] += 1
            if len(pkt) > 1 and pkt[1] in self.tele["ue"]:
                self.tele["ue"][pkt[1]]["rssi"] = rssi
            got = self.reasm.add(pkt)
            if not got:
                deadline = max(deadline, time.time() + 0.010)  # more fragments are coming
                continue
            (kind, uid, rsfn, rslot), msg = got
            if kind == AIR_UL and uid == ue_id and (rsfn, rslot) == (sfn, slot):
                return msg

    GAP_MS = 6   # dark gap at the end of each flash, so consecutive green/blue phases read as separate flashes

    def led_phase(self, color, dur_ms):
        """Light the gNB board LED in <color> for dur_ms (minus the gap), and mirror it to the dashboard."""
        self.board.led_solid(color, False, max(5, int(dur_ms) - self.GAP_MS))
        self.led = (color, False)
        now = time.time()
        if now - self.t_led >= 0.1:   # mirror for the dashboard, at most 10 Hz
            self.t_led = now
            telemetry(k="led", ue=0, ul=False, c={GREEN: "green", BLUE: "blue"}.get(color, "off"))

    def show_leds(self, served_dl, served_ul):
        """Slot scheme LED: green = NR slots. With sidelink windows the NR stretch is flashed when it starts (right
        after a window, see sl_window_led), so here only the no-sidelink case: one green flash every 0.4 s."""
        if self.args.sl_window > 0:
            if self.nr_t0 is None:   # first slot: start the green phase
                self.nr_t0 = time.time()
                self.led_phase(GREEN, self.nr_ms)
            return
        now = time.time()
        if now - self.led_t >= 0.4:
            self.led_t = now
            self.led_phase(GREEN, 200)

    def sl_window_led(self, start, width=None):
        """Called when a sidelink window starts (start=True: blue for the window) and when it ends (green for the
        NR stretch that follows, as long as the last one lasted)."""
        now = time.time()
        if start:
            if self.nr_t0 is not None:
                self.nr_ms = 0.7 * self.nr_ms + 0.3 * (now - self.nr_t0) * 1e3
            self.led_phase(BLUE, width or self.args.sl_window)
        else:
            self.nr_t0 = now
            self.led_phase(GREEN, self.nr_ms)

    def handle_slot(self, msg, addr):
        t_in = time.perf_counter()
        _m, _t, sfn, slot, flags, _r, _n = MSG_HDR.unpack_from(msg)
        served_dl, served_ul = {}, {}
        for t, body in items(msg):
            if t == IT_TB:
                rnti, ln = struct.unpack_from("<HH", body)
                self.stats["dl_bytes"] += ln
                if rnti in self.rnti_ue:
                    served_dl[self.rnti_ue[rnti]] = served_dl.get(self.rnti_ue[rnti], 0) + ln
            elif t == IT_ULGRANT:
                rnti, ln = struct.unpack_from("<HH", body)
                if rnti in self.rnti_ue:
                    served_ul[self.rnti_ue[rnti]] = served_ul.get(self.rnti_ue[rnti], 0) + ln
        self.show_leds(served_dl, served_ul)
        tl = self.tele
        tl["slots"] += 1
        tl["ul_slots"] += bool(flags & 1)
        tl["dl_bytes"] += sum(served_dl.values())
        for u, n in served_dl.items():
            tl["ue"][u]["dl_slots"] += 1
            tl["ue"][u]["dl_bytes"] += n
        for u in served_ul:
            tl["ue"][u]["ul_slots"] += 1

        if self.args.skip_empty and not flags & 1 and not _n:
            # dynamic slotting: a downlink-only slot with nothing in it (no SSB, DCI or TB) is not sent over the
            # air. The nrUE runs the slots it did not hear when the next DL_SLOT arrives (nrlink_ue.c catches up
            # to 40 slots), so it stays in step; the PNF still gets its UL_RESULT below.
            self.stats["skipped"] = self.stats.get("skipped", 0) + 1
        else:
            self.air_send(fragments(AIR_DL, 0, sfn, slot, msg))

        result_items, n_items = b"", 0
        if flags & 1:
            for ue_id in self.ues:
                # every UE is polled every UL slot: a skipped UE looks like PUSCH/PUCCH DTX to the
                # scheduler, which declares UL failure after a few of them. One retry on timeout.
                ul = self.poll(ue_id, sfn, slot)
                if ul is None:
                    ul = self.poll(ue_id, sfn, slot)
                self.tele["polls"] += 1
                if ul is None:
                    self.tele["timeouts"] += 1
                    continue
                rnti = MSG_HDR.unpack_from(ul)[5] if len(ul) >= MSG_HDR.size else 0
                if rnti:
                    self.rnti_ue[rnti] = ue_id
                for t, body in items(ul):
                    if t == IT_SLBSR:
                        if len(body) >= 7:
                            nb, npk, fl = struct.unpack_from("<IHB", body)
                            self.bsr[ue_id] = (time.time(), nb, npk, fl)
                            self.m1["bsr_n"] += 1
                        continue
                    if t == IT_PUSCH:
                        n = struct.unpack_from("<HBH", body)[2]
                        self.stats["ul_bytes"] += n
                        self.tele["ul_bytes"] += n
                        self.tele["ue"][ue_id]["ul_bytes"] += n
                    result_items += struct.pack("<BH", t, len(body)) + body
                    n_items += 1
        out = MSG_HDR.pack(NRLINK_MAGIC, NRLINK_UL_RESULT, sfn, slot, 0, 0, n_items) + result_items
        self.sock.sendto(out, addr)
        self.stats["slots"] += 1
        acc = self.slot_ms[flags & 1]
        acc[0] += (time.perf_counter() - t_in) * 1e3
        acc[1] += 1
        if self.args.sl_window > 0 and slot % 10 == 9 and self.args.sl_mode1:
            self.sl_window_mode1(sfn, slot)
        elif self.args.sl_window > 0 and slot % 10 == 9:
            # AGV relay chain: after the period's last uplink slot, grant the sidelink a quiet window (as a mode 1
            # resource pool would): announce it, then stay off the air. The PNF waits for UL_RESULT meanwhile.
            self.air_send([AIR_HDR.pack(AIR_SLWIN, 0, sfn, slot, 0, 1) + struct.pack("<H", self.args.sl_window)])
            self.sl_window_led(True)
            time.sleep(self.args.sl_window / 1000)
            self.sl_window_led(False)
            self.stats["sl_windows"] = self.stats.get("sl_windows", 0) + 1

    def sl_window_mode1(self, sfn, slot):
        """Mode 1: grants from the SL-BSRs, then the pool, in one window announcement; quiet while it lasts."""
        a, now = self.args, time.time()
        guard, t0 = a.sl_guard, 1.0                     # ms between grants; first grant 1 ms after the announcement
        want = []
        ues = sorted(u for u, (t, nb, npk, fl) in self.bsr.items() if now - t < 0.25 and nb > 0)
        if ues:
            k = self.sl_rr % len(ues)
            ues = ues[k:] + ues[:k]                      # who goes first rotates
            self.sl_rr += 1
        for u in ues:
            _t, nb, npk, fl = self.bsr[u]
            # 1 Mbit/s with sync, header and CRC; per packet the ramp and the receivers' host transfer (see nrlink v7)
            need = (nb * 10 + npk * 750 + 300) / 1000
            want.append([u, max(1.0, min(need, a.sl_window))])
        # a relay reports (SL-BSR flag bit 0) that it carries an out-of-coverage UE: that UE can only use the pool,
        # and alone in it, starting at random in its first half, it never makes the CBR high enough to grow it
        ooc = any(fl & 1 for u, (t, _nb, _np, fl) in self.bsr.items() if now - t < 0.25)
        pool = max(self.pool_ms, a.sl_pool_ooc) if ooc else self.pool_ms
        self.m1["ooc"] += ooc
        budget = a.sl_window - t0 - pool - guard * len(want) - 0.5
        tot = sum(w for _u, w in want)
        if want and tot > budget:                        # more asked than fits: everyone gets the same share of it
            f = max(0.0, budget) / tot
            for w in want:
                w[1] = max(1.0, w[1] * f)
            self.m1["cut"] += 1
        grants, off = [], t0
        for u, w in want:
            grants.append((u, off, w))
            off += w + guard
        grants.append((SL_POOL_ID, off, pool))
        off += pool
        width = off + 0.5
        body = struct.pack("<HB", int(-(-width // 1)), len(grants)) + b"".join(
            SL_GRANT.pack(u, int(o * 10), int(w * 10)) for u, o, w in grants)
        while not self.board.rx.empty():                # what was heard before the window is not the pool's load
            try:
                self.board.rx.get_nowait()
            except queue.Empty:
                break
        self.air_send([AIR_HDR.pack(AIR_SLWIN, 0, sfn, slot, 0, 1) + body])
        self.sl_window_led(True, width)
        time.sleep(width / 1000)
        # the pool's load: the airtime of what the gNB board heard from UEs that had no grant in this window
        granted = {u for u, _o, _w in grants}
        busy = 0.0
        while True:
            try:
                _r, _ts, pkt = self.board.rx.get_nowait()
            except queue.Empty:
                break
            if len(pkt) >= 2 and pkt[0] in (0x0A, 0x0B, 0x09) and pkt[1] + 1 not in granted:
                busy += ((len(pkt) + 9) * 8 + 180) / 1000
        cbr = busy / pool if pool else 0.0
        self.cbr = 0.6 * self.cbr + 0.4 * cbr
        if self.cbr > a.sl_cbr_hi:
            self.pool_ms = min(a.sl_pool_max, self.pool_ms * 1.25 + 0.5)
        elif self.cbr < a.sl_cbr_lo:
            self.pool_ms = max(a.sl_pool_min, self.pool_ms - 0.5)
        self.sl_window_led(False)
        m = self.m1
        m["win"] += 1
        m["win_ms"] += width
        m["pool_ms"] += pool
        m["pool_air_ms"] += busy
        for u, _o, w in grants:
            if u != SL_POOL_ID:
                m["grant_ms"][u] = m["grant_ms"].get(u, 0.0) + w
        self.stats["sl_windows"] = self.stats.get("sl_windows", 0) + 1

    def m1_line(self):
        m, n = self.m1, max(1, self.m1["win"])
        g = " ".join(f"UE{u} {v / n:.1f}" for u, v in sorted(m["grant_ms"].items()))
        line = (f"mode 1: window {m['win_ms'] / n:.1f} ms (pool {m['pool_ms'] / n:.1f}, CBR {self.cbr:.2f}), grant ms/window "
                f"{g or 'none'}, {m['bsr_n']} SL-BSRs, {m['cut']} windows full, {m['ooc']} with a remote UE")
        telemetry(k="slm1", win=m["win"], win_ms=round(m["win_ms"] / n, 2), pool_ms=round(m["pool_ms"] / n, 2),
                  cbr=round(self.cbr, 3), grant_ms={str(u): round(v / n, 2) for u, v in m["grant_ms"].items()},
                  bsr=m["bsr_n"], cut=m["cut"], ooc=m["ooc"])
        self.m1 = self.new_m1()
        return line

    def timing(self, dt):
        """'slot ms DL x UL y rest z': bridge time per DL-only slot and per slot with UL, and the rest of each
        slot's wall time (PNF, VNF, UDP) on average; resets the counters."""
        (d, nd), (u, nu) = self.slot_ms[0], self.slot_ms[1]
        n = nd + nu
        rest = (dt * 1e3 - d - u) / n if n else 0.0
        self.slot_ms = {0: [0.0, 0], 1: [0.0, 0]}
        return f"slot ms DL {d / max(1, nd):.1f} UL {u / max(1, nu):.1f} rest {rest:.1f}"

    def print_stats(self):
        now = time.time()
        dt = now - self.t_stats
        if dt < self.args.stats:
            return
        s = self.stats
        print(f"[gnb] {s['slots'] / dt:5.1f} slots/s  DL {s['dl_bytes'] * 8 / dt / 1e3:6.1f} kb/s  "
              f"UL {s['ul_bytes'] * 8 / dt / 1e3:6.1f} kb/s  air {s['air_pkts'] / dt:5.0f} pkt/s  "
              f"polls {s['polls']} (timeouts {s['poll_timeouts']})  {self.timing(dt)}  skipped {s.get('skipped', 0)}  sl windows {s.get('sl_windows', 0)}  rnti->ue {self.rnti_ue}", flush=True)
        if self.args.sl_mode1:
            print("[gnb] " + self.m1_line(), flush=True)
        self.stats = {k: 0 for k in s}
        self.t_stats = now

    def run(self):
        print(f"gNB bridge listening on udp/{config.GNB_UDP_PORT}", flush=True)
        self.sock.settimeout(1.0)
        while True:
            try:
                msg, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                self.print_stats()
                self.send_tele()
                continue
            if len(msg) >= MSG_HDR.size and msg[0] == NRLINK_MAGIC and msg[1] == NRLINK_DL_SLOT:
                try:
                    self.handle_slot(msg, addr)
                except Exception as e:  # noqa: BLE001  the PNF times out the slot and carries on
                    print(f"[gnb] slot dropped ({e!r})", flush=True)
            self.print_stats()
            self.send_tele()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--poll-timeout", type=float, default=0.025, help="seconds to wait for a UE's answer")
    ap.add_argument("--stats", type=float, default=5.0, help="statistics period in seconds")
    ap.add_argument("--skip-empty", action="store_true", help="dynamic slotting: do not broadcast empty "
                    "downlink-only slots; the nrUEs fill them in themselves")
    ap.add_argument("--sl-window", type=int, default=0, help="ms of quiet for the sidelink after slot 9 of every "
                    "period, announced on the air (AGV relay chain); 0 = off")
    ap.add_argument("--sl-mode1", action="store_true", help="the gNB schedules the sidelink from the UEs' SL-BSRs "
                    "(grants inside the window, the rest a pool for UEs out of coverage); --sl-window is then the most")
    ap.add_argument("--sl-guard", type=float, default=0.8, help="mode 1: ms between two grants")
    ap.add_argument("--sl-pool-ooc", type=float, default=8.0,
                    help="mode 1: smallest pool while a relay reports an out-of-coverage UE (SL-BSR flag), ms")
    ap.add_argument("--sl-pool-min", type=float, default=4.0, help="mode 1: smallest pool, ms")
    ap.add_argument("--sl-pool-max", type=float, default=16.0, help="mode 1: largest pool, ms")
    ap.add_argument("--sl-cbr-hi", type=float, default=0.45, help="mode 1: pool load above which the pool grows")
    ap.add_argument("--sl-cbr-lo", type=float, default=0.2, help="mode 1: pool load below which it shrinks")
    ap.add_argument("--ues", default="", help="UE ids to poll, e.g. 1,2 (default: every UE in config.UES). A UE that is "
                    "not on the air costs two poll timeouts every UL slot")
    GnbBridge(ap.parse_args()).run()
