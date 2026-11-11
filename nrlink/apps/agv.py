#!/usr/bin/env python3
"""AGV testbed: one AGV, the same data over NR (nrlink + OAI nFAPI) and Bluetooth LE (non-3GPP, N3IWF) at once.

The AGV drives a loop through a factory hall and stops at six checkpoints. Each checkpoint is a pole with both
radios on it (an NR cell on nrlink and a Bluetooth LE access point): the AGV's two radio pairs connect there, and
it is where the AGV gets its position corrected. Between checkpoints it guides itself on its LiDAR.

  LiDAR scan (360 beams)          -> nrUE1 (NR)   and n3iwue1 (Bluetooth)   the same bytes, the same moment
  camera detections + own pose    -> nrUE2 (NR)   and n3iwue2 (Bluetooth)   (YOLO-style: class, confidence,
                                                                             range and bearing from the AGV)
  checkpoint sync (edge -> AGV)   <- whichever path carried the request; the AGV takes the first that arrives

Every message carries a sequence number and its send time, so the edge sees each copy twice and the comparison is
one to one. The radio channel (path loss, shadowing, fading per technology) is simulated from the AGV's position;
the latency and loss are measured on the real links. With --fading-drop the simulated outages also drop messages.

  UE PC host:  agv.py ue --core 192.168.10.223     (simulation + the four forwarder sidecars)
  UE PC, sidecar in each UE container's netns:  agv.py fwd --path nr1 ...
  core, sidecar in oai-ext-dn's netns:           agv.py edge          (JSON lines on stdout, read by agv_live.py)
"""
import argparse
import json
import math
import os
import random
import select
import socket
import struct
import sys
import time
import zlib

# ----------------------------------------------------------------------------------------------- world
W, H = 48.0, 30.0
# racks and machines: x0, y0, x1, y1, height (m). Racks are steel, loaded: about 12 dB each through them at 2.4 GHz
RACKS = [(8, 6.5, 20, 8, 5), (8, 15, 20, 17, 5), (8, 20, 20, 22, 5), (28, 8, 40, 10, 5), (28, 14, 40, 16, 5),
         (28, 20, 40, 22, 5), (1.5, 17, 5.5, 24, 2.5)]
ROUTE = [(4, 4), (24, 4), (44, 4), (44, 26), (24, 26), (24, 12), (4, 12)]      # closed loop
CHECKPOINTS = [("Dock", 4, 4), ("Aisle A", 24, 4), ("Cell 3", 44, 4), ("Cell 4", 44, 26), ("Packing", 24, 26),
               ("Buffer", 4, 12)]
AP_H, AGV_ANT_H = 3.0, 0.5           # radio heights on the poles and on the AGV
PALLETS = [(10, 1.6, 11.2, 2.6), (34, 5.4, 35.2, 6.4), (45.6, 12, 46.6, 13.2), (30, 27.6, 31.2, 28.6),
           (25.6, 18, 26.6, 19.2), (6, 13.6, 7.2, 14.6)]
V_MAX, ACC, DEC = 1.2, 0.6, 1.0
DWELL_MIN, DWELL_MAX = 1.0, 4.0      # s at a checkpoint: at least, and the most it waits for a sync
LIDAR_N, LIDAR_RANGE = 360, 20.0
CAM_FOV, CAM_RANGE = math.radians(90), 12.0
CLASSES = ["person", "forklift", "pallet"]
LEVELS = {"L1": {"lidar_hz": 1, "det_hz": 5}, "L2": {"lidar_hz": 2, "det_hz": 10}, "L3": {"lidar_hz": 4, "det_hz": 20}}
MODES = {"both": (0, 1, 2, 3), "nr": (0, 2), "ble": (1, 3)}
PATHS = ["nr1", "ble1", "nr2", "ble2"]
TX_STEPS = [0, -10, -20, -30]       # dBm, the transmit power settings the page offers
FLOW_PATHS = {0: (0, 1), 1: (2, 3)}  # flow 0 LiDAR -> nr1 + ble1, flow 1 detections -> nr2 + ble2
FLOWS = ["lidar", "det"]
PATH_UE = {"nr1": ("oai-nrue1", "oaitun_ue1"), "ble1": ("nrlink-n3iwue1", "gretun-id-2-1"),
           "nr2": ("oai-nrue2", "oaitun_ue1"), "ble2": ("nrlink-n3iwue2", "gretun-id-2-1")}
