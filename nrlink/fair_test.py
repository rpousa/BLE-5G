#!/usr/bin/env python3
"""Both UEs at once: is the air shared fairly? (UE PC, ~/oai/nrlink, same tools as stress.py / compare_links.py)

For each rate, run the same UDP test on every listed UE at the same time (one thread each, same start), then
report what each one got. Usage: fair_test.py [--ues n3iwue1,n3iwue2] [--rates 40,60,80] [--secs 30]
[--dirs dl,ul] [--out f.json]. Stop the live factory first."""
import argparse, json, os, threading, time
import stress as S

ap = argparse.ArgumentParser()
ap.add_argument("--ues", default="n3iwue1,n3iwue2")
ap.add_argument("--rates", default="40,60,80")
ap.add_argument("--secs", type=int, default=30)
ap.add_argument("--dirs", default="dl,ul")
ap.add_argument("--tcp", action="store_true", help="also 30 s of TCP each way, all UEs at once")
ap.add_argument("--out", default=f"logs/compare/fair_{time.strftime('%Y%m%d-%H%M%S')}.json")
A = ap.parse_args()
ues = [next(u for u in S.UES if u[0] == n) for n in A.ues.split(",")]
out = {"_meta": {"ues": A.ues, "secs": A.secs, "udp_len": S.UDP_LEN, "t0": time.time()}, "steps": []}
os.makedirs(os.path.dirname(A.out), exist_ok=True)


def together(fn):
    res = {}
    th = [threading.Thread(target=lambda u=u: res.__setitem__(u[0], fn(u))) for u in ues]
    for t in th: t.start()
    for t in th: t.join()
    return res


for d in A.dirs.split(","):
    for k in map(int, A.rates.split(",")):
        r = together(lambda u: S.iperf(u, A.secs, d == "dl", k))
        out["steps"].append({"dir": d, "offered_kbps": k, "t": time.time(), "res": r})
        S.log(f"{d} {k} kbit/s each: " + "  ".join(f"{n} {x.get('kbps')} kbit/s loss {x.get('loss_pct')} %" + (f" {x['error']}" if x.get("error") else "")
                                                    for n, x in r.items()))
        json.dump(out, open(A.out, "w"), indent=1)
        time.sleep(5)
if A.tcp:
    for d in ("ul", "dl"):
        r = together(lambda u: S.iperf(u, 30, d == "dl"))
        out["steps"].append({"dir": "tcp_" + d, "t": time.time(), "res": r})
        S.log(f"tcp {d}: " + "  ".join(f"{n} {x.get('kbps')} kbit/s" for n, x in r.items()))
        json.dump(out, open(A.out, "w"), indent=1)
        time.sleep(5)
S.log(f"done -> {A.out}")
