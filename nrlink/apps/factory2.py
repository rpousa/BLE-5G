#!/usr/bin/env python3
"""Factory floor, closed loop: the traffic goes the way it would in a real plant.

  eMBB   nrUE1  -> edge   LiDAR scans (uplink): the pole LiDAR is wired to nrUE1's modem
  URLLC  nrUE2  -> edge   AGV position reports (uplink), 10-50 Hz
         edge  -> nrUE2   stop/go command per AGV (downlink), one per report; an AGV that hears no command for 1 s
                          stops by itself (fail-safe)
  video  edge  -> n3iwue1/2  ceiling-camera video to the two operators' handhelds (downlink, Bluetooth)

The two operators walk across the AGV aisles. The edge finds them in the LiDAR scan and stops any AGV whose path
they block; how close an AGV gets to an operator depends on the uplink and downlink delays.

  UE PC, sidecar in oai-nrue2's netns:  factory2.py robots    (AGV physics, reports up, commands down)
  UE PC, sidecar in oai-nrue1's netns:  factory2.py lidar     (scans the true scene, sends them up)
  core,  sidecar in oai-ext-dn's netns: factory2.py edge      (controller + video relay; events on stdout)
  UE PC host:                           factory2.py agent --core 192.168.10.223
Shared on the UE PC through /w (host /dev/shm/fdl2): agv.json (true AGV state), clock (core - UE offset), level.
"""
import argparse
import base64
import json
import math
import os
import socket
import struct
import sys
import time
import zlib

# ----------------------------------------------------------------------------------------------- world
W, H = 60.0, 36.0
EPOCH = 1793900000.0          # scene time = core clock - EPOCH, on both PCs
STATIONS = [("Warehouse", 6, 6), ("Press line", 30, 6), ("Assembly", 54, 6),
            ("Quality", 54, 30), ("Paint", 30, 30), ("Shipping", 6, 30)]
MACHINES = [(14, 11, 0, 24, 17, 3.0), (36, 11, 0, 46, 17, 2.5), (44, 19, 0, 50, 26, 4.0),
            (10, 19, 0, 20, 25, 2.0), (26, 21, 0, 34, 26, 3.5)]
WALLS = [(-0.3, -0.3, 0, 60.3, 0, 6), (-0.3, 36, 0, 60.3, 36.3, 6), (-0.3, 0, 0, 0, 36, 6), (60, 0, 0, 60.3, 36, 6)]
LIDAR_POS = (30.0, 18.0, 3.0)
BEAMS = [-15 + 2 * i for i in range(16)]
AZ = 180
N_AGV = 4
V_MAX, ACC, DEC, DWELL = 1.4, 0.8, 2.0, 3.0
FAILSAFE_S = 1.0
CAMS = {"cam1": (0.0, 30.0), "cam2": (30.0, 60.0)}
CAM_W, CAM_H = 160, 96
# operators: walk between waypoints (x, y, pause s) at OP_V; each stops on an AGV aisle for a while (and blocks it).
# Their crossings are placed where the pole LiDAR can see them past the machines.
OP_V = 0.9
OPERATORS = {"op1": [(25.0, 1.5, 3), (25.0, 6.2, 8), (25.0, 10.5, 3), (25.0, 6.2, 8)],
             "op2": [(48.0, 34.5, 3), (48.0, 30.2, 8), (48.0, 26.5, 3), (48.0, 30.2, 8)]}
OP_CAM = {"op1": "cam1", "op2": "cam2"}      # each operator's handheld shows the camera over their area
LEVELS = {"L1": {"lidar_hz": 1, "rep_hz": 10, "cam_fps": 0.5},
          "L2": {"lidar_hz": 2, "rep_hz": 20, "cam_fps": 1},
          "L3": {"lidar_hz": 4, "rep_hz": 50, "cam_fps": 2}}
EDGE = "192.168.70.132"
P_LIDAR, P_REP, P_CMD, P_CAM1, P_CAM2, P_VIDEO_IN = 6101, 6102, 6202, 6103, 6104, 6300


def operator(name, t):
    wp = OPERATORS[name]
    legs = []
    for i, (x, y, pause) in enumerate(wp):
        nx, ny, _ = wp[(i + 1) % len(wp)]
        legs.append((x, y, pause, nx, ny, math.hypot(nx - x, ny - y) / OP_V))
    u = t % sum(p + w for _, _, p, _, _, w in legs)
    for x, y, pause, nx, ny, walk in legs:
        if u < pause:
            return (x, y)
        u -= pause
        if u < walk:
            f = u / walk
            return (x + (nx - x) * f, y + (ny - y) * f)
        u -= walk
    return wp[0][:2]


