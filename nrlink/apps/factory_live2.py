#!/usr/bin/env python3
"""Live factory, closed loop (factory2.py): serves a page showing the plant as the edge controller sees it.

  core PC:  python3 apps/factory_live2.py [--http 8090]          then open http://<core PC>:8090/
  UE PC:    python3 apps/factory2.py agent --core 192.168.10.223

Runs, on the core PC:
  - the edge controller in a sidecar sharing oai-ext-dn's network (LiDAR + reports in, commands + video out);
  - the ceiling cameras (factory2.py camsrc, Pillow from ~/nxp/venv), which feed video to the edge;
  - the clock server for the UE PC (udp/5299) and the collector for its agent (udp/5300).
Works out every flow's one-way delay on the core clock, the report-to-command reaction time, losses, and how close
each AGV comes to an operator, and pushes it all to the page as server-sent events.
"""
import argparse
import json
import math
import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import factory2 as F   # noqa: E402

ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, "logs", "factory", "live2_core")
FLOWS = ["lidar", "reports", "commands", "cam1", "cam2"]
LOST_AFTER = 30.0
VENV_PY = os.path.expanduser("~/nxp/venv/bin/python")

lock = threading.Lock()
clients = []
st = {"offset": None, "rtt_ms": None, "dst": {}, "level": "L1", "log": [], "agent_t": 0.0, "agent_addr": None,
      "edge": "starting", "cams": "starting", "truth": None,
      "recent": {f: [] for f in FLOWS},          # (t_sent core, delay ms | None)
      "last_n": {"lidar": None, "reports": None},
      "pend_cmd": {}, "pend_cam": {}, "react": [], "clear": [], "near": {}, "mode": {},
      "out": {f: [] for f in FLOWS}, "out_react": []}
st.update(health={}, tele={}, pings={}, level_t=time.time())
BY_LEVEL = os.path.join(LIVE, "by_level.json")
st["by_level"] = F.rd_json(BY_LEVEL, {})      # level -> last settled stats measured at it (kept across restarts)
SETTLE_S = 90.0                                 # a level counts as measured after 90 s on it (queues from the last one gone)   # points not yet pushed to the pages


def _add(flow, t, d):
    """Call with the lock held."""
    st["recent"][flow].append((t, d))
    st["out"][flow].append((round(t, 2), None if d is None else round(d, 1)))
META = {"w": F.W, "h": F.H, "stations": F.STATIONS, "machines": F.MACHINES, "lidar": F.LIDAR_POS, "beams": F.BEAMS,
        "az": F.AZ, "cams": F.CAMS, "operators": F.OPERATORS, "op_v": F.OP_V, "op_cam": F.OP_CAM, "epoch": F.EPOCH,
        "levels": F.LEVELS, "n_agv": F.N_AGV, "modes": F.MODES, "failsafe_s": F.FAILSAFE_S}


def push(ev, keep=True):
    data = ("data: " + json.dumps(ev, separators=(",", ":")) + "\n\n").encode()
    with lock:
        for q, kinds in list(clients):
            if kinds and ev.get("k") not in kinds:
                continue
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def log(line):
    e = {"t": time.time(), "line": line}
    with lock:
        st["log"] = (st["log"] + [e])[-40:]
    push({"k": "log", **e})


def record(flow, t_sent, delay):
    with lock:
        _add(flow, t_sent, delay)


def wr_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    F.wr_json(p, obj)


