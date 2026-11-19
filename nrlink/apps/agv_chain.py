#!/usr/bin/env python3
"""AGV relay chain over the nrlink boards: three AGVs, one gNB on a pole.

The AGV nearest the pole is the pivot: its board carries the fleet's NR link to the gNB (it answers the gNB's polls
for nrUE1 and passes the downlink slots to it). The others reach it over a sidelink chain of real radio hops between
the AGV boards: pivot <- relay <- remote, ordered by distance to the pole. Every AGV broadcasts a position beacon on
the sidelink every 0.5 s; the pivot applies the rule (nearest = pivot, next = relay, last = remote, 2 m hysteresis
held for 0.3 s) to the positions it heard. Board LEDs show the role: pivot blue, relay green, remote red.

Air sharing: all boards are on the nrlink channel. gnb_bridge --sl-window N stays off the air for N ms after slot 9 of
every period and announces the window (AIR_SLWIN); the chain transmits only inside those windows, one board at a time.

  UE PC:   agv_chain.py ue --core 192.168.10.223 --boards 2HOKMMVEX1WMP,GBAMNH4BQ3CSB,GLNAOWUSDREB4 --log run.jsonl
  UE PC, sidecar in oai-nrue1's netns:  agv_chain.py fwd --dev oaitun_ue1        (started by the ue role)
  core:    agv_chain.py clock                                                   (UDP clock for the one-way delays)
  core, sidecar in oai-ext-dn's netns:  agv_chain.py edge                       (JSON lines on stdout)
"""
import argparse
import json
import math
import os
import queue
import random
import select
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# ------------------------------------------------------------------------------------------- world (as on the page)
W, H = 48.0, 30.0
ROUTE = [(4, 4), (24, 4), (44, 4), (44, 26), (24, 26), (24, 12), (4, 12)]
CPV = [0, 1, 2, 3, 4, 6]
POLE = (22.0, 10.5)
# --topology cover: the gNB's signal on the floor. A log-distance path loss (10 dBm per resource element from the pole,
# n = 3.5 among racks), and a shielded cold store in the far corner (insulated steel panels) that adds up to 32 dB once an
# AGV is inside, ramping over the first 3 m past the door line. Below Q_SWITCH an AGV stops sending on its own UE (it
# is about to lose it), below Q_RLF for 1 s its UE loses the connection (radio link failure: the UE is stopped), and
# above Q_SWITCH for 2 s it attaches again.
COLD = (32.0, 17.0, 48.0, 30.0)        # x0, y0, x1, y1 (m)
COLD_DB, COLD_RAMP_M = 32.0, 3.0
Q_SWITCH, Q_RLF = -100.0, -110.0       # dBm


def rsrp(x, y):
    d = max(1.0, math.dist((x, y), POLE))
    depth = min(x - COLD[0], y - COLD[1], COLD[2] - x, COLD[3] - y)
    wall = 0.0 if depth <= 0 else COLD_DB * min(1.0, depth / COLD_RAMP_M)
    return 10.0 - (46.0 + 35.0 * math.log10(d)) - wall
SEGS = [(ROUTE[i], ROUTE[(i + 1) % len(ROUTE)]) for i in range(len(ROUTE))]
SEGL = [math.dist(a, b) for a, b in SEGS]
LOOP = sum(SEGL)
VS = [sum(SEGL[:i]) for i in range(len(SEGL))]
CPS = [VS[v] for v in CPV]
V_MAX, ACC, DEC, GAP_M = 1.2, 0.6, 1.0, 3.0
LEVELS = {"L1": (1, 5), "L2": (2, 10), "L3": (4, 20)}     # LiDAR Hz, detections Hz
NAMES = "ABC"
ROLES = ("pivot", "relay", "remote")
LED_OF_ROLE = (3, 2, 1)                                  # board LED: blue, green, red
LED_NAMES = {0: "off", 1: "red", 2: "green", 3: "blue"}
LIVE_KINDS = {"hold", "rlf", "serve", "uu", "start", "pos", "chain", "hop", "gen", "drop", "cmdrx", "clock", "cov", "attach", "egress", "stats", "led",
              "pc5", "pc5up", "pc5st", "disc", "pc5conn", "rur"}

APP = struct.Struct("<4sBBIdff")     # 'AGC1', agv, flow (0 det, 1 lidar, 2 cmd), seq, t (sender clock), x, y
CHH = struct.Struct("<BB")           # chain header: direction (0 up, 1 down), destination AGV
EDGE, EDGE_PORT, CLOCK_PORT = "192.168.70.132", 6501, 5298
FLOWS = ("det", "lidar", "cmd")


def pos_at(s):
    s %= LOOP
    for i, ((a, b), ln) in enumerate(zip(SEGS, SEGL)):
        if s <= ln or i == len(SEGS) - 1:
            f = min(1.0, s / ln)
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, math.atan2(b[1] - a[1], b[0] - a[0])
        s -= ln


def zone_entries(step=0.1):
    """Route positions (m along the loop) where the gNB signal falls below Q_SWITCH, going forward."""
    out, prev = [], rsrp(*pos_at(LOOP - step)[:2])
    for k in range(int(LOOP / step)):
        q = rsrp(*pos_at(k * step)[:2])
        if prev >= Q_SWITCH > q:
            out.append(k * step)
        prev = q
    return out


class Fleet:
    """The AGVs' motion, as in the page's simulation (same route, spacing, stops). platoon > 0: a convoy, A leading and
    B, C following at that gap (m); only the leader stops at the checkpoints, the followers keep the gap behind it."""

    def lag_room(self, i):
        """Platoon: an AGV with a session keeps the AGV behind it (A-B-C order) within the gap plus slack."""
        if not self.platoon or i == 2 or not self.wait_ok[i]:
            return math.inf
        lag = (self.a[i]["s"] - self.a[i + 1]["s"]) % LOOP
        return max(0.0, self.platoon + self.slack - lag)

    def __init__(self, rng, platoon=0.0):
        self.rng, self.platoon = rng, platoon
        self.hold = [None, None, None]                 # a stop point (m along the loop) an AGV must not pass, or None
        self.wait_ok = [False, False, False]           # may wait for the AGV behind it (set by the controller: it has a session)
        self.slack = 6.0                               # m over the platoon gap before an AGV waits for the one behind
        if platoon:
            self.a = [dict(s=2 + (2 - i) * platoon, v=0.0, dwell=0.0) for i in range(3)]
        else:
            self.a = [dict(s=i * LOOP / 3 + 2, v=0.0, dwell=0.0) for i in range(3)]
        for a in self.a:
            self.place(a)

    @staticmethod
    def place(a):
        a["x"], a["y"], a["h"] = pos_at(a["s"])
        a["d"] = math.dist((a["x"], a["y"]), POLE)

    def step(self, dt):
        for a in self.a:
            if a["dwell"] > 0:
                a["dwell"] -= dt
                if a["dwell"] <= 0:
                    a["s"] = (a["s"] + 0.05) % LOOP
                continue
            to_cp, cpi = min((((cs - a["s"]) % LOOP, k) for k, cs in enumerate(CPS) if (cs - a["s"]) % LOOP > 0.001))
            ahead = min(((b["s"] - a["s"]) % LOOP for b in self.a if b is not a and (b["s"] - a["s"]) % LOOP > 0),
                        default=math.inf)
            if self.platoon and ahead < LOOP / 2:      # a follower: no stops of its own, it keeps the gap
                to_cp = math.inf
                room = min(ahead - self.platoon, self.lag_room(self.a.index(a)))
                if self.hold[self.a.index(a)] is not None:
                    room = min(room, (self.hold[self.a.index(a)] - a["s"]) % LOOP)
                vmax = V_MAX * (1.25 if room > 0.5 else 1.0)
                if room <= a["v"] ** 2 / (2 * DEC) + 0.03:
                    a["v"] = max(0.0, a["v"] - DEC * dt)
                else:
                    a["v"] = min(vmax, a["v"] + ACC * dt)
                a["s"] = (a["s"] + max(0.0, min(a["v"] * dt, room))) % LOOP
                continue
            room = min(to_cp, ahead - GAP_M)
            room = min(room, self.lag_room(self.a.index(a)))
            if self.hold[self.a.index(a)] is not None:
                room = min(room, (self.hold[self.a.index(a)] - a["s"]) % LOOP)
            if room <= a["v"] ** 2 / (2 * DEC) + 0.03:
                a["v"] = max(0.0, a["v"] - DEC * dt)
            else:
                a["v"] = min(V_MAX, a["v"] + ACC * dt)
            step = max(0.0, min(a["v"] * dt, room))
            a["s"] = (a["s"] + step) % LOOP
            if to_cp - step < 0.1 and to_cp <= ahead - GAP_M:
                a["s"], a["v"], a["dwell"] = CPS[cpi], 0.0, 1 + 3 * self.rng.random()
        for a in self.a:
            self.place(a)


