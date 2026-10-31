#!/usr/bin/env python3
"""UE bridge: UE board ~~2.4 GHz~~ <-serial-> this <-UDP-> OAI nrUE (nrlink_ue.c).

DL_SLOT messages received over the air go to the nrUE, which answers with the UL_SLOT message for its
TX slot; that is held until the gNB polls this UE for that slot, then transmitted over the air.

LED on the UE board: blinks its traffic class pattern (eMBB blue x1, URLLC red x2, mMTC green x3)
whenever the gNB serves this UE (a transport block for it in DL, or one sent by it in UL).
"""
import argparse
import queue
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import config  # noqa: E402
from nrlink import (AIR_DL, AIR_POLL, AIR_UL, Board, Reassembler, fragments, items, MSG_HDR, NRLINK_MAGIC,  # noqa: E402
                    NRLINK_UL_SLOT, NRLINK_HELLO, IT_TB, IT_PUSCH, telemetry)

WAIT_UL = 0.015  # s, how long a poll waits for the nrUE to report the slot (gNB side waits 25 ms)


class UeBridge:
    def __init__(self, ue_id):
        self.ue_id = ue_id
        serial_no, self.port, self.cls = config.UES[ue_id]
        self.color, self.blinks = config.CLASS_LED[self.cls]
        self.board = Board(serial_no)
        print(f"UE{ue_id} ({self.cls}) board {serial_no}:", self.board.ping(), flush=True)
        self.board.configure(config.RADIO_CHANNEL, config.RADIO_POWER)
        self.board.led_blink(self.color, self.blinks)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind(("0.0.0.0", self.port))
        self.ue_addr = None
        self.rnti = 0
        self.ul = {}  # (sfn, slot) -> UL_SLOT message from the nrUE
        self.lock = threading.Condition()
        self.reasm = Reassembler()
        self.stats = dict(dl_slots=0, dl_bytes=0, ul_bytes=0, polls=0, ul_sent=0, late=0)
        self.t_stats = time.time()
        self.tele = dict(dl_slots=0, dl_bytes=0, ul_bytes=0, polls=0, late=0, air_rx=0, air_tx=0)
        self.t_tele, self.rssi = time.time(), None

    def udp_loop(self):
        while True:
            msg, addr = self.sock.recvfrom(65535)
            if len(msg) < MSG_HDR.size or msg[0] != NRLINK_MAGIC:
                continue
            _m, t, sfn, slot, _f, rnti, _n = MSG_HDR.unpack_from(msg)
            if self.ue_addr != addr:
                print(f"UE{self.ue_id}: nrUE at {addr}", flush=True)
            self.ue_addr = addr
            if rnti:
                self.rnti = rnti
            if t == NRLINK_UL_SLOT:
                with self.lock:
                    self.ul[(sfn, slot)] = msg
                    self.lock.notify_all()
                    if len(self.ul) > 128:
                        for k in list(self.ul)[:-64]:
                            del self.ul[k]

    def served(self):
        self.board.led_blink(self.color, self.blinks)  # ignored by the board while a blink runs

    def on_dl(self, msg):
        self.stats["dl_slots"] += 1
        self.tele["dl_slots"] += 1
        for t, body in items(msg):
            if t == IT_TB:
                rnti, ln = struct.unpack_from("<HH", body)
                if self.rnti and rnti == self.rnti:
                    self.stats["dl_bytes"] += ln
                    self.tele["dl_bytes"] += ln
                    self.served()
        if self.ue_addr:
            self.sock.sendto(msg, self.ue_addr)

    def on_poll(self, sfn, slot):
        self.stats["polls"] += 1
        # the nrUE reports every UL slot, empty or not: if it has not yet, it is still processing, wait a bit
        with self.lock:
            if not self.lock.wait_for(lambda: (sfn, slot) in self.ul, timeout=WAIT_UL):
                self.stats["late"] += 1
                self.tele["late"] += 1
            msg = self.ul.get((sfn, slot))  # kept: a timed-out poll is retried
        if msg is None or MSG_HDR.unpack_from(msg)[6] == 0:
            msg = MSG_HDR.pack(NRLINK_MAGIC, NRLINK_UL_SLOT, sfn, slot, 0, self.rnti, 0)
        else:
            self.stats["ul_sent"] += 1
            for t, body in items(msg):
                if t == IT_PUSCH:
                    self.stats["ul_bytes"] += struct.unpack_from("<HBH", body)[2]
                    self.tele["ul_bytes"] += struct.unpack_from("<HBH", body)[2]
                    self.served()
        self.tele["polls"] += 1
        for p in fragments(AIR_UL, self.ue_id, sfn, slot, msg):
            self.board.tx(p)
            self.tele["air_tx"] += 1

    def print_stats(self):
        now = time.time()
        dt = now - self.t_stats
        if dt < 5:
            return
        s = self.stats
        print(f"[ue{self.ue_id} {self.cls}] rnti {self.rnti:04x}  {s['dl_slots'] / dt:5.1f} DL slots/s  "
              f"DL {s['dl_bytes'] * 8 / dt / 1e3:6.1f} kb/s  UL {s['ul_bytes'] * 8 / dt / 1e3:6.1f} kb/s  "
              f"polls {s['polls']} (with data {s['ul_sent']}, nrUE late {s['late']})", flush=True)
        self.stats = {k: 0 for k in s}
        self.t_stats = now

    def run(self):
        threading.Thread(target=self.udp_loop, daemon=True).start()
        print(f"UE{self.ue_id} bridge on udp/{self.port}", flush=True)
        while True:
            try:
                self.rssi, _ts, pkt = self.board.rx.get(timeout=1.0)
            except queue.Empty:
                self.print_stats()
                self.send_tele()
                continue
            self.tele["air_rx"] += 1
            try:
                got = self.reasm.add(pkt)
                if got:
                    (kind, uid, sfn, slot), msg = got
                    if kind == AIR_DL:
                        self.on_dl(msg)
                    elif kind == AIR_POLL and uid == self.ue_id:
                        self.on_poll(sfn, slot)
            except Exception as e:  # noqa: BLE001  a bad packet must not take the UE off the air
                print(f"UE{self.ue_id}: dropped a packet ({e!r})", flush=True)
            self.print_stats()
            self.send_tele()

    def send_tele(self):
        now = time.time()
        if now - self.t_tele < 1.0:
            return
        dt, t = now - self.t_tele, self.tele
        telemetry(k="ue", id=self.ue_id, cls=self.cls, rnti=f"{self.rnti:04x}", rssi=self.rssi,
                  dl_slots_s=round(t["dl_slots"] / dt, 1), dl_kbps=round(t["dl_bytes"] * 8 / dt / 1e3, 2),
                  ul_kbps=round(t["ul_bytes"] * 8 / dt / 1e3, 2), polls=t["polls"], late=t["late"],
                  air_rx_pps=round(t["air_rx"] / dt), air_tx_pps=round(t["air_tx"] / dt))
        self.tele = {k: 0 for k in t}
        self.t_tele = now


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ue_id", type=int, choices=sorted(config.UES))
    UeBridge(ap.parse_args().ue_id).run()
