#!/usr/bin/env python3
"""nrlink live dashboard: starts the demo apps over the NR link and serves the dashboard.

  apps   eMBB LiDAR nrUE1 -> ext-dn, URLLC AGV nrUE2 <-> controller, mMTC sensor nrUE3 -> collector
  http   GET /            dashboard.html
         GET /events      Server-Sent Events: app events + bridge telemetry (udp/8091)
         POST /api/cmd    {"k": "obstacles"|"estop"|"lidar"|"interval"|"heat", ...}
  --record N   also write the first N seconds of events to dashboard/recording.json (artifact replay)
"""
import argparse
import json
import os
import queue
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
UE_CONTAINERS = {"lidar": "oai-nrue1", "agv": "oai-nrue2", "sensor": "oai-nrue3"}
SIDE = "nrlink-dn-apps"


def dk(args):
    return ["sg", "docker", "-c", " ".join(args)]


class Hub:
    """Fan-out of events to the connected browsers, plus the state a newly connected browser needs."""

    def __init__(self, record_s):
        self.clients = []
        self.lock = threading.Lock()
        self.snapshot = {}  # latest event per key (except frames, which come at their own pace)
        self.sensor_hist = []
        self.events_log = []
        self.record_s, self.rec, self.rec_t0, self.rec_last_frame = record_s, [], None, 0.0
        self.vacc = {"n": 0, "bytes": 0, "lat": 0.0}
        threading.Thread(target=self.scan_ticker, daemon=True).start()

    def scan_ticker(self):
        """Received LiDAR rate from every scan (the recording keeps only ~2 per second)."""
        t = time.time()
        while True:
            time.sleep(1.0)
            now = time.time()
            with self.lock:
                a, self.vacc = self.vacc, {"n": 0, "bytes": 0, "lat": 0.0}
            dt = now - t
            t = now
            self.publish({"k": "lidar_stats", "t": now, "sps": round(a["n"] / dt, 2),
                          "kbps": round(a["bytes"] * 8 / dt / 1e3, 1),
                          "lat_ms": round(a["lat"] / a["n"], 1) if a["n"] else None,
                          "avg_bytes": round(a["bytes"] / a["n"]) if a["n"] else None})

    def publish(self, ev):
        k = ev.get("k")
        with self.lock:
            if k == "scan":
                self.vacc["n"] += 1
                self.vacc["bytes"] += ev.get("bytes", 0)
                self.vacc["lat"] += ev.get("lat_ms", 0)
            if k == "sensor_rx":
                self.sensor_hist = (self.sensor_hist + [ev])[-60:]
            elif k == "agv_event" or k == "sensor_event":
                self.events_log = (self.events_log + [ev])[-30:]
            if k != "scan":
                self.snapshot[k if k not in ("ue",) else f"ue{ev.get('id')}"] = ev
            self.record(ev)
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.clients.remove(q)

    def record(self, ev):
        if not self.record_s:
            return
        now = ev.get("t", time.time())
        if self.rec_t0 is None:
            if ev.get("k") != "scan":  # start once scans are flowing so the replay opens with a point cloud
                return
            self.rec_t0 = now
        if now - self.rec_t0 > self.record_s:
            path = os.path.join(HERE, "dashboard", "recording.json")
            with open(path, "w") as f:
                json.dump({"recorded": time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(self.rec_t0)),
                           "recorded_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.rec_t0)),
                           "duration_s": self.record_s, "events": self.rec}, f, separators=(",", ":"))
            print(f"recording written: {path} ({len(self.rec)} events)", flush=True)
            self.record_s = 0
            return
        if ev.get("k") == "scan":
            if now - self.rec_last_frame < 0.45:  # ~2 scans/s is enough for the replay
                return
            self.rec_last_frame = now
        self.rec.append(dict(ev, t=round(now - self.rec_t0, 3)))

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self.lock:
            hello = {"k": "hello", "t": time.time(), "live": True, "snapshot": list(self.snapshot.values()),
                     "sensor_hist": self.sensor_hist, "events": self.events_log}
            self.clients.append(q)
        q.put(hello)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