# ------------------------------------------------------------------------------------------------ UE side controller
class Controller:
    def __init__(self, args):
        import config
        import nrlink as N
        self.N, self.cfg, self.args = N, config, args
        self.rng = random.Random(args.seed)
        self.fleet = Fleet(self.rng, getattr(args, "platoon", 0.0))
        self.log_f = open(args.log, "a", buffering=1)
        self.log_n = 0
        self.leds = [None] * 3                         # (colour, blinking) each board's LED shows, as last set
        self.live = None                               # the core PC's live page: every event also goes there
        if args.live:
            h, _, p = args.live.rpartition(":")
            self.live = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (h, int(p)))
        self.boards = []
        for i, sn in enumerate(args.boards.split(",")):
            b = N.Board(sn)
            print(f"AGV {NAMES[i]} board {sn}: {b.ping()}", flush=True)
            b.configure(config.RADIO_CHANNEL, config.RADIO_POWER)
            self.boards.append(b)
        self.txlock = [threading.Lock() for _ in self.boards]
        self.reasm = [N.Reassembler() for _ in self.boards]
        d = [a["d"] for a in self.fleet.a]
        self.chain = sorted(range(3), key=lambda i: d[i])
        self.view = [[None] * 3 for _ in range(3)]     # view[i][j]: position of AGV j as AGV i last heard it
        self.pend = None
        self.lock = threading.RLock()
        # Uu (nrUE1) state, as ue_bridge.py
        if args.uu == "fleet":
            self.usock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.usock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
            self.usock.bind(("0.0.0.0", config.UES[1][1]))
        self.ue_addr, self.rnti, self.ul = None, 0, {}
        self.ulcv = threading.Condition()
        # sidelink air
        self.airq = queue.Queue()                      # (t_queued, node, [packets], meta)
        self.win_end, self.win_seen = 0.0, None
        self.wincv = threading.Condition()
        self.mseq = 0
        self.st = dict(windows=0, sl_pkts=0, sl_msgs=0, sl_drop=0, beacons_rx=0, polls=0, late=0, dl_slots=0, uu_up=0,
                       cmd_rx=0, win_ms_used=0.0)
        self.clock_off = 0.0
        self.uu_busy = False                           # the pivot's board is answering a poll (it cannot receive)
        # app sockets (unix, to the forwarder in nrUE1's netns)
        os.makedirs(args.w, mode=0o777, exist_ok=True)
        os.chmod(args.w, 0o777)
        self.ctl_path = os.path.join(args.w, "ctl.sock")
        try:
            os.unlink(self.ctl_path)
        except OSError:
            pass
        self.us = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.us.bind(self.ctl_path)
        os.chmod(self.ctl_path, 0o777)
        self.seq = [[0, 0] for _ in range(3)]
        self.set_leds()
        self.log("start", platoon=getattr(args, "platoon", 0), hold=getattr(args, "hold", None), boards=args.boards.split(","), level=args.level, chain=self.chain, window_ms=args.window_ms,
                 hyst=args.hyst, age_ms=args.age_ms, guard_ms=args.guard_ms, uu=args.uu, pc5=getattr(args, "pc5", "ctl"),
                 topology=getattr(args, "topology", "chain"), pick=getattr(args, "pick", None), relay=getattr(args, "relay", "app"))

    def log(self, k, **kw):
        kw["k"], kw["t"] = k, round(time.time(), 4)
        line = json.dumps(kw, separators=(",", ":"))
        self.log_f.write(line + "\n")
        if self.live and k in LIVE_KINDS:
            try:
                self.live[0].sendto(line.encode(), self.live[1])
            except OSError:
                pass
        self.log_n += 1
        if self.args.log_max_mb and self.log_n % 20000 == 0 and self.log_f.tell() > self.args.log_max_mb * 1e6:
            os.replace(self.args.log, self.args.log + ".1")            # running for days (live): keep two files
            self.log_f = open(self.args.log, "a", buffering=1)

    def led(self, i, colour, blink=False):
        """Sets board i's RGB LED (caller holds txlock[i]) and logs it when it changes: the live page shows it."""
        b = self.boards[i]
        if blink:
            b.led_blink(colour, 1)
        else:
            b.led_solid(colour)
        if self.leds[i] != (colour, blink):
            self.leds[i] = (colour, blink)
            self.log("led", agv=i, board=self.args.boards.split(",")[i],
                     c=LED_NAMES[colour], blink=int(blink), role=ROLES[self.role(i)])

    def role(self, i):
        return self.chain.index(i)

    def led_colour(self, i):
        return LED_OF_ROLE[self.role(i)]

    def set_leds(self):
        for i, b in enumerate(self.boards):
            with self.txlock[i]:
                self.led(i, self.led_colour(i))          # RGB LED only: the colour of the role on the page

    # ---------------------------------------------------------------- Uu: the pivot's board is nrUE1's radio
    def uu_udp(self):
        N = self.N
        while True:
            msg, addr = self.usock.recvfrom(65535)
            if len(msg) < N.MSG_HDR.size or msg[0] != N.NRLINK_MAGIC:
                continue
            _m, t, sfn, slot, _f, rnti, _n = N.MSG_HDR.unpack_from(msg)
            if self.ue_addr != addr:
                print(f"nrUE1 at {addr}", flush=True)
            self.ue_addr = addr
            if rnti:
                self.rnti = rnti
            if t == N.NRLINK_UL_SLOT:
                with self.ulcv:
                    self.ul[(sfn, slot)] = msg
                    self.ulcv.notify_all()
                    if len(self.ul) > 128:
                        for k in list(self.ul)[:-64]:
                            del self.ul[k]

    def uu_poll(self, i, sfn, slot):
        N = self.N
        self.st["polls"] += 1
        self.uu_busy = True
        with self.ulcv:
            if not self.ulcv.wait_for(lambda: (sfn, slot) in self.ul, timeout=0.015):
                self.st["late"] += 1
            msg = self.ul.get((sfn, slot))
        if msg is None or N.MSG_HDR.unpack_from(msg)[6] == 0:
            msg = N.MSG_HDR.pack(N.NRLINK_MAGIC, N.NRLINK_UL_SLOT, sfn, slot, 0, self.rnti, 0)
        with self.txlock[i]:
            for p in N.fragments(N.AIR_UL, 1, sfn, slot, msg):
                self.boards[i].tx(p)
        self.uu_busy = False

    def on_uu(self, i, pkt):
        """A gNB packet heard by board i. Fleet mode: only the pivot's board is nrUE1's radio."""
        N = self.N
        if self.role(i) != 0:
            return
        got = self.reasm[i].add(pkt)
        if not got:
            return
        (k, u, s1, s2), msg = got
        if k == N.AIR_DL:
            self.st["dl_slots"] += 1
            if self.ue_addr:
                self.usock.sendto(msg, self.ue_addr)
        elif k == N.AIR_POLL and u == 1:
            self.uu_poll(i, s1, s2)

    # ---------------------------------------------------------------- receive, per board
    def rx_loop(self, i):
        N, b = self.N, self.boards[i]
        while True:
            _rssi, _ts, pkt = b.rx.get()
            if len(pkt) < N.AIR_HDR.size:
                continue
            kind, uid, sfn, slot, _frag, _nf = N.AIR_HDR.unpack_from(pkt)
            try:
                if kind == N.AIR_SLWIN:
                    ms = struct.unpack_from("<H", pkt, N.AIR_HDR.size)[0]
                    with self.wincv:
                        if self.win_seen != (sfn, slot):
                            self.win_seen = (sfn, slot)
                            self.win_end = time.time() + ms / 1000 - 0.002
                            self.st["windows"] += 1
                            self.wincv.notify_all()
                    continue
                if kind == N.AIR_BEACON:
                    x, y, ts = struct.unpack_from("<ffd", pkt, N.AIR_HDR.size)
                    self.view[i][uid] = (x, y, time.time())
                    self.st["beacons_rx"] += 1
                    continue
                if kind in (N.AIR_DL, N.AIR_POLL):
                    self.on_uu(i, pkt)
                    continue
                if kind == N.AIR_CH:
                    if uid == i:
                        got = self.reasm[i].add(pkt)
                        if got:
                            (_k, _u, mseq, src), data = got
                            self.on_hop(i, src, data)
                    continue
                self.on_other(i, kind, uid, pkt)
            except Exception as e:   # noqa: BLE001
                print(f"AGV {NAMES[i]}: dropped a packet ({e!r})", flush=True)

    def on_other(self, i, kind, uid, pkt):
        pass

    # ---------------------------------------------------------------- the chain
    def next_hop(self, node, dirn, dest):
        """None: deliver here (dest reached, or the pivot for uplink)."""
        c = self.chain
        k = c.index(node)
        if dirn == 0:
            return None if k == 0 else c[k - 1]
        kd = c.index(dest)
        if kd == k:
            return None
        return c[k + 1] if kd > k else c[k - 1]

    def forward(self, node, dirn, dest, app):
        """The app message is at `node`: deliver it or queue it for the next hop."""
        nxt = self.next_hop(node, dirn, dest)
        agv, flow, seq = APP.unpack_from(app)[1:4]
        if nxt is None:
            if dirn == 0:
                self.deliver_up(node, app, agv, flow, seq)
            else:
                tr = time.time()
                te = APP.unpack_from(app)[4]
                self.st["cmd_rx"] += 1
                self.log("cmdrx", agv=agv, s=seq, te=te, tr=round(tr, 4))
            return
        self.hop_send(node, nxt, CHH.pack(dirn, dest) + app, dict(agv=agv, f=flow, s=seq, a=node, b=nxt))

    def hop_send(self, node, nxt, data, meta):
        """One hop, node -> nxt: here the controller's own radio packets (AIR_CH) in the gNB's windows."""
        with self.lock:
            self.mseq = (self.mseq + 1) & 0xFFFF
            ms = self.mseq
        self.airq.put((time.time(), node, self.N.fragments(self.N.AIR_CH, nxt, ms, node, data), meta))

    def window_jobs(self):
        """Air jobs (t, node, packets, meta) to send first in the window that just opened."""
        return []

    def extra_stats(self):
        return ""

    def deliver_up(self, node, app, agv, flow, seq):
        """The pivot has an uplink message: into the PDU session."""
        try:
            self.us.sendto(app, os.path.join(self.args.w, "fwd.sock"))
            self.st["uu_up"] += 1
            self.log("uu", agv=agv, f=flow, s=seq, via=node)
        except OSError as e:
            self.log("drop", agv=agv, f=flow, s=seq, at=node, why=f"fwd: {e}")

    def on_hop(self, node, src, data):
        dirn, dest = CHH.unpack_from(data)
        app = data[CHH.size:]
        agv, flow, seq = APP.unpack_from(app)[1:4]
        self.st["sl_msgs"] += 1
        self.log("hop", agv=agv, f=flow, s=seq, a=src, b=node)
        self.forward(node, dirn, dest, app)

    def air_loop(self):
        """Sends queued sidelink packets, one board at a time, only inside the gNB's windows."""
        cur = None                                      # (t_q, node, packets left, meta)
        last_node, last_end, last_len, guard = None, 0.0, 0, self.args.guard_ms / 1000
        while True:
            with self.wincv:
                self.wincv.wait_for(lambda: time.time() < self.win_end - 0.006, timeout=1.0)
                end = self.win_end
            t_open = time.time()
            jobs = self.window_jobs()
            while time.time() < end - 0.006:
                if cur is None:
                    try:
                        # the queue first (beacons, controller hops), then the window's jobs (OAI sidelink items): the
                        # beacons are a packet each, and at L2 the sidelink alone would fill every window
                        t_q, node, pkts, meta = self.airq.get_nowait()
                    except queue.Empty:
                        if not jobs:
                            break
                        t_q, node, pkts, meta = jobs.pop(0)
                    if time.time() - t_q > self.args.age_ms / 1000:
                        self.st["sl_drop"] += 1
                        if meta and "agv" in meta:
                            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why="PC5 age")
                        elif meta:
                            self.st["sl_job_age"] = self.st.get("sl_job_age", 0) + 1
                        continue
                    cur = [t_q, node, list(pkts), meta]
                node = cur[1]
                if guard:
                    while self.uu_busy and time.time() < end - 0.006:
                        time.sleep(0.0005)
                    # every board receives every packet and spends ~10 us a byte passing it to its host before it
                    # handles the next one: pace by the last packet's UART time, plus the guard
                    time.sleep(max(0.0, last_end + last_len * 10e-6 + guard - time.time()))
                pkt = cur[2].pop(0)
                with self.txlock[node]:
                    st = self.boards[node].tx(pkt)
                if st:
                    self.st["tx_err"] = self.st.get("tx_err", 0) + 1
                last_node, last_end, last_len = node, time.time(), len(pkt) + 8
                self.st["sl_pkts"] += 1
                if not cur[2]:
                    cur = None
            self.st["win_ms_used"] += (time.time() - t_open) * 1000
            with self.wincv:                            # wait for the next window
                self.wincv.wait_for(lambda: self.win_end > end, timeout=1.0)

    # ---------------------------------------------------------------- election, on the pivot's view
    def elect(self, now):
        piv = self.chain[0]
        d = []
        for j in range(3):
            p = self.view[piv][j] if j != piv else (self.fleet.a[j]["x"], self.fleet.a[j]["y"], now)
            if p is None or now - p[2] > 2.0:          # not heard for 2 s: keep the chain as it is
                self.pend = None
                return
            d.append(math.dist(p[:2], POLE))
        c, h = self.chain, self.args.hyst
        best = min(range(3), key=lambda j: d[j])
        want = None
        if best != c[0] and d[c[0]] - d[best] > h:
            want = [best] + sorted((j for j in c if j != best), key=lambda j: d[j])
        elif d[c[1]] - d[c[2]] > h:
            want = [c[0], c[2], c[1]]
        if not want:
            self.pend = None
            return
        if want[0] != c[0] and not self.can_change_pivot(now):
            self.pend = None
            return
        if not self.pend or self.pend[0] != want:
            self.pend = (want, now)
            return
        if now - self.pend[1] < 0.3:
            return
        old, self.chain, self.pend = self.chain, want, None
        what = (f"pivot {NAMES[old[0]]} -> {NAMES[want[0]]}" if old[0] != want[0]
                else f"relay {NAMES[old[1]]} -> {NAMES[want[1]]}")
        print(f"{time.strftime('%T')} {what}: chain {'-'.join(NAMES[j] for j in want)}", flush=True)
        self.log("chain", old=old, new=want, d=[round(x, 2) for x in d], what=what)
        self.on_chain(old, want)
        self.set_leds()

    def can_change_pivot(self, now):
        return True

    def cmd_entry(self, via=None):
        return self.chain[0]

    def on_chain(self, old, new):
        pass

    def on_ctl(self, p):
        pass

    def tick(self, now):
        pass

    # ---------------------------------------------------------------- app traffic
    def send_app(self, i, flow, size):
        self.seq[i][flow] += 1
        a = self.fleet.a[i]
        tg = time.time()
        app = APP.pack(b"AGC1", i, flow, self.seq[i][flow], tg, a["x"], a["y"]) + bytes(max(0, size - APP.size))
        self.log("gen", agv=i, f=flow, s=self.seq[i][flow], n=len(app) + 28, role=self.role(i))
        self.forward(i, 0, 0, app)

    def ctl_loop(self):
        """Commands from the edge, via the forwarder: they enter at the pivot."""
        while True:
            p = self.us.recv(2048)
            via = None
            if p[:2] == b"DL" and len(p) > 3:
                via, p = p[2] - 1, p[3:]
            if len(p) < APP.size or p[:4] != b"AGC1":
                self.on_ctl(p)
                continue
            dest = p[4]
            if dest > 2:
                continue
            self.forward(self.cmd_entry(via), 1, dest, p)

    def clock_loop(self):
        while True:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            best = None
            for _ in range(20):
                t1 = time.time()
                s.sendto(struct.pack("<d", t1), (self.args.core, CLOCK_PORT))
                try:
                    p = s.recv(64)
                except socket.timeout:
                    continue
                t4 = time.time()
                if best is None or t4 - t1 < best[0]:
                    best = (t4 - t1, struct.unpack_from("<d", p, 8)[0] - (t1 + t4) / 2)
                time.sleep(0.01)
            s.close()
            if best:
                self.clock_off = best[1]
                self.log("clock", off=round(best[1], 5), rtt_ms=round(best[0] * 1000, 3))
            time.sleep(30)

    def sidecar(self):
        name = "agvc-fwd"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        r = subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "container:oai-nrue1",
                            "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "-v", f"{self.args.w}:/w",
                            "nrlink-run:local", "-u", "/a/agv_chain.py", "fwd", "--dev", "oaitun_ue1"],
                           capture_output=True, text=True)
        print("forwarder:", (r.stdout or r.stderr).strip()[:80], flush=True)

    def run(self):
        for fn in ((self.uu_udp,) if self.args.uu == "fleet" else ()) + (self.air_loop, self.ctl_loop, self.clock_loop):
            threading.Thread(target=fn, daemon=True).start()
        for i in range(len(self.boards)):
            threading.Thread(target=self.rx_loop, args=(i,), daemon=True).start()
        self.sidecar()
        lid_hz, det_hz = LEVELS[self.args.level]
        t_prev = time.time()
        nxt_det = [t_prev + self.rng.random() * 0.2 for _ in range(3)]
        nxt_lid = [t_prev + self.rng.random() for _ in range(3)]
        t_beacon = t_pos = t_stats = t_prev
        bseq = 0
        while True:
            now = time.time()
            self.fleet.step(now - t_prev)
            t_prev = now
            if now >= t_beacon:
                t_beacon += self.args.beacon_s
                bseq = (bseq + 1) & 0xFFFF
                for i, a in enumerate(self.fleet.a):
                    pkt = self.N.AIR_HDR.pack(self.N.AIR_BEACON, i, bseq, 0, 0, 1) + struct.pack("<ffd", a["x"], a["y"], now)
                    self.airq.put((now, i, [pkt], None))
                self.elect(now)
            for i in range(3):
                if now >= nxt_det[i]:
                    nxt_det[i] += 1 / det_hz * (0.9 + 0.2 * self.rng.random())
                    self.send_app(i, 0, 30 + 8 * self.rng.randrange(6))
                if now >= nxt_lid[i]:
                    nxt_lid[i] += 1 / lid_hz
                    self.send_app(i, 1, 752)
            self.tick(now)
            if now - t_pos >= 0.2:
                t_pos = now
                self.log("pos", a=[[round(a["x"], 2), round(a["y"], 2), round(a["h"], 3), round(a["d"], 2),
                                    1 if a["dwell"] > 0 else 0] for a in self.fleet.a], chain=self.chain,
                         **({"q": [round(q, 1) for q in self.q]} if hasattr(self, "q") else {}))
            if now - t_stats >= 5:
                dt, t_stats = now - t_stats, now
                s = self.st
                line = (f"windows {s['windows'] / dt:.1f}/s (used {s['win_ms_used'] / max(1, s['windows']):.1f} ms)  "
                        f"sl {s['sl_pkts'] / dt:.0f} pkt/s, {s['sl_msgs']} hops, {s['sl_drop']} dropped, "
                        f"queue {self.airq.qsize()}  beacons heard {s['beacons_rx']}  Uu: {s['dl_slots'] / dt:.0f} DL "
                        f"slots/s, {s['polls']} polls ({s['late']} late), {s['uu_up']} up  cmds {s['cmd_rx']}  txerr {s.get('tx_err', 0)}  "
                        f"chain {'-'.join(NAMES[j] for j in self.chain)}" + self.extra_stats())
                print(time.strftime("%T"), line, flush=True)
                self.log("stats", **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()},
                         dt=round(dt, 2), q=self.airq.qsize(), line=line)
                self.st = {k: 0 for k in s}
                self.st["win_ms_used"] = 0.0
            time.sleep(0.02)


