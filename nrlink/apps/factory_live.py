#!/usr/bin/env python3
"""Live factory downlink: keeps the factory_dl.py traffic running in a loop and serves a page that shows it as it
arrives at the UEs.

  core PC:  python3 apps/factory_live.py [--http 8090]      then open http://<core PC>:8090/
  UE PC:    python3 apps/factory_dl.py agent --core 192.168.10.223

What it does:
  - runs the sender in a sidecar sharing oai-ext-dn's network (factory_dl.py send --loop), 60 s scenes back to back,
    at the level the page picks (default L1);
  - answers the UE PC agent's clock probes (udp/5299) and takes its reports (udp/5300): every packet that arrived,
    the UE addresses (handed on to the sender), the clock offset and the watchdog log;
  - joins packets into frames, works out each frame's one-way delay on the core PC's clock, calls a frame lost when
    it has not arrived whole 30 s after it was due, and pushes it all to the page as server-sent events.
"""
import argparse
import json
import os
import queue
import socket
import statistics
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCEN = os.path.join(ROOT, "logs", "factory", "scen")
LIVE = os.path.join(ROOT, "logs", "factory", "live_core")
FLOWS = ["lidar", "robots", "cam1", "cam2"]
ALLOWED = ["L1", "L2", "L3"]
LOST_AFTER = 30.0
SECS = 60.0

lock = threading.Lock()
clients = []
st = {"loops": [], "offset": None, "rtt_ms": None, "dst": {}, "level": "L1", "frames": {}, "done": {},
      "recent": {f: [] for f in FLOWS}, "log": [], "agent_t": 0.0, "sender": "starting"}
meta = json.load(open(os.path.join(SCEN, "meta.json")))
sched = {}   # level -> flow -> [(n, ts_scene, b64)]
for L in meta["levels"]:
    ev = json.load(open(os.path.join(SCEN, f"{L['name']}.json")))
    sched[L["name"]] = {f: [] for f in FLOWS}
    for t, fl, n, b64 in ev:
        sched[L["name"]][fl].append((n, t, b64))
by_n = {lv: {f: {n: (t, b) for n, t, b in v} for f, v in d.items()} for lv, d in sched.items()}


