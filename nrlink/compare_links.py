#!/usr/bin/env python3
"""compare_links.py: the same tests, one UE at a time, from the same source/sink (ext-dn 192.168.70.132, iperf3
servers of stress.py), to compare 3GPP access over the nrlink GenFSK boards with non-3GPP access over Bluetooth LE.
Everything else stays idle (stop the live factory first).  Usage: compare_links.py [--ues nrUE1,n3iwue1] [--out f.json]"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stress as S

RATES = [10, 20, 30, 40, 50, 60, 80, 100, 120]   # kbit/s offered, UDP 500 B datagrams (S.UDP_LEN), both directions
UDP_S, TCP_S, DRAIN_S = 15, 30, 8


def drain(ue, idle):
    """wait until a ping is back near idle (queues emptied by the last step), at most 30 s"""
    t0 = time.time()
    while time.time() - t0 < 30:
        p = S.ping(ue, 2, 0.5)
        if p.get("recv") and p.get("avg", 1e9) < max(400, 2 * idle):
            return round(time.time() - t0, 1)
    return None


def test(ue, out):
    name = ue[0]; r = out[name] = {"class": ue[1], "t0": time.time()}
    S.log(f"== {name}: idle pings")
    r["ping"] = {sz: S.ping_sz(ue, 30, sz) for sz in (56, 472, 1372)}
    idle = r["ping"][56].get("avg") or 200
    S.log(f"{name}: idle RTT {idle} ms; 472 B {r['ping'][472].get('avg')}; 1372 B {r['ping'][1372].get('avg')}")
    for d, rev in (("udp_ul", False), ("udp_dl", True)):
        r[d] = []
        for k in RATES:
            x = S.iperf(ue, UDP_S, rev, k); x["drain_s"] = drain(ue, idle); r[d].append(x)
            S.log(f"{name}: {d} {k} kbit/s -> {x.get('kbps')} kbit/s, loss {x.get('loss_pct')} %, jitter {x.get('jitter_ms')} ms {x.get('error','')}")
            if x.get("kbps") is not None and x.get("loss_pct", 0) > 60 and k >= 60:
                break                                       # well past saturation: the rest only fills queues
            json.dump(out, open(A.out, "w"), indent=1)
    for d, rev in (("tcp_ul", False), ("tcp_dl", True)):
        r[d] = S.tcp_with_ping(ue, TCP_S, rev); drain(ue, idle)
        S.log(f"{name}: {d} {r[d].get('kbps')} kbit/s, RTT under load {(r[d].get('ping') or {}).get('avg')} ms {r[d].get('error','')}")
    r["t1"] = time.time(); json.dump(out, open(A.out, "w"), indent=1)


def ping_sz(ue, n, size, interval=0.5):
    name, _, _, c, dev, _ = ue
    rc, o, e = S.run(["docker", "exec", c, "ping", "-c", str(n), "-i", str(interval), "-W", "4", "-s", str(size), "-I", dev, S.DN], n * interval + 40)
    import re
    res = {"sent": n, "size": size}
    m = re.search(r"(\d+) packets transmitted, (\d+) received", o)
    if m: res["recv"] = int(m.group(2)); res["loss_pct"] = round(100 * (1 - int(m.group(2)) / max(1, int(m.group(1)))), 1)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", o)
    if m: res.update(min=float(m.group(1)), avg=float(m.group(2)), max=float(m.group(3)), mdev=float(m.group(4)))
    return res
S.ping_sz = ping_sz

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--ues", default="nrUE1,n3iwue1")
    ap.add_argument("--out", default=f"logs/compare/compare_{time.strftime('%Y%m%d-%H%M%S')}.json"); A = ap.parse_args()
    os.makedirs(os.path.dirname(A.out), exist_ok=True)
    out = {"_meta": {"sink": S.DN, "udp_len": S.UDP_LEN, "tcp_win": S.WIN, "rates_kbps": RATES, "udp_s": UDP_S, "tcp_s": TCP_S,
                     "note": "one UE at a time; all other UEs registered but idle; live factory stopped"}}
    for n in A.ues.split(","):
        ue = next(u for u in S.UES if u[0] == n)
        if not S.ue_addr(ue[3], ue[4]): S.log(f"{n}: no PDU address, skipped"); continue
        test(ue, out)
    S.log(f"done -> {A.out}")