# ------------------------------------------------------------------------------------------------ option 2: a UE per AGV
class PerAgvUu(Controller):
    """Each AGV has its own nrUE (oai-nrue1..3, the UE on its own board). Only the pivot is in the gNB's coverage:
    its board passes its UE the slots and answers the gNB's polls with the UE's uplink. A UE out of coverage is
    stopped (its container), and its board answers its polls at once with nothing, which is what the gNB hears from
    a UE that is not there. When an AGV becomes pivot its UE is started and attaches for real: sync on the SSB,
    random access, RRC setup, registration, PDU session. Messages reaching the pivot meanwhile are dropped
    ('no session'), and the pivot does not change again until the attach is over."""

    def __init__(self, args):
        import config
        self.ues = []
        for i in range(3):
            so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            so.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
            so.bind(("0.0.0.0", config.UES[i + 1][1]))
            self.ues.append(dict(sock=so, addr=None, rnti=0, ul={}, cv=threading.Condition(), cov=False, phase="off",
                                 t={}, gen=0))
        self.blink_t = self.ka_t = 0.0
        self.egress = None                             # the AGV whose UE carries the fleet's traffic now
        super().__init__(args)

    def cont(self, i):
        return f"oai-nrue{i + 1}"

    def set_leds(self):
        for i, b in enumerate(self.boards):
            r = self.role(i)
            if self.ues[i]["phase"] == "starting":
                continue                               # blinking in its role's colour: tick()
            with self.txlock[i]:
                self.led(i, self.led_colour(i))

    def update_egress(self):
        up = [i for i in self.chain if self.ues[i]["phase"] == "up"]
        if self.args.in_coverage > 1:
            # a UE that has just attached starts at MCS 0 and some of its first grants fail: leave the traffic on a UE
            # that has been up for a while, if there is one, until it has settled
            now = time.time()
            settled = [i for i in up if now - self.ues[i]["t"].get("up", 0) >= self.args.egress_settle]
            new = settled[0] if settled else (self.egress if self.egress in up else (up[0] if up else None))
        else:
            new = self.chain[0] if self.chain[0] in up else None
        if new != self.egress:
            self.log("egress", agv=new, old=self.egress, chain=self.chain)
            print(f"{time.strftime('%T')} egress: {'-' if new is None else 'AGV ' + NAMES[new] + ' (nrUE' + str(new + 1) + ')'}", flush=True)
            self.egress = new

    def tick(self, now):
        if self.args.in_coverage > 1:
            want = set(self.chain[:self.args.in_coverage])
            self.update_egress()
            for i, u in enumerate(self.ues):
                if i in want and not u["cov"]:
                    self.cover(i)
                elif u["cov"] and i not in want and i != self.egress:
                    self.uncover(i)               # make before break: only once another UE carries the traffic
            if now - self.ka_t >= 0.2 and self.args.keepalive:
                self.ka_t = now
                for i, u in enumerate(self.ues):  # keep a standby UE's uplink in use: the gNB raises its MCS
                    if u["phase"] == "up" and i != self.egress:
                        try:
                            self.us.sendto(b"KA" + bytes([i]) + bytes(57), os.path.join(self.args.w, f"fwd{i + 1}.sock"))
                        except OSError:
                            pass
        else:
            self.update_egress()
        if now - self.blink_t >= 0.5:
            self.blink_t = now
            for i, u in enumerate(self.ues):
                if u["phase"] == "starting":
                    with self.txlock[i]:
                        self.led(i, self.led_colour(i), blink=True)
                    if now - u["t"]["cov"] > self.args.attach_timeout:
                        self.log("attach", agv=i, ue=i + 1, ok=False, why="timeout", **self.rel(u))
                        print(f"{time.strftime('%T')} AGV {NAMES[i]}: nrUE{i + 1} did not attach in {self.args.attach_timeout:.0f} s: restarting it", flush=True)
                        self.uncover(i)
                        if self.role(i) == 0:
                            self.cover(i)

    @staticmethod
    def rel(u):
        t0 = u["t"].get("cov")
        return {k: round(v - t0, 3) for k, v in u["t"].items() if k != "cov"} if t0 else {}

    # ---- the UEs' radio side
    def uu_udp_k(self, i):
        N, u = self.N, self.ues[i]
        while True:
            msg, addr = u["sock"].recvfrom(65535)
            if len(msg) < N.MSG_HDR.size or msg[0] != N.NRLINK_MAGIC:
                continue
            _m, t, sfn, slot, _f, rnti, _n = N.MSG_HDR.unpack_from(msg)
            if u["addr"] != addr:
                u["t"].setdefault("first_msg", time.time())
            u["addr"] = addr
            if rnti and not u["rnti"]:
                u["t"].setdefault("rnti", time.time())
            if rnti:
                u["rnti"] = rnti
            if t == N.NRLINK_UL_SLOT:
                with u["cv"]:
                    u["ul"][(sfn, slot)] = msg
                    u["cv"].notify_all()
                    if len(u["ul"]) > 128:
                        for k in list(u["ul"])[:-64]:
                            del u["ul"][k]

    def on_uu(self, i, pkt):
        N, u = self.N, self.ues[i]
        got = self.reasm[i].add(pkt)
        if not got:
            return
        (k, uid, s1, s2), msg = got
        if k == N.AIR_DL:
            if u["cov"] and u["addr"]:
                self.st["dl_slots"] += 1
                u["sock"].sendto(msg, u["addr"])
        elif k == N.AIR_POLL and uid == i + 1:
            self.st["polls"] += 1
            ans = None
            if u["cov"] and u["addr"]:
                self.uu_busy = True
                with u["cv"]:
                    if not u["cv"].wait_for(lambda: (s1, s2) in u["ul"], timeout=0.015):
                        self.st["late"] += 1
                    ans = u["ul"].get((s1, s2))
            else:
                self.st["dtx"] = self.st.get("dtx", 0) + 1
            if ans is None or N.MSG_HDR.unpack_from(ans)[6] == 0:
                ans = N.MSG_HDR.pack(N.NRLINK_MAGIC, N.NRLINK_UL_SLOT, s1, s2, 0, u["rnti"] if u["cov"] else 0, 0)
            with self.txlock[i]:
                for p in N.fragments(N.AIR_UL, i + 1, s1, s2, ans):
                    self.boards[i].tx(p)
            self.uu_busy = False

    # ---- coverage
    def docker(self, *a):
        return subprocess.run(["docker", *a], capture_output=True, text=True)

    def cover(self, i):
        u = self.ues[i]
        u["gen"] += 1
        gen = u["gen"]
        u.update(cov=True, phase="starting", addr=None, rnti=0, ul={}, t={"cov": time.time()})
        self.log("cov", agv=i, ue=i + 1, on=1)
        print(f"{time.strftime('%T')} AGV {NAMES[i]}: in coverage, starting nrUE{i + 1}", flush=True)

        def go():
            self.docker("start", self.cont(i))
            if gen != u["gen"]:
                return
            u["t"]["started"] = time.time()
            name = f"agvc-fwd{i + 1}"
            self.docker("rm", "-f", name)
            opts, image, extra = self.fwd_opts(i)
            self.docker("run", "-d", "--rm", "--name", name, "--network", f"container:{self.cont(i)}", *opts, "--entrypoint", "python3",
                        "-v", f"{HERE}:/a:ro", "-v", f"{self.args.w}:/w", image, "-u", "/a/agv_chain.py", "fwd",
                        "--dev", "oaitun_ue1", "--k", str(i + 1), *extra)
        threading.Thread(target=go, daemon=True).start()

    def fwd_opts(self, i):
        """docker run options, image and extra fwd arguments of AGV i's UE-side sidecar (L3Relay: NAT and routes)."""
        return [], "nrlink-run:local", []

    def uncover(self, i):
        u = self.ues[i]
        u["gen"] += 1
        was = u["phase"]
        u.update(cov=False, phase="off", addr=None, rnti=0)
        self.log("cov", agv=i, ue=i + 1, on=0, was=was)
        print(f"{time.strftime('%T')} AGV {NAMES[i]}: out of coverage, stopping nrUE{i + 1}", flush=True)

        def go():
            self.docker("rm", "-f", f"agvc-fwd{i + 1}")
            self.docker("stop", "-t", "1", self.cont(i))
        threading.Thread(target=go, daemon=True).start()

    def on_ctl(self, p):
        if p[:3] == b"RDY" and len(p) >= 4:
            i = p[3] - 1
            u = self.ues[i]
            if u["phase"] == "starting":
                u["phase"] = "up"
                u["t"]["up"] = time.time()
                ip = p[4:].decode(errors="replace")
                self.log("attach", agv=i, ue=i + 1, ok=True, ip=ip, **self.rel(u))
                r = self.rel(u)
                print(f"{time.strftime('%T')} AGV {NAMES[i]}: nrUE{i + 1} attached, {ip}, session up {r.get('up', 0):.2f} s after coverage "
                      f"(container {r.get('started', 0):.2f}, first slot {r.get('first_msg', 0):.2f}, RNTI {r.get('rnti', 0):.2f})", flush=True)
                self.set_leds()

    def can_change_pivot(self, now):
        return self.args.in_coverage > 1 or all(u["phase"] != "starting" for u in self.ues)

    def on_chain(self, old, new):
        if old[0] != new[0] and self.args.in_coverage == 1:
            self.uncover(old[0])
            self.cover(new[0])

    def cmd_entry(self, via=None):
        return self.egress if self.egress is not None else self.chain[0]

    def next_hop(self, node, dirn, dest):
        if dirn == 0:                                  # uplink ends at the egress AGV, normally the pivot
            c, e = self.chain, self.cmd_entry()
            k, ke = c.index(node), c.index(e)
            if k == ke:
                return None
            return c[k - 1] if ke < k else c[k + 1]
        return super().next_hop(node, dirn, dest)

    def deliver_up(self, node, app, agv, flow, seq):
        u = self.ues[node]
        if u["phase"] != "up":
            self.log("drop", agv=agv, f=flow, s=seq, at=node, why="no session")
            return
        try:
            self.us.sendto(app, os.path.join(self.args.w, f"fwd{node + 1}.sock"))
            self.st["uu_up"] += 1
            self.log("uu", agv=agv, f=flow, s=seq, via=node)
        except OSError as e:
            self.log("drop", agv=agv, f=flow, s=seq, at=node, why=f"fwd: {e}")

    def sidecar(self):
        for i in range(3):
            self.docker("rm", "-f", f"agvc-fwd{i + 1}")
            self.docker("stop", "-t", "1", self.cont(i))
        for i in range(3):
            threading.Thread(target=self.uu_udp_k, args=(i,), daemon=True).start()
        self.cover(self.chain[0])


