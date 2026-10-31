#!/usr/bin/env python3
"""UE-side demo apps, run inside an nrUE container; all traffic goes through oaitun_ue1 (the NR link).

  ue_app.py lidar    eMBB:  stream 16-beam LiDAR scans to the ext-dn (TCP, uplink-heavy)
  ue_app.py agv      URLLC: factory AGV, 50 Hz position reports up, controller commands down (UDP)
  ue_app.py sensor   mMTC:  sensor node, a 20 B report every few seconds, short receive window after it

Prints JSON lines on stdout for the dashboard server; reads JSON commands on stdin (local stimuli).
"""
import json
import math
import select
import socket
import struct
import sys
import threading
import time
import zlib

DN = "192.168.70.132"
DEV = b"oaitun_ue1"
SO_BINDTODEVICE = 25


def out(**kw):
    kw.setdefault("t", time.time())
    sys.stdout.write(json.dumps(kw, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def bound(kind):
    s = socket.socket(socket.AF_INET, kind)
    s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, DEV)
    return s


def stdin_cmds(handler):
    def loop():
        for line in sys.stdin:
            try:
                handler(json.loads(line))
            except Exception as e:  # noqa: BLE001
                out(k="error", text=f"bad command: {e}")
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- eMBB LiDAR
# 16-beam spinning LiDAR on a pole in the factory (same floor plan as the AGV map), 1 degree azimuth steps
SCAN_HDR = struct.Struct("<IdHHI")  # scan index, send time, beams, azimuth steps, compressed length
LIDAR_POS = (26.0, 26.0, 2.5)
BEAMS = [-15 + 2 * i for i in range(16)]
AZ = 360
# axis-aligned boxes (x0, y0, z0, x1, y1, z1): walls, machines, station docks
STATIC = [(-0.3, -0.3, 0, 60.3, 0, 6), (-0.3, 36, 0, 60.3, 36.3, 6), (-0.3, 0, 0, 0, 36, 6), (60, 0, 0, 60.3, 36, 6),
          (14, 11, 0, 24, 17, 3.0), (36, 11, 0, 46, 17, 2.5), (44, 18, 0, 50, 27, 4.0), (14, 19, 0, 24, 25, 2.0),
          (30, 15, 0, 38, 23, 3.5), (25.3, 25.3, 0, 25.7, 25.7, 2.3)]

def ray_box(o, d, b):
    tmin, tmax = 0.05, 60.0
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


def lidar_rays():
    rays = []
    for el in BEAMS:
        ce, se = math.cos(math.radians(el)), math.sin(math.radians(el))
        for a in range(AZ):
            az = math.radians(a)
            rays.append((ce * math.cos(az), ce * math.sin(az), se))
    return rays


def movers(t):
    """Two workers walking and a forklift on the south aisle."""
    out_ = []
    for i, (x0, x1, y, sp) in enumerate(((27, 34, 27.5, 0.9), (10, 22, 9.0, 1.1))):
        span = x1 - x0
        u = (t * sp / span + i * 0.37) % 2
        x = x0 + span * (u if u < 1 else 2 - u)
        out_.append((x - 0.25, y - 0.25, 0, x + 0.25, y + 0.25, 1.8))
    u = (t * 1.6 / 44) % 2
    fx = 8 + 44 * (u if u < 1 else 2 - u)
    out_.append((fx - 1.1, 29.4, 0, fx + 1.1, 30.6, 2.2))
    return out_


def lidar():
    state = {"on": True}

    def on_cmd(c):
        if c.get("k") == "lidar":
            state["on"] = bool(c.get("on"))
            out(k="lidar_ctl", on=state["on"])
    stdin_cmds(on_cmd)
    o = LIDAR_POS
    rays = lidar_rays()
    base = []
    for d in rays:  # static scene once: nearest box or the floor
        best = (-o[2] / d[2]) if d[2] < -1e-6 else None
        for b in STATIC[:-1]:
            t = ray_box(o, d, b)
            if t is not None and (best is None or t < best):
                best = t
        base.append(best)
    out(k="lidar_state", text=f"{len(rays)} rays per scan, scene ready")
    idx = 0
    while True:
        try:
            s = bound(socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(30)
            s.connect((DN, 6001))
            out(k="lidar_state", text="connected to the perception server")
            while True:
                t0 = time.time()
                if not state["on"]:
                    time.sleep(0.1)
                    continue
                mv = movers(t0)
                rng = bytearray(len(rays) * 2)
                for i, d in enumerate(rays):
                    best = base[i]
                    for b in mv:
                        t = ray_box(o, d, b)
                        if t is not None and (best is None or t < best):
                            best = t
                    cm = 0 if best is None or best > 60 else int(best * 100)
                    rng[2 * i] = cm & 0xFF
                    rng[2 * i + 1] = cm >> 8
                z = zlib.compress(bytes(rng), 6)
                idx += 1
                s.sendall(SCAN_HDR.pack(idx, time.time(), len(BEAMS), AZ, len(z)) + z)  # blocks at link speed
                time.sleep(max(0.0, 0.1 - (time.time() - t0)))  # 10 Hz at most
        except Exception as e:  # noqa: BLE001
            out(k="lidar_state", text=f"reconnecting ({e})")
            time.sleep(2)


# ---------------------------------------------------------------- URLLC AGV
# factory floor 60 m x 36 m, loop through the stations (shared with the dashboard)
STATIONS = [("Warehouse", 6, 6), ("Press line", 30, 6), ("Assembly", 54, 6),
            ("Quality", 54, 30), ("Paint", 30, 30), ("Shipping", 6, 30)]
REP = struct.Struct("<BBIdffffH")  # 'A', state, seq, t_send, x, y, heading, speed, last cmd id  (32 B)
CMD = struct.Struct("<BBIddH")     # 'C', cmd, echo seq, echo t_send, t_send, cmd id               (24 B)
GO, STOP = 1, 2
V_MAX, ACC, DEC, DWELL = 2.0, 1.0, 2.5, 2.0


def agv():
    pts = [(x, y) for _, x, y in STATIONS]
    segs = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        segs.append((a, b, math.dist(a, b)))
    seg, along, v, dwell = 0, 0.0, 0.0, DWELL
    cmd, cmd_id, seq = GO, 0, 0
    rtt = dl = None
    s = bound(socket.SOCK_DGRAM)
    s.setblocking(False)
    period, nxt, last_print = 0.02, time.time(), 0.0
    while True:
        now = time.time()
        # downlink: controller acks / commands
        while True:
            try:
                d = s.recv(64)
            except BlockingIOError:
                break
            if len(d) == CMD.size and d[0] == ord("C"):
                _m, c, eseq, et, ts, cid = CMD.unpack(d)
                rtt = (now - et) * 1000
                dl = (now - ts) * 1000
                if cid != cmd_id:
                    out(k="agv_cmd", cmd="STOP" if c == STOP else "GO", dl_ms=round(dl, 1), id=cid)
                    cmd_id = cid
                cmd = c
        # motion
        a, b, ln = segs[seg]
        if dwell > 0:
            dwell -= period
            state = 3  # docked
        else:
            target = V_MAX if cmd == GO else 0.0
            if v < target:
                v = min(target, v + ACC * period)
            else:
                v = max(target, v - DEC * period)
            # slow down for the station at the end of the segment
            v = min(v, max(0.4, math.sqrt(2 * DEC * max(0.0, ln - along))))
            along += v * period
            if along >= ln:
                seg, along, dwell, v = (seg + 1) % len(segs), 0.0, DWELL, 0.0
                a, b, ln = segs[seg]
            state = 1 if v > 0.01 else 2  # moving / halted
        f = along / ln
        x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        hdg = math.atan2(b[1] - a[1], b[0] - a[0])
        seq += 1
        try:
            s.sendto(REP.pack(ord("A"), state, seq, time.time(), x, y, hdg, v, cmd_id), (DN, 6002))
        except OSError:
            pass
        if now - last_print >= 0.1:
            last_print = now
            out(k="agv", x=round(x, 2), y=round(y, 2), h=round(hdg, 3), v=round(v, 2), state=state, seq=seq,
                station=STATIONS[(seg + 1) % len(STATIONS)][0] if dwell <= 0 else STATIONS[seg][0],
                rtt_ms=None if rtt is None else round(rtt, 1), dl_ms=None if dl is None else round(dl, 1))
        nxt += period
        time.sleep(max(0.0, nxt - time.time()))


# ---------------------------------------------------------------- mMTC sensor
SEN = struct.Struct("<BBHdhHHH")  # 'S', type, seq, t_send, temp 0.01 C, humidity 0.01 %, vibration mg, battery mV (20 B)
CFG = struct.Struct("<BBH")       # 'K', 0, interval s


def sensor():
    state = {"interval": 5, "heat": 0.0}

    def on_cmd(c):
        if c.get("k") == "heat":  # physical stimulus at the sensor (a hot-air gun pointed at it)
            state["heat"] = float(c.get("deg", 15))
            out(k="sensor_event", text=f"heat applied at the sensor (+{state['heat']:.0f} C)")
    stdin_cmds(on_cmd)
    s = bound(socket.SOCK_DGRAM)
    seq, batt, t0 = 0, 3600.0, time.time()
    temp = 21.0
    while True:
        now = time.time()
        # slow room drift + noise, heat stimulus decays with ~60 s time constant
        ambient = 21.0 + 1.5 * math.sin((now - t0) / 600 * 2 * math.pi)
        temp += (ambient + state["heat"] - temp) * 0.35
        state["heat"] *= math.exp(-state["interval"] / 60)
        hum = 45 + 5 * math.sin((now - t0) / 900 * 2 * math.pi) - (temp - ambient) * 0.8
        vib = 12 + 4 * abs(math.sin(now * 1.3)) + (25 if int(now) % 37 < 3 else 0)
        batt -= 0.02
        seq = (seq + 1) & 0xFFFF
        pkt = SEN.pack(ord("S"), 1, seq, time.time(), int(temp * 100), int(max(0, hum) * 100), int(vib), int(batt))
        s.sendto(pkt, (DN, 6003))
        out(k="sensor_tx", seq=seq, temp=round(temp, 2), hum=round(hum, 1), vib=round(vib), batt=int(batt),
            bytes=len(pkt), interval=state["interval"])
        # receive window: 1 s after each uplink, then "sleep" until the next report
        rx_until = time.time() + 1.0
        while (left := rx_until - time.time()) > 0:
            if select.select([s], [], [], left)[0]:
                d = s.recv(64)
                if len(d) == CFG.size and d[0] == ord("K"):
                    state["interval"] = CFG.unpack(d)[2]
                    out(k="sensor_cfg_rx", interval=state["interval"])
        time.sleep(max(0.0, state["interval"] - 1.0))


if __name__ == "__main__":
    {"lidar": lidar, "agv": agv, "sensor": sensor}[sys.argv[1]]()