# --------------------------------------------------------------------------------------------- UE PC agent
def clocksrv(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    while True:
        p, addr = s.recvfrom(64)
        s.sendto(p[:8] + struct.pack("<d", time.time()), addr)


def collector(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s.bind(("0.0.0.0", port))
    last_reply = 0.0
    while True:
        p, addr = s.recvfrom(65535)
        try:
            m = json.loads(p)
        except ValueError:
            continue
        now = time.time()
        st["agent_t"] = now
        if now - last_reply > 1 or st.pop("force_reply", False):   # tell the agent the level (it writes it for the UE side)
            last_reply = now
            s.sendto(json.dumps({"level": st["level"]}).encode(), addr)
        k = m.get("k")
        if k == "clock":
            with lock:
                st["offset"], st["rtt_ms"] = m["offset_s"], m["rtt_ms"]
        elif k == "dst":
            with lock:
                changed = {f: ip for f, ip in m["dst"].items() if st["dst"].get(f) != ip}
                st["dst"].update(m["dst"])
                d = dict(st["dst"])
            wr_json(os.path.join(LIVE, "dst.json"), d)
            for f, ip in changed.items():
                log(f"{f}: UE address now {ip}")
        elif k == "log":
            log(m["line"])
        elif k == "health":
            with lock:
                st["health"]["ue"] = m
            push({"k": "health", "pc": "ue", **m})
        elif k == "tele":
            with lock:
                st["tele"][f"ue{m.get('id')}"] = m
            push(m)
        elif k == "truth" and st["offset"] is not None:
            truth(m)
        elif k == "rx" and st["offset"] is not None:
            for row in m["rows"]:
                cam_rx(row)
        elif k == "cmdrx" and st["offset"] is not None:
            for row in m["rows"]:
                cmd_rx(row)


def truth(m):
    """The AGVs' real state (10 Hz from the robots via the agent): safety distances and stop/start events."""
    t_core = m["t"] + st["offset"]
    ts = t_core - F.EPOCH
    ops = {n: F.operator(n, ts) for n in F.OPERATORS}
    ev = {"k": "truth", "t": round(t_core, 3), "agvs": m["agvs"]}
    lines = []
    with lock:
        st["truth"] = ev
        for i, (x, y, hd, v, mode, cmd) in enumerate(m["agvs"]):
            d, who = min((math.hypot(x - ox, y - oy), n) for n, (ox, oy) in ops.items())
            st["clear"].append((t_core, i, d, who))
            prev = st["mode"].get(i)
            if prev != mode:
                st["mode"][i] = mode
                if mode == 2:
                    lines.append(f"AGV{i + 1} stopped by the edge, {d:.1f} m from {who}")
                elif mode == 3:
                    lines.append(f"AGV{i + 1} FAIL-SAFE STOP: no command for {F.FAILSAFE_S:.0f} s")
                elif mode == 0 and prev in (2, 3):
                    lines.append(f"AGV{i + 1} moving again")
            near = d < 1.0
            if near and not st["near"].get(i):
                lines.append(f"NEAR MISS: AGV{i + 1} within {d:.2f} m of {who} at {v:.1f} m/s")
            st["near"][i] = near
    wr_json(os.path.join(LIVE, "truth.json"), {"t": t_core, "agvs": m["agvs"]})
    push(ev)
    for ln in lines:
        log(ln)


def cmd_rx(row):
    cseq, echo, echo_t, ts, now_ue, states = row
    arr = now_ue + st["offset"]
    with lock:
        st["pend_cmd"].pop(cseq, None)
        st["react"].append((echo_t + st["offset"], (now_ue - echo_t) * 1000))
        st["out_react"].append((round(echo_t + st["offset"], 2), round((now_ue - echo_t) * 1000, 1)))
    record("commands", ts, (arr - ts) * 1000)


def cam_rx(row):
    fl, _lvl, n, _sq, _k, _nk, ts, tr, _ln, ok = row
    if not ok:
        return
    with lock:
        p = st["pend_cam"].pop((fl, n), None)
    if p is None:
        return
    arr = tr + st["offset"]
    delay = (arr - p["t"]) * 1000
    record(fl, p["t"], delay)
    push({"k": "cam", "cam": fl, "n": n, "sent": round(p["t"], 3), "cap": p["cap"], "arr": round(arr, 3),
          "delay": round(delay, 1), "b64": p["b64"]})


# --------------------------------------------------------------------------------------------- edge + cameras
def seq_flow(flow, n, t_sent, delay):
    """Uplink flows are numbered; a gap is a loss."""
    with lock:
        last = st["last_n"][flow]
        if last is not None and n < last - 1000:   # sender restarted
            last = None
        if last is not None and n > last + 1:
            for k in range(last + 1, n):
                _add(flow, t_sent, None)
        if last is None or n > last:
            st["last_n"][flow] = n
        _add(flow, t_sent, delay)


def edge_event(m):
    k, off = m["k"], st["offset"]
    if k == "scan" and off is not None:
        sent = m["sent"] + off
        delay = (m["recv"] - sent) * 1000
        seq_flow("lidar", m["n"], sent, delay)
        push({"k": "scan", "n": m["n"], "sent": round(sent, 3), "recv": round(m["recv"], 3), "delay": round(delay, 1),
              "scene": m["scene"], "nobst": m["nobst"], "b64": m["b64"]})
    elif k == "rep" and off is not None:
        sent = m["sent"] + off
        delay = (m["recv"] - sent) * 1000
        seq_flow("reports", m["seq"], sent, delay)
        # each report says which trial the UE side ran when it was SENT; check fresh ones only
        if delay < 3000 and m["lvl"] != int(st["level"][1:]) and time.time() - st["level_t"] > 10:
            if time.time() - st.get("mismatch_t", 0) > 30:
                st["mismatch_t"] = time.time()
                log(f"UE side is still sending Trial {m['lvl']} while Trial {st['level'][1:]} is set; telling the agent again")
            st["force_reply"] = True
        with lock:
            st["edge_view"] = {"k": "rep", "seq": m["seq"], "sent": round(sent, 3), "delay": round(delay, 1),
                               "agvs": m["agvs"]}
    elif k == "cmd":
        with lock:
            st["pend_cmd"][m["seq"]] = m["t"]
            st["last_cmd"] = {"k": "cmd", "seq": m["seq"], "t": m["t"], "states": m["states"], "scan": m["scan"]}
    elif k == "cam_tx":
        with lock:
            st["pend_cam"][(m["cam"], m["n"])] = {"t": m["t"], "cap": m["cap"], "b64": m["b64"]}


def run_forever(name, cmd, on_line, label):
    while True:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True) if name else None
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        st[label] = "running"
        log(f"{label} started")
        for line in p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                log(f"{label}: " + line.strip()[:200])
                continue
            on_line(m)
        p.wait()
        st[label] = f"exited ({p.returncode}), restarting"
        log(f"{label} exited with {p.returncode}; restarting in 5 s")
        time.sleep(5)


def edge():
    os.makedirs(LIVE, exist_ok=True)
    run_forever("fdl2-edge", ["docker", "run", "--rm", "--name", "fdl2-edge", "--network", "container:oai-ext-dn",
                              "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "-v", f"{LIVE}:/o", "nrlink-run:local",
                              "-u", "/a/factory2.py", "edge", "--dst", "/o/dst.json", "--lvdir", "/o"],
                edge_event, "edge")


def cams():
    run_forever(None, [VENV_PY, "-u", os.path.join(HERE, "factory2.py"), "camsrc", "--truth",
                       os.path.join(LIVE, "truth.json"), "--lvdir", LIVE], lambda m: None, "cams")


def ticker():
    """Edge view at 10 Hz, then once a second: losses, stats and a status line."""
    n = 0
    while True:
        time.sleep(0.1)
        n += 1
        with lock:
            ev, cm = st.get("edge_view"), st.get("last_cmd")
        if ev:
            push({**ev, "cmd": cm})
        if n % 10:
            continue
        now = time.time()
        with lock:
            for seq, t in list(st["pend_cmd"].items()):
                if now - t > LOST_AFTER:
                    _add("commands", t, None)
                    del st["pend_cmd"][seq]
            lost = []
            for key, p in list(st["pend_cam"].items()):
                if now - p["t"] > LOST_AFTER:
                    _add(key[0], p["t"], None)
                    lost.append({"k": "lost", "flow": key[0], "n": key[1], "sent": round(p["t"], 3)})
                    del st["pend_cam"][key]
            stats = {}
            for fl in FLOWS:
                r = [x for x in st["recent"][fl] if x[0] > now - 150]
                st["recent"][fl] = r
                settle = 3.0 if fl in ("lidar", "reports") else LOST_AFTER
                win = [x for x in r if now - 60 - settle < x[0] <= now - settle]
                d = sorted(x[1] for x in r if x[1] is not None and x[0] > now - 60)
                stats[fl] = {"delivered_pct": round(100 * sum(1 for x in win if x[1] is not None) / len(win), 1) if win else None,
                             "p50": round(d[len(d) // 2], 1) if d else None,
                             "p90": round(d[int(.9 * len(d))], 1) if d else None, "n60": len(d)}
            st["react"] = [x for x in st["react"] if x[0] > now - 150]
            rc = sorted(x[1] for x in st["react"] if x[0] > now - 60)
            stats["reaction"] = {"p50": round(rc[len(rc) // 2], 1) if rc else None,
                                 "p90": round(rc[int(.9 * len(rc))], 1) if rc else None, "n60": len(rc)}
            st["clear"] = [x for x in st["clear"] if x[0] > now - 60]
            cl = min(st["clear"], key=lambda x: x[2]) if st["clear"] else None
            stats["clearance"] = {"min_m": round(cl[2], 2), "agv": cl[1] + 1, "op": cl[3]} if cl else None
            st["last_stats"] = stats
            if now - st["level_t"] > SETTLE_S:      # record even when nothing arrived: that is the result
                g, b = st["tele"].get("gnb") or {}, st["tele"].get("ble") or {}
                st["by_level"][st["level"]] = {"t": now, "stats": stats, "ran_s": round(now - st["level_t"]),
                    "nr_ul_kbps": g.get("ul_kbps"), "nr_dl_kbps": g.get("dl_kbps"), "slots_s": g.get("slots_s"),
                    "ble_dl_kbps": [((b.get(i) or {}).get("dl_kbps")) for i in ("ble1", "ble2")]}
                if int(now) % 10 == 0:
                    wr_json(BY_LEVEL, st["by_level"])
            status = {"k": "status", "now": now, "level": st["level"], "dst": st["dst"], "rtt_ms": st["rtt_ms"],
                      "agent_age": round(now - st["agent_t"], 1) if st["agent_t"] else None, "edge": st["edge"],
                      "cams": st["cams"], "stats": stats,
                      "pts": st["out"], "react": st["out_react"], "by_level": st["by_level"],
                      "level_age": round(now - st["level_t"])}
            st["out"], st["out_react"] = {f: [] for f in FLOWS}, []
        for e in lost:
            push(e)
        push(status)


def telemetry_rx():
    """gnb_bridge's dashboard telemetry (udp/8091 on this PC): slot rate, per-UE kbit/s and RSSI, the board LED."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 8091))
    last_led, last_push = None, 0.0
    while True:
        try:
            m = json.loads(s.recv(65535))
        except ValueError:
            continue
        if m.get("k") == "gnb":
            m["k"] = "tele"
            m["id"] = "gnb"
            with lock:
                st["tele"]["gnb"] = m
            push(m)
        elif m.get("k") == "led":
            led = (m["ue"], m["ul"])
            if led != last_led or time.time() - last_push > 1:
                last_led, last_push = led, time.time()
                push({"k": "led", "ue": m["ue"], "ul": m["ul"], "t": m["t"]})


def ble_rates():
    """Bluetooth throughput from the core's tun counters (tx = downlink to the UE), once a second."""
    prev = {}
    while True:
        now, out = time.time(), {"k": "tele", "id": "ble", "t": time.time()}
        for i in ("ble1", "ble2"):
            try:
                tx = int(open(f"/sys/class/net/{i}/statistics/tx_bytes").read())
                rx = int(open(f"/sys/class/net/{i}/statistics/rx_bytes").read())
            except (OSError, ValueError):
                out[i] = None
                prev.pop(i, None)
                continue
            hist = prev.setdefault(i, [])
            hist.append((now, tx, rx))
            del hist[:-6]                     # 5 s window: video is one 2 kB frame every 0.5-2 s
            if len(hist) > 1:
                (t0, tx0, rx0), dt = hist[0], now - hist[0][0]
                out[i] = {"dl_kbps": round((tx - tx0) * 8 / dt / 1e3, 2), "ul_kbps": round((rx - rx0) * 8 / dt / 1e3, 2)}
        with lock:
            st["tele"]["ble"] = out
        push(out)
        time.sleep(1.0)


def ping(cmd):
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
        i = o.find("time=")
        return float(o[i + 5:].split()[0]) if i >= 0 else None
    except Exception:   # noqa: BLE001
        return None


def health():
    """This PC's state every 5 s, and pings over each access every 10 s."""
    n = 0
    while True:
        h = F.host_health()
        if h["containers"].get("f5gc-n3iwf") == "exited":     # free5GC N3IWF panics on PDU setup for a UE it lost
            code = subprocess.run(["docker", "inspect", "-f", "{{.State.ExitCode}}", "f5gc-n3iwf"], capture_output=True,
                                  text=True).stdout.strip()
            subprocess.run(["docker", "start", "f5gc-n3iwf"], capture_output=True)
            log(f"N3IWF had exited (code {code}); started it again, the n3iwues re-register through the watchdog")
        with lock:
            st["health"]["core"] = h
        push({"k": "health", "pc": "core", **h})
        if n % 2 == 0:
            with lock:
                dst = dict(st["dst"])
            jobs = {"ble1": ["ping", "-c1", "-W2", "-s56", "10.70.0.11"], "ble2": ["ping", "-c1", "-W2", "-s56", "10.70.0.12"]}
            for key, ue in (("lidar", "nrue1"), ("robots", "nrue2"), ("cam1", "n3iwue1"), ("cam2", "n3iwue2")):
                if key in dst:
                    jobs[ue] = ["docker", "exec", "oai-ext-dn", "ping", "-c1", "-W5", dst[key]]
            res = {}
            th = [threading.Thread(target=lambda k=k, c=c: res.__setitem__(k, ping(c))) for k, c in jobs.items()]
            for t in th:
                t.start()
            for t in th:
                t.join()
            with lock:
                st["pings"] = {"t": time.time(), **res}
            push({"k": "pings", "t": time.time(), **res})
        n += 1
        time.sleep(5)


def set_level(name):
    wr_json(os.path.join(LIVE, "level"), {"level": name})
    if st.get("level") != name:
        st["level_t"] = time.time()
    st["level"] = name
    log(f"Trial {name[1:]}: LiDAR {F.LEVELS[name]['lidar_hz']} Hz, reports {F.LEVELS[name]['rep_hz']} Hz, "
        f"video {F.LEVELS[name]['cam_fps']} fps")


# --------------------------------------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/arch", "/arch/"):
            self._send(200, open(os.path.join(HERE, "arch_live.html"), "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/arch/events":
            return self.stream({"k": "hello", "now": time.time(), "health": st["health"], "tele": st["tele"],
                                "pings": st["pings"], "stats": st.get("last_stats"), "dst": st["dst"]},
                               kinds={"health", "tele", "led", "pings", "status"})
        elif self.path in ("/", "/index.html"):
            self._send(200, open(os.path.join(HERE, "factory_live2.html"), "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/events":
            with lock:
                hello = {"k": "hello", "now": time.time(), "meta": META, "level": st["level"], "log": st["log"],
                         "truth": st["truth"],
                         "recent": {fl: [(round(t, 2), None if d is None else round(d, 1)) for t, d in v]
                                    for fl, v in st["recent"].items()},
                         "react": [(round(t, 2), round(d, 1)) for t, d in st["react"]]}
            self.stream(hello)
        else:
            self._send(404, b"not found", "text/plain")

    def stream(self, hello, kinds=None):
        q = queue.Queue(maxsize=3000)
        ent = (q, kinds)
        with lock:
            clients.append(ent)
        if True:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(("data: " + json.dumps(hello) + "\n\n").encode())
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        data = b": keepalive\n\n"
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with lock:
                    if ent in clients:
                        clients.remove(ent)

    def do_POST(self):
        if self.path.startswith("/level"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                name = json.loads(self.rfile.read(n) or b"{}").get("level")
            except ValueError:
                name = None
            if name not in F.LEVELS:
                return self._send(400, json.dumps({"error": f"level must be one of {list(F.LEVELS)}"}).encode(),
                                  "application/json")
            set_level(name)
            return self._send(200, json.dumps({"level": name}).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, default=8090)
    ap.add_argument("--level", default="L1", choices=list(F.LEVELS))
    a = ap.parse_args()
    set_level(a.level)
    for fn, arg in ((clocksrv, (5299,)), (collector, (5300,)), (ticker, ()), (edge, ()), (cams, ()),
                    (telemetry_rx, ()), (health, ()), (ble_rates, ())):
        threading.Thread(target=fn, args=arg, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http), H)
    srv.daemon_threads = True
    print(f"factory live (closed loop): http://0.0.0.0:{a.http}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
