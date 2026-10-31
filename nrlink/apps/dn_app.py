#!/usr/bin/env python3
"""Data-network side of the demo apps, run in a sidecar sharing oai-ext-dn's network namespace.

  tcp/6001  eMBB perception server: receives LiDAR scans from UE1 (uplink, paced by TCP)
  udp/6002  URLLC AGV controller: acks every report, sends STOP when an obstacle is ahead of the AGV
  udp/6003  mMTC collector: decodes sensor reports, sends a pending config in the sensor's receive window

JSON lines on stdout for the dashboard server, JSON commands on stdin (obstacles, e-stop, interval).
"""
import base64
import json
import math
import socket
import struct
import sys
import threading
import time

SCAN_HDR = struct.Struct("<IdHHI")
REP = struct.Struct("<BBIdffffH")
CMD = struct.Struct("<BBIddH")
SEN = struct.Struct("<BBHdhHHH")
CFG = struct.Struct("<BBH")
GO, STOP = 1, 2

lock = threading.Lock()
ctl = {"obstacles": [], "estop": False, "interval_pending": None}


def out(**kw):
    kw.setdefault("t", time.time())
    with lock:
        sys.stdout.write(json.dumps(kw, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def commands():
    for line in sys.stdin:
        try:
            c = json.loads(line)
        except ValueError:
            continue
        k = c.get("k")
        if k == "obstacles":
            ctl["obstacles"] = [(float(x), float(y)) for x, y in c.get("list", [])][:8]
            out(k="obstacles", list=ctl["obstacles"])
        elif k == "estop":
            ctl["estop"] = bool(c.get("on"))
            out(k="estop", on=ctl["estop"])
        elif k == "interval":
            ctl["interval_pending"] = max(1, min(60, int(c.get("s", 5))))
            out(k="sensor_cfg", interval=ctl["interval_pending"], pending=True)


# ---------------------------------------------------------------- eMBB
def lidar_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 6001))
    srv.listen(2)
    while True:
        c, addr = srv.accept()
        c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
        out(k="lidar_srv", text=f"LiDAR {addr[0]} connected")
        f = c.makefile("rb")
        try:
            while True:
                h = f.read(SCAN_HDR.size)
                if len(h) < SCAN_HDR.size:
                    raise ConnectionError("stream closed")
                idx, ts, beams, az, n = SCAN_HDR.unpack(h)
                z = f.read(n)
                out(k="scan", idx=idx, beams=beams, az=az, bytes=n + SCAN_HDR.size, raw_bytes=beams * az * 2,
                    lat_ms=round((time.time() - ts) * 1000, 1), z=base64.b64encode(z).decode())
        except (OSError, ConnectionError) as e:
            out(k="lidar_srv", text=f"LiDAR gone ({e})")
            c.close()


# ---------------------------------------------------------------- URLLC
def agv_controller():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 6002))
    cmd, cmd_id, cmd_t = GO, 0, 0.0
    last_seq, rx, lost, lats = None, 0, 0, []
    t_stats = t_pos = time.time()
    halted_reported = False
    while True:
        d, addr = s.recvfrom(64)
        now = time.time()
        if len(d) != REP.size or d[0] != ord("A"):
            continue
        _m, state, seq, ts, x, y, hdg, v, ack_id = REP.unpack(d)
        ul = (now - ts) * 1000
        rx += 1
        lats.append(ul)
        if last_seq is not None and seq > last_seq + 1:
            lost += seq - last_seq - 1
        last_seq = seq
        # safety logic: obstacle ahead within 6 m and 1.5 m of the AGV's track -> STOP
        hx, hy = math.cos(hdg), math.sin(hdg)
        blocking = None
        for ox, oy in ctl["obstacles"]:
            dx, dy = ox - x, oy - y
            along, lateral = dx * hx + dy * hy, abs(dx * hy - dy * hx)
            if -0.3 < along < 6.0 and lateral < 1.5:
                blocking = (ox, oy, along)
                if math.hypot(dx, dy) < 0.6 and v > 0.05:
                    out(k="agv_event", level="crit", text=f"collision with obstacle at ({ox:.0f}, {oy:.0f}) m")
        want = STOP if (ctl["estop"] or blocking) else GO
        if want != cmd:
            cmd, cmd_id, cmd_t = want, (cmd_id + 1) & 0xFFFF, now
            halted_reported = False
            why = "e-stop" if ctl["estop"] else (f"obstacle {blocking[2]:.1f} m ahead" if blocking else "path clear")
            out(k="agv_event", level="warn" if want == STOP else "ok",
                text=f"controller sends {'STOP' if want == STOP else 'GO'} ({why})")
        if cmd == STOP and state == 2 and ack_id == cmd_id and not halted_reported:
            halted_reported = True
            gap = f", {blocking[2] - 0.5:.1f} m short of the obstacle" if blocking else ""
            out(k="agv_event", level="ok", text=f"AGV halted {(now - cmd_t) * 1000:.0f} ms after STOP{gap}")
        s.sendto(CMD.pack(ord("C"), cmd, seq, ts, time.time(), cmd_id), addr)
        if now - t_pos >= 0.1:
            t_pos = now
            out(k="ctrl", x=round(x, 2), y=round(y, 2), h=round(hdg, 3), v=round(v, 2), state=state,
                ul_ms=round(ul, 1), cmd="STOP" if cmd == STOP else "GO")
        if now - t_stats >= 1.0:
            lats.sort()
            n = len(lats)
            out(k="ctrl_stats", rx=rx, lost=lost, ul_p50=round(lats[n // 2], 1) if n else None,
                ul_p99=round(lats[min(n - 1, int(n * 0.99))], 1) if n else None,
                ul_max=round(lats[-1], 1) if n else None, rate=round(n / (now - t_stats), 1))
            lats, t_stats = [], now


# ---------------------------------------------------------------- mMTC
def sensor_collector():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 6003))
    while True:
        d, addr = s.recvfrom(64)
        now = time.time()
        if len(d) != SEN.size or d[0] != ord("S"):
            continue
        _m, _ty, seq, ts, t100, h100, vib, batt = SEN.unpack(d)
        out(k="sensor_rx", seq=seq, temp=t100 / 100, hum=h100 / 100, vib=vib, batt=batt, bytes=len(d),
            ul_ms=round((now - ts) * 1000, 1))
        # downlink only right after an uplink, while the device listens
        if ctl["interval_pending"] is not None:
            iv, ctl["interval_pending"] = ctl["interval_pending"], None
            s.sendto(CFG.pack(ord("K"), 0, iv), addr)
            out(k="sensor_cfg", interval=iv, pending=False)


if __name__ == "__main__":
    for f in (lidar_server, agv_controller, sensor_collector):
        threading.Thread(target=f, daemon=True).start()
    out(k="dn_ready")
    commands()