EDGE = "192.168.70.132"
PORTS = {0: 6401, 1: 6402}

# ----------------------------------------------------------------------------------------------- radio
# Both technologies use the same 1 Mbit/s GFSK front end (FRDM-MCXW72; nrlink runs GenFSK on the BLE 1M PHY).
# nrlink stays on one channel (2402 MHz); Bluetooth LE hops over 37 data channels, a new one every connection event.
# Path loss: 3GPP TR 38.901 indoor factory, sparse clutter, low base station (InF-SL); LOS when no rack is in the way.
RADIO = {"freq_mhz": 2440, "tx_dbm": 0.0, "ant_dbi": 0.0, "sens_dbm": -95.0, "model": "3GPP TR 38.901 InF-SL",
         "sf_los_db": 4.0, "sf_nlos_db": 5.7, "shadow_m": 6.0, "k_los_db": 6.0, "hyst_db": 3.0,
         "nr_fail_ms": 1000, "ble_sup_ms": 420, "env_doppler_hz": 0.5, "ble_ci_ms": 15}

HDR = struct.Struct("<4sBBIdfff")   # 'AGV1', path, flow, seq, t_gen (UE clock), est x, y, heading
DET = struct.Struct("<BBHhH")       # class, confidence*255, range cm, bearing centideg, size cm
SYN = struct.Struct("<4sBHBfffd")   # 'SYN1', path, visit, checkpoint, x, y, heading, t_edge


def route_segments():
    return [(ROUTE[i], ROUTE[(i + 1) % len(ROUTE)]) for i in range(len(ROUTE))]


SEGS = route_segments()
LOOP = sum(math.dist(a, b) for a, b in SEGS)
CP_S = []                            # each checkpoint's distance along the loop
for _n, cx, cy in CHECKPOINTS:
    s0 = 0.0
    for a, b in SEGS:
        L = math.dist(a, b)
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        t = (cx - a[0]) * ux + (cy - a[1]) * uy
        if -1e-6 <= t <= L + 1e-6 and abs((cx - a[0]) * uy - (cy - a[1]) * ux) < 1e-6:
            CP_S.append(s0 + t)
            break
        s0 += L


def at(s):
    s %= LOOP
    for a, b in SEGS:
        L = math.dist(a, b)
        if s <= L:
            f = s / L
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, math.atan2(b[1] - a[1], b[0] - a[0])
        s -= L
    return ROUTE[0][0], ROUTE[0][1], 0.0


def person(i, t):
    if i == 0:   # walks along the aisle beside rack D
        u = (t * 0.9 / 16) % 2
        return 26 + 16 * (u if u < 1 else 2 - u), 6.2
    u = (t * 0.8 / 18) % 2
    return 46.5, 6 + 18 * (u if u < 1 else 2 - u)


def forklift(t):
    u = (t * 1.5 / 16) % 2
    return 26 + 16 * (u if u < 1 else 2 - u), 18.0


def obstacles(t):
    """Everything the LiDAR and camera can see at time t: (kind, box) or (kind, circle)."""
    o = [("rack", r[:4]) for r in RACKS] + [("pallet", p) for p in PALLETS]
    fx, fy = forklift(t)
    o.append(("forklift", (fx - 1.2, fy - 0.6, fx + 1.2, fy + 0.6)))
    for i in range(2):
        px, py = person(i, t)
        o.append(("person", (px, py, 0.3)))
    o += [("wall", (-1, -1, 0, H + 1)), ("wall", (W, -1, W + 1, H + 1)), ("wall", (-1, -1, W + 1, 0)),
          ("wall", (-1, H, W + 1, H + 1))]
    return o


def ray_box(ox, oy, dx, dy, b):
    t0, t1 = -1e9, 1e9
    for o, d, lo, hi in ((ox, dx, b[0], b[2]), (oy, dy, b[1], b[3])):
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return None
            continue
        a, c = (lo - o) / d, (hi - o) / d
        t0, t1 = max(t0, min(a, c)), min(t1, max(a, c))
    if t1 < max(t0, 0):
        return None
    return t0 if t0 > 0 else None


