#!/usr/bin/env python3
"""gNB bridge: OAI PNF (nrlink_gnb.c) <-UDP-> this <-serial-> gNB board ~~2.4 GHz~~ UE boards.

Per slot: broadcast the DL_SLOT message over the air, and when the slot has UL PDUs, poll each UE board
for what its UE transmits in that slot. The collected UL items go back to the PNF as UL_RESULT, which
also releases the PNF to the next slot.

LEDs on the gNB board: RGB = traffic class of the UE being served in the slot (eMBB blue, URLLC red,
mMTC green), LED1 on for an uplink allocation, off for downlink.
"""
import argparse
import queue
import socket
import struct
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import config  # noqa: E402
from nrlink import (AIR_DL, AIR_POLL, AIR_UL, Board, Reassembler, fragments, items, MSG_HDR, NRLINK_MAGIC,  # noqa: E402
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

    def show_leds(self, served_dl, served_ul):
        """served_*: ue_id -> bytes in this slot. The board blinks the colour of each UE served in the last second
        in turn, every 0.4 s: blue for eMBB (nrUE1), red for URLLC (nrUE2), so with both active it alternates."""
        now = time.time()
        for u in set(served_dl) | set(served_ul):
            self.recent[u] = now
        if now - self.led_t >= 0.4:
            self.led_t = now
            active = sorted(u for u, t in self.recent.items() if now - t < 1.0)
            if active:
                self.rr = (self.rr + 1) % len(active)
                u = active[self.rr]
                color = config.CLASS_LED[config.UES[u][2]][0]
                self.board.led_blink(color, 1)
                self.led = (color, u in served_ul)
            else:
                self.led = (OFF, False)
        if now - self.t_led >= 0.1:  # mirror of the board LEDs for the dashboard, 10 Hz
            self.t_led = now
            ue = next((u for u in config.UES if config.CLASS_LED[config.UES[u][2]][0] == self.led[0]), 0)
            telemetry(k="led", ue=ue if self.led[0] != OFF else 0, ul=bool(self.led[1]))

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
              f"polls {s['polls']} (timeouts {s['poll_timeouts']})  {self.timing(dt)}  skipped {s.get('skipped', 0)}  rnti->ue {self.rnti_ue}", flush=True)
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
    ap.add_argument("--ues", default="", help="UE ids to poll, e.g. 1,2 (default: every UE in config.UES). A UE that is "
                    "not on the air costs two poll timeouts every UL slot")
    GnbBridge(ap.parse_args()).run()