class Apps:
    def __init__(self, hub):
        self.hub, self.procs = hub, {}

    def start(self):
        subprocess.run(dk(["docker", "rm", "-f", SIDE]), capture_output=True)
        for mode, c in UE_CONTAINERS.items():
            subprocess.run(dk(["docker", "cp", f"{HERE}/apps/ue_app.py", f"{c}:/tmp/ue_app.py"]), check=True)
            subprocess.run(dk(["docker", "exec", c, "pkill", "-f", "ue_app.py"]), capture_output=True)
        self.spawn("dn", ["docker", "run", "--rm", "-i", "--name", SIDE, "--network", "container:oai-ext-dn",
                          "-v", f"{HERE}/apps:/apps:ro",
                          "nrlink-run:local", "python3", "-u", "/apps/dn_app.py"])
        time.sleep(2)
        for mode, c in UE_CONTAINERS.items():
            self.spawn(mode, ["docker", "exec", "-i", c, "python3", "-u", "/tmp/ue_app.py", mode])

    def spawn(self, name, args):
        p = subprocess.Popen(dk(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.procs[name] = p

        def reader():
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                ev["src"] = name
                self.hub.publish(ev)
            self.hub.publish({"k": "app_down", "t": time.time(), "src": name})
        threading.Thread(target=reader, daemon=True).start()

    def send(self, name, cmd):
        p = self.procs.get(name)
        if p and p.poll() is None:
            p.stdin.write(json.dumps(cmd) + "\n")
            p.stdin.flush()

    def stop(self):
        for p in self.procs.values():
            p.terminate()
        subprocess.run(dk(["docker", "rm", "-f", SIDE]), capture_output=True)
        for c in UE_CONTAINERS.values():
            subprocess.run(dk(["docker", "exec", c, "pkill", "-f", "ue_app.py"]), capture_output=True)


def telemetry_listener(hub):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 8091))
    while True:
        d, _ = s.recvfrom(65535)
        try:
            ev = json.loads(d)
        except ValueError:
            continue
        ev["src"] = "bridge"
        hub.publish(ev)


def make_handler(hub, apps):
    page = os.path.join(HERE, "dashboard", "dashboard.html")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def send_json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html", "/dashboard.html"):
                # same document skeleton the artifact host wraps around the page
                body = (b"<!doctype html><html><head><meta charset=utf-8><meta name=viewport "
                        b"content=\"width=device-width,initial-scale=1,viewport-fit=cover\"><style>"
                        b":root{color-scheme:light;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}"
                        b"body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}"
                        b"</style></head><body>" + open(page, "rb").read() + b"</body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/hello":
                self.send_json(200, {"live": True})
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = hub.subscribe()
                try:
                    while True:
                        try:
                            ev = q.get(timeout=15)
                            self.wfile.write(b"data: " + json.dumps(ev, separators=(",", ":")).encode() + b"\n\n")
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    hub.unsubscribe(q)
            else:
                self.send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/cmd":
                return self.send_json(404, {"error": "not found"})
            try:
                cmd = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except ValueError:
                return self.send_json(400, {"error": "body must be JSON"})
            k = cmd.get("k")
            if k in ("obstacles", "estop", "interval"):
                apps.send("dn", cmd)
            elif k == "lidar":
                apps.send("lidar", cmd)
            elif k == "heat":
                apps.send("sensor", cmd)
            else:
                return self.send_json(400, {"error": f"unknown command {k!r}"})
            self.send_json(200, {"ok": True})

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--record", type=int, default=0, help="seconds of events to save for the artifact replay")
    args = ap.parse_args()
    hub = Hub(args.record)
    threading.Thread(target=telemetry_listener, args=(hub,), daemon=True).start()
    apps = Apps(hub)
    apps.start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(hub, apps))
    srv.daemon_threads = True
    print(f"dashboard on http://0.0.0.0:{args.port}/", flush=True)
    try:
        srv.serve_forever()
    finally:
        apps.stop()


if __name__ == "__main__":
    main()