def ray_circle(ox, oy, dx, dy, c):
    cx, cy, r = c
    fx, fy = ox - cx, oy - cy
    b = fx * dx + fy * dy
    disc = b * b - (fx * fx + fy * fy - r * r)
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    return t if t > 0 else None


def cast(ox, oy, ang, obs, rmax):
    dx, dy = math.cos(ang), math.sin(ang)
    best = None
    for kind, g in obs:
        t = ray_circle(ox, oy, dx, dy, g) if kind == "person" else ray_box(ox, oy, dx, dy, g)
        if t is not None and t < rmax and (best is None or t < best):
            best = t
    return best


def seg_hits_box(x0, y0, x1, y1, b):
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for o, d, lo, hi in ((x0, dx, b[0], b[2]), (y0, dy, b[1], b[3])):
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return False
            continue
        a, c = (lo - o) / d, (hi - o) / d
        t0, t1 = max(t0, min(a, c)), min(t1, max(a, c))
        if t0 > t1:
            return False
    return True


def racks_between(x0, y0, x1, y1):
    return sum(1 for r in RACKS if seg_hits_box(x0, y0, x1, y1, r))


# ----------------------------------------------------------------------------------------------- channel
class Fading:
    """Rician / Rayleigh fading. Fixed channel: Clarke's model by sum of sinusoids, phases advance with the AGV's
    speed (a stopped AGV in a fade stays in it). Hopping channel: a new independent draw every connection event."""

    def __init__(self, rng, n=16):
        self.rng = rng
        self.a = [rng.uniform(0, 2 * math.pi) for _ in range(n)]
        self.ph = [rng.uniform(0, 2 * math.pi) for _ in range(n)]
        self.los = rng.uniform(0, 2 * math.pi)

    def step(self, v, dt):
        lam = 3e8 / (RADIO["freq_mhz"] * 1e6)
        for i, a in enumerate(self.a):
            fd = v / lam * math.cos(a) + RADIO["env_doppler_hz"] * math.cos(a + 1.3)
            self.ph[i] += 2 * math.pi * fd * dt

    def gain_db(self, k):
        n = len(self.ph)
        re = sum(math.cos(p) for p in self.ph) / math.sqrt(n)
        im = sum(math.sin(p) for p in self.ph) / math.sqrt(n)
        s = math.sqrt(k / (k + 1)) if k > 0 else 0.0
        d = math.sqrt(1 / (k + 1))
        hr, hi = s * math.cos(self.los) + d * re, s * math.sin(self.los) + d * im
        return 10 * math.log10(max(hr * hr + hi * hi, 1e-9))

    def draw_db(self, k):
        s = math.sqrt(k / (k + 1)) if k > 0 else 0.0
        d = math.sqrt(1 / (k + 1) / 2)
        hr, hi = s + d * self.rng.gauss(0, 1), d * self.rng.gauss(0, 1)
        return 10 * math.log10(max(hr * hr + hi * hi, 1e-9))


def path_loss(x, y, cx, cy):
    """dB from the AGV at (x, y) to the checkpoint pole at (cx, cy), and whether it is line of sight."""
    d = max(1.0, math.sqrt((cx - x) ** 2 + (cy - y) ** 2 + (AP_H - AGV_ANT_H) ** 2))
    f = RADIO["freq_mhz"] / 1000
    los = racks_between(x, y, cx, cy) == 0
    pl_los = 31.84 + 21.5 * math.log10(d) + 19.0 * math.log10(f)
    return (pl_los if los else max(pl_los, 33.0 + 25.5 * math.log10(d) + 20.0 * math.log10(f))), los