# ------------------------------------------------------------------------------------------------ option 3: OAI's NR sidelink
SL_LINKS = [(0, 1), (1, 2), (2, 0)]   # PC5 pairs (sync reference AGV, nearby AGV): A-B, B-C, C-A
SL_PORT = 6600                        # UDP port of the hop messages over the sidelink's IP (oaitun)
SL_IP = ("10.0.0.1", "10.0.0.100")    # the sidelink UEs' tun addresses: sync reference, nearby UE
AIR_SLX = 0x0B                        # one board's sidelink items for the window: n parts, each (link, side, len, items)
SLX_PART = struct.Struct("<BBH")


class Pc5Oai(PerAgvUu):
    """Option 3: the hops between AGVs go over OAI's NR sidelink (release tree, mode 2). Every pair of AGVs has a PC5
    link: a sync-reference sidelink nrUE on one AGV and a nearby sidelink nrUE on the other (6 sidelink UEs, two per
    AGV), so a role change only re-routes: no sidelink UE restarts. Each sidelink UE's PHY is nrlink_sl.c; this
    controller paces it in real time (as sl_bridge.py) and carries its PSBCH, SCI and transport blocks between the two
    AGV boards, inside the gNB's sidelink windows. A hop is an IP/UDP packet over the sidelink's tun (SDAP, PDCP, RLC,
    MAC); each AGV's computer (this controller) routes it on: a layer-3 relay. Uu is option 2 (a UE per AGV)."""

    def __init__(self, args):
        import sl_bridge as SB
        self.SB = SB
        SB.air_max = args.sl_air_packets * 248 - 2
        SB.MAX_AGE = args.sl_max_age
        SB.log = lambda *a: print(time.strftime("%T"), "sl:", *a, flush=True)
        self.sl = []
        for l, (m, p) in enumerate(SL_LINKS):
            self.sl.append(dict(l=l, agv=(m, p), ue=(SB.UE(5600 + 2 * l), SB.UE(5601 + 2 * l)), ip=[None, None],
                                seq=0, st=dict(tx=0, rx=0, txB=0, rxB=0, psbch=0)))
        self.sljobs = []
        self.slpending = set()
        super().__init__(args)
        self.slreasm = [self.N.Reassembler() for _ in self.boards]

    # ---- the sidelink UEs and their forwarders
    def sl_name(self, l, side, fwd=False):
        return f"agvc-sl{'f' if fwd else ''}{l}{'mp'[side]}"

    def sl_start(self, l, side):
        L, ue = self.sl[l], self.sl[l]["ue"][side]
        B, C = "/oai-ran/build_sl", "/oai-ran/targets/PROJECTS/NR-SIDELINK/CONF"
        cpus = self.args.sl_cpus.split(",")[(2 * l + side) % len(self.args.sl_cpus.split(","))]
        conf = "sl_sync_ref_nofb.conf --sync-ref" if side == 0 else "sl_ue1_nofb.conf"
        name = self.sl_name(l, side)
        self.docker("rm", "-f", name, self.sl_name(l, side, True))
        r = self.docker("run", "-d", "--name", name, "--network", "sl-net", "--ip", f"172.30.99.{20 + 2 * l + side}",
                        "--cpuset-cpus", cpus, "--cap-add", "NET_ADMIN", "--cap-add", "SYS_NICE", "--ulimit", "core=0",
                        "--device", "/dev/net/tun", "-e", f"NRLINK_SL=172.30.99.1:{5600 + 2 * l + side}", "--entrypoint", "bash",
                        "-w", B, "-e", f"LD_LIBRARY_PATH={B}", "-v", f"{self.args.sl_tree}:/oai-ran", "ran-base:local", "-c",
                        f"./nr-uesoftmodem -O {C}/{conf.split()[0]} --sa --sl-mode 2 {' '.join(conf.split()[1:])} "
                        f"--thread-pool -1,-1,-1,-1 > {B}/{name}.log 2>&1")
        print(f"{time.strftime('%T')} PC5 {NAMES[L['agv'][0]]}-{NAMES[L['agv'][1]]}: {name} on AGV {NAMES[L['agv'][side]]} "
              f"({'sync reference' if side == 0 else 'nearby UE'}): {(r.stdout or r.stderr).strip()[:12]}", flush=True)
        self.sl_attached(l, side, name)
        opts, image, extra = self.slfwd_opts(l, side)
        self.docker("run", "-d", "--rm", "--name", self.sl_name(l, side, True), "--network", f"container:{name}", *opts,
                    "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "-v", f"{self.args.w}:/w", image, "-u",
                    "/a/agv_chain.py", "slfwd", "--link", str(l), "--side", str(side), "--peer", SL_IP[1 - side], *extra)

        def pace():
            ue.wait_hello()
            if side == 0:
                ue.set_clock(0)          # the sync reference's clock; the nearby UE's follows its PSBCH
            ue.pace(self.args.sl_pace)
        threading.Thread(target=pace, daemon=True).start()

    def sl_attached(self, l, side, name):
        """The sidelink UE's container exists (L3Relay: put it on its AGV's LAN)."""

    def slfwd_opts(self, l, side):
        return [], "nrlink-run:local", []

    def sidecar(self):
        super().sidecar()
        for l in range(3):
            for side in (0, 1):
                self.sl_start(l, side)
        self.log("pc5", links=[[l, m, p] for l, (m, p) in enumerate(SL_LINKS)], mode=2, tree=self.args.sl_tree)

    def link_of(self, a, b):
        for L in self.sl:
            if set(L["agv"]) == {a, b}:
                return L, L["agv"].index(a)
        raise KeyError((a, b))

    # ---- a hop: an IP packet over the sidelink
    def hop_send(self, node, nxt, data, meta):
        L, side = self.link_of(node, nxt)
        if L["ip"][side] is None or L["ip"][1 - side] is None:
            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why="PC5 not up")
            return
        try:
            self.us.sendto(data, os.path.join(self.args.w, f"sl{L['l']}{'mp'[side]}.sock"))
            self.log("pc5tx", agv=meta["agv"], f=meta["f"], s=meta["s"], a=node, b=nxt, l=L["l"])
        except OSError as e:
            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why=f"slfwd: {e}")

    def on_ctl(self, p):
        if p[:3] == b"SLH" and len(p) > 5:
            L, side = self.sl[p[3]], p[4]
            self.on_hop(L["agv"][side], L["agv"][1 - side], p[5:])
        elif p[:3] == b"SLR" and len(p) > 5:
            L, side = self.sl[p[3]], p[4]
            ip = p[5:].decode(errors="replace")
            if L["ip"][side] != ip:
                L["ip"][side] = ip
                self.log("pc5up", l=L["l"], side=side, agv=L["agv"][side], ip=ip)
                print(f"{time.strftime('%T')} PC5 {NAMES[L['agv'][0]]}-{NAMES[L['agv'][1]]}: AGV {NAMES[L['agv'][side]]}'s "
                      f"sidelink UE up, {ip}", flush=True)
        else:
            super().on_ctl(p)

    # ---- the sidelink UEs' slots over the air: one message per board and window, carrying both of its UEs' items
    def window_jobs(self):
        SB, N, now = self.SB, self.N, time.time()
        busy = {j[1] for j in self.sljobs if j[3]}
        for i in range(3):
            if i in busy:
                continue                               # its last message is still on the air
            parts, budget = [], self.SB.air_max
            for L in self.sl:
                for side in (0, 1):
                    ue = L["ue"][side]
                    if L["agv"][side] != i or ue.addr is None or ue.t0 is None:
                        continue
                    out = ue.take_outbox()
                    if not out:
                        continue
                    msg = SB.pack_items(out)
                    parts.append(SLX_PART.pack(L["l"], side, len(msg)) + msg)
                    L["st"]["tx"] += len(out)
                    L["st"]["txB"] += len(msg)
            if not parts:
                continue
            self.slseq = (getattr(self, "slseq", 0) + 1) & 0xFFFF
            pkts = N.fragments(AIR_SLX, i, self.slseq, 0, bytes([len(parts)]) + b"".join(parts))
            self.sljobs.append((now, i, pkts, dict(sl=1)))
        return self.sljobs

    def on_other(self, i, kind, uid, pkt):
        SB = self.SB
        if kind != AIR_SLX or uid > 2 or uid == i:
            return
        got = self.slreasm[i].add(pkt)
        if not got:
            return
        data, off = got[1], 1
        for _ in range(data[0]):
            l, side, n = SLX_PART.unpack_from(data, off)
            off += SLX_PART.size
            msg = data[off:off + n]
            off += n
            L = self.sl[l]
            rside = 1 - side                           # the receiving side of that link
            if L["agv"][rside] != i:
                continue                               # another AGV's link
            its = SB.unpack_items(msg)
            ue = L["ue"][rside]
            if rside == 1:
                psbch = [s for t, s, _r in its if t == SB.SLI_PSBCH]
                if psbch:
                    L["st"]["psbch"] += 1
                    ref = psbch[-1] - 4
                    cur = None if ue.t0 is None else int((time.monotonic() - ue.t0) / SB.SLOT_S)
                    if cur is None or cur < ref - 2:
                        ue.set_clock(ref)
            if ue.t0 is not None and ue.addr is not None:
                ue.give([r for _t, _s, r in its])
                L["st"]["rx"] += len(its)
                L["st"]["rxB"] += len(msg)

    def extra_stats(self):
        out = []
        for L in self.sl:
            s, (um, up) = L["st"], L["ue"]
            out.append(f"{NAMES[L['agv'][0]]}{NAMES[L['agv'][1]]} {s['tx']}/{s['rx']} it {s['txB'] // 1000}/{s['rxB'] // 1000} kB "
                       f"drop {um.dropped}+{up.dropped}")
            self.log("pc5st", l=L["l"], **s, drop=[um.dropped, up.dropped], slot=[um.slot, up.slot])
            L["st"] = dict.fromkeys(s, 0)
        return "  PC5 " + ", ".join(out)


