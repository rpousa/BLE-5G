#!/usr/bin/env python3
"""AGV relay chain, live: the core PC's page for the chain running on the boards (apps/agv_chain.py ue --live).

  core PC:  python3 apps/agv_chain_live.py [--http 8090] [--udp 5301]      then open http://<core PC>:8090/
  UE PC:    agv_chain.py ue ... --live <core PC>:5301

The controller on the UE PC sends every event it logs (positions, roles, hops, messages sent, board LEDs, attaches,
stats) here by UDP. This server also runs the edge (agv_chain.py edge, in a sidecar sharing oai-ext-dn's network) and
the clock for the one-way delays (udp/5298) when they are not already running, joins what the edge received with what
the AGVs sent into one-way delays, and pushes it all to the page (apps/agv_chain_live.html) over server-sent events.
Times stay on the UE PC's clock, as in the recorded runs; the edge's are moved onto it with the controller's offsets.
"""
import argparse
import collections
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agv_chain as AC        # noqa: E402
import factory_live2 as FL     # noqa: E402  (/arch: the architecture page, its health, telemetry and pings)

ROOT = os.path.dirname(HERE)
RUNS_DIRS = [HERE, os.path.expanduser("~/BLE-5G/docs/pages")]      # the recorded runs the page's replay loads
PAGE = "agv_chain_live.html"
KEEP = 200.0          # s of history sent to a page that connects
LOST_AFTER = 10.0     # s after sending, a message the edge has not received counts as lost
EDGE_C = "agvc-edge"

lock = threading.Lock()
hist = collections.deque()            # events for a new page: (t, event)
pend = []                             # events not yet pushed
clients = []                          # queues of connected pages
S = dict(via={}, start=None, off=0.0, gen={}, early={}, cmd={}, cmdrx={}, chain=[0, 1, 2], leds=[None] * 3, last_rx=0.0,
         stats=None, edge_ok=False, ctl_addr=None)


def emit(ev):
    """Caller holds lock."""
    hist.append(ev)
    pend.append(ev)


def trim(now_ue):
    while hist and hist[0].get("t", now_ue) < now_ue - KEEP and hist[0]["k"] not in ("start",):
        hist.popleft()


def role_of(agv):
    return S["chain"].index(agv)


# ------------------------------------------------------------------------------------------------ controller events
def on_ctl(m):
    k, t = m["k"], m["t"]
    if k == "start":
        S.update(start=m, gen={}, early={}, cmd={}, cmdrx={}, via={}, chain=list(m["chain"]), leds=[None] * 3)
        hist.clear()
        emit(m)
        return
    if S["start"] is None:            # the controller started before this server: begin from here
        S["start"] = dict(k="start", t=t, chain=m.get("chain", S["chain"]), boards=[], level="?", late=1)
        emit(S["start"])
    if k == "clock":
        S["off"] = m["off"]
    elif k == "pos":
        S["chain"] = list(m["chain"])
        emit(dict(k="pos", t=t, a=m["a"], chain=m["chain"], **({"q": m["q"]} if "q" in m else {})))
        trim(t)
    elif k == "chain":
        S["chain"] = list(m["new"])
        emit(dict(k="chain", t=t, new=m["new"]))
    elif k == "hop":
        emit(dict(k="hop", t=t, a=m["a"], b=m["b"], f=m["f"]))
    elif k == "gen":
        key = (m["agv"], m["f"], m["s"])
        e = S["early"].pop(key, None)
        if e is not None and abs(e["tg"] - t) < 0.01:
            emit(dict(k="msg", t=t, agv=m["agv"], f=m["f"], role=m["role"], d=round((e["tr"] - (e["tg"] + S["off"])) * 1000, 1),
                      via=S["via"].pop(key, -1)))
        else:
            S["gen"][key] = (t, m["role"])
    elif k == "uu":
        S["via"][(m["agv"], m["f"], m["s"])] = m["via"]           # the AGV whose UE sent it up
    elif k == "cmdrx":
        S["cmdrx"][(m["agv"], m["s"])] = m["tr"]
    elif k == "led":
        S["leds"][m["agv"]] = m
        emit(m)
    elif k in ("cov", "attach", "egress", "pc5", "pc5up", "start", "serve", "rlf", "hold", "pc5conn", "rur", "disc"):
        emit(m)
    elif k == "stats":
        S["stats"] = m
        emit(dict(k="stats", t=t, line=m.get("line", ""), windows=m.get("windows"), dt=m.get("dt")))
    elif k == "drop":
        pass                            # a drop shows as a lost message after LOST_AFTER