class Link:
    """One radio link of the AGV (one of the four boards) to its serving checkpoint."""

    def __init__(self, name, rng):
        self.name, self.hop = name, name.startswith("ble")
        self.rng = rng
        self.shadow = [rng.gauss(0, 1) for _ in CHECKPOINTS]         # unit normal, scaled by the LOS / NLOS sigma
        self.fad = Fading(rng)
        self.cp, self.mean, self.inst, self.los = 0, -60.0, -60.0, True
        self.down_ms = 0.0          # current outage length
        self.hist = []              # (t, outage) per tick, last 30 s
        self.drops, self.longest = [], 0.0
        self.handovers = 0

    def step(self, t, x, y, v, ds, dt, tx_dbm=None):
        r = math.exp(-ds / RADIO["shadow_m"])
        q = math.sqrt(max(0.0, 1 - r * r))
        self.shadow = [r * s + q * self.rng.gauss(0, 1) for s in self.shadow]
        tx = RADIO["tx_dbm"] if tx_dbm is None else tx_dbm
        means = []
        for i, (_n, cx, cy) in enumerate(CHECKPOINTS):
            pl, los = path_loss(x, y, cx, cy)
            means.append((tx + 2 * RADIO["ant_dbi"] - pl + self.shadow[i] * RADIO["sf_los_db" if los else "sf_nlos_db"], los))
        best = max(range(len(means)), key=lambda i: means[i][0])
        if best != self.cp and means[best][0] > means[self.cp][0] + RADIO["hyst_db"]:
            self.cp = best
            self.handovers += 1
        self.mean, self.los = means[self.cp]
        k = 10 ** (RADIO["k_los_db"] / 10) if self.los else 0.0
        self.fad.step(v, dt)
        if self.hop:     # connection events in this tick, each on a new channel: the tick gets through if one does
            n = max(1, round(dt * 1000 / RADIO["ble_ci_ms"]))
            draws = [self.mean + self.fad.draw_db(k) for _ in range(n)]
            self.inst = draws[0]
            ok = max(draws) >= RADIO["sens_dbm"]
        else:
            self.inst = self.mean + self.fad.gain_db(k)
            ok = self.inst >= RADIO["sens_dbm"]
        if ok:
            if self.down_ms:
                self.longest = max(self.longest, self.down_ms)
            self.down_ms = 0.0
        else:
            self.down_ms += dt * 1000
            lim = RADIO["ble_sup_ms"] if self.hop else RADIO["nr_fail_ms"]
            if self.down_ms - dt * 1000 < lim <= self.down_ms:
                self.drops.append(t)
        self.hist.append((t, not ok))
        while self.hist and self.hist[0][0] < t - 30:
            self.hist.pop(0)
        self.drops = [d for d in self.drops if d > t - 300]
        return ok

    def summary(self):
        n = len(self.hist) or 1
        return {"cp": self.cp, "mean": round(self.mean, 1), "inst": round(self.inst, 1), "los": self.los,
                "out_pct": round(100 * sum(1 for _, o in self.hist if o) / n, 1), "down_ms": round(self.down_ms),
                "longest_ms": round(max(self.longest, self.down_ms)), "drops_5min": len(self.drops),
                "handovers": self.handovers}


# ----------------------------------------------------------------------------------------------- the AGV
class Agv:
    def __init__(self, rng):
        self.rng = rng
        self.s, self.v = 0.0, 0.0
        self.x, self.y, self.hd = at(0)
        self.ex = self.ey = self.eh = 0.0            # localisation error (estimate - truth)
        self.state, self.t_state = "drive", 0.0
        self.next_cp = 1
        self.visit = 0
        self.req = None                              # (visit, cp, t_req) while waiting for a sync
        self.synced = {}                             # visit -> first path
        self.req_t = {}                              # visit -> when the AGV stopped and asked
        self.odo = 0.0

    def est(self):
        return self.x + self.ex, self.y + self.ey, self.hd + self.eh

    def step(self, t, dt):
        """Returns the distance moved."""
        if self.state == "dwell":
            waited = t - self.t_state
            got = self.req and self.req[0] in self.synced
            if (waited >= DWELL_MIN and got) or waited >= DWELL_MAX:
                self.state, self.req = "drive", None
                self.next_cp = (self.next_cp + 1) % len(CHECKPOINTS)
            return 0.0
        to_cp = (CP_S[self.next_cp] - self.s) % LOOP
        v_stop = math.sqrt(max(0.0, 2 * DEC * max(0.0, to_cp - 0.05)))
        target = min(V_MAX, max(0.15, v_stop))
        self.v = min(target, self.v + ACC * dt) if self.v < target else max(target, self.v - DEC * dt)
        ds = min(self.v * dt, to_cp)
        self.s = (self.s + ds) % LOOP
        self.x, self.y, self.hd = at(self.s)
        if to_cp - ds < 0.02:
            self.v = 0.0
            self.state, self.t_state = "dwell", t
            self.visit += 1
            self.req = (self.visit, self.next_cp, t)
            self.req_t[self.visit] = t
            for k in [k for k in self.req_t if k < self.visit - 20]:
                del self.req_t[k]
        # odometry drift: random walk plus a 1 % scale error along the way
        self.odo += ds
        g = self.rng.gauss
        self.ex += g(0, 0.015) * math.sqrt(ds) + 0.01 * ds * math.cos(self.hd)
        self.ey += g(0, 0.015) * math.sqrt(ds) + 0.01 * ds * math.sin(self.hd)
        self.eh += g(0, math.radians(0.3)) * math.sqrt(ds)
        return ds

    def lidar_fix(self, ranges):
        """Scan matching against the hall map: the more structure within 8 m, the more of the error it removes."""
        feat = sum(1 for r in ranges if r is not None and r < 8.0)
        q = min(1.0, feat / 120)
        self.ex *= 1 - 0.5 * q
        self.ey *= 1 - 0.5 * q
        self.eh *= 1 - 0.6 * q
        return q

    def sync(self, visit, path):
        if visit not in self.synced:
            self.synced[visit] = path
            self.ex = self.rng.gauss(0, 0.01)
            self.ey = self.rng.gauss(0, 0.01)
            self.eh = self.rng.gauss(0, math.radians(0.2))
            return True
        return False