# ------------------------------------------------------------------------------------------------ option 4: two gateways
class DualGw:
    """Two AGVs connected to the gNB (the two nearest the pole, both UEs attached: gateway 1 and gateway 2), and the
    third (remote) one sidelink hop from either. Each gateway sends its own traffic over its own UE; the remote chooses,
    per message, which gateway to send through (--pick). An AGV whose UE has a settled session always sends directly:
    also an old gateway that has just become the remote, until the new gateway's UE is up (make before break). Commands
    come back the way the AGV's last message went (the edge answers each AGV at the address it last heard it from)
    and go from that gateway straight to their AGV. What the remote knows of the gateways is what their beacons say,
    every --beacon-s: is its session up and settled, and how many bytes it has sent up since the last beacon.
    LEDs: the remote red; the gateway the remote sends through now (NR and sidelink) blue; the other gateway (NR only)
    green; blinking in that colour while the AGV's UE attaches. --pick sticky keeps the remote on one gateway for at least
    --sticky-s and switches when the other's last beacon shows it sent under 60 % as much, or the current one is not ready."""

    def __init__(self, args):
        args.in_coverage = 2
        self.uu_b = [0, 0, 0]                          # bytes each UE has sent up since the last beacon
        self.gw = [dict(ready=False, rate=0.0) for _ in range(3)]   # what the last beacons said
        self.gw_t = time.time()
        self.alt = 0
        self.picks = [0, 0, 0]
        self.serving, self.serve_t = None, 0.0         # the gateway the remote sends through now (its LED is blue)
        super().__init__(args)

    def ready(self, i, now=None):
        u = self.ues[i]
        return u["phase"] == "up" and (now or time.time()) - u["t"].get("up", 0) >= self.args.egress_settle

    def beacon_info(self, now):
        dt = max(0.05, now - self.gw_t)
        for i in range(3):
            self.gw[i] = dict(ready=self.ready(i, now), rate=self.uu_b[i] / dt)
            self.uu_b[i] = 0
        self.gw_t = now

    def update_egress(self):
        self.egress = None                             # no single egress: every gateway sends its own

    def tick(self, now):
        if now - self.gw_t >= self.args.beacon_s:
            self.beacon_info(now)
        want = set(self.chain[:2])
        both = all(self.ready(i, now) for i in want)
        for i, u in enumerate(self.ues):
            if i in want and not u["cov"]:
                self.cover(i)
            elif u["cov"] and i not in want and (both or u["phase"] != "up"):
                self.uncover(i)                        # make before break: once both gateways carry traffic
        saved, self.args.in_coverage = self.args.in_coverage, 0   # the base: blink and attach timeouts only
        try:
            super().tick(now)
        finally:
            self.args.in_coverage = saved

    def pick(self, node):
        """The gateway this AGV sends through, from the gateways' beacons; None: deliver here."""
        if self.ready(node):
            return None
        cand = [g for g in self.chain if g != node and self.gw[g]["ready"]]
        if not cand:
            if self.ues[node]["phase"] == "up":
                return None                            # nobody settled: its own fresh session rather than a ping-pong
            cand = [g for g in self.chain if g != node and self.ues[g]["phase"] == "up"]
        if not cand:
            return None                                # nobody has a session: dropped here ('no session')
        if len(cand) == 1:
            return cand[0]
        p = self.args.pick
        if p == "split":
            self.alt ^= 1
            return cand[self.alt]
        if p == "near":                                # the nearest gateway; the current one while within --hyst of it
            me = self.fleet.a[node]
            dist = lambda g: math.dist((me["x"], me["y"]), (self.fleet.a[g]["x"], self.fleet.a[g]["y"]))
            best, cur = min(cand, key=dist), getattr(self, "serv", {}).get(node, self.serving)
            return cur if cur in cand and dist(cur) <= dist(best) + self.args.hyst else best
        if p == "first":
            return cand[0]                             # gateway 1 (nearest the pole) unless it is not ready
        if p == "sticky":
            cur, now = self.serving, time.time()
            if cur in cand:
                other = [g for g in cand if g != cur][0]
                if now - self.serve_t < self.args.sticky_s or self.gw[other]["rate"] >= 0.6 * self.gw[cur]["rate"]:
                    return cur
                return other
        return min(cand, key=lambda g: (self.gw[g]["rate"], self.chain.index(g)))     # load: the less busy

    def next_hop(self, node, dirn, dest):
        if dirn == 1:
            return None if node == dest else dest      # every pair of AGVs has a link: straight to it
        g = self.pick(node)
        if node == self.chain[2]:
            if g is not None:
                self.picks[g] += 1
            self.set_serving(g)
        return g

    def set_serving(self, g):
        """The gateway carrying the remote's traffic (None: the remote sends up its own UE, or nothing)."""
        if g == self.serving:
            return
        self.serving, self.serve_t = g, time.time()
        self.log("serve", g=g, chain=self.chain)
        self.set_leds()

    def led_colour(self, i):
        r = self.role(i)
        if r == 2:
            return LED_OF_ROLE[2]                      # red: the remote
        return LED_OF_ROLE[0] if i == self.serving else LED_OF_ROLE[1]   # blue: serving the remote; green: NR only

    def on_chain(self, old, new):
        super().on_chain(old, new)
        if self.serving is not None and self.serving not in new[:2]:
            self.set_serving(None)

    def deliver_up(self, node, app, agv, flow, seq):
        self.uu_b[node] += len(app) + 28
        super().deliver_up(node, app, agv, flow, seq)

    def cmd_entry(self, via=None):
        if via is not None and self.ues[via]["phase"] == "up":
            return via
        return self.chain[0]

    def can_change_pivot(self, now):
        return True

    def extra_stats(self):
        p, self.picks = self.picks, [0, 0, 0]
        return (super().extra_stats() + "  remote via " + " ".join(f"{NAMES[g]} {p[g]}" for g in range(3) if p[g])
                + "  UE B/s " + " ".join(f"{NAMES[g]} {self.gw[g]['rate']:.0f}" for g in range(3)))