def push(ev):
    data = ("data: " + json.dumps(ev, separators=(",", ":")) + "\n\n").encode()
    with lock:
        for q in list(clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def log(line):
    e = {"t": time.time(), "line": line}
    with lock:
        st["log"] = (st["log"] + [e])[-30:]
    push({"k": "log", **e})


# --------------------------------------------------------------------------------------------- clock + agent
def clocksrv(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    while True:
        p, addr = s.recvfrom(64)
        s.sendto(p[:8] + struct.pack("<d", time.time()), addr)


def loop_of(ts):
    """The sender loop a packet stamped at ts (core clock) belongs to."""
    best = None
    for lp in st["loops"]:
        if lp["t0"] - 0.5 <= ts:
            best = lp
    return best


def collector(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s.bind(("0.0.0.0", port))
    while True:
        p, _ = s.recvfrom(65535)
        try:
            m = json.loads(p)
        except ValueError:
            continue
        st["agent_t"] = time.time()
        k = m.get("k")
        if k == "clock":
            with lock:
                st["offset"], st["rtt_ms"] = m["offset_s"], m["rtt_ms"]
        elif k == "dst":
            with lock:
                changed = {f: ip for f, ip in m["dst"].items() if st["dst"].get(f) != ip}
                st["dst"].update(m["dst"])
            os.makedirs(LIVE, exist_ok=True)
            with open(os.path.join(LIVE, "dst.json.tmp"), "w") as f:
                json.dump(st["dst"], f)
            os.replace(os.path.join(LIVE, "dst.json.tmp"), os.path.join(LIVE, "dst.json"))
            for f, ip in changed.items():
                log(f"{f}: UE address now {ip}")
        elif k == "log":
            log(m["line"])
        elif k == "rx" and st["offset"] is not None:
            for row in m["rows"]:
                rx_row(row)


def rx_row(row):
    fl, lvl, n, _sq, _k, _nk, ts, tr, _ln, ok = row
    with lock:
        lp = loop_of(ts)
        if lp is None:
            return
        key = (fl, lp["loop"], n)
        if key in st["done"]:
            return
        fr = st["frames"].setdefault(key, {"first": ts})
        fr["first"] = min(fr["first"], ts)
        if not ok:
            return
        arr = tr + st["offset"]
        delay = (arr - fr["first"]) * 1000
        del st["frames"][key]
        st["done"][key] = True
        t_scene, b64 = by_n[lp["level"]][fl].get(n, (None, None))
        rec = (lp["t0"] + (t_scene or 0), delay)
        st["recent"][fl].append(rec)
    ev = {"k": "frame", "flow": fl, "loop": lp["loop"], "level": lp["level"], "n": n, "ts": t_scene,
          "sent": round(fr["first"], 3), "arr": round(arr, 3), "delay": round(delay, 1)}
    if fl != "robots" and b64:
        ev["b64"] = b64
    push(ev)


def janitor():
    """Lost frames, per-flow statistics over the last 60 s, and a status line, once a second."""
    while True:
        time.sleep(1.0)
        now = time.time()
        lost = []
        with lock:
            for lp in st["loops"][-3:]:
                if lp.get("checked_to", -1) >= SECS:
                    continue
                upto = now - lp["t0"] - LOST_AFTER
                for fl in FLOWS:
                    for n, t, _ in sched[lp["level"]][fl]:
                        if lp.get("checked_to", -1) < t <= upto and (fl, lp["loop"], n) not in st["done"]:
                            lost.append({"k": "lost", "flow": fl, "loop": lp["loop"], "level": lp["level"], "n": n,
                                         "ts": t, "sent": round(lp["t0"] + t, 3)})
                            st["recent"][fl].append((lp["t0"] + t, None))
                            st["done"][(fl, lp["loop"], n)] = True
                lp["checked_to"] = max(lp.get("checked_to", -1), upto)
            # forget old bookkeeping
            keep = {lp["loop"] for lp in st["loops"][-3:]}
            st["done"] = {k: v for k, v in st["done"].items() if k[1] in keep}
            st["frames"] = {k: v for k, v in st["frames"].items() if k[1] in keep}
            stats = {}
            for fl in FLOWS:
                r = [x for x in st["recent"][fl] if x[0] > now - 120]
                st["recent"][fl] = r
                win = [x for x in r if now - 90 < x[0] <= now - LOST_AFTER]   # settled: arrived or called lost
                d = sorted(x[1] for x in r if x[1] is not None and x[0] > now - 60)
                stats[fl] = {"delivered_pct": round(100 * sum(1 for x in win if x[1] is not None) / len(win), 1) if win else None,
                             "p50": round(d[len(d) // 2], 1) if d else None,
                             "p90": round(d[int(.9 * len(d))], 1) if d else None,
                             "n60": len(d)}
            status = {"k": "status", "now": now, "level": st["level"], "dst": st["dst"], "rtt_ms": st["rtt_ms"],
                      "agent_age": round(now - st["agent_t"], 1) if st["agent_t"] else None, "sender": st["sender"],
                      "loop": st["loops"][-1] if st["loops"] else None, "stats": stats}
        for e in lost:
            push(e)
        push(status)


# --------------------------------------------------------------------------------------------- sender
def sender():
    os.makedirs(LIVE, exist_ok=True)
    while True:
        with lock:
            d = dict(st["dst"])
        if len(d) < 1:
            st["sender"] = "waiting for UE addresses from the UE PC agent"
            time.sleep(2)
            continue
        subprocess.run(["docker", "rm", "-f", "fdl-live-tx"], capture_output=True)
        cmd = ["docker", "run", "--rm", "--name", "fdl-live-tx", "--network", "container:oai-ext-dn",
               "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "-v", f"{SCEN}:/s:ro", "-v", f"{LIVE}:/o",
               "nrlink-run:local", "-u", "/a/factory_dl.py", "send", "--scen", "/s", "--loop", "--levels", "L1",
               "--level-file", "/o/level", "--dst-file", "/o/dst.json", "--secs", str(SECS), "--dst"] + \
              [f"{k}={v}" for k, v in d.items()]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        st["sender"] = "running"
        log("sender started in ext-dn")
        for line in p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                log("sender: " + line.strip()[:200])
                continue
            if "t0" in m:
                with lock:
                    lp = {"loop": len(st["loops"]), "level": m["level"], "t0": m["t0"]}
                    st["loops"] = (st["loops"] + [lp])[-6:]
                push({"k": "loop", **lp})
        p.wait()
        st["sender"] = f"exited ({p.returncode}), restarting"
        log(f"sender exited with {p.returncode}; restarting in 5 s")
        time.sleep(5)


def set_level(name):
    os.makedirs(LIVE, exist_ok=True)
    with open(os.path.join(LIVE, "level"), "w") as f:
        f.write(name)
    st["level"] = name
    log(f"level {name} from the next loop")


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
        if self.path in ("/", "/index.html"):
            self._send(200, open(os.path.join(HERE, "factory_live.html"), "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/events":
            q = queue.Queue(maxsize=2000)
            with lock:
                hello = {"k": "hello", "now": time.time(), "meta": meta["floor"], "levels": meta["levels"],
                         "allowed": ALLOWED, "level": st["level"], "loops": st["loops"], "log": st["log"]}
                clients.append(q)
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
                    if q in clients:
                        clients.remove(q)
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.startswith("/level"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                name = json.loads(self.rfile.read(n) or b"{}").get("level")
            except ValueError:
                name = None
            if name not in ALLOWED:
                return self._send(400, json.dumps({"error": f"level must be one of {ALLOWED}"}).encode(), "application/json")
            set_level(name)
            return self._send(200, json.dumps({"level": name}).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, default=8090)
    ap.add_argument("--level", default="L1", choices=ALLOWED)
    a = ap.parse_args()
    set_level(a.level)
    for fn, arg in ((clocksrv, 5299), (collector, 5300)):
        threading.Thread(target=fn, args=(arg,), daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    threading.Thread(target=sender, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http), H)
    srv.daemon_threads = True
    print(f"factory live: http://0.0.0.0:{a.http}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