def lidar_scan(agv, t):
    obs = obstacles(t)
    out = []
    for i in range(LIDAR_N):
        r = cast(agv.x, agv.y, agv.hd + math.radians(i), obs, LIDAR_RANGE)
        out.append(None if r is None else max(0.05, r + agv.rng.gauss(0, 0.01)))
    return out


def camera(agv, t):
    dets = []
    objs = [("person", person(i, t), 0.6) for i in range(2)]
    objs.append(("forklift", forklift(t), 2.4))
    objs += [("pallet", ((p[0] + p[2]) / 2, (p[1] + p[3]) / 2), 1.2) for p in PALLETS]
    for cls, (ox, oy), size in objs:
        dx, dy = ox - agv.x, oy - agv.y
        r = math.hypot(dx, dy)
        b = (math.atan2(dy, dx) - agv.hd + math.pi) % (2 * math.pi) - math.pi
        if r > CAM_RANGE or abs(b) > CAM_FOV / 2 or racks_between(agv.x, agv.y, ox, oy):
            continue
        if agv.rng.random() > 0.95:
            continue
        conf = max(0.3, min(0.99, 0.98 - 0.03 * r + agv.rng.gauss(0, 0.03)))
        r_m = r + agv.rng.gauss(0, 0.05 + 0.01 * r)
        b_m = b + agv.rng.gauss(0, math.radians(1.0))
        dets.append((CLASSES.index(cls), conf, r_m, b_m, size))
    return dets


def pack(path, flow, seq, tg, est, body):
    return HDR.pack(b"AGV1", path, flow, seq, tg, est[0], est[1], est[2]) + body


def lidar_body(ranges):
    return b"".join(struct.pack("<H", 0xFFFF if r is None else min(0xFFFE, int(r * 100))) for r in ranges)


def det_body(agv, dets):
    cp, visit = (agv.req[1], agv.req[0]) if agv.req else (255, 0)
    b = struct.pack("<BHB", cp, visit, len(dets))
    for c, conf, r, bear, size in dets:
        b += DET.pack(c, int(conf * 255), int(r * 100), int(math.degrees(bear) * 100), int(size * 100))
    return b


# ----------------------------------------------------------------------------------------------- UE side
def bound_udp(dev):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 18)
    return s