class CoverGw(DualGw):
    """Coverage by where the AGV is (--topology cover): an AGV uses its own UE while the gNB's signal there is good
    (rsrp() >= Q_SWITCH and its session settled); otherwise it sends over one sidelink hop to an AGV that is (a gateway,
    from the beacons, --pick). Inside the cold store its UE loses the connection (radio link failure) and is stopped; out
    again, it attaches, and keeps using the sidelink until its session has settled. No AGV is a gateway or remote by
    rank: with all three in coverage, all three send on their own UEs.
    LEDs: red, the AGV sends over the sidelink (blinking while its UE attaches); blue, a gateway carrying another AGV's
    traffic; green, in coverage on its own UE only."""

    def __init__(self, args):
        self.q = [0.0, 0.0, 0.0]
        self.qt = [None, None, None]                   # since when the signal is past the threshold that changes coverage
        self.serv = {}                                 # remote -> the gateway it sends through now
        self.led_sig = None
        self.entries = zone_entries()
        self.upq = {}
        self.slhold = {}
        super().__init__(args)
        for i, a in enumerate(self.fleet.a):
            self.q[i] = rsrp(a["x"], a["y"])

    def signal_ok(self, i):
        return self.q[i] >= Q_SWITCH

    def ready(self, i, now=None):
        return self.signal_ok(i) and super().ready(i, now)

    def role(self, i):
        if not self.ready(i) and (self.ues[i]["phase"] != "up" or self.serv.get(i) is not None):
            return 2                                   # sends over the sidelink (red); session up, settling, on its own UE: not red
        return 0 if i in self.serv.values() else 1     # blue: carries another AGV's traffic; green: its own only

    def led_colour(self, i):
        return LED_OF_ROLE[self.role(i)]

    def tick(self, now):
        if now - self.gw_t >= self.args.beacon_s:
            self.beacon_info(now)
        for i, a in enumerate(self.fleet.a):
            q = self.q[i] = rsrp(a["x"], a["y"])
            u = self.ues[i]
            if u["cov"]:
                if q < Q_RLF:
                    self.qt[i] = self.qt[i] or now
                    if now - self.qt[i] >= 1.0:
                        self.qt[i] = None
                        self.log("rlf", agv=i, q=round(q, 1))
                        self.uncover(i)                # radio link failure: the connection is lost
                else:
                    self.qt[i] = None
            else:
                if q >= Q_SWITCH:
                    self.qt[i] = self.qt[i] or now
                    if now - self.qt[i] >= 2.0:
                        self.qt[i] = None
                        self.cover(i)
                else:
                    self.qt[i] = None
        for r in [r for r, g in self.serv.items() if self.ready(r) or not self.ready(g)]:
            self.set_serve(r, None)
        self.hold_check(now)
        self.fleet.wait_ok = [self.args.hold == "one" and self.ready(i) for i in range(3)]
        self.flush_upq(now)
        sig = tuple(self.led_colour(i) for i in range(3))
        if sig != self.led_sig:
            self.led_sig = sig
            self.set_leds()
        saved, self.args.in_coverage = self.args.in_coverage, 0   # the base: blink and attach timeouts only
        try:
            PerAgvUu.tick(self, now)
        finally:
            self.args.in_coverage = saved

    def next_hop(self, node, dirn, dest):
        if dirn == 1:
            return None if node == dest else dest
        g = self.pick(node)
        if g is not None:
            self.picks[g] += 1
        self.set_serve(node, g)
        return g

    UPQ_S = 5.0                                        # s: an AGV's app holds uplink while its own UE attaches

    def deliver_up(self, node, app, agv, flow, seq):
        """Out of the dead zone and attaching, with no relay in reach: the app queues the message for its own UE
        (as it would wait for the modem) rather than dropping it; dropped only after UPQ_S."""
        if self.ues[node]["phase"] != "up" and node == agv and self.signal_ok(node):
            self.upq.setdefault(node, []).append((time.time(), app, agv, flow, seq))
            return
        super().deliver_up(node, app, agv, flow, seq)

    def flush_upq(self, now):
        for node, q in list(self.upq.items()):
            if not q:
                continue
            if self.ues[node]["phase"] == "up":
                self.upq[node] = []
                for _, app, agv, flow, seq in q:
                    super().deliver_up(node, app, agv, flow, seq)
            else:
                t = time.time()
                keep = [m for m in q if t - m[0] < self.UPQ_S]
                for _, app, agv, flow, seq in q[:len(q) - len(keep)]:
                    self.log("drop", agv=agv, f=flow, s=seq, at=node, why="no session (queued 5 s)")
                self.upq[node] = keep

    SL_MARGIN = 2.0                                    # m: stop while the relay is still this much nearer than the gNB
    HOLD_MARGIN = 1.5                                  # m before the point where the signal falls below Q_SWITCH
    HOLD_LOOK = 6.0                                    # m: decide this far ahead of the stop point

    def hold_check(self, now):
        """The last AGV with a settled session does not drive into the dead zone: it stops before it, keeping the
        platoon's only way up, until another AGV has a settled session again (the one ahead, out and re-attached)."""
        if not self.fleet.platoon:
            return
        ready = [i for i in range(3) if self.ready(i)]
        want, why = [None, None, None], [None, None, None]
        if self.args.hold == "one":
            # one sidelink at a time: an AGV with a session does not drive into the dead zone while another AGV is on the
            # sidelink, whether it is that AGV's relay or would be a second AGV needing one; the AGV on the sidelink
            # drives on until it has its own direct connection (settled), then the held AGV goes in
            red = [j for j in range(3) if j not in ready]
            for i in ready:
                others = [j for j in red if j != i]
                if not others:
                    continue
                relay = any(self.serv.get(j) == i for j in others)
                if self.fleet.hold[i] is not None:
                    want[i] = self.fleet.hold[i]
                else:
                    s = self.fleet.a[i]["s"]
                    for e in self.entries:
                        stop = (e - self.HOLD_MARGIN) % LOOP
                        if (stop - s) % LOOP <= self.HOLD_LOOK:
                            want[i] = stop
                why[i] = "relaying AGV " + "".join(NAMES[j] for j in others if self.serv.get(j) == i) if relay else \
                    "AGV " + "".join(NAMES[j] for j in others) + " is on the sidelink; a second one would need another"
        elif len(ready) == 1:
            i = ready[0]
            why[i] = "the only one with a session"
            if self.fleet.hold[i] is not None:
                want[i] = self.fleet.hold[i]            # holding: until another AGV is ready
            else:
                s = self.fleet.a[i]["s"]
                for e in self.entries:
                    stop = (e - self.HOLD_MARGIN) % LOOP
                    if (stop - s) % LOOP <= self.HOLD_LOOK:
                        want[i] = stop
        if self.args.hold == "one":
            # on the sidelink and back in coverage, but its own session not up yet: it keeps its relay, and stops
            # before driving out of the relay's reach (the relay no longer nearer than the gNB); on when its session is up
            for i in range(3):
                if i in ready or self.ues[i]["phase"] == "up" or not self.signal_ok(i):
                    continue
                g = self.serv.get(i)
                if self.fleet.hold[i] is not None and self.slhold.get(i) is not None:
                    want[i], why[i] = self.fleet.hold[i], self.slhold[i]
                    continue
                if g is None:
                    continue
                me, rl = self.fleet.a[i], self.fleet.a[g]
                margin = math.dist((me["x"], me["y"]), POLE) - math.dist((me["x"], me["y"]), (rl["x"], rl["y"]))
                if margin < self.SL_MARGIN:
                    want[i] = (me["s"] + 0.3) % LOOP
                    why[i] = f"keeping its sidelink to AGV {NAMES[g]} until its own session is up"
        for i in range(3):
            if want[i] != self.fleet.hold[i]:
                sl = why[i] is not None and why[i].startswith("keeping its sidelink")
                if want[i] is not None and sl:
                    self.slhold[i] = why[i]
                    print(f"{time.strftime('%T')} AGV {NAMES[i]}: {why[i]}, stopping", flush=True)
                    self.log("hold", agv=i, on=1, at=round(want[i], 2), why=why[i], kind="sidelink")
                elif want[i] is None and self.slhold.pop(i, None):
                    print(f"{time.strftime('%T')} AGV {NAMES[i]}: own session up, moving on", flush=True)
                    self.log("hold", agv=i, on=0, kind="sidelink")
                elif want[i] is not None:
                    print(f"{time.strftime('%T')} AGV {NAMES[i]}: {why[i]}, holding before the dead zone", flush=True)
                    self.log("hold", agv=i, on=1, at=round(want[i], 2), why=why[i])
                else:
                    print(f"{time.strftime('%T')} AGV {NAMES[i]}: no AGV on the sidelink needs it, moving on" if self.args.hold == "one" else f"{time.strftime('%T')} AGV {NAMES[i]}: another AGV has a session again, moving on", flush=True)
                    self.log("hold", agv=i, on=0)
                self.fleet.hold[i] = want[i]

    def set_serve(self, r, g):
        if self.serv.get(r) == g:
            return
        if g is None:
            self.serv.pop(r, None)
        else:
            self.serv[r] = g
            self.serving, self.serve_t = g, time.time()
        if not self.serv:
            self.serving = None
        self.log("serve", g=g, r=r, all={str(k): v for k, v in self.serv.items()}, chain=self.chain)
        self.led_sig = None                            # LEDs again at the next tick

    def on_chain(self, old, new):
        PerAgvUu.on_chain(self, old, new)

    def pick(self, node):
        """The nearest AGV with a session, among those nearer to this one than the gNB is (a relay farther away than
        the gNB would not be heard either); None: its own UE, or no way up at all (the message is dropped)."""
        if self.ready(node):
            return None
        me = self.fleet.a[node]
        d = lambda g: math.dist((me["x"], me["y"]), (self.fleet.a[g]["x"], self.fleet.a[g]["y"]))
        reach = math.dist((me["x"], me["y"]), POLE)
        near = [g for g in range(3) if g != node and d(g) < reach]
        cand = [g for g in near if self.gw[g]["ready"]] or [g for g in near if self.ues[g]["phase"] == "up" and self.signal_ok(g)]
        if not cand:
            return None
        best, cur = min(cand, key=d), self.serv.get(node)
        return cur if cur in cand and d(cur) <= d(best) + self.args.hyst else best

    def extra_stats(self):
        return (super().extra_stats() + "  rsrp " + " ".join(f"{NAMES[i]} {self.q[i]:.0f}" for i in range(3))
                + "  sidelink: " + (" ".join(f"{NAMES[r]}->{NAMES[g]}" for r, g in self.serv.items()) or "none"))


# ------------------------------------------------------------------------------------------------ layer-3 UE-to-network relay
L3_IMAGE = "agvc-relay:local"         # nrlink-run + iptables/conntrack (deploy/agvc-relay)
DISC_PORT = 6602                      # PC5 signalling between the AGVs' sidelink UEs (discovery, link setup), over the sidelink IP
RSC = 0x0A6C01                        # relay service code: "AGV fleet edge" (one service, one PDU session per relay)
D_ANN, D_DCR, D_DCA, D_REL = 1, 2, 3, 4   # discovery announcement (Model A), direct link request / accept / release
DSC = struct.Struct("<4sBIBB4s")      # 'PC5D', type, RSC, from AGV, about AGV, IPv4 (the remote's address in an accept)
SD_HYST_DB = 2.0                      # dB: keep the current relay unless another is this much stronger
DCR_RETRY_S, L3Q_S = 0.3, 3.0


def lan_net(i):
    return f"agvc-lan-{'abc'[i]}"         # one AGV's internal network: its UE's netns and its two sidelink UEs' netns


def lan_ip(i, host):
    return f"10.30.{i}.{host}"            # .2 the AGV's nrUE; .10 + 2 * link + side its sidelink UEs


def remote_ip(i):
    return f"10.0.0.{200 + i}"            # the address a relay gives AGV i on the PC5 link (its pool on every link is 10.0.0.200-202)


def sl_rsrp(p, q):
    """SD-RSRP of a discovery message between two AGVs: the gNB's path loss law at the same power (no store walls)."""
    d = max(1.0, math.dist(p, q))
    return 10.0 - (46.0 + 35.0 * math.log10(d))


