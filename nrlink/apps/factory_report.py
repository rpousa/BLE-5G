#!/usr/bin/env python3
"""Turn one factory_dl.py run into the page's data: per level and flow, every frame with its scene time, whether it
arrived whole (all fragments, CRC ok), its one-way delay and arrival time; plus the payloads that arrived (LiDAR
scans and camera JPEGs), one file per level.

  factory_report.py RUN_DIR SCEN_DIR OUT_DIR

One-way delay = receive time on the UE PC, moved to the core PC clock with the LAN offset measured before and after
the run (interpolated), minus the send time stamped by the sender in ext-dn.
"""
import base64
import json
import os
import statistics
import sys

FLOWS = ["lidar", "robots", "cam1", "cam2"]


def pct(v, q):
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


def main(run, scen, out):
    os.makedirs(out, exist_ok=True)
    meta = json.load(open(os.path.join(scen, "meta.json")))
    cb = json.load(open(os.path.join(run, "clock_before.json")))
    ca = json.load(open(os.path.join(run, "clock_after.json")))

    def off(t_ue):   # core clock - UE clock at UE time t_ue
        f = (t_ue - cb["t"]) / max(1.0, ca["t"] - cb["t"])
        return cb["offset_s"] + (ca["offset_s"] - cb["offset_s"]) * min(1.0, max(0.0, f))

    starts = [json.loads(l) for l in open(os.path.join(run, "tx_levels.jsonl"))]
    t0 = {int(s["level"][1:]): s["t0"] for s in starts}
    rx = {}
    for fl in FLOWS:
        p = os.path.join(run, f"rx_{fl}.jsonl")
        rows = []
        if os.path.exists(p):
            for line in open(p):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        rx[fl] = rows
    report = {"meta": meta, "clock": {"before": cb, "after": ca, "drift_ms": round((ca["offset_s"] - cb["offset_s"]) * 1000, 2)},
              "levels": []}
    for info in meta["levels"]:
        lvl = int(info["name"][1:])
        if lvl not in t0:
            continue
        ev = json.load(open(os.path.join(scen, f"{info['name']}.json")))
        sent = {fl: {} for fl in FLOWS}
        for t, fl, n, b64 in ev:
            sent[fl][n] = (t, b64)
        L = {"name": info["name"], "info": info, "t0": t0[lvl], "flows": {}}
        payload = {"lidar": {}, "cam1": {}, "cam2": {}}
        for fl in FLOWS:
            got = {}     # frame -> dict
            pk_delay = []
            for f_, lv, n, sq, k, nk, ts, tr, ln, ok in rx[fl]:
                if lv != lvl:
                    continue
                tc = tr + off(tr)
                d = (tc - ts) * 1000
                pk_delay.append(d)
                g = got.setdefault(n, {"frags": set(), "nk": nk, "first_send": ts, "last": tc, "ok": None})
                g["frags"].add(k)
                g["first_send"] = min(g["first_send"], ts)
                g["last"] = max(g["last"], tc)
                if ok is not None:
                    g["ok"] = ok
            frames = []
            delays = []
            for n in sorted(sent[fl]):
                ts_scene = sent[fl][n][0]
                g = got.get(n)
                whole = bool(g and len(g["frags"]) == g["nk"] and g["ok"])
                if whole:
                    d = (g["last"] - g["first_send"]) * 1000
                    delays.append(d)
                    frames.append([n, ts_scene, round(g["last"] - t0[lvl], 4), round(d, 1)])
                    if fl in payload:
                        payload[fl][n] = sent[fl][n][1]
                else:
                    frames.append([n, ts_scene, None, None])
            delays.sort()
            pk_delay.sort()
            nsent = len(sent[fl])
            nwhole = len(delays)
            secs = info.get("secs", meta["secs"])
            byts = sum(len(base64.b64decode(sent[fl][f[0]][1])) for f in frames if f[2] is not None)
            L["flows"][fl] = {
                "sent": nsent, "whole": nwhole, "delivered_pct": round(100 * nwhole / max(1, nsent), 1),
                "offered_kbps": info["kbps"][fl], "goodput_kbps": round(byts * 8 / meta["secs"] / 1000, 1),
                "packets_rx": len(pk_delay),
                "delay_ms": {"min": pct(delays, 0), "p50": pct(delays, .5), "p90": pct(delays, .9),
                             "p99": pct(delays, .99), "max": pct(delays, 1)},
                "jitter_ms": round(statistics.pstdev(delays), 1) if len(delays) > 1 else None,
                "frames": frames}
        report["levels"].append({k: v for k, v in L.items()})
        with open(os.path.join(out, f"payload_{info['name']}.json"), "w") as f:
            json.dump(payload, f, separators=(",", ":"))
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(report, f, separators=(",", ":"))
    for L in report["levels"]:
        print(L["name"], {fl: (v["delivered_pct"], v["goodput_kbps"], v["delay_ms"]["p50"], v["delay_ms"]["p90"])
                          for fl, v in L["flows"].items()})


if __name__ == "__main__":
    main(*sys.argv[1:4])