def forklift(t):
    u = (t * 1.6 / 44) % 2
    return (8 + 44 * (u if u < 1 else 2 - u), 33.0)


def people_boxes(t):
    out = []
    for n in OPERATORS:
        x, y = operator(n, t)
        out.append((x - 0.25, y - 0.25, 0, x + 0.25, y + 0.25, 1.8))
    fx, fy = forklift(t)
    out.append((fx - 1.1, fy - 0.6, 0, fx + 1.1, fy + 0.6, 2.2))
    return out


def agv_box(x, y, hd):
    hx = 0.6 * abs(math.cos(hd)) + 0.4 * abs(math.sin(hd))
    hy = 0.6 * abs(math.sin(hd)) + 0.4 * abs(math.cos(hd))
    return (x - hx, y - hy, 0, x + hx, y + hy, 0.7)


def ray_box(o, d, b):
    tmin, tmax = 0.05, 80.0
    for i in range(3):
        if abs(d[i]) < 1e-9:
            if o[i] < b[i] or o[i] > b[i + 3]:
                return None
            continue
        t1, t2 = (b[i] - o[i]) / d[i], (b[i + 3] - o[i]) / d[i]
        if t1 > t2:
            t1, t2 = t2, t1
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


def rays():
    out = []
    for el in BEAMS:
        ce, se = math.cos(math.radians(el)), math.sin(math.radians(el))
        for a in range(AZ):
            az = math.radians(a * 360 / AZ)
            out.append((ce * math.cos(az), ce * math.sin(az), se))
    return out