class L3Relay:
    """--relay l3: a layer-3 UE-to-network relay, as in TS 23.304 (5G ProSe), over the OAI sidelink UEs.
    Data: the remote AGV's app sends ordinary IP/UDP to the edge from the address its relay gave it (10.0.0.200+i) on the
    PC5 link; the kernel in the relay's sidelink-UE netns forwards it over the AGV's internal LAN to the relay's own UE,
    which NATs it into its PDU session (IPv4 L3 relay: the relay is a NAT router). The edge answers the NAT address and
    the reply comes back the same way. The relay's controller never sees the remote's messages.
    Control (emulated PC5 signalling, over the same sidelink IP): an AGV whose UE has a session and good signal sends a
    discovery announcement (Model A) with the relay service code on each of its PC5 links every --beacon-s; a remote
    measures its SD-RSRP and selects a relay itself (TS 38.304 style: SD-RSRP above what its own Uu would be, strongest,
    with hysteresis); it then sends a direct link request, the relay answers with an accept carrying the remote's IPv4
    address and sends a Remote UE Report (remote id and address) up its PDU session; release on leaving it. The remote
    queues its app messages meanwhile (up to L3Q_S)."""

    def __init__(self, args):
        self.disc = [{} for _ in range(3)]     # remote -> {relay: (t, SD-RSRP)} from announcements heard
        self.pc5c = {}                         # remote -> relay it has a direct link with
        self.dcr = {}                          # remote -> (relay, t sent): request outstanding
        self.l3q = {}                          # remote -> [(t, app, flow, seq)] waiting for the link
        self.ann_t = 0.0
        self.l3st = dict(ann=0, dcr=0, dca=0, rel=0, app=0, q=0)
        self.relaying = set()                  # (relay, remote) links the relay has accepted (one report each)
        super().__init__(args)

    # ---- plumbing
    def sidecar(self):
        if self.docker("image", "inspect", L3_IMAGE).returncode:
            raise SystemExit(f"--relay l3 needs the image {L3_IMAGE}: docker build -t {L3_IMAGE} deploy/agvc-relay")
        for i in range(3):
            if self.docker("network", "inspect", lan_net(i)).returncode:
                self.docker("network", "create", "--internal", "--subnet", f"10.30.{i}.0/24", lan_net(i))
            self.docker("network", "disconnect", "-f", lan_net(i), self.cont(i))
            r = self.docker("network", "connect", "--ip", lan_ip(i, 2), lan_net(i), self.cont(i))
            print(f"{time.strftime('%T')} L3 relay: AGV {NAMES[i]}'s LAN {lan_net(i)}: {self.cont(i)} {lan_ip(i, 2)}"
                  f"{' ' + r.stderr.strip() if r.returncode else ''}", flush=True)
        super().sidecar()

    def sl_attached(self, l, side, name):
        i = SL_LINKS[l][side]
        r = self.docker("network", "connect", "--ip", lan_ip(i, 10 + 2 * l + side), lan_net(i), name)
        if r.returncode:
            print(f"{time.strftime('%T')} L3 relay: {name} not on {lan_net(i)}: {r.stderr.strip()}", flush=True)

    def slfwd_opts(self, l, side):
        i = SL_LINKS[l][side]
        return ["--privileged"], L3_IMAGE, ["--l3-agv", str(i), "--l3-ue", lan_ip(i, 2), "--l3-self", lan_ip(i, 10 + 2 * l + side)]

    def fwd_opts(self, i):
        routes = []
        for l, pair in enumerate(SL_LINKS):
            if i in pair:
                side = pair.index(i)
                routes.append(f"{remote_ip(pair[1 - side])}={lan_ip(i, 10 + 2 * l + side)}")
        return ["--privileged"], L3_IMAGE, ["--l3-lan", lan_ip(i, 2), "--l3-routes", ",".join(routes)]

    # ---- PC5 signalling
    def side_of(self, a, b):
        L, side = self.link_of(a, b)
        return L, side, os.path.join(self.args.w, f"sl{L['l']}{'mp'[side]}.sock")

    def pc5d(self, frm, to, typ, about=None, ip="0.0.0.0"):
        L, side, sock = self.side_of(frm, to)
        if L["ip"][side] is None or L["ip"][1 - side] is None:
            return False
        m = DSC.pack(b"PC5D", typ, RSC, frm, frm if about is None else about, socket.inet_aton(ip))
        try:
            self.us.sendto(b"DSC" + m, sock)
            return True
        except OSError:
            return False

    def announce(self, now):
        if now - self.ann_t < self.args.beacon_s:
            return
        self.ann_t = now
        for g in range(3):
            if self.ues[g]["phase"] == "up" and self.signal_ok(g):
                for o in range(3):
                    if o != g and self.pc5d(g, o, D_ANN, about=1 if self.ready(g) else 0):
                        self.l3st["ann"] += 1

    def on_pc5d(self, me, peer, m):
        _mg, typ, rsc, frm, about, ip = DSC.unpack_from(m)
        if rsc != RSC or frm != peer:
            return
        now = time.time()
        if typ == D_ANN:
            a, b = self.fleet.a[me], self.fleet.a[peer]
            new = peer not in self.disc[me] or now - self.disc[me][peer][0] > 3 * self.args.beacon_s
            self.disc[me][peer] = (now, sl_rsrp((a["x"], a["y"]), (b["x"], b["y"])), bool(about))
            if new:
                self.log("disc", agv=me, relay=peer, sd=round(self.disc[me][peer][1], 1))
        elif typ == D_DCR:                     # I am asked to relay `peer`
            ok = self.ues[me]["phase"] == "up"
            if ok:
                self.pc5d(me, peer, D_DCA, about=peer, ip=remote_ip(peer))   # again for a repeated request: its accept was lost
                if (me, peer) not in self.relaying:
                    self.relaying.add((me, peer))
                    self.remote_report(me, peer, "connect")
        elif typ == D_DCA and about == me:
            d = self.dcr.get(me)
            if d and d[0] == peer:
                del self.dcr[me]
                self.pc5c[me] = peer
                self.l3st["dca"] += 1
                ipa = socket.inet_ntoa(ip)
                self.log("pc5conn", agv=me, relay=peer, on=1, ip=ipa, ms=round((now - d[1]) * 1000))
                print(f"{time.strftime('%T')} L3 relay: AGV {NAMES[me]} -> AGV {NAMES[peer]}: direct link up, address {ipa} "
                      f"({(now - d[1]) * 1000:.0f} ms)", flush=True)
                self.flush_l3q(me)
        elif typ == D_REL:
            if (me, peer) in self.relaying:
                self.relaying.discard((me, peer))
                self.remote_report(me, peer, "release")

    def remote_report(self, relay, remote, ev):
        """Remote UE Report (TS 23.304 6.5.1): the relay tells the network who is behind it, up its own PDU session."""
        m = b"RUR1" + json.dumps({"relay": relay, "remote": remote, "ip": remote_ip(remote), "ev": ev}).encode()
        try:
            self.us.sendto(m, os.path.join(self.args.w, f"fwd{relay + 1}.sock"))
        except OSError:
            pass
        self.log("rur", relay=relay, remote=remote, ev=ev)

    def release(self, r):
        g = self.pc5c.pop(r, None)
        if g is not None:
            self.pc5d(r, g, D_REL)
            self.l3st["rel"] += 1
            self.log("pc5conn", agv=r, relay=g, on=0)
            print(f"{time.strftime('%T')} L3 relay: AGV {NAMES[r]} released its link to AGV {NAMES[g]}", flush=True)

    # ---- relay selection by the remote, from what it heard
    def pick(self, node):
        if self.ready(node):
            return None
        now, me = time.time(), self.fleet.a[node]
        uu_free = 10.0 - (46.0 + 35.0 * math.log10(max(1.0, math.dist((me["x"], me["y"]), POLE))))
        heard = {g: v for g, v in self.disc[node].items() if now - v[0] <= 3 * self.args.beacon_s and v[1] > uu_free}
        cand = [g for g, v in heard.items() if v[2]] or list(heard)
        if not cand:
            return None
        sd = lambda g: heard[g][1]
        best, cur = max(cand, key=sd), self.serv.get(node)
        return cur if cur in cand and sd(cur) >= sd(best) - SD_HYST_DB else best

    def set_serve(self, r, g):
        if self.pc5c.get(r) is not None and self.pc5c.get(r) != g:
            self.release(r)
        if g is None:
            self.dcr.pop(r, None)
        super().set_serve(r, g)

    # ---- data
    def hop_send(self, node, nxt, data, meta):
        dirn = data[0]
        app = data[CHH.size:]
        if dirn != 0 or meta["agv"] != node:
            # an L3 relay does not route app messages itself: only the IP layer does
            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why="L3: not routed by the app")
            return
        if self.pc5c.get(node) == nxt:
            self.l3_send(node, nxt, app, meta)
            return
        self.l3q.setdefault(node, []).append((time.time(), app, meta))
        self.l3st["q"] += 1
        d = self.dcr.get(node)
        if d is None or d[0] != nxt or time.time() - d[1] > DCR_RETRY_S:
            if self.pc5d(node, nxt, D_DCR):
                self.dcr[node] = (nxt, time.time() if d is None or d[0] != nxt else d[1])
                self.l3st["dcr"] += 1

    def l3_send(self, node, nxt, app, meta):
        L, side, sock = self.side_of(node, nxt)
        try:
            self.us.sendto(b"APP" + app, sock)
            self.l3st["app"] += 1
            self.log("hop", agv=meta["agv"], f=meta["f"], s=meta["s"], a=node, b=nxt, l3=1)
            self.log("uu", agv=meta["agv"], f=meta["f"], s=meta["s"], via=nxt, l3=1)   # its route: the relay's PDU session
        except OSError as e:
            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why=f"slfwd: {e}")

    def flush_l3q(self, node, now=None):
        q = self.l3q.get(node)
        if not q:
            return
        g, now = self.pc5c.get(node), now or time.time()
        if g is not None:
            self.l3q[node] = []
            for _t, app, meta in q:
                self.l3_send(node, g, app, meta)
            return
        keep = [m for m in q if now - m[0] < L3Q_S]
        for _t, app, meta in q[:len(q) - len(keep)]:
            self.log("drop", agv=meta["agv"], f=meta["f"], s=meta["s"], at=node, why="L3: no direct link")
        self.l3q[node] = keep

    def tick(self, now):
        self.announce(now)
        for r in list(self.l3q):
            if self.pc5c.get(r) is None and self.serv.get(r) is None and self.ready(r):
                # it has its own session again: what waited for a relay goes up its own UE
                q, self.l3q[r] = self.l3q[r], []
                for _t, app, meta in q:
                    self.deliver_up(r, app, meta["agv"], meta["f"], meta["s"])
            else:
                self.flush_l3q(r, now)
        for r, (g, t) in list(self.dcr.items()):
            if now - t > DCR_RETRY_S and self.serv.get(r) == g:
                self.pc5d(r, g, D_DCR)
        super().tick(now)

    def on_ctl(self, p):
        if p[:3] in (b"SLA", b"SLD") and len(p) > 5:
            L, side = self.sl[p[3]], p[4]
            me, peer = L["agv"][side], L["agv"][1 - side]
            if p[:3] == b"SLA":
                if len(p) - 5 >= DSC.size and p[5:9] == b"PC5D":
                    self.on_pc5d(me, peer, p[5:])
            else:                              # a command from the edge, reached me through my relay
                m = p[5:]
                if len(m) >= APP.size and m[:4] == b"AGC1":
                    agv, flow, seq, te = APP.unpack_from(m)[1:5]
                    if agv == me:
                        self.st["cmd_rx"] += 1
                        self.log("cmdrx", agv=agv, s=seq, te=te, tr=round(time.time(), 4), l3=1)
            return
        super().on_ctl(p)

    def extra_stats(self):
        s, self.l3st = self.l3st, dict.fromkeys(self.l3st, 0)
        links = " ".join(f"{NAMES[r]}->{NAMES[g]}" for r, g in self.pc5c.items()) or "none"
        return (super().extra_stats() + f"  L3: links {links}, ann {s['ann']}, req {s['dcr']}, acc {s['dca']}, rel {s['rel']}, "
                f"via relay {s['app']}, queued {s['q']}")


class CoverOai(CoverGw, Pc5Oai):
    pass


class CoverL3(L3Relay, CoverOai):
    pass


class CoverCtl(CoverGw, PerAgvUu):
    pass


class DualOai(DualGw, Pc5Oai):
    pass


class DualCtl(DualGw, PerAgvUu):
    pass


# ------------------------------------------------------------------------------------------------ sidecars
def fwd(a):
    """In oai-nrue1's netns: app messages from the pivot out over the PDU session, commands back."""
    me = os.path.join(a.w, f"fwd{a.k}.sock" if a.k else "fwd.sock")
    try:
        os.unlink(me)
    except OSError:
        pass
    u = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    u.bind(me)
    os.chmod(me, 0o777)
    ctl = os.path.join(a.w, "ctl.sock")
    s = None
    print(f"fwd: {a.dev} -> {a.edge}:{EDGE_PORT}", flush=True)
    while True:
        if s is None:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, a.dev.encode())
                s.connect((a.edge, EDGE_PORT))
                ip = s.getsockname()[0]
                if ip == "0.0.0.0":
                    raise OSError("no address yet")
                if a.l3_lan:
                    l3_ue_setup(a)
                if a.k:
                    u.sendto(b"RDY" + bytes([a.k]) + ip.encode(), ctl)
                print(f"fwd: {a.dev} up, {ip}", flush=True)
            except OSError:
                if s:
                    s.close()
                s = None
                time.sleep(0.05)
                continue
        r, _, _ = select.select([u, s], [], [], 1.0)
        if u in r:
            m = u.recv(4096)
            try:
                s.send(m)
            except OSError:
                s.close()
                s = None
                continue
        if s in r:
            try:
                p = s.recv(2048)
                u.sendto((b"DL" + bytes([a.k]) + p) if a.k else p, ctl)   # which UE it came in on
            except OSError:
                pass


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def l3_ue_setup(a):
    """L3 relay, in the AGV's UE netns (privileged sidecar): what comes in from the AGV's sidelink UEs (a GRE link to each,
    over the AGV's internal LAN: Docker drops other addresses on an internal network) goes into the PDU session with NAT
    (masquerade), and the remotes' addresses route back to the sidelink UE of their link."""
    cmds = ["sysctl -qw net.ipv4.ip_forward=1",
            f"iptables -t nat -C POSTROUTING -o {a.dev} -s 10.0.0.0/24 -j MASQUERADE 2>/dev/null || "
            f"iptables -t nat -A POSTROUTING -o {a.dev} -s 10.0.0.0/24 -j MASQUERADE",
            f"ip route replace default dev {a.dev} table 100"]
    for r in filter(None, a.l3_routes.split(",")):
        dst, via = r.split("=")
        t = "agvs" + via.split(".")[-1]
        cmds += [f"ip tunnel del {t} 2>/dev/null; ip tunnel add {t} mode gre local {a.l3_lan} remote {via} ttl 64",
                 f"ip link set {t} up",
                 f"ip rule del iif {t} lookup 100 2>/dev/null; ip rule add iif {t} lookup 100",
                 f"ip route replace {dst}/32 dev {t}"]
    cmds.append("for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > $f; done")
    bad = [c for c in cmds if sh(c).returncode]
    print(f"fwd: L3 relay set up: GRE to the sidelink UEs -> {a.dev} (NAT), routes {a.l3_routes}" + (f"; FAILED: {bad}" if bad else ""), flush=True)


