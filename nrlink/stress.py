#!/usr/bin/env python3
"""Stress test of the four UEs of the split setup, all with the same tools (iperf3 and ping from the nrUE image).

  core PC:  ./stress.py servers          iperf3 3.16 servers (nrUE image) in oai-ext-dn's netns, ports 5221-5224
  UE PC:    ./stress.py run [--quick]    tests below, results in logs/stress/<time>.json (+ latest.json)
            ./stress.py stop             (core PC) stop the servers again

nrUE1/2 run iperf3 in their own container (oai-nrue<k>, bound to oaitun_ue1). The n3iwue image has only ping, so
n3iwue1/2 get a throwaway sidecar from the nrUE image in their network namespace (docker run --network
container:nrlink-n3iwue<k>), bound to the PDU tunnel gretun-id-2-1. Same binaries, same server, same options.

Per UE alone: idle RTT; TCP up and down (with RTT under load); UDP up and down at a rate sweep (loss, jitter).
Then all four at once: TCP up, then TCP down, with RTT under load.
Only round trips are measured: the two PCs' clocks are not synchronised, so one-way delays would be meaningless.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

DN = "192.168.70.132"
NR_IMAGE = "nrlink-run:local"
UES = [  # name, class, kind, container, interface, server port
    ("nrUE1", "eMBB", "nr", "oai-nrue1", "oaitun_ue1", 5221),
    ("nrUE2", "URLLC", "nr", "oai-nrue2", "oaitun_ue1", 5222),
    ("n3iwue1", "non-3GPP", "n3", "nrlink-n3iwue1", "gretun-id-2-1", 5223),
    ("n3iwue2", "non-3GPP", "n3", "nrlink-n3iwue2", "gretun-id-2-1", 5224),
]
RATES = {"nr": [25, 50, 100, 200, 400],   # kbit/s, UDP sweep
         "n3": [10, 20, 30, 40, 50]}       # Bluetooth links are shaped to 40 kbit/s: sweeping far past that hung the
                                           # hci_bb boards (115200 baud UART, no flow control), so stop just above it
UDP_LEN = 500                     # bytes: fits every path (gretun MTU 1438)
WIN = "32K"                       # TCP window: about the nrUE path's bandwidth x delay
HERE = os.path.dirname(os.path.abspath(__file__))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return -1, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"


def ue_addr(c, dev):
    _, o, _ = run(["docker", "exec", c, "ip", "-4", "-o", "addr", "show", "dev", dev], 15)
    m = re.search(r"inet (12\.1\.1\.\d+)", o)
    return m.group(1) if m else None


def ping(ue, n, interval=0.5):
    name, _, _, c, dev, _ = ue
    rc, o, e = run(["docker", "exec", c, "ping", "-c", str(n), "-i", str(interval), "-W", "3", "-I", dev, DN], n * interval + 30)
    res = {"sent": n}
    m = re.search(r"(\d+) packets transmitted, (\d+) received", o)
    if m:
        res["recv"] = int(m.group(2))
        res["loss_pct"] = round(100 * (1 - int(m.group(2)) / max(1, int(m.group(1)))), 1)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", o)
    if m:
        res.update(min=float(m.group(1)), avg=float(m.group(2)), max=float(m.group(3)), mdev=float(m.group(4)))
    if "recv" not in res:
        res["error"] = (e or o).strip()[-200:] or f"ping exit {rc}"
    return res


def iperf(ue, secs, reverse=False, udp_kbps=None):
    name, _, kind, c, dev, port = ue
    addr = ue_addr(c, dev)
    if not addr:
        return {"error": f"{dev} has no PDU address"}
    # timeout inside the container: a client stuck on a stalled path must not outlive its test (and a killed
    # `docker run` would leave its container running)
    tmo = ["timeout", "-k", "5", str(secs + 60), "iperf3"]
    pre = (["docker", "exec", c] + tmo if kind == "nr" else
           ["docker", "run", "--rm", "--network", f"container:{c}", "--entrypoint", "timeout", NR_IMAGE] + tmo[1:])
    cmd = pre + ["-c", DN, "-p", str(port), "-B", addr, "--bind-dev", dev, "-J", "-t", str(secs),
                 "--connect-timeout", "15000"]
    if reverse:
        cmd += ["-R", "--rcv-timeout", "20000"]   # receive timeout: only valid when this end receives
    if udp_kbps:
        cmd += ["-u", "-b", f"{udp_kbps}k", "-l", str(UDP_LEN)]
    else:
        cmd += ["-w", WIN]
    rc, o, e = run(cmd, secs + 80)
    try:
        j = json.loads(o)
    except ValueError:
        return {"error": (e or o).strip()[-200:] or f"iperf3 exit {rc}"}
    if j.get("error"):
        return {"error": j["error"]}
    end = j.get("end", {})
    if udp_kbps:
        s = end.get("sum_received") or end.get("sum") or {}
        snt = end.get("sum_sent") or end.get("sum") or {}
        return {"offered_kbps": udp_kbps, "kbps": round(s.get("bits_per_second", 0) / 1e3, 1),
                "loss_pct": round(s.get("lost_percent", snt.get("lost_percent", 0)), 1),
                "jitter_ms": round(s.get("jitter_ms", snt.get("jitter_ms", 0)), 1)}
    rcv, snt = end.get("sum_received", {}), end.get("sum_sent", {})
    return {"kbps": round(rcv.get("bits_per_second", 0) / 1e3, 1), "bytes": rcv.get("bytes", 0),
            "secs": round(rcv.get("seconds", 0), 1), "retransmits": snt.get("retransmits")}


def tcp_with_ping(ue, secs, reverse):
    """TCP for secs seconds and, alongside, pings from the same UE: the RTT under load."""
    box = {}
    t = threading.Thread(target=lambda: box.update(ping=ping(ue, max(4, int((secs - 4) / 0.5)))))
    res = {}
    th = threading.Thread(target=lambda: res.update(iperf(ue, secs, reverse)))
    th.start()
    time.sleep(3)          # let TCP ramp up first (the nrUE path needs a few seconds for its first window)
    t.start()
    th.join()
    t.join()
    res["rtt_loaded"] = box.get("ping")
    return res


WATCHDOG_LOG = os.path.join(HERE, "logs", "ble_watchdog.log")


def wait_session(ue, maxwait=240):
    """n3iwue: the Bluetooth watchdog (ble_watchdog.sh) brings a lost session back; wait for it instead of failing.
    Returns the seconds waited (0 if it was up), or None if it did not come back."""
    t0 = time.time()
    while True:
        if ue_addr(ue[3], ue[4]):
            return round(time.time() - t0)
        if time.time() - t0 > maxwait:
            return None
        time.sleep(5)


def outages(t0, t1, ue):
    """Watchdog recoveries of this UE between t0 and t1 (epoch s): board resets and n3iwue restarts."""
    k = ue[0][-1]
    ev = []
    try:
        for line in open(WATCHDOG_LOG):
            f = line.split(" ", 3)
            if len(f) == 4 and f[0].isdigit() and t0 <= int(f[0]) <= t1 and (f"BLE{k} " in f[3] or f"n3iwue{k}" in f[3]):
                ev.append(f"{f[2]} {f[3].strip()}")
    except OSError:
        pass
    return ev


def timed(ue, fn):
    """Run one test of a UE; for an n3iwue first wait for its session and record the watchdog events during it."""
    if ue[2] != "n3":
        return fn()
    waited = wait_session(ue)
    if waited is None:
        return {"error": "no PDU session (watchdog did not bring it back in 240 s)"}
    t0 = time.time()
    res = fn()
    ev = outages(int(t0), int(time.time()) + 1, ue)
    if waited:
        res["waited_for_session_s"] = waited
    if ev:
        res["outages"] = ev
    return res


def health(ue):
    name, _, kind, c, dev, _ = ue
    a = ue_addr(c, dev)
    return {"pdu_addr": a, "ok": bool(a)}


def cmd_run(args):
    global T_START
    T_START = int(time.time())
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "dn": DN, "udp_len": UDP_LEN, "tcp_window": WIN,
           "rates_kbps": RATES, "ues": {}, "all": {}}
    secs, usecs = (8, 5) if args.quick else (20, 10)
    ues = [u for u in UES if not args.only or u[0] in args.only.split(",")]
    for u in ues:
        out["ues"][u[0]] = {"class": u[1], "kind": u[2], "iface": u[4], "before": health(u)}
    solo = [u for u in ues if not args.solo or u[0] in args.solo.split(",")]
    for u in solo:
        name = u[0]
        r = out["ues"][name]
        if not r["before"]["ok"] and (u[2] != "n3" or wait_session(u) is None):
            log(f"{name}: no PDU session, skipped")
            continue
        log(f"{name}: idle RTT")
        r["rtt_idle"] = timed(u, lambda: ping(u, 20))
        log(f"{name}: idle {r['rtt_idle'].get('avg')} ms; TCP up {secs}s")
        r["tcp_ul"] = timed(u, lambda: tcp_with_ping(u, secs, False))
        log(f"{name}: TCP up {r['tcp_ul'].get('kbps')} kbit/s; TCP down {secs}s")
        r["tcp_dl"] = timed(u, lambda: tcp_with_ping(u, secs, True))
        log(f"{name}: TCP down {r['tcp_dl'].get('kbps')} kbit/s; UDP sweep")
        r["udp_ul"], r["udp_dl"] = [], []
        for k in (RATES[u[2]][::2] if args.quick else RATES[u[2]]):
            r["udp_ul"].append(timed(u, lambda: iperf(u, usecs, False, k)))
            r["udp_dl"].append(timed(u, lambda: iperf(u, usecs, True, k)))
            log(f"{name}: UDP {k} kbit/s: up {r['udp_ul'][-1].get('kbps')} ({r['udp_ul'][-1].get('loss_pct')}% loss), "
                f"down {r['udp_dl'][-1].get('kbps')} ({r['udp_dl'][-1].get('loss_pct')}% loss)")
        r["after_solo"] = health(u)
    for u in ues:
        if u[2] == "n3":
            wait_session(u)
    live = [u for u in ues if health(u)["ok"]]
    if len(live) > 1:
        for d, rev in (("tcp_ul", False), ("tcp_dl", True)):
            log(f"all {len(live)} UEs at once: {d} {secs + 10}s")
            res = {}
            th = [threading.Thread(target=lambda u=u: res.update({u[0]: timed(u, lambda: tcp_with_ping(u, secs + 10, rev))}))
                  for u in live]
            for t in th:
                t.start()
            for t in th:
                t.join()
            out["all"][d] = res
            log("  " + ", ".join(f"{n} {v.get('kbps')} kbit/s" for n, v in res.items()))
    for u in ues:
        out["ues"][u[0]]["after"] = health(u)
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    out["watchdog_events"] = [line.strip().split(" ", 1)[1] for line in open(WATCHDOG_LOG)
                              if line.split(" ", 1)[0].isdigit() and int(line.split(" ", 1)[0]) >= T_START] \
        if os.path.exists(WATCHDOG_LOG) else []
    d = os.path.join(HERE, "logs", "stress")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + ".json")
    for p in (f, os.path.join(d, "latest.json")):
        with open(p, "w") as fh:
            json.dump(out, fh, indent=1)
    log(f"results: {f}")


def cmd_servers(stop=False):
    """iperf3 3.16 from the nrUE image in oai-ext-dn's network namespace: ext-dn's own iperf3 3.9 has no
    --idle-timeout, and a client lost on a stalled path left its server stuck (new clients then hang)."""
    run(["docker", "exec", "oai-ext-dn", "pkill", "-f", "iperf3 -s -D -p 522"], 15)   # older ext-dn servers
    for u in UES:
        run(["docker", "rm", "-f", f"stress-srv-{u[5]}"], 30)
    if stop:
        log("stress servers stopped")
        return
    for u in UES:
        rc, o, e = run(["docker", "run", "-d", "--name", f"stress-srv-{u[5]}", "--network", "container:oai-ext-dn",
                        "--entrypoint", "iperf3", NR_IMAGE, "-s", "-p", str(u[5]), "--idle-timeout", "30"], 30)
        log(f"iperf3 server for {u[0]} on {DN}:{u[5]}" + ("" if rc == 0 else f" failed: {e.strip()}"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["servers", "stop", "run"])
    ap.add_argument("--quick", action="store_true", help="short tests (8 s TCP, 5 s UDP, 3 rates)")
    ap.add_argument("--only", help="comma-separated UE names, e.g. n3iwue1,nrUE1")
    ap.add_argument("--solo", help="of those, the UEs also tested alone (default: all); the rest only run together")
    a = ap.parse_args()
    if a.what == "run":
        cmd_run(a)
    else:
        cmd_servers(stop=a.what == "stop")


if __name__ == "__main__":
    sys.exit(main())