def fwd(a):
    """Sidecar in one UE container: messages from the simulation (unix socket) out over this UE's device, and the
    edge's syncs back to the simulation with their arrival time."""
    me = os.path.join(a.w, f"fwd-{a.path}.sock")
    try:
        os.unlink(me)
    except OSError:
        pass
    u = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    u.bind(me)
    os.chmod(me, 0o777)
    sim = os.path.join(a.w, "sim.sock")
    s, t_open = None, 0.0
    print(f"fwd {a.path}: {a.dev} -> {a.edge}", flush=True)
    while True:
        if s is None and time.time() - t_open > 2:
            t_open = time.time()
            try:
                s = bound_udp(a.dev)
            except OSError as e:
                print(f"fwd {a.path}: {a.dev}: {e}", flush=True)
                s = None
        r, _, _ = select.select([u] + ([s] if s else []), [], [], 1.0)
        if u in r:
            m = u.recv(4096)
            if s and len(m) >= HDR.size:
                try:
                    s.sendto(m, (a.edge, PORTS[m[5]]))
                except OSError:
                    s.close()
                    s = None
        if s and s in r:
            try:
                p = s.recv(512)
            except OSError:
                continue
            try:
                u.sendto(p + struct.pack("<d", time.time()), sim)
            except OSError:
                pass