def scan_points(raw):
    """(x, y, z) of each return in a decoded scan."""
    pts = []
    o = LIDAR_POS
    for i in range(len(raw) // 2):
        cm = raw[2 * i] | (raw[2 * i + 1] << 8)
        if not cm:
            continue
        el, az = math.radians(BEAMS[i // AZ]), math.radians((i % AZ) * 360 / AZ)
        r = cm / 100
        pts.append((o[0] + r * math.cos(el) * math.cos(az), o[1] + r * math.cos(el) * math.sin(az), o[2] + r * math.sin(el)))
    return pts


def loop_path():
    pts = [(x, y) for _, x, y in STATIONS]
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


SEGS = loop_path()
LOOP_LEN = sum(math.dist(a, b) for a, b in SEGS)


def rd_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def wr_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, p)


def scene_t(wdir):
    return time.time() + rd_json(os.path.join(wdir, "clock"), {"offset_s": 0.0})["offset_s"] - EPOCH


def level_of(wdir):
    lv = rd_json(os.path.join(wdir, "level"), {}).get("level", "L1")
    return lv if lv in LEVELS else "L1"


def bound_udp(dev=None):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if dev:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    return s


# ----------------------------------------------------------------------------------------------- wire
FRAG = struct.Struct("<4sBBHIIIdHH")   # as factory_dl: magic, flow, level, run, frame, seq, crc, t_send, frag, nfrag
FLOWS = ["lidar", "robots", "cam1", "cam2"]
CHUNK = 900
REP = struct.Struct("<4sBIdB")         # 'REP2', level, seq, t_send (UE clock), n
REP_AGV = struct.Struct("<ffffBB")     # x, y, heading, speed, mode, command held
CMD = struct.Struct("<4sIIddB")        # 'CMD2', seq, echo report seq, echo report t_send (UE), t_send (core), n
GO, STOP = 1, 2
MODES = ["run", "dwell", "stop_cmd", "failsafe", "queue"]


def send_frags(s, dst, flow, lvl, n, seq, data, ts_fn=time.time):
    crc = zlib.crc32(data)
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    for k, c in enumerate(chunks):
        try:
            s.sendto(FRAG.pack(b"FDL1", FLOWS.index(flow), lvl, 0, n, seq + k, crc, ts_fn(), k, len(chunks)) + c, dst)
        except OSError:
            pass
    return len(chunks)


# ----------------------------------------------------------------------------------------------- robots (UE)
class Agv:
    def __init__(self, i):
        self.s = i * LOOP_LEN / N_AGV     # distance along the loop
        self.v = 0.0
        self.dwell = 0.0
        self.cmd = STOP
        self.cmd_t = 0.0
        self.mode = 1

    def pose(self):
        s = self.s % LOOP_LEN
        for a, b in SEGS:
            ln = math.dist(a, b)
            if s <= ln:
                f = s / ln
                return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, math.atan2(b[1] - a[1], b[0] - a[0]), ln - s
            s -= ln
        a, b = SEGS[-1]
        return b[0], b[1], math.atan2(b[1] - a[1], b[0] - a[0]), 0.0


def robots(a):
    wdir = a.w
    agvs = [Agv(i) for i in range(N_AGV)]
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", P_CMD))
    rx.setblocking(False)
    tx = bound_udp(a.dev)
    log = open(os.path.join(a.out, "cmd_rx.jsonl"), "w", buffering=1)
    dt, seq, nxt_rep, nxt_w = 0.02, 0, 0.0, 0.0
    last = time.time()
    while True:
        now = time.time()
        while True:      # commands from the edge
            try:
                p = rx.recv(256)
            except BlockingIOError:
                break
            if len(p) < CMD.size or p[:4] != b"CMD2":
                continue
            _m, cseq, echo, echo_t, ts, n = CMD.unpack_from(p)
            st = list(p[CMD.size:CMD.size + n])
            for g, c in zip(agvs, st):
                g.cmd, g.cmd_t = c, now
            log.write(json.dumps([cseq, echo, round(echo_t, 6), round(ts, 6), round(now, 6), st]) + "\n")
        step = min(0.1, now - last)
        last = now
        poses = [g.pose() for g in agvs]
        for i, g in enumerate(agvs):
            x, y, hd, left = poses[i]
            if g.dwell > 0:
                g.dwell -= step
                g.mode = 1
                target = 0.0
                if g.dwell <= 0:
                    g.s += 0.06          # leave the station
            else:
                failsafe = now - g.cmd_t > FAILSAFE_S
                queue = False             # on-board bumper: keep 2.5 m behind the AGV ahead
                for j, (x2, y2, _, _) in enumerate(poses):
                    if j != i:
                        dx, dy = x2 - x, y2 - y
                        ahead = dx * math.cos(hd) + dy * math.sin(hd)
                        if 0 < ahead < 2.5 and abs(-dx * math.sin(hd) + dy * math.cos(hd)) < 0.8:
                            queue = True
                if failsafe:
                    g.mode, target = 3, 0.0
                elif g.cmd == STOP:
                    g.mode, target = 2, 0.0
                elif queue:
                    g.mode, target = 4, 0.0
                else:
                    g.mode, target = 0, V_MAX
                target = min(target, math.sqrt(2 * DEC * max(0.0, left)) + 0.05)   # slow into the station
                if left < 0.03:
                    g.dwell, g.v, target = DWELL, 0.0, 0.0
            if g.v < target:
                g.v = min(target, g.v + ACC * step)
            else:
                g.v = max(target, g.v - DEC * step)
            g.s = (g.s + g.v * step) % LOOP_LEN
        if now >= nxt_w:          # true state for the LiDAR sim and the agent
            nxt_w = now + 0.05
            wr_json(os.path.join(wdir, "agv.json"), {"t": now, "agvs": [[round(p[0], 3), round(p[1], 3), round(p[2], 4),
                    round(g.v, 3), g.mode, g.cmd] for p, g in zip(poses, agvs)]})
        lv = level_of(wdir)
        if now >= nxt_rep:        # position reports, uplink
            nxt_rep = max(now, nxt_rep + 1.0 / LEVELS[lv]["rep_hz"]) if nxt_rep else now
            body = b"".join(REP_AGV.pack(p[0], p[1], p[2], g.v, g.mode, g.cmd) for p, g in zip(poses, agvs))
            try:
                tx.sendto(REP.pack(b"REP2", int(lv[1:]), seq, time.time(), N_AGV) + body, (EDGE, P_REP))
            except OSError:
                pass
            seq += 1
        time.sleep(max(0.0, dt - (time.time() - now)))


# ----------------------------------------------------------------------------------------------- lidar (UE)
def lidar(a):
    wdir = a.w
    R = rays()
    o = LIDAR_POS
    base = []
    for d in R:
        best = (-o[2] / d[2]) if d[2] < -1e-6 else None
        for b in WALLS + MACHINES:
            t = ray_box(o, d, b)
            if t is not None and (best is None or t < best):
                best = t
        base.append(best)
    tx = bound_udp(a.dev)
    n, seq, nxt = 0, 0, time.time()
    while True:
        lv = level_of(wdir)
        t = scene_t(wdir)
        st = rd_json(os.path.join(wdir, "agv.json"), {"agvs": []})
        dyn = people_boxes(t) + [agv_box(x, y, hd) for x, y, hd, *_ in st["agvs"]]
        rng = bytearray(len(R) * 2)
        for i, d in enumerate(R):
            best = base[i]
            for b in dyn:
                r = ray_box(o, d, b)
                if r is not None and (best is None or r < best):
                    best = r
            cm = 0 if best is None or best > 80 else int(best * 100)
            rng[2 * i], rng[2 * i + 1] = cm & 0xFF, cm >> 8
        data = struct.pack("<d", t) + zlib.compress(bytes(rng), 9)    # scene time of the scan + ranges
        seq += send_frags(tx, (EDGE, P_LIDAR), "lidar", int(lv[1:]), n, seq, data)
        n += 1
        nxt += 1.0 / LEVELS[lv]["lidar_hz"]
        nxt = max(nxt, time.time())
        time.sleep(max(0.0, nxt - time.time()))


# ----------------------------------------------------------------------------------------------- edge (core)
def obstacles(pts):
    """Returns in the scan that are people or vehicles: 0.9-2.5 m high (above the 0.7 m AGVs), off the static map."""
    out = []
    for x, y, z in pts:
        if not (0.9 < z < 2.5) or x < 0.6 or y < 0.6 or x > W - 0.6 or y > H - 0.6:
            continue
        if any(b[0] - 0.3 < x < b[3] + 0.3 and b[1] - 0.3 < y < b[4] + 0.3 for b in MACHINES):
            continue
        out.append((x, y))
    return out


def blocked(pose, obst):
    x, y, hd = pose
    c, s = math.cos(hd), math.sin(hd)
    for ox, oy in obst:
        dx, dy = ox - x, oy - y
        ahead, lat = dx * c + dy * s, -dx * s + dy * c
        if 0.2 < ahead < 4.5 and abs(lat) < 1.2:
            return True
    return False


def edge(a):
    def ev(**k):
        sys.stdout.write(json.dumps(k, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    socks = {}
    for name, port in (("lidar", P_LIDAR), ("rep", P_REP), ("video", P_VIDEO_IN)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
        s.bind(("0.0.0.0", port))
        s.setblocking(False)
        socks[name] = s
    tx = bound_udp()
    import select
    frames, obst, obst_n, cmd_seq, cam_seq = {}, [], None, 0, {"cam1": 0, "cam2": 0}
    dst, t_dst = {}, 0.0
    while True:
        r, _, _ = select.select(list(socks.values()), [], [], 0.5)
        now = time.time()
        if now - t_dst > 2:
            t_dst = now
            dst = rd_json(a.dst, dst)
        for s in r:
            while True:
                try:
                    p, addr = s.recvfrom(65535)
                except BlockingIOError:
                    break
                now = time.time()
                if s is socks["lidar"] and len(p) >= FRAG.size and p[:4] == b"FDL1":
                    _m, _f, lvl, _r, n, _sq, crc, ts, k, nk = FRAG.unpack_from(p)
                    fr = frames.setdefault(n, {"first": ts, "parts": {}})
                    fr["parts"][k] = p[FRAG.size:]
                    fr["first"] = min(fr["first"], ts)
                    if len(fr["parts"]) == nk:
                        data = b"".join(fr["parts"][i] for i in range(nk))
                        del frames[n]
                        if zlib.crc32(data) != crc:
                            continue
                        t_scene = struct.unpack_from("<d", data)[0]
                        pts = scan_points(zlib.decompress(data[8:]))
                        obst, obst_n = obstacles(pts), n
                        ev(k="scan", n=n, lvl=lvl, sent=fr["first"], recv=now, scene=t_scene, nobst=len(obst),
                           b64=base64.b64encode(data[8:]).decode())
                    if len(frames) > 50:
                        frames.pop(min(frames))
                elif s is socks["rep"] and len(p) >= REP.size and p[:4] == b"REP2":
                    _m, lvl, seq, ts, n = REP.unpack_from(p)
                    ag = [REP_AGV.unpack_from(p, REP.size + i * REP_AGV.size) for i in range(n)]
                    states = [STOP if blocked(g[:3], obst) else GO for g in ag]
                    t_cmd = time.time()
                    try:
                        tx.sendto(CMD.pack(b"CMD2", cmd_seq, seq, ts, t_cmd, n) + bytes(states), (addr[0], P_CMD))
                    except OSError:
                        pass
                    ev(k="rep", seq=seq, lvl=lvl, sent=ts, recv=now, src=addr[0],
                       agvs=[[round(g[0], 2), round(g[1], 2), round(g[2], 3), round(g[3], 2), g[4], g[5]] for g in ag])
                    ev(k="cmd", seq=cmd_seq, echo=seq, t=t_cmd, states=states, scan=obst_n)
                    cmd_seq += 1
                elif s is socks["video"] and len(p) > 17 and p[:4] == b"JPG2":
                    ci, n, cap = struct.unpack_from("<BId", p, 4)
                    cam = ["cam1", "cam2"][ci]
                    if cam in dst:
                        t_tx = time.time()
                        cam_seq[cam] += send_frags(tx, (dst[cam], P_CAM1 if cam == "cam1" else P_CAM2), cam,
                                                   int(level_of(a.lvdir)[1:]), n, cam_seq[cam], p[17:])
                        ev(k="cam_tx", cam=cam, n=n, cap=cap, t=t_tx, size=len(p) - 17,
                           b64=base64.b64encode(p[17:]).decode())


# ----------------------------------------------------------------------------------------------- cameras (core host)
def camera_frame(cam, t, agvs, n):
    """One ceiling-camera JPEG at scene time t, with the AGVs where they really are (agvs from the robots' truth)."""
    from PIL import Image, ImageDraw    # Pillow: run camsrc with ~/nxp/venv/bin/python
    import io
    x0, x1 = CAMS[cam]
    s = CAM_W / (x1 - x0)
    im = Image.new("RGB", (CAM_W, CAM_H), (92, 98, 104))
    g = ImageDraw.Draw(im)
    P = lambda x, y: ((x - x0) * s, (y / H) * CAM_H)  # noqa: E731
    for (a, b) in SEGS:
        g.line([P(*a), P(*b)], fill=(178, 160, 60), width=max(1, int(1.6 * s)))
    for _, x, y in STATIONS:
        g.rectangle([P(x - 2, y - 2), P(x + 2, y + 2)], outline=(200, 205, 210))
    for b in MACHINES:
        sh = int(60 + 30 * b[5])
        g.rectangle([P(b[0], b[1]), P(b[3], b[4])], fill=(sh, sh + 6, sh + 14), outline=(30, 34, 40))
    fx, fy = forklift(t)
    g.rectangle([P(fx - 1.1, fy - 0.6), P(fx + 1.1, fy + 0.6)], fill=(230, 180, 20))
    for x, y, hd, _v, mode, _c in agvs:
        c, sn = math.cos(hd), math.sin(hd)
        pts = [(x + c * dx - sn * dy, y + sn * dx + c * dy) for dx, dy in ((0.8, 0), (-0.6, 0.5), (-0.6, -0.5))]
        g.polygon([P(*p) for p in pts], fill=(255, 60, 40) if mode in (2, 3) else (255, 140, 40), outline=(40, 20, 10))
    for name in OPERATORS:                                  # hi-vis vests
        cx, cy = P(*operator(name, t))
        g.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(200, 255, 40), outline=(0, 0, 0))
    g.rectangle([0, 0, 100, 10], fill=(0, 0, 0))
    g.text((2, 0), f"{cam.upper()} {time.strftime('%H:%M:%S', time.gmtime(t + EPOCH))} #{n}", fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=38, optimize=True)
    return buf.getvalue()


def camsrc(a):
    """Ceiling cameras: render each at the level's frame rate and hand the JPEG to the edge (udp/6300)."""
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = {"cam1": 0, "cam2": 0}
    nxt = {"cam1": time.time(), "cam2": time.time() + 0.25}
    while True:
        now = time.time()
        fps = LEVELS[level_of(a.lvdir)]["cam_fps"]
        for ci, cam in enumerate(("cam1", "cam2")):
            if now >= nxt[cam]:
                nxt[cam] = max(now, nxt[cam] + 1.0 / fps)
                tr = rd_json(a.truth, {"agvs": []})
                t = now - EPOCH
                jpg = camera_frame(cam, t, tr.get("agvs", []), n[cam])
                try:
                    tx.sendto(b"JPG2" + struct.pack("<BId", ci, n[cam], now) + jpg, (a.edge, P_VIDEO_IN))
                except OSError:
                    pass
                n[cam] += 1
        time.sleep(0.05)


# ----------------------------------------------------------------------------------------------- agent (UE PC host)
SIDECARS = {   # name: (UE container, command, extra args)
    "fdl2-robots": ("oai-nrue2", ["/a/factory2.py", "robots", "--dev", "oaitun_ue1"]),
    "fdl2-lidar": ("oai-nrue1", ["/a/factory2.py", "lidar", "--dev", "oaitun_ue1"]),
    "fdl-live-rx-cam1": ("nrlink-n3iwue1", ["/a/factory_dl.py", "recv", "--port", "6103", "--dev", "gretun-id-2-1",
                                            "--out", "/o/rx_cam1.jsonl", "--secs", "1e9"]),
    "fdl-live-rx-cam2": ("nrlink-n3iwue2", ["/a/factory_dl.py", "recv", "--port", "6104", "--dev", "gretun-id-2-1",
                                            "--out", "/o/rx_cam2.jsonl", "--secs", "1e9"]),
}


def host_health():
    """Containers, nrlink processes, boards on USB and Bluetooth adapters of this PC."""
    import subprocess

    def out(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except Exception:   # noqa: BLE001
            return ""
    cont = {}
    for line in out("docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}").splitlines():
        n, _, stt = line.partition("\t")
        cont[n] = stt
    procs = [l.split(None, 1)[1] for l in out("pgrep", "-af", "gnb_bridge.py|ue_bridge.py|ble_ip_link.py|ble_watchdog").splitlines()
             if " " in l and "pgrep" not in l]
    ser = [n.split("_")[-1].replace("-if02", "") for n in (os.listdir("/dev/serial/by-id") if os.path.isdir("/dev/serial/by-id") else [])]
    hci, cur = {}, None
    for line in out("hciconfig").splitlines():
        if line and not line[0].isspace():
            cur = line.split(":")[0]
            hci[cur] = {"addr": None, "up": False}
        elif cur and "BD Address" in line:
            hci[cur]["addr"] = line.split()[2]
        elif cur and "UP RUNNING" in line:
            hci[cur]["up"] = True
    conns = [l.split()[2] for l in out("hcitool", "con").splitlines() if l.strip().startswith(">") and len(l.split()) > 2]
    return {"containers": cont, "procs": procs, "serial": ser, "hci": hci, "le_conns": conns, "t": time.time()}


def agent(a):
    import subprocess

    def sh(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:   # noqa: BLE001
            return ""
    os.makedirs(a.dir, exist_ok=True)
    os.makedirs(a.w, exist_ok=True)
    try:
        os.remove(os.path.join(a.w, "level"))     # never start on a trial left over from before: the server says which
    except OSError:
        pass
    me = os.path.dirname(os.path.abspath(__file__))
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.setblocking(False)
    core = (a.core, a.port)

    def send(m):
        try:
            out.sendto(json.dumps(m, separators=(",", ":")).encode(), core)
        except OSError:
            pass
    parent, pos = {}, {}
    wd = os.path.expanduser("~/oai/nrlink/logs/ble_watchdog.log")
    wd_pos = os.path.getsize(wd) if os.path.exists(wd) else 0
    t_sup = t_dst = t_clk = t_truth = t_health = 0.0
    tele = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)     # ue_bridge telemetry (the dashboard's port)
    try:
        tele.bind(("127.0.0.1", 8091))
        tele.setblocking(False)
    except OSError:
        tele = None
    files = {"cam1": "rx_cam1.jsonl", "cam2": "rx_cam2.jsonl", "cmd": "cmd_rx.jsonl"}
    while True:
        now = time.time()
        try:          # level from the core server (it answers every message)
            while True:
                m = json.loads(out.recv(4096))
                if "level" in m and m["level"] in LEVELS and \
                        rd_json(os.path.join(a.w, "level"), {}).get("level") != m["level"]:
                    wr_json(os.path.join(a.w, "level"), {"level": m["level"]})
                    send({"k": "log", "line": f"UE side now on Trial {m['level'][1:]}"})
        except (BlockingIOError, ValueError, OSError):
            pass
        if now - t_sup > 5:
            t_sup = now
            for name, (ue, args) in SIDECARS.items():
                pid = sh("docker", "inspect", "-f", "{{.Id}} {{.State.Pid}} {{.State.Running}}", ue)   # Pid: a restart keeps the Id
                running = sh("docker", "inspect", "-f", "{{.State.Running}}", name) == "true"
                if pid.endswith("true") and (not running or parent.get(name) != pid):
                    sh("docker", "rm", "-f", name)
                    sh("docker", "run", "-d", "--rm", "--name", name, "--network", f"container:{ue}", "--entrypoint",
                       "python3", "-v", f"{me}:/a:ro", "-v", f"{os.path.abspath(a.dir)}:/o", "-v", f"{a.w}:/w",
                       "nrlink-run:local", "-u", *args)
                    parent[name] = pid
                    for k, f in files.items():
                        if (k == "cmd" and name == "fdl2-robots") or k in name:
                            pos[k] = 0
                    send({"k": "log", "line": f"{name} (re)started in {ue}"})
        while tele:
            try:
                m = json.loads(tele.recv(4096))
            except (BlockingIOError, ValueError):
                break
            if m.get("k") == "ue":
                send({**m, "k": "tele"})
        if now - t_health > 5:
            t_health = now
            send({"k": "health", "pc": "ue", **host_health()})
        if now - t_dst > 5:
            t_dst = now
            d = {}
            for cam, ue in (("cam1", "nrlink-n3iwue1"), ("cam2", "nrlink-n3iwue2"), ("lidar", "oai-nrue1"), ("robots", "oai-nrue2")):
                ifc = "gretun-id-2-1" if cam.startswith("cam") else "oaitun_ue1"
                o = sh("docker", "exec", ue, "ip", "-4", "-o", "addr", "show", ifc)
                ip = [w.split("/")[0] for w in o.split() if w.startswith("12.1.1.")]
                if ip:
                    d[cam] = ip[0]
            send({"k": "dst", "dst": d})
        if now - t_clk > 30:
            t_clk = now
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            best = None
            for _ in range(40):
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
                wr_json(os.path.join(a.w, "clock"), {"offset_s": best[1], "rtt_ms": round(best[0] * 1000, 3)})
                send({"k": "clock", "offset_s": best[1], "rtt_ms": round(best[0] * 1000, 3), "t": time.time()})
        if now - t_truth >= 0.1:
            t_truth = now
            st = rd_json(os.path.join(a.w, "agv.json"), None)
            if st:
                send({"k": "truth", **st})
        for k, f in files.items():
            p = os.path.join(a.dir, f)
            if not os.path.exists(p):
                continue
            sz = os.path.getsize(p)
            if sz < pos.get(k, 0):
                pos[k] = 0
            if sz > pos.get(k, 0):
                with open(p) as fh:
                    fh.seek(pos.get(k, 0))
                    data = fh.read()
                cut = data.rfind("\n") + 1
                pos[k] = pos.get(k, 0) + len(data[:cut].encode())
                rows = []
                for line in data[:cut].splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
                for i in range(0, len(rows), 40):
                    send({"k": "rx" if k != "cmd" else "cmdrx", "rows": rows[i:i + 40]})
        if os.path.exists(wd) and os.path.getsize(wd) > wd_pos:
            with open(wd) as fh:
                fh.seek(wd_pos)
                for line in fh.read().splitlines():
                    send({"k": "log", "line": "watchdog: " + line.split(" ", 3)[-1]})
            wd_pos = os.path.getsize(wd)
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("robots", "lidar"):
        g = sp.add_parser(n)
        g.add_argument("--dev")
        g.add_argument("--w", default="/w")
        g.add_argument("--out", default="/o")
    g = sp.add_parser("edge")
    g.add_argument("--dst", default="/o/dst.json")
    g.add_argument("--lvdir", default="/o")
    g = sp.add_parser("camsrc")
    g.add_argument("--truth", required=True)
    g.add_argument("--lvdir", required=True)
    g.add_argument("--edge", default=EDGE)
    g = sp.add_parser("agent")
    g.add_argument("--core", required=True)
    g.add_argument("--port", type=int, default=5300)
    g.add_argument("--clock-port", type=int, default=5299)
    g.add_argument("--dir", default=os.path.expanduser("~/oai/nrlink/logs/factory/live2"))
    g.add_argument("--w", default="/dev/shm/fdl2")
    a = ap.parse_args()
    return {"robots": robots, "lidar": lidar, "edge": edge, "agent": agent, "camsrc": camsrc}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