def udp_rx(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s.bind(("0.0.0.0", port))
    while True:
        p, addr = s.recvfrom(65535)
        try:
            m = json.loads(p)
        except ValueError:
            continue
        with lock:
            S["last_rx"], S["ctl_addr"] = time.time(), addr[0]
            try:
                on_ctl(m)
            except (KeyError, ValueError, TypeError, IndexError) as e:
                print("bad event", m.get("k"), repr(e), flush=True)


# ------------------------------------------------------------------------------------------------ edge (core clock)
def on_edge(m):
    k = m.get("k")
    if k == "rx":
        key = (m["agv"], m["f"], m["s"])
        g = S["gen"].get(key)
        if g is None:
            S["early"][key] = m         # the controller's event is still on its way
            return
        if abs(m["tg"] - g[0]) > 0.01:
            return                      # an older run's message with the same number
        del S["gen"][key]
        emit(dict(k="msg", t=g[0], agv=key[0], f=key[1], role=g[1], d=round((m["tr"] - (m["tg"] + S["off"])) * 1000, 1),
                  via=S["via"].pop(key, -1)))
    elif k == "cmd":
        te_ue = m["te"] - S["off"]
        S["cmd"][(m["agv"], m["s"])] = (te_ue, role_of(m["agv"]))


def edge_reader():
    while True:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", EDGE_C], capture_output=True, text=True)
        if r.stdout.strip() != "true":
            subprocess.run(["docker", "rm", "-f", EDGE_C], capture_output=True)
            r = subprocess.run(["docker", "run", "-d", "--name", EDGE_C, "--network", "container:oai-ext-dn",
                                "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "nrlink-run:local", "-u", "/a/agv_chain.py",
                                "edge"], capture_output=True, text=True)
            print(time.strftime("%T"), "edge started" if r.returncode == 0 else f"edge did not start: {r.stderr.strip()}",
                  flush=True)
            if r.returncode:
                time.sleep(10)
                continue
        p = subprocess.Popen(["docker", "logs", "-f", "--since", "1s", EDGE_C], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        S["edge_ok"] = True
        for ln in p.stdout:
            if not ln.startswith("{"):
                continue
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            with lock:
                on_edge(m)
        S["edge_ok"] = False
        p.wait()
        time.sleep(2)


def clock_server():
    try:
        AC.clock(None)
    except OSError:
        print("clock: udp/%d already served (agv_chain.py clock)" % AC.CLOCK_PORT, flush=True)


# ------------------------------------------------------------------------------------------------ joining, pushing
def ticker():
    while True:
        time.sleep(0.25)
        now = time.time()
        with lock:
            now_ue = now - S["off"]
            for key, (t, role) in list(S["gen"].items()):
                if now_ue - t > LOST_AFTER:
                    del S["gen"][key]
                    emit(dict(k="msg", t=t, agv=key[0], f=key[1], role=role, d=None, via=S["via"].pop(key, -1)))
            for key, (te, role) in list(S["cmd"].items()):
                tr = S["cmdrx"].pop(key, None)
                if tr is not None:
                    del S["cmd"][key]
                    emit(dict(k="msg", t=te, agv=key[0], f=2, role=role, d=round((tr - te) * 1000, 1)))
                elif now_ue - te > LOST_AFTER:
                    del S["cmd"][key]
                    emit(dict(k="msg", t=te, agv=key[0], f=2, role=role, d=None))
            for key, m in list(S["early"].items()):
                if now - m["tr"] > LOST_AFTER:
                    del S["early"][key]
            if len(S["via"]) > 20000:
                S["via"].clear()
            for key, tr in list(S["cmdrx"].items()):
                if now_ue - tr > LOST_AFTER:
                    del S["cmdrx"][key]
            st = dict(k="state", now_ue=round(now_ue, 3), ctl=round(now - S["last_rx"], 1) if S["last_rx"] else None,
                      edge=S["edge_ok"], off=S["off"])
            batch, pend[:] = pend[:] + [st], []
            cl = list(clients)
        data = json.dumps(dict(ev=batch), separators=(",", ":"))
        for q in cl:
            if len(q) < 400:
                q.append(data)


def snap_events():
    """History for a page that connects, with each board's current LED right after the start (they may be older)."""
    h = list(hist)
    k = 1 if h and h[0]["k"] == "start" else 0
    return h[:k] + [e for e in S["leds"] if e and e not in h] + h[k:]


class H(FL.H):
    def log_message(self, *a):
        pass

    def send_file(self, path, ctype):
        try:
            body = open(path, "rb").read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(HERE, PAGE), "text/html; charset=utf-8")
        if path in ("/arch", "/arch/", "/arch/events"):
            return FL.H.do_GET(self)
        if path.startswith("/agv-") and path.endswith("-runs.json") and "/" not in path[1:]:
            for d in RUNS_DIRS:
                p = os.path.join(d, path[1:])
                if os.path.exists(p):
                    return self.send_file(p, "application/json")
            return self.send_error(404)
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = collections.deque()
            with lock:
                snap = json.dumps(dict(snap=1, ev=snap_events() + [dict(k="state", now_ue=round(time.time() - S["off"], 3),
                                                                     ctl=None, edge=S["edge_ok"], off=S["off"])]),
                                  separators=(",", ":"))
                clients.append(q)
            try:
                self.wfile.write(f"data: {snap}\n\n".encode())
                self.wfile.flush()
                while True:
                    while q:
                        self.wfile.write(f"data: {q.popleft()}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with lock:
                    clients.remove(q)
            return
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, default=8090)
    ap.add_argument("--udp", type=int, default=5301)
    ap.add_argument("--page", default="agv_chain_live.html", help="the page in apps/: agv_chain_live.html or agv_dual_live.html")
    a = ap.parse_args()
    global PAGE
    PAGE = a.page
    for f in (udp_rx, ):
        threading.Thread(target=f, args=(a.udp,), daemon=True).start()
    for f in (edge_reader, clock_server, ticker, FL.telemetry_rx, FL.health, FL.ble_rates):
        threading.Thread(target=f, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http), H)
    srv.daemon_threads = True
    print(f"AGV relay chain live: http://0.0.0.0:{a.http}/  (controller events on udp/{a.udp})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