def ue(a):
    import subprocess

    def sh(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:   # noqa: BLE001
            return ""
    os.makedirs(a.w, mode=0o777, exist_ok=True)
    os.chmod(a.w, 0o777)
    me_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, me_dir)
    try:
        from factory2 import host_health
    except Exception:   # noqa: BLE001
        host_health = None
    simp = os.path.join(a.w, "sim.sock")
    try:
        os.unlink(simp)
    except OSError:
        pass
    us = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    us.bind(simp)
    os.chmod(simp, 0o777)
    us.setblocking(False)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.setblocking(False)
    core = (a.core, a.port)

    def send(m):
        try:
            out.sendto(json.dumps(m, separators=(",", ":")).encode(), core)
        except OSError:
            pass
    cfg = {"level": a.level, "mode": "both", "fade": bool(a.fading_drop), "tx": a.tx_dbm}
    rng = random.Random(a.seed)
    agv = Agv(rng)
    links = {p: Link(p, random.Random(a.seed * 10 + i)) for i, p in enumerate(PATHS)}
    seq = {0: 0, 1: 0}
    t_next = {0: 0.0, 1: 0.0}
    t_gen_of = {}                     # (flow, seq) -> t_gen, for the sync reaction time
    sent_rows, sync_rows = [], []
    parent = {}
    t_sup = t_hl = t_dst = t_clk = t_truth = t_flush = t_sum = 0.0
    last_q = 0.0
    tick, t_prev = 0.05, time.time()
    send({"k": "log", "line": f"AGV simulation started (seed {a.seed}, {LOOP:.0f} m loop, {len(CHECKPOINTS)} checkpoints)"})
    while True:
        now = time.time()
        dt, t_prev = now - t_prev, now
        # ---- level / mode from the core server
        try:
            while True:
                m = json.loads(out.recv(4096))
                for k in ("level", "mode", "fade", "tx"):
                    if k in m and m[k] != cfg[k] and (k != "level" or m[k] in LEVELS) and (k != "mode" or m[k] in MODES) \
                            and (k != "tx" or m[k] in TX_STEPS):
                        cfg[k] = m[k]
                        send({"k": "log", "line": f"UE side: {k} now {m[k]}"})
        except (BlockingIOError, ValueError, OSError):
            pass
        # ---- sidecars
        if now - t_sup > 5:
            t_sup = now
            for p, (cont, dev) in PATH_UE.items():
                name = f"agv-fwd-{p}"
                pid = sh("docker", "inspect", "-f", "{{.Id}} {{.State.Pid}} {{.State.Running}}", cont)
                running = sh("docker", "inspect", "-f", "{{.State.Running}}", name) == "true"
                if pid.endswith("true") and (not running or parent.get(name) != pid):
                    sh("docker", "rm", "-f", name)
                    sh("docker", "run", "-d", "--rm", "--name", name, "--network", f"container:{cont}", "--entrypoint",
                       "python3", "-v", f"{me_dir}:/a:ro", "-v", f"{a.w}:/w", "nrlink-run:local", "-u", "/a/agv.py",
                       "fwd", "--path", p, "--dev", dev)
                    parent[name] = pid
                    send({"k": "log", "line": f"{name} (re)started in {cont}"})
        # ---- AGV, channel
        ds = agv.step(now, dt)
        ok = {p: lk.step(now, agv.x, agv.y, agv.v, ds, dt, cfg["tx"]) for p, lk in links.items()}
        # ---- syncs back from the edge
        while True:
            try:
                p = us.recv(512)
            except (BlockingIOError, OSError):
                break
            if len(p) < SYN.size + 8 or p[:4] != b"SYN1":
                continue
            _, path, visit, cp, sx, sy, sh_, t_edge = SYN.unpack_from(p)
            t_rx = struct.unpack_from("<d", p, len(p) - 8)[0]
            if cfg["fade"] and not ok[PATHS[path]]:
                continue
            first = agv.sync(visit, PATHS[path])
            t_req = agv.req_t.get(visit)
            sync_rows.append([PATHS[path], visit, cp, round(t_rx, 4), None if t_req is None else round(t_req, 4), first,
                              round(t_edge, 4)])
        # ---- messages: the same bytes on both paths of the flow
        lv = LEVELS[cfg["level"]]
        for flow, hz in ((0, lv["lidar_hz"]), (1, lv["det_hz"])):
            if now < t_next[flow]:
                continue
            t_next[flow] = max(t_next[flow] + 1 / hz, now - 0.5)
            seq[flow] += 1
            n, tg = seq[flow], time.time()
            if flow == 0:
                ranges = lidar_scan(agv, now)
                last_q = agv.lidar_fix(ranges)
                body = lidar_body(ranges)
            else:
                body = det_body(agv, camera(agv, now))
            est = agv.est()
            mask = fmask = dmask = 0
            for path in FLOW_PATHS[flow]:
                if path not in MODES[cfg["mode"]]:
                    continue
                if cfg["fade"] and not ok[PATHS[path]]:
                    fmask |= 1 << path
                    continue
                try:
                    us.sendto(pack(path, flow, n, tg, est, body), os.path.join(a.w, f"fwd-{PATHS[path]}.sock"))
                    mask |= 1 << path
                except OSError:
                    dmask |= 1 << path
            t_gen_of[(flow, n)] = tg
            sent_rows.append([flow, n, round(tg, 4), mask, fmask, dmask, len(body) + HDR.size])
        # ---- reports to the core server
        if now - t_flush > 0.5 and (sent_rows or sync_rows):
            t_flush = now
            if sent_rows:
                send({"k": "sent", "rows": sent_rows})
                sent_rows = []
            if sync_rows:
                send({"k": "syncrx", "rows": sync_rows})
                sync_rows = []
        if now - t_truth >= 0.2:
            t_truth = now
            ex, ey, eh = agv.est()
            m = {"k": "truth", "t": now, "x": round(agv.x, 3), "y": round(agv.y, 3), "hd": round(agv.hd, 4),
                 "v": round(agv.v, 2), "est": [round(ex, 3), round(ey, 3), round(eh, 4)],
                 "err_m": round(math.hypot(agv.ex, agv.ey), 3), "state": agv.state, "next_cp": agv.next_cp,
                 "visit": agv.visit, "req": agv.req, "lidar_q": round(last_q, 2), "cfg": cfg,
                 "people": [list(map(lambda v: round(v, 2), person(i, now))) for i in range(2)],
                 "forklift": [round(v, 2) for v in forklift(now)],
                 "ch": {p: {"cp": lk.cp, "mean": round(lk.mean, 1), "inst": round(lk.inst, 1), "ok": ok[p]}
                        for p, lk in links.items()}}
            if now - t_sum > 1:
                t_sum = now
                m["chsum"] = {p: lk.summary() for p, lk in links.items()}
            send(m)
        if now - t_hl > 5 and host_health:
            t_hl = now
            send({"k": "health", "pc": "ue", **host_health()})
        if now - t_dst > 5:
            t_dst = now
            d = {}
            for p, (cont, dev) in PATH_UE.items():
                o = sh("docker", "exec", cont, "ip", "-4", "-o", "addr", "show", dev)
                ip = [w.split("/")[0] for w in o.split() if w.startswith("12.1.1.")]
                if ip:
                    d[p] = ip[0]
            send({"k": "dst", "dst": d})
        if now - t_clk > 30:
            t_clk = now
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            best = None
            for _ in range(30):
                t1 = time.time()
                s.sendto(struct.pack("<d", t1), (a.core, a.clock_port))
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
                send({"k": "clock", "offset_s": best[1], "rtt_ms": round(best[0] * 1000, 3), "t": time.time()})
        if len(t_gen_of) > 20000:
            for k in sorted(t_gen_of)[:10000]:
                t_gen_of.pop(k, None)
        time.sleep(max(0.0, tick - (time.time() - now)))


