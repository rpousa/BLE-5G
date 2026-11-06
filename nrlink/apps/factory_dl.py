#!/usr/bin/env python3
"""Downlink-only factory traffic: one factory floor, three kinds of downlink traffic, all from ext-dn.

  eMBB   -> nrUE1    LiDAR scans of the floor (16 beams x 180 azimuths, zlib), from a pole in the middle
  URLLC  -> nrUE2    robot positioning: where each of the 4 AGVs is, from the edge positioning server
  video  -> n3iwue1  ceiling camera CAM1 (west half of the floor), JPEG frames, over Bluetooth
  video  -> n3iwue2  ceiling camera CAM2 (east half), JPEG frames, over Bluetooth

All four come from the same scene clock, so the page can replay them on one floor, in sync.

  core PC, host venv with Pillow:  factory_dl.py gen --out DIR            (renders every level ahead of time)
  core PC, ext-dn netns sidecar:    factory_dl.py send --scen DIR --dst flow=ip ...
  UE PC, in each UE's netns:        factory_dl.py recv --port P [--dev IF] --out FILE --secs S
  clocks (the two PCs differ by hours, no NTP):
  core PC host:                     factory_dl.py clocksrv
  UE PC host:                       factory_dl.py clock --host 192.168.10.223   -> offset core - UE
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

# ----------------------------------------------------------------------------------------------- scene
W, H = 60.0, 36.0
STATIONS = [("Warehouse", 6, 6), ("Press line", 30, 6), ("Assembly", 54, 6),
            ("Quality", 54, 30), ("Paint", 30, 30), ("Shipping", 6, 30)]
# machines (x0, y0, z0, x1, y1, z1)
MACHINES = [(14, 11, 0, 24, 17, 3.0), (36, 11, 0, 46, 17, 2.5), (44, 19, 0, 50, 26, 4.0),
            (10, 19, 0, 20, 25, 2.0), (26, 21, 0, 34, 26, 3.5)]
WALLS = [(-0.3, -0.3, 0, 60.3, 0, 6), (-0.3, 36, 0, 60.3, 36.3, 6), (-0.3, 0, 0, 0, 36, 6), (60, 0, 0, 60.3, 36, 6)]
LIDAR_POS = (30.0, 18.0, 3.0)
BEAMS = [-15 + 2 * i for i in range(16)]
AZ = 180
N_AGV = 4
AGV_V, AGV_DWELL = 1.4, 3.0
CAMS = {"cam1": (0.0, 30.0), "cam2": (30.0, 60.0)}   # x range each ceiling camera sees (all of y)
CAM_W, CAM_H = 160, 96

# levels: (name, lidar Hz, robot Hz, camera fps)
LEVELS = [("L1", 1, 10, 0.5), ("L2", 2, 20, 1), ("L3", 4, 50, 2), ("L4", 8, 100, 4)]


def _loop():
    pts = [(x, y) for _, x, y in STATIONS]
    segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    per = [math.dist(a, b) / AGV_V + AGV_DWELL for a, b in segs]
    return segs, per, sum(per)


SEGS, PER, LOOP_T = _loop()


def agvs(t):
    """[(x, y, heading rad, speed m/s)] of the AGVs at scene time t: they ride the station loop, evenly spaced."""
    out = []
    for i in range(N_AGV):
        u = (t + i * LOOP_T / N_AGV) % LOOP_T
        for (a, b), p in zip(SEGS, PER):
            if u < p:
                break
            u -= p
        mv = p - AGV_DWELL
        hd = math.atan2(b[1] - a[1], b[0] - a[0])
        if u < mv:
            f = u / mv
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, hd, AGV_V))
        else:
            out.append((b[0], b[1], hd, 0.0))
    return out


def movers(t):
    """Two workers walking and a forklift on the south aisle (box, kind)."""
    out = []
    for i, (x0, x1, y, sp) in enumerate(((26, 36, 28.5, 0.9), (8, 22, 9.0, 1.1))):
        span = x1 - x0
        u = (t * sp / span + i * 0.37) % 2
        x = x0 + span * (u if u < 1 else 2 - u)
        out.append(((x - 0.25, y - 0.25, 0, x + 0.25, y + 0.25, 1.8), "worker"))
    u = (t * 1.6 / 44) % 2
    fx = 8 + 44 * (u if u < 1 else 2 - u)
    out.append(((fx - 1.1, 32.4, 0, fx + 1.1, 33.6, 2.2), "forklift"))
    return out


def agv_box(a):
    x, y, hd, _ = a
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


class Lidar:
    def __init__(self):
        o = LIDAR_POS
        self.rays = []
        for el in BEAMS:
            ce, se = math.cos(math.radians(el)), math.sin(math.radians(el))
            for a in range(AZ):
                az = math.radians(a * 360 / AZ)
                self.rays.append((ce * math.cos(az), ce * math.sin(az), se))
        self.base = []
        for d in self.rays:
            best = (-o[2] / d[2]) if d[2] < -1e-6 else None
            for b in WALLS + MACHINES:
                t = ray_box(o, d, b)
                if t is not None and (best is None or t < best):
                    best = t
            self.base.append(best)

    def scan(self, t):
        o = LIDAR_POS
        dyn = [b for b, _ in movers(t)] + [agv_box(a) for a in agvs(t)]
        rng = bytearray(len(self.rays) * 2)
        for i, d in enumerate(self.rays):
            best = self.base[i]
            for b in dyn:
                r = ray_box(o, d, b)
                if r is not None and (best is None or r < best):
                    best = r
            cm = 0 if best is None or best > 80 else int(best * 100)
            rng[2 * i], rng[2 * i + 1] = cm & 0xFF, cm >> 8
        return zlib.compress(bytes(rng), 9)


def camera_frame(cam, t, n):
    from PIL import Image, ImageDraw   # only the generator needs Pillow
    x0, x1 = CAMS[cam]
    s = CAM_W / (x1 - x0)
    im = Image.new("RGB", (CAM_W, CAM_H), (92, 98, 104))
    g = ImageDraw.Draw(im)
    P = lambda x, y: ((x - x0) * s, (y / H) * CAM_H)  # noqa: E731
    for i in range(len(STATIONS)):   # aisle loop, yellow floor tape
        a, b = STATIONS[i][1:], STATIONS[(i + 1) % len(STATIONS)][1:]
        g.line([P(*a), P(*b)], fill=(178, 160, 60), width=max(1, int(1.6 * s)))
    for name, x, y in STATIONS:
        g.rectangle([P(x - 2, y - 2), P(x + 2, y + 2)], outline=(200, 205, 210))
    for b in MACHINES:
        sh = int(60 + 30 * b[5])
        g.rectangle([P(b[0], b[1]), P(b[3], b[4])], fill=(sh, sh + 6, sh + 14), outline=(30, 34, 40))
    for b, kind in movers(t):
        if kind == "worker":
            cx, cy = P((b[0] + b[3]) / 2, (b[1] + b[4]) / 2)
            g.ellipse([cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5], fill=(240, 240, 240))
        else:
            g.rectangle([P(b[0], b[1]), P(b[3], b[4])], fill=(230, 180, 20))
    for i, a in enumerate(agvs(t)):
        x, y, hd, _ = a
        c, sn = math.cos(hd), math.sin(hd)
        pts = [(x + c * dx - sn * dy, y + sn * dx + c * dy) for dx, dy in ((0.8, 0), (-0.6, 0.5), (-0.6, -0.5))]
        g.polygon([P(*p) for p in pts], fill=(255, 110, 40), outline=(40, 20, 10))
    g.rectangle([0, 0, 92, 10], fill=(0, 0, 0))
    g.text((2, 0), f"{cam.upper()} {t:06.2f}s #{n}", fill=(255, 255, 255))
    import io
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=38, optimize=True)
    return buf.getvalue()


def gen(a):
    os.makedirs(a.out, exist_ok=True)
    lid = Lidar()
    meta = {"secs": a.secs, "levels": [], "floor": {"w": W, "h": H, "stations": STATIONS, "machines": MACHINES,
            "lidar": LIDAR_POS, "beams": BEAMS, "az": AZ, "cams": CAMS, "cam_px": [CAM_W, CAM_H],
            "agvs": N_AGV, "loop_t": LOOP_T}}
    for name, lhz, rhz, fps in LEVELS:
        ev = []   # (t, flow, frame, bytes)
        for i in range(int(a.secs * lhz)):
            ev.append((i / lhz, "lidar", i, lid.scan(i / lhz)))
        for i in range(int(a.secs * rhz)):
            t = i / rhz
            body = struct.pack("<dB", t, N_AGV) + b"".join(struct.pack("<ffff", *r) for r in agvs(t))
            ev.append((t, "robots", i, body))
        for cam in CAMS:
            for i in range(int(a.secs * fps)):
                ev.append((i / fps + (0.25 / fps if cam == "cam2" else 0), cam, i, camera_frame(cam, i / fps, i)))
        ev.sort(key=lambda e: e[0])
        sz = {}
        for e in ev:
            sz.setdefault(e[1], []).append(len(e[3]))
        info = {"name": name, "lidar_hz": lhz, "robot_hz": rhz, "cam_fps": fps,
                "kbps": {f: round(sum(v) * 8 / a.secs / 1000, 1) for f, v in sz.items()},
                "mean_bytes": {f: round(sum(v) / len(v)) for f, v in sz.items()}}
        meta["levels"].append(info)
        with open(os.path.join(a.out, f"{name}.json"), "w") as f:
            json.dump([[round(t, 4), fl, n, base64.b64encode(b).decode()] for t, fl, n, b in ev], f)
        print(json.dumps(info), flush=True)
    with open(os.path.join(a.out, "meta.json"), "w") as f:
        json.dump(meta, f)


# ----------------------------------------------------------------------------------------------- wire
HDR = struct.Struct("<4sBBHIIIdHH")   # magic, flow, level, run, frame, seq, crc32 of the frame, t_send, frag, nfrag
MAGIC = b"FDL1"
FLOWS = ["lidar", "robots", "cam1", "cam2"]
CHUNK = 900


def send(a):
    dst = dict(x.split("=") for x in a.dst)
    port = dict(lidar=6101, robots=6102, cam1=6103, cam2=6104)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    run = int.from_bytes(os.urandom(2), "little")
    levels = a.levels.split(",")
    log = open(a.log, "w") if a.log else None
    seq = {f: 0 for f in FLOWS}
    cache = {}

    def plan():
        if not a.loop:
            yield from enumerate(levels)
            return
        i = 0
        while True:   # endless: the level is read from --level-file before every loop
            name = levels[0]
            try:
                want = open(a.level_file).read().strip()
                if want in [x[0] for x in LEVELS]:
                    name = want
            except OSError:
                pass
            yield i, name
            i += 1
    for li, name in plan():
        if name not in cache:
            cache[name] = json.load(open(os.path.join(a.scen, f"{name}.json")))
        ev = cache[name]
        lvl = int(name[1:])
        t0 = time.time() + 1.0
        print(json.dumps({"level": name, "t0": t0, "run": run, "loop": li}), flush=True)
        if log:
            log.write(json.dumps({"level": name, "t0": t0, "run": run}) + "\n")
        last_dst = 0.0
        for t, fl, n, b64 in ev:
            if a.dst_file and time.time() - last_dst > 2:   # UE addresses change when a UE re-registers
                last_dst = time.time()
                try:
                    dst.update(json.load(open(a.dst_file)))
                except (OSError, ValueError):
                    pass
            if fl not in dst:
                continue
            data = base64.b64decode(b64)
            crc = zlib.crc32(data)
            due = t0 + t
            d = due - time.time()
            if d > 0:
                time.sleep(d)
            chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
            for k, c in enumerate(chunks):
                ts = time.time()
                try:
                    s.sendto(HDR.pack(MAGIC, FLOWS.index(fl), lvl, run, n, seq[fl], crc, ts, k, len(chunks)) + c,
                             (dst[fl], port[fl]))
                except OSError:
                    pass
                seq[fl] += 1
            if log:
                log.write(f'{fl} {n} {t} {len(data)} {len(chunks)}\n')
        if a.loop:
            time.sleep(max(0.0, t0 + a.secs - time.time() - 1.0))   # next loop starts secs after this one
        elif li < len(levels) - 1:
            time.sleep(a.gap)
    if log:
        log.close()


def _rx_socket(a):
    """Bound UDP socket, or None while the device is missing (an n3iwue restart takes its gretun away)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if a.dev:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, a.dev.encode())
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        s.bind(("0.0.0.0", a.port))
    except OSError:
        s.close()
        return None
    s.settimeout(0.5)
    return s


