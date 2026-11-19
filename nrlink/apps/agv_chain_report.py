#!/usr/bin/env python3
"""Joins an AGV relay chain run: the controller's log (UE PC, run_*.jsonl) and the edge's log (core, docker logs of
agvc-edge) into per-message one-way delays by role, and writes a compact trace for the page's replay.

  agv_chain_report.py run_L1.jsonl edge_L1.jsonl [--from T0] [--to T1] [--trace out.json]
The UE PC's clock is put on the core's with the offsets the controller measured (k=clock lines).
"""
import argparse
import bisect
import json
import statistics

ROLES = ("pivot", "relay", "remote")
FLOWS = ("det", "lidar", "cmd")


def load(p):
    out = []
    with open(p) as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def pct(v, p):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(p * len(s)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ctl")
    ap.add_argument("edge")
    ap.add_argument("--from", dest="t0", type=float, default=0, help="seconds after the start to begin")
    ap.add_argument("--to", dest="t1", type=float, default=1e12)
    ap.add_argument("--trace")
    a = ap.parse_args()
    ctl, edge = load(a.ctl), load(a.edge)
    start = next(m for m in ctl if m["k"] == "start")
    T0 = start["t"]
    clk = sorted((m["t"], m["off"]) for m in ctl if m["k"] == "clock")
    ct = [c[0] for c in clk]

    def off(t):          # core - UE, nearest measurement
        i = min(len(clk) - 1, bisect.bisect_left(ct, t))
        return clk[i][1]
    win = (T0 + a.t0, T0 + a.t1)
    gen = {}
    for m in ctl:
        if m["k"] == "gen":
            gen[(m["agv"], m["f"], m["s"])] = m
    drops = {(m["agv"], m["f"], m["s"]) for m in ctl if m["k"] == "drop"}
    via = {(m["agv"], m["f"], m["s"]): m["via"] for m in ctl if m["k"] == "uu"}   # the AGV whose UE sent it up
    rows = []            # (t_ue, agv, flow, role, delay_ms or None)
    got = set()
    for m in edge:
        if m["k"] != "rx":
            continue
        key = (m["agv"], m["f"], m["s"])
        g = gen.get(key)
        if not g or not (win[0] <= g["t"] <= win[1]):
            continue
        if abs(m["tg"] - g["t"]) > 0.01:     # an older run's message with the same number (nrUE1 kept it queued)
            continue
        got.add(key)
        rows.append((g["t"], m["agv"], FLOWS[m["f"]], ROLES[g["role"]], (m["tr"] - (m["tg"] + off(m["tg"]))) * 1000,
                     via.get(key, -1)))
    for key, g in gen.items():
        if key not in got and win[0] <= g["t"] <= win[1] - 10:
            rows.append((g["t"], key[0], FLOWS[key[1]], ROLES[g["role"]], None, via.get(key, -1)))
    # commands: the receiving AGV's role when it arrived (from the chain log)
    chains = sorted((m["t"], m["new"]) for m in ctl if m["k"] == "chain")
    chain0 = start["chain"]

    def chain_at(t):
        c = chain0
        for tt, cc in chains:
            if tt > t:
                break
            c = cc
        return c
    cmd_sent = {(m["agv"], m["s"]): m["te"] for m in edge if m["k"] == "cmd"}
    cmd_got = {}
    for m in ctl:
        if m["k"] == "cmdrx":
            cmd_got[(m["agv"], m["s"])] = m
    for key, te in cmd_sent.items():
        t_ue = te - off(te)
        if not (win[0] <= t_ue <= win[1] - 10):
            continue
        r = cmd_got.get(key)
        role = ROLES[chain_at(t_ue).index(key[0])]
        rows.append((t_ue, key[0], "cmd", role, None if r is None else (r["tr"] + off(r["tr"]) - te) * 1000, -1))
    # summary
    print(f"{a.ctl}: {len(rows)} messages, window {a.t0:.0f}-{min(a.t1, rows and max(r[0] for r in rows) - T0 or 0):.0f} s")
    summ = {}
    for role in ROLES:
        for fl in FLOWS:
            v = [r[4] for r in rows if r[3] == role and r[2] == fl]
            d = [x for x in v if x is not None]
            if not v:
                continue
            s = dict(n=len(v), p50=pct(d, .5), p90=pct(d, .9), lost=100 * (len(v) - len(d)) / len(v))
            summ[f"{role}|{fl}"] = s
            print(f"  {role:6s} {fl:5s} n {s['n']:5d}  p50 {s['p50'] or 0:7.1f}  p90 {s['p90'] or 0:7.1f} ms  lost {s['lost']:.1f} %")
    if start.get("topology") == "dual":
        # which gateway carried the remote's messages: the one it chose, or its own UE (an old gateway still attached)
        rem = [r for r in rows if r[3] == "remote" and r[2] != "cmd"]
        ch = chain_at
        n = {"g1": 0, "g2": 0, "own": 0, "none": 0}
        for r in rem:
            c = ch(r[0])
            n["none" if r[5] < 0 else "own" if r[5] == r[1] else "g1" if r[5] == c[0] else "g2"] += 1
        print(f"  remote's uplink via: gateway 1 {n['g1']}, gateway 2 {n['g2']}, its own UE {n['own']}, not sent up {n['none']}"
              f"  (pick {start.get('pick')})")
        for gi in (0, 1):
            d = [r[4] for r in rem if r[4] is not None and r[5] >= 0 and r[5] != r[1] and ch(r[0]).index(r[5]) == gi]
            if d:
                print(f"    remote via gateway {gi + 1}: n {len(d)}  p50 {pct(d, .5):.0f}  p90 {pct(d, .9):.0f} ms")
    nch = [c for c in chains if win[0] <= c[0] <= win[1]]
    span = (min(win[1], max(r[0] for r in rows)) - win[0]) / 60
    print(f"  chain changes {len(nch)} ({len(nch) / span:.1f} a minute), drops logged {len(drops)}")
    st = [m for m in ctl if m["k"] == "stats" and win[0] <= m["t"] <= win[1]]
    if st:
        dt = sum(m["dt"] for m in st)
        print(f"  windows {sum(m['windows'] for m in st) / dt:.1f}/s, sidelink {sum(m['sl_pkts'] for m in st) / dt:.0f} pkt/s,"
              f" window time used {sum(m['win_ms_used'] for m in st) / max(1, sum(m['windows'] for m in st)):.1f} ms")
    att = [m for m in ctl if m["k"] == "attach" and win[0] <= m["t"] <= win[1]]
    covs = [m for m in ctl if m["k"] == "cov" and m["on"] == 1 and win[0] <= m["t"] <= win[1]]
    if covs:
        ups = sorted(m["up"] for m in att if m.get("ok"))
        nos = [m for m in ctl if m["k"] == "drop" and m.get("why") == "no session" and win[0] <= m["t"] <= win[1]]
        print(f"  pivot changes (attaches) {len(covs)}: sessions up {len(ups)}, after {ups[0]:.2f}-{ups[-1]:.2f} s, median "
              f"{ups[len(ups) // 2]:.2f} s" if ups else "  no attach finished")
        for k in ("started", "first_msg", "rnti", "up"):
            v = sorted(m[k] for m in att if m.get("ok") and k in m)
            if v:
                print(f"    {k:9s} median {v[len(v) // 2]:.2f} s  ({v[0]:.2f}-{v[-1]:.2f})")
        print(f"  dropped at the pivot for want of a session: {len(nos)} messages; failed attaches {sum(1 for m in att if not m.get('ok'))}")
    if a.trace:
        pos = [m for m in ctl if m["k"] == "pos" and win[0] <= m["t"] <= win[1]]
        hops = [m for m in ctl if m["k"] == "hop" and win[0] <= m["t"] <= win[1]]
        tb = win[0]
        LEDC = {"off": -1, "blue": 0, "green": 1, "red": 2}
        leds = [[round(m["t"] - tb, 2), m["agv"], LEDC.get(m["c"], -1), m.get("blink", 0)] for m in ctl if m["k"] == "led"
                and m["t"] <= win[1]]
        led0 = {}
        for l in leds:                                   # the state at the window's start, then the changes in it
            if l[0] <= 0:
                led0[l[1]] = [0.0] + l[1:]
        leds = list(led0.values()) + [l for l in leds if l[0] > 0]
        tr = dict(t0=tb, pos=[[round(m["t"] - tb, 2), m["a"], m["chain"]] + ([m["q"]] if "q" in m else []) for m in pos],
                  serv=[[round(m["t"] - tb, 2), m["r"], -1 if m["g"] is None else m["g"]] for m in ctl
                        if m["k"] == "serve" and "r" in m and win[0] - 120 <= m["t"] <= win[1]],
                  leds=leds, holds=[[round(m["t"] - tb, 2), m["agv"], m["on"]] for m in ctl if m["k"] == "hold" and win[0] <= m["t"] <= win[1]],
                  rlf=[[round(m["t"] - tb, 2), m["agv"]] for m in ctl if m["k"] == "rlf" and win[0] <= m["t"] <= win[1]],
                  chains=[[round(t - tb, 2), c] for t, c in chains if win[0] <= t <= win[1]],
                  msgs=[[round(r[0] - tb, 3), r[1], FLOWS.index(r[2]), ROLES.index(r[3]),
                         None if r[4] is None else round(r[4], 1), r[5]] for r in sorted(rows, key=lambda r: r[0])],
                  hops=[[round(m["t"] - tb, 3), m["a"], m["b"], m["f"]] for m in hops],
                  summary=summ, level=start["level"], window_ms=start["window_ms"], uu=start.get("uu", "fleet"),
                  topology=start.get("topology", "chain"), pick=start.get("pick"),
                  att=[[round(c["t"] - tb, 2), c["agv"],
                        next((round(m["t"] - tb, 2) for m in att if m["agv"] == c["agv"] and m.get("ok") and m["t"] >= c["t"]), None)]
                       for c in covs])
        json.dump(tr, open(a.trace, "w"), separators=(",", ":"))
        print("trace:", a.trace)


if __name__ == "__main__":
    main()