def l3_sl_setup(a, dev):
    """L3 relay, in a sidelink UE's netns (privileged sidecar). As a relay: what arrives over PC5 for elsewhere goes to the
    AGV's own UE over the LAN. As a remote: the address the relay gives it, and the edge reached through the peer."""
    me = remote_ip(a.l3_agv)
    # rp_filter off on every interface: a reply from the edge comes in on the LAN while the route back to the edge points
    # out over PC5 (this netns is also a remote's), and strict filtering (the default on eth1) would drop it
    cmds = ["sysctl -qw net.ipv4.ip_forward=1 net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0",
            f"ip rule del iif {dev} lookup 100 2>/dev/null; ip rule add iif {dev} lookup 100",
            f"ip tunnel del agvu 2>/dev/null; ip tunnel add agvu mode gre local {a.l3_self} remote {a.l3_ue} ttl 64",
            "ip link set agvu up",
            "ip route replace default dev agvu table 100",
            f"ip addr replace {me}/32 dev {dev}",
            f"ip route replace {EDGE}/32 via {a.peer} dev {dev} src {me}",
            "for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > $f; done"]
    bad = [c for c in cmds if sh(c).returncode]
    print(f"slfwd {a.link}{'mp'[a.side]}: L3 set up on {dev}: me {me}, edge via {a.peer}, relayed over GRE to {a.l3_ue}"
          + (f"; FAILED: {bad}" if bad else ""), flush=True)
    return not bad


def slfwd(a):
    """In a sidelink UE's netns: hop messages from the controller out over the sidelink's IP, and back.
    --l3-agv: also the L3 relay's PC5 side: the remote's app socket (from its relay-given address to the edge) and the
    PC5 signalling socket (discovery, direct link), each reported to the controller."""
    side = "mp"[a.side]
    me = os.path.join(a.w, f"sl{a.link}{side}.sock")
    try:
        os.unlink(me)
    except OSError:
        pass
    u = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    u.bind(me)
    os.chmod(me, 0o777)
    ctl = os.path.join(a.w, "ctl.sock")
    s, t_rdy = None, 0.0
    app = dsc = None
    l3 = a.l3_agv is not None
    while True:
        if s is None:
            for _i, dev in socket.if_nameindex():
                if not dev.startswith("oaitun"):
                    continue
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
                    s.bind(("0.0.0.0", SL_PORT))
                    s.connect((a.peer, SL_PORT))
                    ip = s.getsockname()[0]
                    if ip == "0.0.0.0":
                        raise OSError("no address")
                    print(f"slfwd {a.link}{side}: {dev} {ip} -> {a.peer}", flush=True)
                    if l3:
                        try:
                            l3_sl_setup(a, dev)
                            app = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            app.bind((remote_ip(a.l3_agv), 0))
                            app.connect((EDGE, EDGE_PORT))
                            dsc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            dsc.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
                            dsc.bind(("0.0.0.0", DISC_PORT))
                        except OSError as e:
                            print(f"slfwd {a.link}{side}: L3 sockets: {e}", flush=True)
                    break
                except OSError:
                    s.close()
                    s = None
            if s is None:
                time.sleep(0.2)
                continue
        now = time.time()
        if now - t_rdy > 2.0:                          # repeated: the controller may have restarted
            t_rdy = now
            u.sendto(b"SLR" + bytes([a.link, a.side]) + ip.encode(), ctl)
        r, _, _ = select.select([x for x in (u, s, app, dsc) if x], [], [], 0.5)
        if u in r:
            m = u.recv(4096)
            try:
                if m[:3] == b"APP" and app:
                    app.send(m[3:])
                elif m[:3] == b"DSC" and dsc:
                    dsc.sendto(m[3:], (a.peer, DISC_PORT))
                else:
                    s.send(m)
            except OSError:
                pass
        if s in r:
            try:
                p = s.recv(4096)
                u.sendto(b"SLH" + bytes([a.link, a.side]) + p, ctl)
            except OSError:
                pass
        for sk, tag in ((app, b"SLD"), (dsc, b"SLA")):
            if sk and sk in r:
                try:
                    u.sendto(tag + bytes([a.link, a.side]) + sk.recv(4096), ctl)
                except OSError:
                    pass


def edge(a):
    """In oai-ext-dn's netns: logs every uplink message, sends each AGV a command every 0.5 s."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", EDGE_PORT))
    peer, peers, seq, t_cmd = None, {}, [0, 0, 0], time.time()
    print(json.dumps({"k": "edge", "port": EDGE_PORT}), flush=True)
    while True:
        r, _, _ = select.select([s], [], [], 0.05)
        if r:
            p, addr = s.recvfrom(4096)
            tr = time.time()
            if p[:4] == b"RUR1":                       # a relay's Remote UE Report
                try:
                    rep_ = json.loads(p[4:])
                except ValueError:
                    rep_ = {}
                print(json.dumps({"k": "rur", "tr": round(time.time(), 4), "src": addr[0], **rep_}, separators=(",", ":")), flush=True)
                continue
            if len(p) >= APP.size and p[:4] == b"AGC1":
                _, agv, flow, sq, tg, x, y = APP.unpack_from(p)
                peer = addr
                if agv < 3:
                    peers[agv] = (addr, tr)
                print(json.dumps({"k": "rx", "agv": agv, "f": flow, "s": sq, "tg": round(tg, 4), "tr": round(tr, 4),
                                  "n": len(p) + 28, "src": addr[0]}, separators=(",", ":")), flush=True)
        now = time.time()
        if peer and now - t_cmd >= 0.5:
            t_cmd = now
            for i in range(3):
                seq[i] += 1
                m = APP.pack(b"AGC1", i, 2, seq[i], time.time(), 0, 0) + bytes(40 - APP.size if APP.size < 40 else 0)
                pa = peers.get(i)
                to = pa[0] if pa and now - pa[1] < 2.0 else peer     # back the way its last message came
                try:
                    s.sendto(m, to)
                except OSError:
                    pass
                print(json.dumps({"k": "cmd", "agv": i, "s": seq[i], "te": round(time.time(), 4), "to": to[0]},
                                 separators=(",", ":")), flush=True)


def clock(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", CLOCK_PORT))
    while True:
        p, addr = s.recvfrom(64)
        s.sendto(p[:8] + struct.pack("<d", time.time()), addr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("ue")
    g.add_argument("--core", required=True)
    g.add_argument("--boards", required=True, help="AGV A,B,C board serials")
    g.add_argument("--level", default="L1", choices=list(LEVELS))
    g.add_argument("--hyst", type=float, default=2.0)
    g.add_argument("--hold", choices=["last", "one"], default="last", help="platoon holds: last = the only AGV with a session "
                   "holds before the dead zone; one = one sidelink at a time, an AGV with a session holds while another is on the sidelink")
    g.add_argument("--platoon", type=float, default=0.0, help="the AGVs drive as a convoy at this gap (m), A leading; 0: spread out")
    g.add_argument("--window-ms", type=int, default=20, help="the gNB bridge's --sl-window (logged only)")
    g.add_argument("--age-ms", type=float, default=250, help="drop a sidelink message that waited this long")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--guard-ms", type=float, default=0.5, help="gap after each packet's UART time before the next one (0: no pacing)")
    g.add_argument("--beacon-s", type=float, default=0.5, help="position beacon period")
    g.add_argument("--uu", default="fleet", choices=["fleet", "per-agv"],
                   help="fleet: one nrUE for all, its radio moves to the pivot's board; per-agv: a UE per AGV, only the pivot's in coverage")
    g.add_argument("--in-coverage", type=int, default=1, choices=[1, 2],
                   help="per-agv: UEs attached at once: 1 = the pivot's; 2 = the pivot's and the relay's (make before break)")
    g.add_argument("--keepalive", type=int, default=0,
                   help="per-agv, 2 in coverage: a standby UE sends 60 B every 0.2 s. Off: the gNB grants every attached UE "
                        "each TDD period anyway (ulsch_max_frame_inactivity = 0), and the keepalive only took uplink")
    g.add_argument("--pc5", default="ctl", choices=["ctl", "oai"],
                   help="the hops between AGVs: ctl = the controller's own radio packets; oai = OAI's NR sidelink (option 3, needs --uu per-agv)")
    g.add_argument("--sl-tree", default=os.path.expanduser("~/oai/sl-5g-nr"), help="oai: the sidelink release tree (build_sl)")
    g.add_argument("--sl-cpus", default="10-11,12-13,14-15,16-17,18-19,20-21", help="oai: cpusets of the 6 sidelink UEs")
    g.add_argument("--sl-pace", type=float, default=0.001, help="oai: seconds between RUNs of a sidelink UE")
    g.add_argument("--sl-air-packets", type=int, default=6, help="oai: radio packets of one UE's items per window at most")
    g.add_argument("--sl-max-age", type=int, default=600, help="oai: slots a sidelink item may wait for the air (0.5 ms)")
    g.add_argument("--egress-settle", type=float, default=8.0,
                   help="per-agv, 2 in coverage: seconds a newly attached UE waits before it takes the fleet's traffic")
    g.add_argument("--attach-timeout", type=float, default=30, help="per-agv: restart a UE that has no session after this")
    g.add_argument("--topology", default="chain", choices=["chain", "dual", "cover"],
                   help="chain: pivot <- relay <- remote; dual: two gateways attached, the remote picks one per message (needs --uu per-agv)")
    g.add_argument("--sticky-s", type=float, default=5.0, help="dual, --pick sticky: the least time on one gateway")
    g.add_argument("--pick", default="load", choices=["load", "sticky", "split", "near", "first"],
                   help="dual: how the remote picks its gateway: load = the one that sent less in the last beacon period; "
                        "sticky = stay on one gateway (see --sticky-s) unless the other is clearly less busy; split = alternate; "
                        "near = the nearer to it; first = gateway 1 unless not ready")
    g.add_argument("--relay", default="app", choices=["app", "l3"],
                   help="cover with --pc5 oai: app = the relay AGV's controller passes the remote's messages on; l3 = a layer-3 "
                        "UE-to-network relay (IP forwarding and NAT in the relay's netns, discovery, direct link, remote UE report)")
    g.add_argument("--log", required=True)
    g.add_argument("--log-max-mb", type=float, default=0, help="move the log to <log>.1 when it passes this size (0: never)")
    g.add_argument("--live", help="HOST:PORT of the core PC's live page (agv_chain_live.py): events also go there by UDP")
    g.add_argument("--w", default="/dev/shm/agvc")
    g = sp.add_parser("fwd")
    g.add_argument("--dev", required=True)
    g.add_argument("--k", type=int, default=0, help="per-agv: the UE number (socket fwd<k>.sock, ready signal)")
    g.add_argument("--w", default="/w")
    g.add_argument("--edge", default=EDGE)
    g.add_argument("--l3-lan", help="L3 relay: this UE's address on its AGV's LAN")
    g.add_argument("--l3-routes", default="", help="L3 relay: remote address=sidelink UE LAN address, ...")
    g = sp.add_parser("slfwd")
    g.add_argument("--link", type=int, required=True)
    g.add_argument("--side", type=int, required=True, help="0 sync reference, 1 nearby UE")
    g.add_argument("--peer", required=True)
    g.add_argument("--w", default="/w")
    g.add_argument("--l3-agv", type=int, help="L3 relay: the AGV this sidelink UE belongs to")
    g.add_argument("--l3-ue", help="L3 relay: that AGV's UE address on its LAN")
    g.add_argument("--l3-self", help="L3 relay: this sidelink UE's address on its AGV's LAN")
    sp.add_parser("edge")
    sp.add_parser("clock")
    a = ap.parse_args()
    if a.cmd == "ue":
        if a.pc5 == "oai" and a.uu != "per-agv":
            ap.error("--pc5 oai needs --uu per-agv")
        if a.topology in ("dual", "cover") and a.uu != "per-agv":
            ap.error("--topology dual/cover needs --uu per-agv")
        if a.relay == "l3" and not (a.topology == "cover" and a.pc5 == "oai"):
            ap.error("--relay l3 needs --topology cover --pc5 oai")
        if a.topology == "cover":
            (CoverL3 if a.relay == "l3" else CoverOai if a.pc5 == "oai" else CoverCtl)(a).run()
        elif a.topology == "dual":
            (DualOai if a.pc5 == "oai" else DualCtl)(a).run()
        else:
            (Pc5Oai if a.pc5 == "oai" else PerAgvUu if a.uu == "per-agv" else Controller)(a).run()
    else:
        {"fwd": fwd, "slfwd": slfwd, "edge": edge, "clock": clock}[a.cmd](a)


if __name__ == "__main__":
    main()