def recv(a):
    end = time.time() + a.secs
    frames = {}
    s, last = None, time.time()
    with open(a.out, "w", buffering=1) as f:
        while time.time() < end:
            if s is None:
                s = _rx_socket(a)
                if s is None:
                    time.sleep(1)
                    continue
                last = time.time()
            try:
                p = s.recv(4096)
            except socket.timeout:
                if a.dev and time.time() - last > 5:     # quiet: re-bind, the device may have been recreated
                    s.close()
                    s = None
                continue
            last = time.time()
            now = time.time()
            if len(p) < HDR.size or p[:4] != MAGIC:
                continue
            _m, fl, lvl, run, n, sq, crc, ts, k, nk = HDR.unpack_from(p)
            key = (lvl, n)
            fr = frames.setdefault(key, {})
            fr[k] = p[HDR.size:]
            ok = None
            if len(fr) == nk:
                ok = zlib.crc32(b"".join(fr[i] for i in range(nk))) == crc
                del frames[key]
            f.write(json.dumps([FLOWS[fl], lvl, n, sq, k, nk, round(ts, 6), round(now, 6), len(p), ok]) + "\n")
            if len(frames) > 2000:
                frames.pop(next(iter(frames)))


# ----------------------------------------------------------------------------------------------- clocks
def clocksrv(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", a.port))
    while True:
        p, addr = s.recvfrom(64)
        s.sendto(p[:8] + struct.pack("<d", time.time()), addr)


def clock(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.3)
    best = None
    for _ in range(a.n):
        t1 = time.time()
        s.sendto(struct.pack("<d", t1), (a.host, a.port))
        try:
            p = s.recv(64)
        except socket.timeout:
            continue
        t4 = time.time()
        t2 = struct.unpack_from("<d", p, 8)[0]
        rtt = t4 - t1
        if best is None or rtt < best[0]:
            best = (rtt, t2 - (t1 + t4) / 2)
        time.sleep(0.01)
    print(json.dumps({"t": time.time(), "offset_s": best[1], "rtt_ms": round(best[0] * 1000, 3)}))


# ----------------------------------------------------------------------------------------------- UE PC agent
AGENT_FLOWS = {"lidar": ("oai-nrue1", 6101, None, "oaitun_ue1"), "robots": ("oai-nrue2", 6102, None, "oaitun_ue1"),
               "cam1": ("nrlink-n3iwue1", 6103, "gretun-id-2-1", "gretun-id-2-1"),
               "cam2": ("nrlink-n3iwue2", 6104, "gretun-id-2-1", "gretun-id-2-1")}


def _sh(*cmd):
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:   # noqa: BLE001
        return ""


def _offset(host, port, n=40):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.3)
    best = None
    for _ in range(n):
        t1 = time.time()
        s.sendto(struct.pack("<d", t1), (host, port))
        try:
            p = s.recv(64)
        except socket.timeout:
            continue
        t4 = time.time()
        t2 = struct.unpack_from("<d", p, 8)[0]
        if best is None or t4 - t1 < best[0]:
            best = (t4 - t1, t2 - (t1 + t4) / 2)
        time.sleep(0.01)
    return best