# ----------------------------------------------------------------------------------------------- edge
def edge(a):
    """In oai-ext-dn's netns: receives every copy, prints one JSON line per copy (the first copy of each message
    also carries what the edge makes of it), and answers checkpoint requests on the path they came in on."""
    socks = {}
    for flow, port in PORTS.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        s.bind(("0.0.0.0", port))
        socks[s] = flow
    seen = {0: {}, 1: {}}
    synced = {}          # (path, visit, checkpoint) -> when answered; a restarted AGV counts visits from 1 again
    out = sys.stdout
    print(json.dumps({"k": "edge", "line": f"edge listening on {sorted(PORTS.values())}"}), flush=True)
    while True:
        r, _, _ = select.select(list(socks), [], [], 1.0)
        for s in r:
            p, addr = s.recvfrom(4096)
            tr = time.time()
            if len(p) < HDR.size or p[:4] != b"AGV1":
                continue
            _, path, flow, seq, tg, x, y, hd = HDR.unpack_from(p)
            body = p[HDR.size:]
            first = seq not in seen[flow]
            m = {"k": "rx", "p": path, "f": flow, "s": seq, "tg": tg, "tr": tr, "n": len(p), "x": round(x, 2),
                 "y": round(y, 2), "hd": round(hd, 3), "first": first}
            if first:
                seen[flow][seq] = tr
                if len(seen[flow]) > 5000:
                    for k in sorted(seen[flow])[:2500]:
                        del seen[flow][k]
            if flow == 0 and first:
                n = len(body) // 2
                pts = []
                for i in range(0, n, 3):
                    v = struct.unpack_from("<H", body, 2 * i)[0]
                    if v != 0xFFFF:
                        rr = v / 100
                        ang = hd + math.radians(i * 360 / n)
                        pts.append([round(x + rr * math.cos(ang), 2), round(y + rr * math.sin(ang), 2)])
                m["pts"] = pts
            elif flow == 1 and len(body) >= 4:
                cp, visit, nd = struct.unpack_from("<BHB", body)
                if first:
                    dets = []
                    for i in range(nd):
                        c, conf, rcm, bcd, size = DET.unpack_from(body, 4 + i * DET.size)
                        ang = hd + math.radians(bcd / 100)
                        dets.append([CLASSES[c], round(conf / 255, 2), round(x + rcm / 100 * math.cos(ang), 2),
                                     round(y + rcm / 100 * math.sin(ang), 2), size / 100])
                    m["dets"] = dets
                if cp != 255:
                    m["req"] = [cp, visit]
                    key = (path, visit, cp)
                    if tr - synced.get(key, 0) > 30:
                        synced[key] = tr
                        if len(synced) > 4000:
                            synced.clear()
                        _n, cx, cy = CHECKPOINTS[cp]
                        te = time.time()
                        try:
                            s.sendto(SYN.pack(b"SYN1", path, visit, cp, cx, cy, 0.0, te), addr)
                        except OSError:
                            pass
                        print(json.dumps({"k": "sync", "p": path, "visit": visit, "cp": cp, "t": te}), file=out)
            print(json.dumps(m, separators=(",", ":")), file=out)
        out.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("ue")
    g.add_argument("--core", required=True)
    g.add_argument("--port", type=int, default=5300)
    g.add_argument("--clock-port", type=int, default=5299)
    g.add_argument("--w", default="/dev/shm/agv")
    g.add_argument("--level", default="L1", choices=list(LEVELS))
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--fading-drop", action="store_true", help="simulated outages also drop the messages")
    g.add_argument("--tx-dbm", type=float, default=0.0, choices=TX_STEPS, help="simulated transmit power")
    g = sp.add_parser("fwd")
    g.add_argument("--path", required=True, choices=PATHS)
    g.add_argument("--dev", required=True)
    g.add_argument("--w", default="/w")
    g.add_argument("--edge", default=EDGE)
    sp.add_parser("edge")
    a = ap.parse_args()
    return {"ue": ue, "fwd": fwd, "edge": edge}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
