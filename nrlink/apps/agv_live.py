#!/usr/bin/env python3
"""AGV testbed, core PC: the page and the measurements (apps/agv.py has the scenario).

  core PC:  python3 apps/agv_live.py [--http 8090]          then open http://<core PC>:8090/
  UE PC:    python3 apps/agv.py ue --core 192.168.10.223

Runs the edge (agv.py edge) in a sidecar sharing oai-ext-dn's network, the clock server for the UE PC (udp/5299)
and the collector for its simulation (udp/5300). Every message goes out on an NR path and a Bluetooth path at the
same moment, so for each one it works out both one-way delays on the core clock, which copy won and by how much,
what was lost, and how long a checkpoint request took to be answered on each path. The /arch page and its health
checks come from factory_live2 unchanged. Everything pushed to the page is also written to logs/agv/trace_*.jsonl.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agv as A               # noqa: E402
import factory_live2 as L     # noqa: E402  (push, log, SSE clients, /arch, clock server, health, telemetry)

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "logs", "agv")
WINDOW = 60.0       # s of history in the live statistics
LOST_AFTER = 10.0   # s after sending, a copy that has not arrived counts as lost
SETTLE = 60.0       # s on one setting before its numbers count for the comparison table
KINDS = {"hello", "truth", "rx", "stats", "log", "sync", "health", "pings", "cfg", "bycfg"}

lock = threading.Lock()
S = {"cfg": {"level": "L1", "mode": "both", "fade": False, "tx": 0.0}, "cfg_t": time.time(), "offset": None,
     "rtt_ms": None, "truth": None, "chsum": None, "dst": {},
     "exp": {},        # (path, flow, seq) -> [tg, bytes]   copies sent and not yet seen or written off
     "got": {},        # (path, flow, seq) -> t   copies that arrived before the UE's report that they were sent
     "arr": {},        # (flow, seq) -> {path: delay_ms}    for the race between the two copies
     "rx": [],         # (t, path, flow, delay_ms, bytes)
     "lost": [],       # (t, path, flow)
     "faded": [],      # (t, path, flow)   not sent: the simulated channel was out (--fading-drop)
     "race": [],       # (t, flow, winner path, margin ms or None when the other copy was lost)
     "sync": [],       # (t, path, reaction ms, first)
     "new": [], "newlost": [], "sent": {p: [] for p in A.PATHS}}
BY_CFG_F = os.path.join(OUT, "by_cfg.json")
by_cfg = L.F.rd_json(BY_CFG_F, {})
trace = None


def cfg_key(c):
    return f"{c['mode']} · {c['level']} · {c['tx']:g} dBm" + (" · fading drops" if c.get("fade") else "")


def push(ev):
    L.push(ev)
    if trace and ev.get("k") not in ("health", "pings", "tele", "led"):
        try:
            trace.write(json.dumps(ev, separators=(",", ":")) + "\n")
        except (OSError, ValueError):
            pass


def log(line):
    L.log(line)
    if trace:
        try:
            trace.write(json.dumps({"k": "log", "t": time.time(), "line": line}) + "\n")
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------------------------- UE PC
def collector(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s.bind(("0.0.0.0", port))
    last = 0.0
    while True:
        p, addr = s.recvfrom(65535)
        try:
            m = json.loads(p)
        except ValueError:
            continue
        now = time.time()
        L.st["agent_t"] = now
        if now - last > 1 or S.pop("force_reply", False):
            last = now
            s.sendto(json.dumps(S["cfg"]).encode(), addr)
        k = m.get("k")
        if k == "clock":
            with lock:
                S["offset"], S["rtt_ms"] = m["offset_s"], m["rtt_ms"]
            L.st["offset"] = m["offset_s"]
        elif k == "log":
            log(m["line"])
        elif k == "health":
            with L.lock:
                L.st["health"]["ue"] = m
            push({"k": "health", "pc": "ue", **m})
        elif k == "dst":
            with lock:
                ch = {p: ip for p, ip in m["dst"].items() if S["dst"].get(p) != ip}
                S["dst"].update(m["dst"])
            with L.lock:     # the /arch page's pings use the factory's names
                for p, f in (("nr1", "lidar"), ("nr2", "robots"), ("ble1", "cam1"), ("ble2", "cam2")):
                    if p in m["dst"]:
                        L.st["dst"][f] = m["dst"][p]
            for p, ip in ch.items():
                log(f"{p}: UE address {ip}")
        elif k == "sent":
            with lock:
                for flow, seq, tg, mask, fmask, dmask, nb in m["rows"]:
                    for i, p in enumerate(A.PATHS):
                        if mask >> i & 1:
                            if S["got"].pop((i, flow, seq), None) is None:
                                S["exp"][(i, flow, seq)] = [tg, nb]
                            S["sent"][p].append((now, nb))
                        if fmask >> i & 1:
                            S["faded"].append((now, i, flow))
        elif k == "syncrx":
            with lock:
                for p, visit, cp, t_rx, t_req, first, t_edge in m["rows"]:
                    if t_req is not None:
                        S["sync"].append((now, A.PATHS.index(p), (t_rx - t_req) * 1000, bool(first)))
            for p, visit, cp, t_rx, t_req, first, t_edge in m["rows"]:
                if first and t_req is not None:
                    push({"k": "sync", "t": now, "p": p, "visit": visit, "cp": cp, "ms": round((t_rx - t_req) * 1000)})
        elif k == "truth":
            with lock:
                S["truth"] = m
                if "chsum" in m:
                    S["chsum"] = m["chsum"]
                if m.get("cfg") and m["cfg"] != S["cfg"]:
                    S["force_reply"] = True
            push(m)


# --------------------------------------------------------------------------------------------- edge
def edge_line(m):
    k = m.get("k")
    if k == "rx":
        with lock:
            key = (m["p"], m["f"], m["s"])                 # arrived, whether or not the clock offset is known yet
            if S["exp"].pop(key, None) is None:
                S["got"][key] = time.time()
            off = S["offset"]
            if off is None:
                return
            d = (m["tr"] - (m["tg"] + off)) * 1000
            S["rx"].append((m["tr"], m["p"], m["f"], d, m["n"]))
            S["new"].append([round(m["tr"], 2), m["p"], m["f"], round(d, 1)])
            a = S["arr"].setdefault((m["f"], m["s"]), {})
            a[m["p"]] = d
            pair = A.FLOW_PATHS[m["f"]]
            if all(p in a for p in pair):
                w = min(pair, key=lambda p: a[p])
                S["race"].append((m["tr"], m["f"], w, abs(a[pair[0]] - a[pair[1]])))
                del S["arr"][(m["f"], m["s"])]
        m["d"] = round(d, 1)
        push(m)          # every copy: the page draws where each technology last placed the AGV
    elif k == "sync":
        pass
    elif k == "edge":
        log(m.get("line", ""))


def edge():
    os.makedirs(OUT, exist_ok=True)
    L.run_forever("agv-edge", ["docker", "run", "--rm", "--name", "agv-edge", "--network", "container:oai-ext-dn",
                               "--entrypoint", "python3", "-v", f"{HERE}:/a:ro", "nrlink-run:local", "-u", "/a/agv.py",
                               "edge"], edge_line, "edge")


# --------------------------------------------------------------------------------------------- statistics
def pct(v, q):
    v = [x for x in v if x is not None]
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * len(v)))], 1)


def stats(t0, t1):
    """Everything measured between t0 and t1 (core clock), per path and per flow."""
    out = {"paths": {}, "race": {}, "sync": {}}
    for i, p in enumerate(A.PATHS):
        flow = 0 if i < 2 else 1
        rx = [r for r in S["rx"] if t0 <= r[0] < t1 and r[1] == i]
        lost = sum(1 for r in S["lost"] if t0 <= r[0] < t1 and r[1] == i)
        faded = sum(1 for r in S["faded"] if t0 <= r[0] < t1 and r[1] == i)
        d = [r[3] for r in rx]
        n = len(rx) + lost
        out["paths"][p] = {"flow": A.FLOWS[flow], "rx": len(rx), "lost": lost, "faded": faded,
                           "loss_pct": round(100 * lost / n, 1) if n else None,
                           "p50": pct(d, 0.5), "p90": pct(d, 0.9), "p99": pct(d, 0.99),
                           "kbps": round(sum(r[4] for r in rx) * 8 / max(1.0, t1 - t0) / 1000, 2)}
    for f, pair in A.FLOW_PATHS.items():
        rs = [r for r in S["race"] if t0 <= r[0] < t1 and r[1] == f]
        wins = {A.PATHS[p]: sum(1 for r in rs if r[2] == p) for p in pair}
        out["race"][A.FLOWS[f]] = {"n": len(rs), "wins": wins,
                                   "margin_p50": pct([r[3] for r in rs], 0.5), "margin_p90": pct([r[3] for r in rs], 0.9)}
    for i, p in enumerate(A.PATHS[2:], 2):
        v = [r for r in S["sync"] if t0 <= r[0] < t1 and r[1] == i]
        out["sync"][p] = {"n": len(v), "first": sum(1 for r in v if r[3]), "p50": pct([r[2] for r in v], 0.5),
                          "p90": pct([r[2] for r in v], 0.9)}
    return out


def ticker():
    while True:
        try:
            _ticker()
        except Exception as e:   # noqa: BLE001  never let one bad value stop the statistics
            import traceback
            traceback.print_exc()
            log(f"statistics: {e!r}, restarting them")
            time.sleep(2)


def _ticker():
    global by_cfg
    n = 0
    while True:
        time.sleep(1.0)
        now = time.time()
        with lock:
            off = S["offset"]
            if off is not None:      # copies not seen LOST_AFTER s after they were sent
                for key, (tg, nb) in list(S["exp"].items()):
                    if tg + off < now - LOST_AFTER:
                        del S["exp"][key]
                        S["lost"].append((now, key[0], key[1]))
                        S["newlost"].append([round(now, 2), key[0], key[1]])
                        a = S["arr"].pop((key[1], key[2]), None)
                        if a:                                   # the other copy made it: it wins outright
                            w = next(iter(a))
                            S["race"].append((now, key[1], w, None))
            for key in [k for k, t in S["got"].items() if t < now - 120]:
                del S["got"][key]
            cut = now - max(WINDOW, SETTLE) - 600
            for k in ("rx", "lost", "faded", "race", "sync"):
                S[k] = [r for r in S[k] if r[0] > cut]
            for k in list(S["arr"]):
                if len(S["arr"]) < 4000:
                    break
                del S["arr"][k]
            n += 1
            if n % 2:
                continue
            st = stats(now - WINDOW, now)
            ev = {"k": "stats", "t": now, "win": WINDOW, "cfg": S["cfg"], "cfg_age": round(now - S["cfg_t"]),
                  "offset_ms": None if off is None else round(off * 1000, 1), "clock_rtt_ms": S["rtt_ms"],
                  "chsum": S["chsum"], "pts": S["new"], "lostpts": S["newlost"], **st}
            S["new"], S["newlost"] = [], []
            age = now - S["cfg_t"]
            if age >= SETTLE + 30:   # settled: the numbers since SETTLE s after the change count for this setting
                key = cfg_key(S["cfg"])
                by_cfg[key] = {"cfg": dict(S["cfg"]), "t": now, "secs": round(age - SETTLE),
                               "chsum": S["chsum"], **stats(S["cfg_t"] + SETTLE, now)}
                ev["bycfg"] = by_cfg
                if n % 20 == 0:
                    L.wr_json(BY_CFG_F, by_cfg)
        push(ev)


def set_cfg(**kw):
    with lock:
        ch = {k: v for k, v in kw.items() if v is not None and S["cfg"].get(k) != v}
        if not ch:
            return S["cfg"]
        S["cfg"].update(ch)
        S["cfg_t"] = time.time()
        S["force_reply"] = True
        c = dict(S["cfg"])
    log("setting: " + ", ".join(f"{k} {v}" for k, v in ch.items()) + f" -> {cfg_key(c)}")
    push({"k": "cfg", "cfg": c, "t": time.time()})
    return c


META = {"w": A.W, "h": A.H, "racks": A.RACKS, "route": A.ROUTE, "checkpoints": A.CHECKPOINTS, "pallets": A.PALLETS,
        "paths": A.PATHS, "flows": A.FLOWS, "levels": A.LEVELS, "modes": list(A.MODES), "radio": A.RADIO,
        "tx_steps": A.TX_STEPS, "lidar_n": A.LIDAR_N, "cam_fov_deg": 90, "cam_range": A.CAM_RANGE,
        "window": WINDOW, "settle": SETTLE, "lost_after": LOST_AFTER}


class H(L.H):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, open(os.path.join(HERE, "agv_live.html"), "rb").read(), "text/html; charset=utf-8")
        if self.path == "/events":
            with lock:
                hello = {"k": "hello", "now": time.time(), "meta": META, "cfg": S["cfg"], "truth": S["truth"],
                         "log": L.st["log"], "bycfg": by_cfg, "live": True,
                         "offset_ms": None if S["offset"] is None else round(S["offset"] * 1000, 1),
                         "pts": [[round(r[0], 2), r[1], r[2], round(r[3], 1)] for r in S["rx"] if r[0] > time.time() - WINDOW]}
            return self.stream(hello, kinds=KINDS)
        if self.path.startswith("/window"):   # /window?secs=150: the statistics of the last secs seconds, any setting
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            secs = float((q.get("secs") or ["150"])[0])
            now = time.time()
            with lock:
                body = {"t": now, "secs": secs, "cfg": S["cfg"], "chsum": S["chsum"], **stats(now - secs, now)}
            return self._send(200, json.dumps(body).encode(), "application/json")
        if self.path == "/by_cfg.json":
            return self._send(200, json.dumps(by_cfg, indent=1).encode(), "application/json")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/cfg") or self.path.startswith("/level"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                b = {}
            err = None
            if b.get("level") is not None and b["level"] not in A.LEVELS:
                err = f"level must be one of {list(A.LEVELS)}"
            if b.get("mode") is not None and b["mode"] not in A.MODES:
                err = f"mode must be one of {list(A.MODES)}"
            if b.get("tx") is not None and b["tx"] not in A.TX_STEPS:
                err = f"tx must be one of {A.TX_STEPS}"
            if err:
                return self._send(400, json.dumps({"error": err}).encode(), "application/json")
            c = set_cfg(level=b.get("level"), mode=b.get("mode"), tx=b.get("tx"),
                        fade=None if b.get("fade") is None else bool(b["fade"]))
            return self._send(200, json.dumps(c).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


def main():
    global trace
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, default=8090)
    ap.add_argument("--level", default="L1", choices=list(A.LEVELS))
    ap.add_argument("--mode", default="both", choices=list(A.MODES))
    ap.add_argument("--tx-dbm", type=float, default=0.0, choices=A.TX_STEPS)
    ap.add_argument("--fading-drop", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    trace = open(os.path.join(OUT, time.strftime("trace_%Y%m%d-%H%M%S.jsonl")), "a", buffering=1)
    S["cfg"] = {"level": a.level, "mode": a.mode, "fade": a.fading_drop, "tx": a.tx_dbm}
    trace.write(json.dumps({"k": "hello", "now": time.time(), "meta": META, "cfg": S["cfg"]}) + "\n")
    for fn, arg in ((L.clocksrv, (5299,)), (collector, (5300,)), (ticker, ()), (edge, ()), (L.telemetry_rx, ()),
                    (L.health, ()), (L.ble_rates, ())):
        threading.Thread(target=fn, args=arg, daemon=True).start()
    srv = L.ThreadingHTTPServer(("0.0.0.0", a.http), H)
    srv.daemon_threads = True
    print(f"AGV testbed: http://0.0.0.0:{a.http}/  (/arch for the architecture)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