def agent(a):
    """UE PC: keep one receiver per UE running (restarted when its UE container is recreated), forward every arrival,
    the UE addresses, the clock offset and the watchdog log to the core's live server."""
    os.makedirs(a.dir, exist_ok=True)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (a.core, a.port)
    send = lambda m: out.sendto(json.dumps(m, separators=(",", ":")).encode(), dst)   # noqa: E731
    parent, pos = {}, {}
    wd = os.path.expanduser("~/oai/nrlink/logs/ble_watchdog.log")
    wd_pos = os.path.getsize(wd) if os.path.exists(wd) else 0
    t_sup = t_dst = t_clk = 0.0
    me = os.path.dirname(os.path.abspath(__file__))
    while True:
        now = time.time()
        if now - t_sup > 5:
            t_sup = now
            for fl, (ue, port, dev, _) in AGENT_FLOWS.items():
                pid = _sh("docker", "inspect", "-f", "{{.Id}} {{.State.Running}}", ue)
                name = f"fdl-live-rx-{fl}"
                running = _sh("docker", "inspect", "-f", "{{.State.Running}}", name) == "true"
                big = os.path.exists(os.path.join(a.dir, f"rx_{fl}.jsonl")) and \
                    os.path.getsize(os.path.join(a.dir, f"rx_{fl}.jsonl")) > 50e6
                if pid.endswith("true") and (not running or parent.get(fl) != pid or big):
                    _sh("docker", "rm", "-f", name)
                    cmd = ["docker", "run", "-d", "--rm", "--name", name, "--network", f"container:{ue}",
                           "--entrypoint", "python3", "-v", f"{me}:/a:ro", "-v", f"{os.path.abspath(a.dir)}:/o",
                           "nrlink-run:local", "-u", "/a/factory_dl.py", "recv", "--port", str(port),
                           "--out", f"/o/rx_{fl}.jsonl", "--secs", "1e9"] + (["--dev", dev] if dev else [])
                    _sh(*cmd)
                    parent[fl] = pid
                    pos[fl] = 0
                    send({"k": "log", "line": f"receiver for {fl} (re)started in {ue}"})
        if now - t_dst > 5:
            t_dst = now
            d = {}
            for fl, (ue, _, _, ifc) in AGENT_FLOWS.items():
                o = _sh("docker", "exec", ue, "ip", "-4", "-o", "addr", "show", ifc)
                ip = [w.split("/")[0] for w in o.split() if w.startswith("12.1.1.")]
                if ip:
                    d[fl] = ip[0]
            send({"k": "dst", "dst": d})
        if now - t_clk > 30:
            t_clk = now
            b = _offset(a.core, a.clock_port)
            if b:
                send({"k": "clock", "offset_s": b[1], "rtt_ms": round(b[0] * 1000, 3), "t": time.time()})
        for fl in AGENT_FLOWS:
            p = os.path.join(a.dir, f"rx_{fl}.jsonl")
            if not os.path.exists(p):
                continue
            sz = os.path.getsize(p)
            if sz < pos.get(fl, 0):
                pos[fl] = 0
            if sz > pos.get(fl, 0):
                with open(p) as f:
                    f.seek(pos.get(fl, 0))
                    data = f.read()
                cut = data.rfind("\n") + 1
                pos[fl] = pos.get(fl, 0) + len(data[:cut].encode())
                rows = []
                for line in data[:cut].splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
                for i in range(0, len(rows), 40):
                    send({"k": "rx", "rows": rows[i:i + 40]})
        if os.path.exists(wd) and os.path.getsize(wd) > wd_pos:
            with open(wd) as f:
                f.seek(wd_pos)
                for line in f.read().splitlines():
                    send({"k": "log", "line": "watchdog: " + line.split(" ", 3)[-1]})
            wd_pos = os.path.getsize(wd)
        time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("gen"); g.add_argument("--out", required=True); g.add_argument("--secs", type=float, default=60)
    g = sp.add_parser("send"); g.add_argument("--scen", required=True); g.add_argument("--dst", nargs="+", required=True)
    g.add_argument("--levels", default="L1,L2,L3,L4"); g.add_argument("--gap", type=float, default=10)
    g.add_argument("--log"); g.add_argument("--dst-file", help="JSON {flow: ip}, re-read every 2 s")
    g.add_argument("--loop", action="store_true", help="repeat forever, level from --level-file")
    g.add_argument("--level-file"); g.add_argument("--secs", type=float, default=60, help="loop length (scene length)")
    g = sp.add_parser("recv"); g.add_argument("--port", type=int, required=True); g.add_argument("--dev")
    g.add_argument("--out", required=True); g.add_argument("--secs", type=float, required=True)
    g = sp.add_parser("clocksrv"); g.add_argument("--port", type=int, default=5299)
    g = sp.add_parser("clock"); g.add_argument("--host", required=True); g.add_argument("--port", type=int, default=5299)
    g.add_argument("-n", type=int, default=200)
    g = sp.add_parser("agent"); g.add_argument("--core", required=True); g.add_argument("--port", type=int, default=5300)
    g.add_argument("--clock-port", type=int, default=5299); g.add_argument("--dir", default=os.path.expanduser("~/oai/nrlink/logs/factory/live"))
    a = ap.parse_args()
    return {"gen": gen, "send": send, "recv": recv, "clocksrv": clocksrv, "clock": clock, "agent": agent}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
