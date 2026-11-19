#!/usr/bin/env python3
"""Builds ~/BLE-5G/docs/pages/agv-dual-gateway.html (+ agv-dual-runs.json) from the AGV Relay Chain page's style and
replay code, the dual-gateway board runs, and the findings in dual_findings.html."""
import html, math
import json
import os
import re

S = os.path.dirname(os.path.abspath(__file__))  # dual_findings.html lives next to this script
P = os.path.expanduser("~/BLE-5G/docs/pages")
R = os.path.expanduser("~/BLE-5G/results/agv_chain")
chain = open(f"{P}/agv-relay-chain.html", encoding="utf-8").read()
CH_RUNS = json.load(open(f"{P}/agv-chain-runs.json"))

DUAL = [  # (key on the page, trace, window ms, pick)
    ("Coverage · layer-3 relay, secured: PC5 security, measured SD-RSRP, QoS, Remote UE Report to the SMF (live scenario)", "cover_l3e_L1_w25", 25, "near"),
    ("Coverage · layer-3 relay, 3GPP procedures: thresholds, PC5-S, DHCPv4, relay PDU session", "cover_l3b_L1_w25", 25, "near"),
    ("Coverage · layer-3 relay, first version: discovery, direct link, IP forwarding and NAT", "cover_l3_L1_w25", 25, "near"),
    ("Coverage · platoon, sidelink kept until the NR link is up", "cover_p8sl_L1_w25", 25, "near"),
    ("Coverage · platoon, one sidelink at a time, kept together", "cover_p8one_L1_w25", 25, "near"),
    ("Coverage · platoon, one sidelink at a time, convoy apart", "cover_p8onespread_L1_w25", 25, "near"),
    ("Coverage · platoon, last link holds, uplink queued", "cover_p8lastq_L1_w25", 25, "near"),
    ("Coverage · platoon, last link holds", "cover_p8last_L1_w25", 25, "near"),
    ("Coverage · platoon 5 m, no holds", "cover_p5_L1_w25", 25, "near"),
    ("Coverage · spread out, nearest gateway", "cover_near_L1_w25", 25, "near"),
    ("Coverage · spread out, sticky pick", "cover_L1_w25", 25, "sticky"),
    ("L1 · 25 ms windows", "dual_L1_w25", 25, "load"),
    ("L2 · 25 ms windows", "dual_L2_w25", 25, "load"),
    ("L2 · 35 ms windows", "dual_L2_w35", 35, "load"),
    ("L2 · 50 ms windows", "dual_L2_w50", 50, "load"),
    ("L2 · 25 ms, always gateway 1", "dual_L2_w25_first", 25, "first"),
    ("L2 · 35 ms, always gateway 1", "dual_L2_w35_first", 35, "first"),
]
CHAIN = [  # the relay chain (option 3) at the same settings, from its page's data
    ("L1 · 25 ms windows", "L1 · OAI sidelink"),
    ("L2 · 25 ms windows", "L2 · OAI sidelink"),
    ("L2 · 35 ms windows", "L2 · OAI sidelink, 35 ms windows"),
    ("L2 · 50 ms windows", "L2 · OAI sidelink, 50 ms windows"),
]


def load_runs():
    runs = {}
    for key, name, w, pick in DUAL:
        f = f"{R}/trace_{name}.json"
        if os.path.exists(f):
            tr = json.load(open(f))
            tr["window_ms"], tr["pick"] = w, pick
            tr["serve"] = serve_of(tr)
            tr["relay"] = "l3" if "_l3" in name else "app"
            runs[key] = tr
    return runs


def serve_of(tr):
    """Change points [t, gateway or -1] of the gateway the remote sent through (-1: its own UE), by message order."""
    out, cur = [], None
    for m in sorted(tr["msgs"], key=lambda m: m[0]):
        if m[3] != 2 or m[2] == 2 or len(m) < 6 or m[5] is None or m[5] < 0:
            continue
        g = -1 if m[5] == m[1] else m[5]
        if g != cur:
            out.append([m[0], g])
            cur = g
    return out


def cell(s, role, flow):
    v = s.get(f"{role}|{flow}")
    if not v:
        return "<td>–</td>"
    p = v["p50"]
    ps = "–" if p is None else (f"{p / 1000:.1f} s" if p >= 1000 else f"{p:.0f} ms")
    bad = ' class="bad"' if v["lost"] >= 5 else ""
    return f'<td{bad}>{ps} · {v["lost"]:.1f} %</td>'


def via_share(tr):
    """Share of the remote's delivered uplink by the gateway that carried it (from msgs[5] and the chain at that time)."""
    ch = [[0, tr["pos"][0][2]]] + tr["chains"]
    n = {"g1": 0, "g2": 0, "own": 0}
    k = 0
    for m in tr["msgs"]:
        t, agv, f, role, d = m[:5]
        via = m[5] if len(m) > 5 else -1
        while k + 1 < len(ch) and ch[k + 1][0] <= t:
            k += 1
        c = ch[k][1]
        if role != 2 or f == 2 or via is None or via < 0 or d is None:
            continue
        n["own" if via == agv else "g1" if via == c[0] else "g2"] += 1
    tot = sum(n.values()) or 1
    return n, tot


def results_table(runs):
    rows = []
    for key, name, w, pick in DUAL:
        tr = runs.get(key)
        if not tr:
            continue
        if tr.get("topology") == "cover":
            continue                                   # in the coverage card: its roles are paths, not gateways
        s = tr["summary"]
        n, tot = via_share(tr)
        rows.append(f'<tr><td><b>{html.escape(key)}</b></td><td>two gateways</td>{cell(s, "pivot", "det")}{cell(s, "relay", "det")}'
                    f'{cell(s, "remote", "det")}{cell(s, "remote", "lidar")}{cell(s, "remote", "cmd")}'
                    f'<td class="n">{100 * n["g1"] / tot:.0f} / {100 * n["g2"] / tot:.0f}'
                    f' / {100 * n["own"] / tot:.0f}</td></tr>')
        ck = next((c for k, c in CHAIN if k == key), None)
        if ck and ck in CH_RUNS:
            s = CH_RUNS[ck]["summary"]
            rows.append(f'<tr class="sub"><td></td><td>relay chain</td>{cell(s, "pivot", "det")}{cell(s, "relay", "det")}'
                        f'{cell(s, "remote", "det")}{cell(s, "remote", "lidar")}{cell(s, "remote", "cmd")}<td class="n">two hops</td></tr>')
    return "\n".join(rows)


def cover_table(runs):
    """The coverage runs by path: the report's role slots are the sender's LED when it sent."""
    out = []
    for key, name, w, pick in DUAL:
        tr = runs.get(key)
        if not tr or tr.get("topology") != "cover" or out:
            continue
        s = tr["summary"]
        M = [m for m in tr["msgs"] if m[2] != 2]
        red = 100 * sum(m[3] == 2 for m in M) / max(1, len(M))
        per = [100 * sum(m[3] == 2 for m in M if m[1] == i) / max(1, sum(1 for m in M if m[1] == i)) for i in range(3)]
        ups = sorted(a[2] - a[0] for a in tr.get("att", []) if a[2] is not None)
        holds = [h for h in tr.get("holds", []) if h[2] == 1]
        hd = []
        for h in holds:
            off = next((x[0] for x in tr["holds"] if x[1] == h[1] and x[2] == 0 and x[0] > h[0]), None)
            if off is not None:
                hd.append(off - h[0])
        hd.sort()
        cap = (f"{(tr['pos'][-1][0] - tr['pos'][0][0]) / 60:.0f} min; {red:.0f} % of the uplink messages over the sidelink "
               f"(A {per[0]:.0f}, B {per[1]:.0f}, C {per[2]:.0f} %); {len(tr.get('rlf', []))} radio link failures, {len(ups)} attaches"
               + (f", median {ups[len(ups) // 2]:.1f} s" if ups else "")
               + (f"; {len(holds)} holds before the dead zone, median {hd[len(hd) // 2]:.0f} s" if hd else ""))
        out.append(f'<tr class="sub"><td colspan="4"><b>{html.escape(key)}</b> · {cap}</td></tr>')
        for slot, lab, col in (("relay", "own UE (green)", "--rrelay"), ("pivot", "own UE, also relaying (blue)", "--rpivot"),
                               ("remote", "over the sidelink (red)", "--rremote")):
            out.append(f'<tr><td><b style="color:var({col})">{lab}</b></td>{cell(s, slot, "det")}{cell(s, slot, "lidar")}{cell(s, slot, "cmd")}</tr>')
    if not out:
        return '<p class="hint">No coverage run recorded yet.</p>'
    return ('<div class="scroll"><table><thead><tr><th>Sent</th><th>Positioning p50 · lost</th><th>LiDAR p50 · lost</th><th>Commands p50 · lost</th></tr></thead><tbody>'
            + "".join(out) + '</tbody></table></div><p class="hint" style="margin:8px 0 0">Lost includes messages that had no way up. Commands are grouped by the AGV&#x27;s LED when the edge sent them.</p>')


LOOP_M = 124.0  # m, one lap (agv_chain.LOOP)
COMPARE = {  # trace -> (what changed, how the AGVs drive, relay rule, holds, uplink while attaching)
    "cover_L1_w25": ("Coverage by position", "spread out, a third of a lap apart", "any AGV with a session, sticky (less busy) pick", "none", "dropped"),
    "cover_near_L1_w25": ("Nearest gateway", "spread out", "the nearest AGV with a session", "none", "dropped"),
    "cover_p5_L1_w25": ("Platoon, relays in reach", "platoon, 5 m gaps", "the nearest, only if nearer than the gNB", "none", "dropped"),
    "cover_p8last_L1_w25": ("Last link holds", "platoon, 8 m gaps", "the nearest, only if nearer than the gNB", "the only AGV with a session stops at the door", "dropped"),
    "cover_p8lastq_L1_w25": ("Uplink queued", "platoon, 8 m gaps", "the nearest, only if nearer than the gNB", "the only AGV with a session stops at the door", "queued up to 5 s"),
    "cover_p8onespread_L1_w25": ("One sidelink at a time", "platoon, 8 m gaps, but only the AGVs behind wait, so it comes apart", "the nearest, only if nearer than the gNB", "an AGV with a session stops at the door while another is on the sidelink", "queued up to 5 s"),
    "cover_p8sl_L1_w25": ("Sidelink kept until the NR link is up", "platoon kept together; an AGV on the sidelink stops before it would drive out of its relay's reach, until its own session is up", "the nearest, only if nearer than the gNB", "as above, plus that stop", "queued up to 5 s (not needed)"),
    "cover_l3e_L1_w25": ("Secured relay, measured links, QoS, report to the SMF", "platoon kept together, as above", "as above, plus: SD-RSRP from the boards' measured beacons (each link's deviation from its calibrated level, layer-3 filtered); PC5-S integrity protected and the remote's traffic and DHCP in ESP (AES-GCM, keys per link after TS 33.503); positioning and LiDAR as PQI 58 and 59, mapped to 5QI 7 and 9 with a priority queue each; Remote UE Report as NAS (TS 24.501) on the relay PDU session, answered by the SMF", "as above", "queued up to 3 s until the link has its address"),
    "cover_l3b_L1_w25": ("3GPP procedures for the relay", "platoon kept together, as above", "SIB12-style thresholds (TS 38.331): a relay only while its Uu RSRP is in [−95, −31] dBm and its relay PDU session is up; a remote only below −100 dBm, from relays heard above −90 dBm SD-RSRP, 2 dB to reselect; PC5-S link set-up (TS 24.554) with null security, the address by DHCPv4 from the relay, the remote's traffic NATed into the relay's own PDU session for DNN relay", "as above; the relay's reach is now the SD-RSRP threshold", "queued up to 3 s until the link has its address"),
    "cover_l3_L1_w25": ("Layer-3 relay, first version", "platoon kept together, as above", "the remote picks: the strongest discovery announcement (SD-RSRP) above its own Uu signal, 2 dB hysteresis; then a direct link request and accept, and the relay reports it up (Remote UE Report); the IP packets are routed and NATed by the relay's kernel", "as above", "queued up to 3 s until the direct link is up"),
    "cover_p8one_L1_w25": ("One sidelink at a time, platoon kept together", "platoon, 8 m gaps; an AGV with a session waits for the one behind (gap + 6 m)", "the nearest, only if nearer than the gNB", "an AGV with a session stops at the door while another is on the sidelink", "queued up to 5 s"),
}


CMPNOTES = ['<b>Spread out</b>, the AGVs a third of a lap apart: every relay is in reach somewhere and almost nothing is lost, but the relay can be across the floor, farther than the gNB, which a real sidelink would not reach.', '<b>Platoon, relays in reach</b>: only an AGV nearer than the gNB may relay. In a convoy, all three end up in the cold store together, and 8 % of the uplink is lost (1,083 messages), 105 s with no AGV on the gNB at all.', '<b>Last link holds</b>: the last AGV with a session stops at the door, so the platoon is never cut off, but the leader, out of the store and attaching, is by then farther from the held AGV than the gNB: 80 messages lost, once a lap. <b>Queuing the uplink</b> for up to 5 s while its own UE attaches brings that to 1.', '<b>One sidelink at a time</b>: only the AGVs behind ever waited, so after a few holds the convoy came apart around the loop and the AGV in the store had no relay in reach: 12 % lost. (With the convoy closed into a ring, every AGV counted as a follower and skipped the checkpoint stops, hence the faster pace.)', '<b>Platoon kept together</b>: an AGV with a session waits for the one behind, so the AGV at the door is always near enough to relay. 0 of 15,249 uplink messages lost; 2 % arrive after more than 1 s, mostly queued while an AGV leaving the store attaches. The cost is pace: about 22 laps an hour against 29, and the sidelink&#x27;s p90 rises to about 400 ms.', '<b>Sidelink kept until the NR link is up</b>: leaving the store, the leader used to drive away from its relay at the door before its own UE had attached, and its uplink waited in the queue. Now an AGV on the sidelink stops while its relay is still 2 m nearer than the gNB and drives on once its session is up: 6 stops in 14 minutes, all AGV A (the leader, whose relay is behind it), about 3 s each. 0 uplink lost, nothing queued, messages over 1 s down from 323 to 77, and the sidelink&#x27;s p90 back down to 287 ms, at no cost in pace.', '<b>Layer-3 relay</b>, first version (19 Nov): the same driving, but the relay is now chosen and run as in TS 23.304. The remote picks its relay from discovery announcements (SD-RSRP), sets up a direct link (request to accept: 129 ms median, 26 links) and gets an IP address on it; the relay reports it to the edge (56 Remote UE Reports in 16 minutes) and routes and NATs its packets in the kernel. In 15 minutes: 2 of 16,238 uplink messages lost, both LiDAR scans on an AGV&#x27;s own UE, none through a relay. The 132 over 1 s came in two bursts around re-attaches, 92 of them from AGVs on their own UE. Sidelink positioning 189 ms p50, 305 ms p90: about 10–20 ms more than the application relay, at the same pace.', '<b>3GPP procedures for the relay</b> (19 Nov): who may relay and who may use a relay now follows SIB12-style thresholds (TS 38.331): an AGV announces itself only while its Uu RSRP is between −95 and −31 dBm and its <b>relay PDU session</b> (DNN relay) is up; an AGV selects a relay only below −100 dBm, from announcements above −90 dBm SD-RSRP. The link is set up with the TS 24.554 PC5-S exchange (request, security mode command and complete with null algorithms, accept with &quot;IP address configuration: DHCPv4 server&quot;), and the remote gets its address by <b>DHCPv4</b> from the relay; the relay reports it with that address. Set-up takes 651 ms (median of 23 links, 567–777 ms) instead of 129 ms: 8 messages over the sidelink instead of 2, none retransmitted, none rejected. In 15 minutes: 1 of 16,260 uplink messages lost (on an AGV&#x27;s own UE), 21 over 1 s instead of 132, 11 stops to keep a relay in reach instead of 17 (the reach is now the SD-RSRP threshold, about 34 m). Delays are 30–45 ms higher on every path, also on an AGV&#x27;s own UE (positioning 185 ms p50, against 139): most likely the second PDU session on every UE, not yet checked.', '<b>Secured relay, measured links, QoS, report to the SMF</b>, the live scenario since 19 Nov: four more steps. <b>PC5 security</b>: keys per link from both sides&#x27; nonces (KNRP-sess after TS 33.503, the root generated per run instead of provisioned), every PC5-S message from the security mode command on carries a MAC, and the remote&#x27;s IP traffic and DHCP go in ESP (AES-GCM-128) over the sidelink. <b>Measured SD-RSRP</b>: each board pair is calibrated on its first 40 beacons and the selection uses the path-loss model plus the link&#x27;s measured, filtered deviation (−9 to +3.5 dB seen). <b>QoS</b>: positioning PQI 58 → 5QI 7, LiDAR PQI 59 → 5QI 9, carried as DSCP and served by a priority queue on every interface that sends relayed traffic (OAI has one QoS flow per session, so no separate flows on Uu). <b>Remote UE Report over NAS</b>: the relay&#x27;s UE sends it in UL NAS transport on its relay PDU session; a patched free5GC SMF decodes it, tracks each relay&#x27;s remote UEs and answers: 50 reports, 50 responses, about 0.1–0.2 s each. In 15 minutes: 2 of 16,256 uplink messages lost, both on an AGV&#x27;s own UE, none through a relay; 25 over 1 s; link set-up 683 ms median (26 links, 530–932 ms), no MAC failures; relayed positioning 209 · 346 ms, the same as without security. A run between the two (security and measured links, no QoS) gave the same numbers; one recorded while the SMF and the UE were being rebuilt on the same PCs is not used.']


def cover_metrics(tr):
    t0, t1 = tr["pos"][0][0], tr["pos"][-1][0]
    M = [m for m in tr["msgs"] if m[2] != 2]
    red = [m for m in M if m[3] == 2]
    st, last, off = {}, t0, [0.0, 0.0, 0.0, 0.0]          # seconds with 0..3 AGVs off the gNB (red)
    for t, a, c, *_ in sorted(tr["leds"]) + [[t1, -1, None]]:
        t = max(t0, min(t, t1))
        if len(st) == 3:
            off[sum(v == 2 for v in st.values())] += t - last
        last = t
        if a >= 0:
            st[a] = c
    H = tr.get("holds", [])
    hd = sorted(next((x[0] for x in H if x[1] == h[1] and x[2] == 0 and x[0] > h[0]), t1) - h[0] for h in H if h[2] == 1)
    dist = sum(math.dist(a[0][0][:2], b[0][0][:2]) for a, b in zip([p[1:] for p in tr["pos"]], [p[1:] for p in tr["pos"][1:]]))
    ups = sorted(a[2] - a[0] for a in tr.get("att", []) if a[2] is not None)
    rs = tr["summary"].get("remote|det") or {}
    return dict(mins=(t1 - t0) / 60, laps_h=dist / LOOP_M / ((t1 - t0) / 3600), share=100 * len(red) / max(1, len(M)),
                max_off=max(k for k in range(4) if off[k] > 0.5), off2=off[2] + off[3], off3=off[3], holds=len(hd),
                hold_med=hd[len(hd) // 2] if hd else None, lost=sum(m[4] is None for m in M), n=len(M),
                late=sum(m[4] is not None and m[4] >= 1000 for m in M), rlf=len(tr.get("rlf", [])), att_med=ups[len(ups) // 2] if ups else None,
                sl_p50=rs.get("p50"), sl_p90=rs.get("p90"))


def compare_table(runs):
    rows = []
    for key, name, w, pick in DUAL:
        tr = runs.get(key)
        if not tr or name not in COMPARE:
            continue
        what, drive, relay, hold, q = COMPARE[name]
        m = cover_metrics(tr)
        bad = lambda c: ' class="bad"' if c else ""
        rows.append(
            f'<tr><td><b>{html.escape(what)}</b>{" · live" if not rows else ""}<div class="hint">{html.escape(drive)}; relay: {html.escape(relay)}; '
            f'holds: {html.escape(hold)}; uplink while attaching: {q}</div></td>'
            f'<td>{m["mins"]:.0f} min<div class="hint">{m["laps_h"]:.0f} laps/h</div></td>'
            f'<td>{m["share"]:.0f} %</td>'
            f'<td{bad(m["max_off"] >= 2)}>{m["max_off"]}<div class="hint">{m["off2"]:.0f} s with 2+, {m["off3"]:.0f} s all 3</div></td>'
            f'<td>{m["holds"] or "–"}' + (f'<div class="hint">median {m["hold_med"]:.0f} s</div>' if m["hold_med"] is not None else "") + '</td>'
            f'<td{bad(m["lost"] > 0.001 * m["n"])}>{m["lost"]:,}<div class="hint">{100 * m["lost"] / max(1, m["n"]):.2f} % of {m["n"]:,}</div></td>'
            f'<td>{m["late"]}</td>'
            f'<td>{m["sl_p50"]:.0f} · {m["sl_p90"]:.0f} ms</td></tr>')
    rows.reverse()                                         # oldest first, the live scenario last
    return ('<div class="scroll"><table class="cmp"><thead><tr><th>Step (each run on the boards, L1, 25 ms windows)</th><th>Run</th><th>Uplink over the sidelink</th>'
            '<th>Most AGVs off the gNB at once</th><th>Holds</th><th>Uplink lost</th><th>Over 1 s</th><th>Sidelink positioning p50 · p90</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>')


def grab_fn(src, start):
    """The source of a top-level function or class starting at `start` (brace matched)."""
    i = src.index(start)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise ValueError(start)


def positioning(page):
    """'pose' was a loose name for the det flow: each message carries the AGV's position."""
    for x, y in (("Gateway 1 / pivot pose", "Gateway 1 / pivot positioning"), ("Gateway 2 / relay pose", "Gateway 2 / relay positioning"),
                 ("makes a pose message", "makes a positioning message"), ("1 pose made", "1 positioning message made"),
                 ("measured: pose ", "measured: positioning "), ("a remote pose up", "a remote positioning message up"), ("<dt>pose</dt>", "<dt>positioning</dt>")):
        page = page.replace(x, y)
    page = re.sub(r"\bposes\b", "positioning messages", page)
    page = re.sub(r"\bPoses\b", "Positioning messages", page)
    page = re.sub(r"\bpose\b", "positioning", page)
    return re.sub(r"\bPose\b", "Positioning", page)


def main():
    runs = load_runs()
    rnd = lambda o: round(o, 3) if isinstance(o, float) else [rnd(x) for x in o] if isinstance(o, list) else {k: rnd(v) for k, v in o.items()} if isinstance(o, dict) else o
    for k, tr in enumerate(runs.values()):                     # headings to 0.01 rad; positions at 0.2 s but in the two newest runs
        if k >= 1:
            tr["pos"] = tr["pos"][::2]
        for p in tr["pos"]:
            for a in p[1]:
                if len(a) > 2 and isinstance(a[2], float):
                    a[2] = round(a[2], 2)
    json.dump(rnd(runs), open(f"{P}/agv-dual-runs.json", "w"), separators=(",", ":"))   # ms / mm: keeps the file under 16 MB
    style = chain[chain.index("<style>"):chain.index("</style>")]
    head = '<meta charset="utf-8">\n' + chain[:chain.index("<style>")].replace("<title>AGV Relay Chain</title>", "<title>AGV Dual Gateway</title>")
    js = chain[chain.index("<script>") + 8:]
    consts = "\n".join(l for l in js.splitlines() if re.match(r"const (\$|W|RACKS|ROUTE|CPV|CPN|POLE|AGV|RCOL|FLOWN) ", l))
    lib = "\n".join([
        grab_fn(js, "function pct("), "const fmt = v => v == null ? \"–\" : v >= 1000 ? (v / 1000).toFixed(1) + \" s\" : Math.round(v) + \"\";",
        "let COL = {};", grab_fn(js, "function readCols("), grab_fn(js, "function fit("),
        grab_fn(js, "function drawTimeline("), grab_fn(js, "function drawScatter("),
        grab_fn(js, "function lo("), grab_fn(js, "class Replay ")])
    findings = open(f"{S}/dual_findings.html", encoding="utf-8").read() if os.path.exists(f"{S}/dual_findings.html") else ""
    page = TEMPLATE.replace("%HEAD%", head).replace("%STYLE%", style + EXTRA_CSS).replace("%RESULTS%", results_table(runs)) \
        .replace("%FINDINGS%", findings).replace("%COVERTABLE%", cover_table(runs)).replace("%COMPARE%", compare_table(runs)).replace("%CMPNOTES%", "".join(f"<li>{n}</li>" for n in CMPNOTES)).replace("%CONSTS%", consts).replace("%LIB%", lib).replace("%SVG%", SVG)
    l3 = next((runs[k] for k, n, *_ in DUAL if n == "cover_l3e_L1_w25" and k in runs), None)
    if l3:
        sm = l3["summary"]
        f = lambda k, q: (sm.get(k) or {}).get(q)
        meas = (f"L1, measured through the relay (p50 · p90): positioning {f('remote|det', 'p50'):.0f} · {f('remote|det', 'p90'):.0f} ms, "
                f"LiDAR {f('remote|lidar', 'p50'):.0f} · {f('remote|lidar', 'p90'):.0f} ms, command {f('remote|cmd', 'p50'):.0f} · {f('remote|cmd', 'p90'):.0f} ms")
    else:
        meas = "L1, measured through the relay: run not recorded yet"
    page = page.replace("%SEQMEAS%", meas)
    page = positioning(page)
    open(f"{P}/agv-dual-gateway.html", "w", encoding="utf-8").write(page)
    print(f"agv-dual-gateway.html {len(page)} bytes, runs: {list(runs)}")


EXTRA_CSS = """
.cvsvg { width: 100%; min-width: 720px; height: auto; display: block; }
.cvsvg .lb { fill: var(--ink-3); font: 11.5px var(--mono); }
.cvsvg .t { fill: var(--ink); font: 600 14px var(--display); }
.cvsvg .grid { stroke: var(--line); stroke-width: 1; }
.cvsvg .sig { fill: none; stroke: var(--uu); stroke-width: 2.4; stroke-linejoin: round; }
.cvsvg .cold { fill: color-mix(in srgb, var(--rremote) 13%, transparent); }
.cvsvg .reatt { fill: color-mix(in srgb, var(--pc5) 13%, transparent); }
.cvsvg .thr { stroke: var(--pc5); stroke-width: 1.4; stroke-dasharray: 6 4; } .cvsvg .thrt { fill: var(--pc5); }
.cvsvg .thr2 { stroke: var(--rremote); stroke-width: 1.4; stroke-dasharray: 6 4; } .cvsvg .thr2t { fill: var(--rremote); }
.cvsvg .f0 { fill: var(--uu); } .cvsvg .f1 { fill: var(--core); } .cvsvg .f2 { fill: var(--pc5); }
.flow3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 900px) { .flow3 { grid-template-columns: 1fr; } }
.flowp { border: 1px solid var(--line); border-radius: 6px; padding: 10px 10px 6px; background: var(--panel); min-width: 0; }
.flowp h3 { font: 600 1rem var(--display); margin: 0; }
.flowp .fs { font: .78rem var(--mono); color: var(--ink-3); margin: 2px 0 6px; }
.flowp .fq { font: .8rem var(--mono); color: var(--ink-2); margin: 4px 0 0; }
.flowp canvas { width: 100%; height: 190px; display: block; }
#replay .charts { grid-template-columns: 1fr; }
.bd > * { min-width: 0; }
.net .diagram { overflow-x: auto; padding: 6px 4px 2px; border-bottom: 1px solid var(--line); }
.net svg { display: block; width: 100%; min-width: 1180px; height: auto; }
.net .side { padding: 12px 14px; display: grid; grid-template-columns: minmax(220px, 320px) minmax(0, 1fr); align-items: start; gap: 10px 28px; }
@media (max-width: 720px) { .net .side { grid-template-columns: 1fr; } }
.legend { display: grid; gap: 7px; font-size: .84rem; color: var(--ink-2); }
.legend i { display: inline-block; width: 22px; height: 0; border-top: 3px solid; vertical-align: middle; margin-right: 7px; }
.legend b { font-weight: 600; color: var(--ink); }
.detail h3 { font-size: 1.05rem; margin: 2px 0 0; }
.detail .label { font-size: .72rem; letter-spacing: .09em; text-transform: uppercase; color: var(--ink-3); }
.detail dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 8px 0 0; font-size: .88rem; }
.detail dt { color: var(--ink-3); }
.detail dd { margin: 0; font-family: var(--mono); font-size: .82rem; overflow-wrap: anywhere; }
.detail p { margin: 8px 0 0; color: var(--ink-2); font-size: .92rem; max-width: 75ch; }
#arch3 .lanehd { font-family: var(--display); font-size: 14px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
#arch3 .lanesub { font-family: var(--mono); font-size: 10.5px; fill: var(--ink-3); }
#arch3 .pcframe { fill: none; stroke: var(--ink-3); stroke-width: 1.5; stroke-dasharray: 8 5; }
#arch3 .pctitle { font-family: var(--display); font-size: 15px; font-weight: 700; letter-spacing: .06em; fill: var(--ink); }
#arch3 .pcsub { font-family: var(--mono); font-size: 11px; fill: var(--ink-3); }
#arch3 .node { cursor: pointer; }
#arch3 .node rect.box { fill: var(--panel); stroke: var(--line); stroke-width: 1.2; }
#arch3 .node.stopped rect.box { stroke-dasharray: 5 4; }
#arch3 .node.stopped text { opacity: .55; }
#arch3 .node:hover rect.box, #arch3 .node.sel rect.box, #arch3 .node:focus-visible rect.box { stroke: var(--ink); stroke-width: 2.2; stroke-dasharray: none; }
#arch3 .node:focus, #arch3 .link:focus { outline: none; }
#arch3 .n1 { font-family: var(--display); font-size: 15px; font-weight: 600; fill: var(--ink); }
#arch3 .n2 { font-family: var(--mono); font-size: 10.5px; fill: var(--ink-2); }
#arch3 .n3 { font-family: var(--mono); font-size: 10.5px; fill: var(--ink-3); }
#arch3 .link { cursor: pointer; }
#arch3 .link .hit { stroke: transparent; stroke-width: 16; fill: none; }
#arch3 .link .base { fill: none; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }
#arch3 .link.ctl .base { stroke-width: 1.6; stroke: var(--ink-3); }
#arch3 .link.idle .base { stroke-width: 1.8; stroke-dasharray: 5 5; opacity: .6; }
#arch3 .link .flow { fill: none; stroke-width: 3; stroke: var(--panel); stroke-dasharray: 2 14; stroke-linecap: round; animation: aflow 2.4s linear infinite; opacity: .9; }
#arch3 .link:not(.up) .flow { display: none; }
#arch3 .link:hover .base, #arch3 .link.sel .base, #arch3 .link:focus-visible .base { stroke: var(--ink); opacity: 1; }
@keyframes aflow { to { stroke-dashoffset: -64; } }
#arch3 .ltxt { font-family: var(--mono); font-size: 11px; fill: var(--ink-2); paint-order: stroke; stroke: var(--panel-2); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; }
#arch3 .led { stroke: var(--panel); stroke-width: 1.5; }
#arch3 .led.blink { animation: ablink .8s steps(1) infinite; }
@keyframes ablink { 50% { opacity: .15; } }
@media (prefers-reduced-motion: reduce) { #arch3 .link .flow, #arch3 .led.blink { animation: none; } }
.tabs { display: flex; gap: 4px; padding: 12px 14px 0; border-bottom: 1px solid var(--line); overflow-x: auto; }
.tabs [role="tab"] { font: 600 .98rem var(--display); color: var(--ink-2); background: transparent; border: 1px solid transparent; border-bottom: 0; border-radius: 6px 6px 0 0; padding: 8px 16px 7px; margin-bottom: -1px; cursor: pointer; white-space: nowrap; }
.tabs [role="tab"]:hover { color: var(--ink); background: var(--panel-2); }
.tabs [role="tab"][aria-selected="true"] { color: var(--ink); background: var(--panel); border-color: var(--line); border-bottom: 1px solid var(--panel); box-shadow: inset 0 3px 0 var(--c); }
.tabs [role="tab"]:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
.slotsvg { width: 100%; min-width: 760px; height: auto; display: block; }
.slotsvg .t { fill: var(--ink); font: 600 14px var(--display); }
.slotsvg .lb { fill: var(--ink-3); font: 11.5px var(--mono); }
.slotsvg .sl { fill: #fff; font: 600 11px var(--mono); pointer-events: none; }
.slotsvg .sem + .sl { fill: var(--ink-2); }
.slotsvg .sdl { fill: var(--uu); } .slotsvg .sem { fill: var(--line); } .slotsvg .sS { fill: color-mix(in srgb, var(--uu) 55%, var(--panel)); }
.slotsvg .sU { fill: color-mix(in srgb, var(--uu) 75%, #000); } .slotsvg .sW, .slotsvg .sP { fill: var(--pc5); }
.slotsvg .sB { fill: var(--rpivot); } .slotsvg .sG { fill: var(--rrelay); } .slotsvg .sR { fill: var(--rremote); }
.slotsvg .sF { fill: color-mix(in srgb, var(--pc5) 25%, var(--panel)); }
.slotsvg .sF + .sl { fill: var(--ink-2); }
.slotsvg .br { fill: none; stroke: var(--ink-3); stroke-width: 1.2; }
.arch2 svg { width: 100%; min-width: 760px; height: auto; display: block; }
.arch2 .bx { fill: var(--panel-2); stroke: var(--line); stroke-width: 1.4; }
.arch2 .t { fill: var(--ink); font: 600 15px var(--display); }
.arch2 .s { fill: var(--ink-2); font: 12.5px var(--mono); }
.arch2 .uu { stroke: var(--uu); stroke-width: 3.2; fill: none; }
.arch2 .pc5 { stroke: var(--pc5); stroke-width: 3.2; fill: none; }
.arch2 .pc5.alt { stroke-dasharray: 7 6; stroke-width: 2.2; }
.arch2 .bcn { stroke: var(--ink-3); stroke-width: 1.4; stroke-dasharray: 2 5; fill: none; }
.arch2 .lu { fill: var(--uu); font: 600 12.5px var(--mono); }
.arch2 .lp { fill: var(--pc5); font: 600 12.5px var(--mono); }
.arch2 .lb { fill: var(--ink-3); font: 12px var(--mono); }
.arch2 .ah-uu { fill: var(--uu); } .arch2 .ah-pc5 { fill: var(--pc5); }
tr.sub td { color: var(--ink-2); border-top: 0; }
table td.bad, table tr.sub td.bad { color: var(--bad); }
#run { max-width: 100%; min-width: 0; }
.ctl label { max-width: 100%; min-width: 0; }
.cmpt td, .cmpt th { white-space: normal; }
.steps2 { margin: 0; padding-left: 20px; display: grid; gap: 6px; max-width: 110ch; }
"""

SVG = """<svg viewBox="0 0 1000 340" role="img" aria-label="Two AGVs attached to the gNB as gateways, the remote one sidelink hop from either; it picks a gateway per message">
  <defs>
    <marker id="d-uu" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah-uu"/></marker>
    <marker id="d-pc5" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah-pc5"/></marker>
  </defs>
  <rect x="20" y="100" width="200" height="140" rx="6" class="bx" style="stroke:var(--uu);stroke-width:2.2"/>
  <text x="36" y="128" class="t">gNB on the pole</text>
  <text x="36" y="152" class="s">OAI gNB (VNF + PNF)</text>
  <text x="36" y="172" class="s">gNB board, core PC</text>
  <text x="36" y="192" class="s">free5GC, UPF</text>
  <text x="36" y="212" class="s">edge server</text>
  <rect x="420" y="20" width="230" height="110" rx="6" class="bx" style="stroke:var(--rpivot);stroke-width:2.4"/>
  <text x="436" y="48" class="t">Gateway · LED blue</text>
  <text x="436" y="72" class="s">serving the remote now</text>
  <text x="436" y="92" class="s">its UE attached, own session</text>
  <text x="436" y="112" class="s">its data + the remote&#x27;s</text>
  <rect x="420" y="210" width="230" height="110" rx="6" class="bx" style="stroke:var(--rrelay);stroke-width:2.4"/>
  <text x="436" y="238" class="t">Gateway · LED green</text>
  <text x="436" y="262" class="s">NR only, standing by</text>
  <text x="436" y="282" class="s">its UE attached, own session</text>
  <text x="436" y="302" class="s">sends its own data</text>
  <rect x="780" y="115" width="200" height="110" rx="6" class="bx" style="stroke:var(--rremote);stroke-width:2.4"/>
  <text x="796" y="143" class="t">Remote · LED red</text>
  <text x="796" y="167" class="s">no UE attached</text>
  <text x="796" y="187" class="s">one hop to either</text>
  <text x="796" y="207" class="s">picks per message</text>
  <path d="M418 75 L224 140" class="uu" marker-end="url(#d-uu)" marker-start="url(#d-uu)"/>
  <path d="M418 265 L224 200" class="uu" marker-end="url(#d-uu)" marker-start="url(#d-uu)"/>
  <text x="236" y="70" class="lu">Uu · its own nrUE</text>
  <text x="236" y="284" class="lu">Uu · its own nrUE</text>
  <path d="M778 150 L654 92" class="pc5" marker-end="url(#d-pc5)" marker-start="url(#d-pc5)"/>
  <path d="M778 190 L654 250" class="pc5 alt" marker-end="url(#d-pc5)" marker-start="url(#d-pc5)"/>
  <text x="690" y="96" class="lp">PC5</text>
  <text x="690" y="262" class="lp">PC5</text>
  <path d="M535 132 L535 208" class="bcn"/>
  <text x="545" y="166" class="lb">beacons every 0.5 s:</text>
  <text x="545" y="184" class="lb">position, session up, bytes sent</text>
  <text x="780" y="60" class="lb">solid: the gateway the</text>
  <text x="780" y="78" class="lb">remote sends through now</text>
  <text x="780" y="262" class="lb">commands come back</text>
  <text x="780" y="280" class="lb">through the gateway its</text>
  <text x="780" y="298" class="lb">last message went up</text>
</svg>"""

TEMPLATE = r"""%HEAD%%STYLE%</style>
<main class="wrap">
  <header class="top">
    <div class="title">
      <h1>AGV Dual Gateway</h1>
      <div class="sub">Three AGVs, one gNB on a pole. The two AGVs nearest the pole are both connected to the gNB: each is a <b>gateway</b> with its own UE and PDU session, and sends its own data straight up. The third, the <b>remote</b>, is one sidelink hop from either gateway and chooses, for each message, which one to send through. A variant of the <a href="https://claude.ai/artifact/HmRQJySnJCGZ5MnJPLWGWs">AGV relay chain</a>, run on the same four boards. The live scenario is now <a href="#coverage">coverage</a>: an AGV in the shielded cold store reaches the edge through another AGV acting as a <b>layer-3 UE-to-network relay</b>, as in 3GPP TS 23.304: it discovers the relay, sets up a PC5 direct link, gets an IP address from it, and its IP packets are routed and NATed into the relay&#x27;s PDU session.</div>
    </div>
    <span class="pill"><span class="dot"></span>board runs 12–19 Nov 2026 · OAI gNB and nrUE1–3, OAI NR sidelink (mode 2), free5GC · layer-3 relay since 19 Nov</span>
  </header>

  <section class="card" aria-label="Architecture">
    <div class="hd"><h2>Two gateways, one remote</h2><span class="meta">blue Uu (NR to the gNB) · amber PC5 (sidelink)</span></div>
    <div class="bd scroll arch2">%SVG%</div>
    <div class="bd" style="border-top:1px solid var(--line)">
      <span class="label">A remote pose, step by step (the dual-gateway runs: relayed by the application)</span>
      <ol class="steps2">
        <li>The remote AGV's computer makes a pose message (position, detections, timestamp).</li>
        <li>It looks at the last beacons it heard from the two gateways. Each beacon carries the gateway's position, whether its PDU session is up and settled (8 s after the attach), and how many bytes it sent up since its last beacon.</li>
        <li>It picks a gateway from them. In the recorded runs the rule is &quot;load&quot;: the gateway that sent less, decided again at every beacon, so the remote&#x27;s traffic alternates between the two. The live scenario uses &quot;sticky&quot;: the remote stays on its gateway at least 5 s and moves only when the other one&#x27;s beacon shows it sent under 60 % as much, so the gateway serving it changes every few seconds at most. A gateway whose session is not settled is not picked; if neither is, it uses the one whose session is up.</li>
        <li>The message crosses one PC5 hop to that gateway, over OAI's NR sidelink: the remote's sidelink UE for that pair, inside the gNB's sidelink window, then the gateway's sidelink UE.</li>
        <li>The gateway's computer hands it to its own nrUE, which sends it up in its own uplink grants, beside the gateway's own messages.</li>
        <li>gNB, UPF, edge server: the edge logs the arrival time and the address it came from.</li>
        <li>The edge sends each AGV's commands to the address its last message came from, so a command for the remote comes down through the gateway it last used, which passes it over their PC5 link.</li>
      </ol>
      <p class="hint" style="max-width:110ch;margin:10px 0 0">In these runs the gateways relay at the application: the gateway&#x27;s program receives the remote&#x27;s message and sends it again as its own. The coverage scenario now relays at layer 3 instead (<code>--relay l3</code>, see <a href="#sequences">Packet sequence</a>): the remote sends ordinary IP packets to the edge from an address its relay gave it, and the relay&#x27;s kernel routes and NATs them into its PDU session without the program seeing them.</p>
    </div>
    <div class="bd scroll" style="border-top:1px solid var(--line)">
      <span class="label">Against the relay chain</span>
      <table class="cmpt">
        <thead><tr><th></th><th>Relay chain (option 3)</th><th>Dual gateway</th></tr></thead>
        <tbody>
          <tr><td>UEs attached</td><td>pivot's and relay's; only the pivot's carries traffic, the relay's waits</td><td>both gateways', both carry traffic</td></tr>
          <tr><td>The farthest AGV's uplink</td><td>two PC5 hops: remote → relay → pivot</td><td>one PC5 hop, to the gateway it picks</td></tr>
          <tr><td>The middle AGV's uplink</td><td>one PC5 hop to the pivot</td><td>its own UE, no hop</td></tr>
          <tr><td>Downlink commands</td><td>all through the pivot's UE, then down the chain</td><td>through the UE the AGV's last message used</td></tr>
          <tr><td>Role change</td><td>the new pivot's UE attaches while the old one carries on</td><td>the new gateway's UE attaches while the old gateway, now remote, keeps its session until both gateways are settled</td></tr>
          <tr><td>How a hop is relayed</td><td>by the application, one hop at a time</td><td>by the application in these runs; at layer 3 (IP routing and NAT) in the live coverage scenario</td></tr>
          <tr><td>LEDs</td><td>pivot blue · relay green · remote red</td><td>red the remote · blue the gateway the remote sends through now (NR and sidelink) · green the other gateway (NR only); blinking while that AGV&#x27;s UE attaches</td></tr>
        </tbody>
      </table>
    </div>
  </section>



  <section class="card net" aria-label="Architecture" id="arch">
    <div class="hd"><h2>Architecture</h2><span class="meta" id="archnow">follows the replay below · click any box or link</span></div>
    <div class="diagram"><svg id="arch3" role="img" aria-label="Architecture: free5GC, OpenAirInterface, host software and the NXP boards across the core PC and the UE PC, coloured by each AGV's LED in the replay"></svg></div>
    <div class="side">
      <div class="legend">
        <span><i style="border-color:var(--rpivot)"></i><b>blue</b> relaying another AGV (coverage) · the gateway serving the remote (dual runs): NR and sidelink</span>
        <span><i style="border-color:var(--rrelay)"></i><b>green</b> its own UE only: NR only</span>
        <span><i style="border-color:var(--rremote)"></i><b>red</b> over the sidelink, through the blue AGV</span>
        <span><i style="border-color:var(--ink-2)"></i>shared: core, gNB, edge</span>
        <span><i style="border-color:var(--ink-3);border-top-width:2px;border-top-style:dashed"></i>idle: control, beacons only, or a stopped UE</span>
        <span class="hint">Moving dots mark links carrying data at the replay&#x27;s time. The LEDs are the boards&#x27;, as the controller set them; a board blinks while its UE attaches. The gNB board&#x27;s LED follows the slot scheme instead: it flashes green for each stretch of NR slots and blue for each sidelink window.</span>
      </div>
      <div class="detail" id="adetail" aria-live="polite"></div>
    </div>
  </section>

  <section class="card" aria-label="Packet sequence and slot scheme" id="sequences">
    <div class="hd"><h2>Packet sequence and slot scheme</h2><span class="meta">a remote positioning message and a command, through a layer-3 relay · how NR and the sidelink share the air</span></div>
    <div class="tabs" role="tablist" aria-label="View">
      <button type="button" role="tab" id="tab-seq" aria-controls="opt-seq" aria-selected="true" style="--c:var(--rremote)">Packet sequence · layer-3 relay</button>
      <button type="button" role="tab" id="tab-app" aria-controls="opt-app" aria-selected="false" tabindex="-1" style="--c:var(--rpivot)">Application relay (dual runs)</button>
      <button type="button" role="tab" id="tab-slot" aria-controls="opt-slot" aria-selected="false" tabindex="-1" style="--c:var(--pc5)">Slot scheme</button>
    </div>
    <div role="tabpanel" id="opt-seq" aria-labelledby="tab-seq" tabindex="0">
      <div class="bd scroll arch2"><svg viewBox="0 0 1000 760" role="img" aria-label="Sequence of the layer-3 relay with 3GPP procedures: discovery under configured thresholds, PC5-S direct link set-up with security mode, DHCPv4 from the relay, the Remote UE Report up the relay PDU session, then a remote positioning message as IP through the relay's kernel, GRE tunnel and NAT into the relay PDU session, and a command back">
  <g class="lb" text-anchor="middle">
    <text x="80" y="22">Remote AGV</text><text x="80" y="38">app + SL UE</text>
    <text x="280" y="22">Relay&#x27;s SL UE</text><text x="280" y="38">routing, DHCPv4</text>
    <text x="480" y="22">Relay&#x27;s nrUE</text><text x="480" y="38">GRE, NAT, 2 sessions</text>
    <text x="680" y="22">gNB</text><text x="680" y="38">board, PNF, VNF</text>
    <text x="890" y="22">AMF, SMF, UPF, edge</text><text x="890" y="38">core PC</text>
  </g>
  <path d="M80 48 V750 M280 48 V750 M480 48 V750 M680 48 V750 M890 48 V750" class="bcn"/>
  <text x="90" y="64" class="s">A · once per link, in the windows · relay: Uu in [−95, −31] dBm and relay session up</text>
  <path d="M280 88 L90 88" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="120" y="82" class="lp">announce (Model A, RSC)</text>
  <text x="96" y="108" class="lb">Uu &lt; −100 dBm: pick SD-RSRP &gt; −90 dBm</text>
  <path d="M80 130 L270 130" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="96" y="124" class="lp">DL ESTABLISHMENT REQ</text>
  <path d="M280 158 L90 158" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="120" y="152" class="lp">SECURITY MODE CMD + MAC</text>
  <path d="M80 186 L270 186" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="96" y="180" class="lp">SECURITY MODE COMPL + MAC</text>
  <path d="M280 214 L90 214" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="120" y="208" class="lp">ACCEPT: IP cfg DHCPv4</text>
  <path d="M80 246 L270 246" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="96" y="240" class="lp">DHCP DISCOVER (in ESP)</text>
  <path d="M280 270 L90 270" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="120" y="264" class="lp">OFFER</text>
  <path d="M80 294 L270 294" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="96" y="288" class="lp">REQUEST</text>
  <path d="M280 318 L90 318" class="pc5 alt" marker-end="url(#d-pc5)"/>
  <text x="120" y="312" class="lp">ACK: 10.40.x.2</text>
  <path d="M280 338 L470 338" class="bcn" marker-end="url(#d-uu)"/>
  <path d="M480 356 L880 356" class="uu" style="stroke-dasharray:7 6;stroke-width:2.2" marker-end="url(#d-uu)"/>
  <text x="490" y="350" class="lu">Remote UE Report (NAS): id, 10.40.x.2</text>
  <text x="490" y="374" class="lb">UL NAS transport, relay session; SMF answers</text>
  <text x="90" y="408" class="s">B · every message: IP in ESP, the relay&#x27;s program never sees it</text>
  <text x="96" y="432" class="s">1 positioning: UDP 10.40.x.2 → edge, DSCP 34 (PQI 58)</text>
  <path d="M80 460 L270 460" class="pc5" marker-end="url(#d-pc5)"/>
  <text x="96" y="452" class="lp">2 ESP over PC5</text>
  <path d="M280 494 L470 494" class="bcn" marker-end="url(#d-uu)"/>
  <text x="290" y="486" class="lb">3 ESP off, iif oaitun → GRE</text>
  <path d="M480 528 L670 528" class="uu" marker-end="url(#d-uu)"/>
  <text x="490" y="520" class="lu">4 NAT to 12.1.2.x, 5QI 7 queue</text>
  <path d="M680 562 L880 562" class="uu" marker-end="url(#d-uu)"/>
  <text x="690" y="554" class="lu">5 GTP-U, UPF, edge</text>
  <path d="M890 604 L690 604" class="uu" marker-end="url(#d-uu)"/>
  <text x="712" y="596" class="lu">6 cmd to 12.1.2.x:port</text>
  <path d="M680 638 L490 638" class="uu" marker-end="url(#d-uu)"/>
  <text x="520" y="630" class="lu">7 Uu downlink</text>
  <path d="M480 672 L290 672" class="bcn" marker-end="url(#d-uu)"/>
  <text x="306" y="664" class="lb">8 un-NAT, GRE back</text>
  <path d="M280 706 L90 706" class="pc5" marker-end="url(#d-pc5)"/>
  <text x="120" y="698" class="lp">9 PC5 (ESP), next window</text>
  <text x="90" y="732" class="s">%SEQMEAS%</text>
  <text x="90" y="752" class="lb">dashed: signalling · solid: data · dotted: inside the relay AGV (no radio)</text>
</svg></div>
      <div class="bd" style="border-top:1px solid var(--line)"><p class="hint" style="max-width:110ch;margin:0">As a layer-3 UE-to-network relay in TS 23.304, with the procedures of TS 24.554 and the thresholds of TS 38.304 / 38.331. <b>A</b>: the thresholds are configured as SIB12 would carry them (field names of SL-RelayUE-Config, SL-RemoteUE-Config, SL-ReselectionConfig). An AGV whose Uu RSRP is between threshLowRelay (−95 dBm) and threshHighRelay (−31 dBm), whose session has settled and whose <b>relay PDU session</b> (DNN relay, its second session) is up, sends a Model A discovery announcement with the relay service code on each PC5 link every 0.5 s. An AGV whose Uu falls below threshHighRemote (−100 dBm) picks the strongest announcement above sl-RSRP-Thresh (−90 dBm), keeping its relay unless another is 2 dB stronger, and sets up the link with PC5-S: request, security mode command and complete (null algorithms), accept with &quot;IP address configuration: DHCPv4 server&quot;. It then asks the relay&#x27;s DHCPv4 server for an address from that relay&#x27;s pool, and the relay sends a Remote UE Report with it up the relay PDU session. When the remote has its own session again it releases the address and the link (DHCPRELEASE, DIRECT LINK RELEASE REQUEST / ACCEPT), and the relay reports that too. <b>B</b>: the remote&#x27;s app sends ordinary UDP to the edge. Inside the relay AGV a policy route sends what arrives on the sidelink through a GRE tunnel to the nrUE&#x27;s namespace, which masquerades it into the relay PDU session (12.1.2.0/24), apart from the relay&#x27;s own traffic (12.1.1.0/24). Since then: the security mode command picks real algorithms, the keys come from both sides&#x27; nonces (after TS 33.503) and every PC5-S message from it on carries a MAC; the remote&#x27;s traffic and DHCP go in ESP (AES-GCM) over the sidelink, its DSCP copied to the ESP header, so each flow keeps its QoS class (positioning PQI 58 → 5QI 7, LiDAR PQI 59 → 5QI 9) to the relay session&#x27;s priority queue; the SD-RSRP adds each link&#x27;s measured deviation to the path-loss model; and the Remote UE Report goes as NAS to the SMF, which answers it (a copy still goes to the edge for this page). Emulated: the PC5 discovery and PC5-S messages and DHCP go as UDP over the sidelink IP (OAI&#x27;s sidelink carries only IP); the PC5-S information elements are tag-length-value; the PC5 root key is generated per run, not provisioned; ESP stands in for PDCP ciphering on PC5; the distance part of the SD-RSRP is modelled, since the boards share a desk.</p></div>
    </div>
    <div role="tabpanel" id="opt-app" aria-labelledby="tab-app" tabindex="0" hidden>
      <div class="bd scroll arch2"><svg viewBox="0 0 1000 470" role="img" aria-label="Sequence of a remote pose up through the gateway serving it, and of a command back">
  <g class="lb" text-anchor="middle">
    <text x="90" y="22">Remote</text><text x="90" y="38">computer + SL UE</text>
    <text x="290" y="22">Gateway (blue)</text><text x="290" y="38">SL UE + computer</text>
    <text x="490" y="22">Gateway&#x27;s nrUE</text><text x="490" y="38">its board</text>
    <text x="690" y="22">gNB</text><text x="690" y="38">board, PNF, VNF</text>
    <text x="890" y="22">UPF, edge</text><text x="890" y="38">core PC</text>
  </g>
  <path d="M90 48 V460 M290 48 V460 M490 48 V460 M690 48 V460 M890 48 V460" class="bcn"/>
  <path d="M90 70 L280 70" class="bcn" marker-end="url(#d-pc5)"/>
  <text x="100" y="62" class="lb">beacons</text>
  <path d="M290 84 L100 84" class="bcn" marker-end="url(#d-pc5)"/>
  <text x="100" y="112" class="s">1 pose made; pick the gateway</text>
  <path d="M90 140 L280 140" class="pc5" marker-end="url(#d-pc5)"/>
  <text x="100" y="132" class="lp">2 PC5 hop, in a window</text>
  <path d="M290 178 L480 178" class="bcn" marker-end="url(#d-uu)"/>
  <text x="300" y="170" class="lb">3 to its own nrUE (fwd)</text>
  <path d="M490 216 L680 216" class="uu" marker-end="url(#d-uu)"/>
  <text x="500" y="208" class="lu">4 Uu, its own grants</text>
  <path d="M690 254 L880 254" class="uu" marker-end="url(#d-uu)"/>
  <text x="700" y="246" class="lu">5 GTP-U, UPF, edge</text>
  <path d="M890 300 L700 300" class="uu" marker-end="url(#d-uu)"/>
  <text x="720" y="292" class="lu">6 cmd to that UE&#x27;s IP</text>
  <path d="M690 338 L500 338" class="uu" marker-end="url(#d-uu)"/>
  <text x="520" y="330" class="lu">7 Uu downlink</text>
  <path d="M490 376 L300 376" class="bcn" marker-end="url(#d-uu)"/>
  <text x="320" y="368" class="lb">8 fwd, tagged by UE</text>
  <path d="M290 414 L100 414" class="pc5" marker-end="url(#d-pc5)"/>
  <text x="120" y="406" class="lp">9 PC5, next window</text>
  <text x="100" y="448" class="s">dual runs, L1, measured: pose 205 ms, command 119 ms (p50)</text>
</svg></div>
    </div>
    <div role="tabpanel" id="opt-slot" aria-labelledby="tab-slot" tabindex="0" hidden>
      <div class="bd scroll"><svg class="slotsvg" viewBox="0 0 1000 389" role="img" aria-label="Slot scheme: one TDD frame of NR slots on nrlink, then a 25 ms sidelink window, with the gNB board&#x27;s LED; the window in detail; and the cycle with 25 and 50 ms windows">
<text x="0" y="16" class="t">One cycle, 25 ms window (idle slot times as measured on nrlink)</text>
<text x="0" y="52" class="lb">NR</text>
<rect x="70.0" y="30" width="24.9" height="34" class="sdl"><title>slot 0: downlink with content, about 2.7 ms</title></rect>
<text x="83.0" y="52" class="sl" text-anchor="middle">D0</text>
<rect x="95.9" y="30" width="5.7" height="34" class="sem"><title>slot 1: empty downlink, not sent, 0.7 ms</title></rect>
<rect x="102.6" y="30" width="5.7" height="34" class="sem"><title>slot 2: empty downlink, not sent, 0.7 ms</title></rect>
<rect x="109.4" y="30" width="5.7" height="34" class="sem"><title>slot 3: empty downlink, not sent, 0.7 ms</title></rect>
<rect x="116.1" y="30" width="5.7" height="34" class="sem"><title>slot 4: empty downlink, not sent, 0.7 ms</title></rect>
<rect x="122.8" y="30" width="5.7" height="34" class="sem"><title>slot 5: empty downlink, not sent, 0.7 ms</title></rect>
<rect x="129.5" y="30" width="24.9" height="34" class="sdl"><title>slot 6: downlink with content, about 2.7 ms</title></rect>
<text x="142.5" y="52" class="sl" text-anchor="middle">D6</text>
<rect x="155.4" y="30" width="105.6" height="34" class="sS"><title>slot 7: special slot, about 11 ms</title></rect>
<text x="208.7" y="52" class="sl" text-anchor="middle">S7</text>
<rect x="262.0" y="30" width="105.6" height="34" class="sU"><title>slot 8: uplink: a poll to each attached UE&#x27;s board, about 11 ms</title></rect>
<text x="315.3" y="52" class="sl" text-anchor="middle">U8</text>
<rect x="368.6" y="30" width="105.6" height="34" class="sU"><title>slot 9: uplink: a poll to each attached UE&#x27;s board, about 11 ms</title></rect>
<text x="421.8" y="52" class="sl" text-anchor="middle">U9</text>
<rect x="475.1" y="30" width="239.0" height="34" class="sW"><title>sidelink window: 25 ms, the gNB bridge stays off the air</title></rect>
<text x="595.1" y="52" class="sl" text-anchor="middle">sidelink window 25 ms</text>
<path d="M70 72 V80 H473.1 V72" class="br"/><text x="70" y="96" class="lb">NR frame ≈ 42 ms idle: D to all attached UEs, U polls their boards</text>
<path d="M476.1 72 V80 H713.1 V72" class="br"/><text x="595.1" y="96" class="lb" text-anchor="middle">after slot 9</text>
<text x="0" y="122" class="lb">gNB LED</text>
<rect x="70" y="110" width="345.7" height="14" rx="3" fill="var(--rrelay)"><title>green: NR slots, for the NR stretch less 6 ms</title></rect>
<rect x="476.1" y="110" width="181.6" height="14" rx="3" fill="var(--rpivot)"><title>blue: the sidelink window, less 6 ms</title></rect>
<text x="665.7" y="122" class="lb">green NR · blue sidelink · 6 ms dark between</text>
<text x="0" y="168" class="t">The window in detail (coverage, layer-3 relay): beacons, PC5 signalling, then the remote&#x27;s IP packets</text>
<rect x="70" y="184" width="73" height="34" class="sP"><title>PSBCH: sync: the boards&#x27; slot clock</title></rect>
<text x="108" y="206" class="sl" text-anchor="middle">PSBCH</text>
<rect x="145" y="184" width="88" height="34" class="sG"><title>position beacons, each AGV every 0.5 s (the controller&#x27;s positions for the replay; no role in the relay choice)</title></rect>
<text x="189" y="206" class="sl" text-anchor="middle">beacons</text>
<rect x="235" y="184" width="98" height="34" class="sB"><title>discovery announcement (Model A): every AGV allowed to relay (Uu RSRP within the thresholds, relay PDU session up), on each of its PC5 links every 0.5 s, 43 B IP</title></rect>
<text x="284" y="206" class="sl" text-anchor="middle">announce</text>
<rect x="335" y="184" width="88" height="34" class="sP"><title>link set-up, once per link: PC5-S request, security mode command and complete, accept (34–60 B IP each), then DHCPv4 discover, offer, request, ack (281 B IP each); a release later</title></rect>
<text x="379" y="206" class="sl" text-anchor="middle">PC5-S · DHCP</text>
<rect x="425" y="184" width="163" height="34" class="sR"><title>remote → relay: the remote&#x27;s IP packet as it is (a positioning message one TB, a LiDAR scan four)</title></rect>
<text x="506" y="206" class="sl" text-anchor="middle">remote IP → relay</text>
<rect x="590" y="184" width="70" height="34" class="sB"><title>relay → remote: a command routed back (un-NATed)</title></rect>
<text x="625" y="206" class="sl" text-anchor="middle">cmd</text>
<rect x="662" y="184" width="156" height="34" class="sF"><title>spare: a retry, or a LiDAR scan&#x27;s remaining packets</title></rect>
<text x="740" y="206" class="sl" text-anchor="middle">spare</text>
<text x="70" y="234" class="lb" text-anchor="middle">0 ms</text>
<text x="220" y="234" class="lb" text-anchor="middle">5 ms</text>
<text x="370" y="234" class="lb" text-anchor="middle">10 ms</text>
<text x="520" y="234" class="lb" text-anchor="middle">15 ms</text>
<text x="670" y="234" class="lb" text-anchor="middle">20 ms</text>
<text x="820" y="234" class="lb" text-anchor="middle">25 ms</text>
<text x="70" y="254" class="lb">Only what is queued goes out (PC5-S and DHCP: 8 messages once per link); drawn to scale only roughly. One remote at L1: ≈ 9 packets/s up, 2 down.</text>
<text x="0" y="282" class="t">Wider windows: the same NR work, fewer cycles a second (measured, dual runs at L2)</text>
<text x="0" y="309" class="lb">25 ms</text>
<rect x="70" y="294" width="430" height="20" class="sdl"/><rect x="501" y="294" width="139" height="20" class="sW"/>
<text x="651" y="309" class="lb">102 ms, 9.8/s · Uu 76 %</text>
<text x="0" y="339" class="lb">35 ms</text>
<rect x="70" y="324" width="439" height="20" class="sdl"/><rect x="510" y="324" width="195" height="20" class="sW"/>
<text x="716" y="339" class="lb">114 ms, 8.8/s · Uu 69 %</text>
<text x="0" y="369" class="lb">50 ms</text>
<rect x="70" y="354" width="437" height="20" class="sdl"/><rect x="508" y="354" width="279" height="20" class="sW"/>
<text x="798" y="369" class="lb">128 ms, 7.8/s · Uu 61 %</text>
</svg></div>
    </div>
    <div class="bd" style="border-top:1px solid var(--line)"><p class="hint" style="max-width:110ch">Air sharing as in the relay chain: after slot 9 of every TDD frame the gNB bridge announces a 25 ms sidelink window and stays off the air; the boards carry the sidelink UEs&#x27; slots (PSBCH, SCI, transport blocks) only inside it, beacons first. The rest of the frame the boards carry the gNB&#x27;s slots: downlink to every attached UE, and polls that each attached UE&#x27;s board answers with its uplink. The gNB board&#x27;s LED shows this split: a green flash for each NR stretch, a blue one for each window. With the layer-3 relay nothing changes on the air: the PC5 signalling and the remote&#x27;s IP packets are transport blocks of the sidelink UEs like any other, and the relay&#x27;s uplink, its own and its remote&#x27;s, goes in its own UE&#x27;s grants. An AGV on its own UE skips the sidelink entirely.</p></div>
  </section>
  <section class="card" aria-label="Window tests" id="results">
    <div class="hd"><h2>Board runs: sidelink window widths</h2><span class="meta">ten minutes a run, from minute 1 · pose p50 and loss</span></div>
    <div class="bd scroll">
      <table>
        <thead><tr><th>Run</th><th>Topology</th><th>Gateway 1 / pivot pose</th><th>Gateway 2 / relay pose</th><th>Remote pose</th><th>Remote LiDAR</th><th>Command to the remote</th><th>Remote via g1 / g2 / own UE %</th></tr></thead>
        <tbody>
%RESULTS%
        </tbody>
      </table>
      <p class="hint" style="padding-top:8px">These runs relayed at the application (the gateway&#x27;s program re-sends the remote&#x27;s message), before the layer-3 relay. Gateway 1 and 2 here are by distance to the pole (nearest first), whichever one served the remote. Each dual-gateway run has the relay chain at the same load and window under it (from the relay chain page's runs). In the chain the first two columns are the pivot and the relay. Loss of 5 % or more in red. The last column splits the remote&#x27;s delivered uplink by the UE that carried it: gateway 1&#x27;s, gateway 2&#x27;s, or its own while it still has a session after a role change. Load: L1 = LiDAR 1 Hz and poses 5 Hz per AGV, L2 = 2 Hz and 10 Hz. The window is the gNB's sidelink window after slot 9 of every TDD frame; the boards stay off the air for the gNB the rest of the time.</p>
    </div>
    <div class="bd" style="border-top:1px solid var(--line)">
      <ul class="plan" style="padding-left:18px">%FINDINGS%</ul>
    </div>
  </section>


  <section class="card" aria-label="Coverage route" id="coverage">
    <div class="hd"><h2>Coverage: where the gNB does not reach</h2><span class="meta">the live scenario since 13 Nov · <code>--topology cover</code></span></div>
    <div class="bd">
      <p style="margin:0 0 10px;max-width:110ch">The AGVs drive the same loop as a platoon: A leads and stops at the checkpoints, B and C follow 8 m apart, and an AGV with a session waits for the one behind it when the gap grows past 14 m. Whether an AGV can use the gNB depends on where it is, not on its rank by distance. A log-distance path loss (n = 3.5 among racks) puts the signal at about −80 dBm almost everywhere on the floor; the far corner is a shielded cold store (insulated steel panels), which adds up to 32 dB over the first 3 m past its door line. Inside, the signal falls to −118 dBm. Below −100 dBm an AGV stops sending on its own UE and goes over one sidelink hop through another AGV that still has a session. Since 19 Nov that AGV is a <b>layer-3 UE-to-network relay</b> as in TS 23.304: with thresholds configured as SIB12 would carry them, every AGV whose Uu RSRP is between −95 and −31 dBm and whose relay PDU session is up sends a discovery announcement on its PC5 links every 0.5 s; an AGV below −100 dBm measures each one&#x27;s SD-RSRP (the same path-loss law, no store walls, since the door is open), keeps only relays above −90 dBm (about 34 m), takes the strongest (2 dB to reselect), sets up the link with PC5-S and gets its address by DHCPv4 from that relay. Once on a relay it keeps it until its own session is up, and stops before the relay&#x27;s SD-RSRP would fall within 2 dB of −90 dBm. Before, the controller simply picked the nearest AGV with a session, and only one nearer than the gNB; below −110 dBm for 1 s its UE has a radio link failure and is stopped, which really drops its connection to the gNB. Back above −100 dBm for 2 s it attaches again, and keeps using the sidelink until its session has settled (attach 3.7 s median, then 8 s). Only one AGV is on the sidelink at a time: an AGV with a session stops 1.5 m before the dead zone while another AGV is on the sidelink, either because it is that AGV&#x27;s relay or because going in would need a second sidelink. The AGV on the sidelink never waits: it drives on until it has its own direct connection again (session up and settled), and then the AGV at the door goes in. Coming out of the store, an AGV keeps its sidelink until its own session is up (about 3.7 s): if driving on would take it out of its relay&#x27;s reach, with the relay no longer nearer than the gNB, it stops 2 m short of that and waits for its session. In practice that is the leader, whose relay waits at the door behind it. As a fallback the app also queues the uplink for up to 5 s while its own UE attaches, and up to 3 s while a direct link is set up.</p>
      <div class="scroll"><svg class="cvsvg" viewBox="0 0 1000 290" role="img" aria-label="The gNB signal along the AGVs&#x27; route: about -80 dBm almost everywhere, falling to -118 dBm inside the cold store, below the switch and radio-link-failure thresholds">
<rect x="465.1" y="20" width="132.1" height="210" class="cold"/>
<rect x="597.2" y="20" width="126.1" height="210" class="reatt"/>
<text x="531" y="36" class="lb" text-anchor="middle">cold store</text>
<text x="601" y="36" class="lb">re-attach, settle</text>
<line x1="60" x2="980" y1="20.0" y2="20.0" class="grid"/><text x="54" y="24.0" class="lb" text-anchor="end">-40</text>
<line x1="60" x2="980" y1="69.4" y2="69.4" class="grid"/><text x="54" y="73.4" class="lb" text-anchor="end">-60</text>
<line x1="60" x2="980" y1="118.8" y2="118.8" class="grid"/><text x="54" y="122.8" class="lb" text-anchor="end">-80</text>
<line x1="60" x2="980" y1="168.2" y2="168.2" class="grid"/><text x="54" y="172.2" class="lb" text-anchor="end">-100</text>
<line x1="60" x2="980" y1="217.6" y2="217.6" class="grid"/><text x="54" y="221.6" class="lb" text-anchor="end">-120</text>
<line x1="60" x2="980" y1="168.2" y2="168.2" class="thr"/><text x="976" y="163.2" class="lb thrt" text-anchor="end">−100 dBm: stop using its own UE, go sidelink</text>
<line x1="60" x2="980" y1="192.9" y2="192.9" class="thr2"/><text x="976" y="187.9" class="lb thr2t" text-anchor="end">−110 dBm for 1 s: radio link failure, UE lost</text>
<path class="sig" d="M60.0 121.0 L61.5 120.6 L63.0 120.2 L64.5 119.8 L65.9 119.5 L67.4 119.1 L68.9 118.7 L70.4 118.3 L71.9 117.9 L73.4 117.5 L74.8 117.1 L76.3 116.7 L77.8 116.3 L79.3 115.9 L80.8 115.5 L82.3 115.0 L83.7 114.6 L85.2 114.2 L86.7 113.8 L88.2 113.3 L89.7 112.9 L91.2 112.4 L92.6 112.0 L94.1 111.5 L95.6 111.1 L97.1 110.6 L98.6 110.2 L100.1 109.7 L101.5 109.2 L103.0 108.7 L104.5 108.3 L106.0 107.8 L107.5 107.3 L109.0 106.8 L110.5 106.3 L111.9 105.8 L113.4 105.3 L114.9 104.8 L116.4 104.3 L117.9 103.7 L119.4 103.2 L120.8 102.7 L122.3 102.1 L123.8 101.6 L125.3 101.1 L126.8 100.5 L128.3 100.0 L129.7 99.4 L131.2 98.9 L132.7 98.3 L134.2 97.7 L135.7 97.2 L137.2 96.6 L138.6 96.0 L140.1 95.4 L141.6 94.9 L143.1 94.3 L144.6 93.7 L146.1 93.1 L147.5 92.6 L149.0 92.0 L150.5 91.4 L152.0 90.8 L153.5 90.3 L155.0 89.7 L156.5 89.1 L157.9 88.6 L159.4 88.0 L160.9 87.5 L162.4 87.0 L163.9 86.4 L165.4 85.9 L166.8 85.4 L168.3 85.0 L169.8 84.5 L171.3 84.0 L172.8 83.6 L174.3 83.2 L175.7 82.8 L177.2 82.4 L178.7 82.1 L180.2 81.8 L181.7 81.5 L183.2 81.3 L184.6 81.0 L186.1 80.8 L187.6 80.7 L189.1 80.6 L190.6 80.5 L192.1 80.4 L193.5 80.4 L195.0 80.4 L196.5 80.5 L198.0 80.6 L199.5 80.7 L201.0 80.8 L202.5 81.0 L203.9 81.3 L205.4 81.5 L206.9 81.8 L208.4 82.1 L209.9 82.4 L211.4 82.8 L212.8 83.2 L214.3 83.6 L215.8 84.0 L217.3 84.5 L218.8 85.0 L220.3 85.4 L221.7 85.9 L223.2 86.4 L224.7 87.0 L226.2 87.5 L227.7 88.0 L229.2 88.6 L230.6 89.1 L232.1 89.7 L233.6 90.3 L235.1 90.8 L236.6 91.4 L238.1 92.0 L239.5 92.6 L241.0 93.1 L242.5 93.7 L244.0 94.3 L245.5 94.9 L247.0 95.4 L248.5 96.0 L249.9 96.6 L251.4 97.2 L252.9 97.7 L254.4 98.3 L255.9 98.9 L257.4 99.4 L258.8 100.0 L260.3 100.5 L261.8 101.1 L263.3 101.6 L264.8 102.1 L266.3 102.7 L267.7 103.2 L269.2 103.7 L270.7 104.3 L272.2 104.8 L273.7 105.3 L275.2 105.8 L276.6 106.3 L278.1 106.8 L279.6 107.3 L281.1 107.8 L282.6 108.3 L284.1 108.7 L285.5 109.2 L287.0 109.7 L288.5 110.2 L290.0 110.6 L291.5 111.1 L293.0 111.5 L294.5 112.0 L295.9 112.4 L297.4 112.9 L298.9 113.3 L300.4 113.8 L301.9 114.2 L303.4 114.6 L304.8 115.0 L306.3 115.5 L307.8 115.9 L309.3 116.3 L310.8 116.7 L312.3 117.1 L313.7 117.5 L315.2 117.9 L316.7 118.3 L318.2 118.7 L319.7 119.1 L321.2 119.5 L322.6 119.8 L324.1 120.2 L325.6 120.6 L327.1 121.0 L328.6 121.3 L330.1 121.7 L331.5 122.1 L333.0 122.4 L334.5 122.8 L336.0 123.1 L337.5 123.5 L339.0 123.8 L340.5 124.2 L341.9 124.5 L343.4 124.8 L344.9 125.2 L346.4 125.5 L347.9 125.8 L349.4 126.2 L350.8 126.5 L352.3 126.8 L353.8 127.1 L355.3 127.5 L356.8 127.8 L358.3 127.7 L359.7 127.6 L361.2 127.5 L362.7 127.4 L364.2 127.3 L365.7 127.3 L367.2 127.2 L368.6 127.1 L370.1 127.0 L371.6 127.0 L373.1 126.9 L374.6 126.8 L376.1 126.8 L377.5 126.7 L379.0 126.7 L380.5 126.6 L382.0 126.6 L383.5 126.5 L385.0 126.5 L386.5 126.4 L387.9 126.4 L389.4 126.4 L390.9 126.3 L392.4 126.3 L393.9 126.3 L395.4 126.3 L396.8 126.2 L398.3 126.2 L399.8 126.2 L401.3 126.2 L402.8 126.2 L404.3 126.2 L405.7 126.2 L407.2 126.2 L408.7 126.2 L410.2 126.2 L411.7 126.2 L413.2 126.2 L414.6 126.3 L416.1 126.3 L417.6 126.3 L419.1 126.3 L420.6 126.4 L422.1 126.4 L423.5 126.4 L425.0 126.5 L426.5 126.5 L428.0 126.6 L429.5 126.6 L431.0 126.7 L432.5 126.7 L433.9 126.8 L435.4 126.8 L436.9 126.9 L438.4 127.0 L439.9 127.0 L441.4 127.1 L442.8 127.2 L444.3 127.3 L445.8 127.3 L447.3 127.4 L448.8 127.5 L450.3 127.6 L451.7 127.7 L453.2 127.8 L454.7 133.1 L456.2 138.5 L457.7 143.9 L459.2 149.2 L460.6 154.6 L462.1 160.0 L463.6 165.4 L465.1 170.7 L466.6 176.1 L468.1 181.5 L469.5 186.9 L471.0 192.3 L472.5 197.7 L474.0 203.1 L475.5 208.5 L477.0 208.6 L478.5 208.7 L479.9 208.8 L481.4 209.0 L482.9 209.1 L484.4 209.2 L485.9 209.4 L487.4 209.5 L488.8 209.7 L490.3 209.8 L491.8 209.9 L493.3 210.1 L494.8 210.2 L496.3 210.4 L497.7 210.5 L499.2 210.7 L500.7 210.8 L502.2 211.0 L503.7 211.1 L505.2 211.3 L506.6 211.4 L508.1 211.6 L509.6 211.7 L511.1 211.9 L512.6 212.0 L514.1 212.2 L515.5 212.3 L517.0 212.5 L518.5 212.7 L520.0 212.8 L521.5 212.6 L523.0 212.4 L524.5 212.1 L525.9 211.9 L527.4 211.7 L528.9 211.4 L530.4 211.2 L531.9 211.0 L533.4 210.7 L534.8 210.5 L536.3 210.3 L537.8 210.0 L539.3 209.8 L540.8 209.6 L542.3 209.3 L543.7 209.1 L545.2 208.9 L546.7 208.6 L548.2 208.4 L549.7 208.1 L551.2 207.9 L552.6 207.7 L554.1 207.4 L555.6 207.2 L557.1 206.9 L558.6 206.7 L560.1 206.5 L561.5 206.2 L563.0 206.0 L564.5 205.7 L566.0 205.5 L567.5 205.2 L569.0 205.0 L570.5 204.8 L571.9 204.5 L573.4 204.3 L574.9 204.0 L576.4 203.8 L577.9 203.5 L579.4 203.3 L580.8 203.1 L582.3 202.8 L583.8 202.6 L585.3 202.3 L586.8 202.1 L588.3 196.6 L589.7 191.1 L591.2 185.6 L592.7 180.1 L594.2 174.6 L595.7 169.1 L597.2 163.6 L598.6 158.1 L600.1 152.6 L601.6 147.1 L603.1 141.6 L604.6 136.1 L606.1 130.6 L607.5 125.1 L609.0 119.6 L610.5 119.4 L612.0 119.1 L613.5 118.9 L615.0 118.7 L616.5 118.5 L617.9 118.3 L619.4 118.1 L620.9 117.9 L622.4 117.7 L623.9 117.5 L625.4 117.3 L626.8 117.1 L628.3 116.9 L629.8 116.7 L631.3 116.5 L632.8 116.4 L634.3 116.2 L635.7 116.0 L637.2 115.8 L638.7 115.7 L640.2 115.5 L641.7 115.4 L643.2 115.2 L644.6 115.0 L646.1 114.9 L647.6 114.8 L649.1 114.6 L650.6 114.5 L652.1 114.4 L653.5 114.3 L655.0 114.1 L656.5 114.0 L658.0 113.9 L659.5 113.8 L661.0 113.7 L662.5 113.6 L663.9 113.6 L665.4 113.5 L666.9 113.4 L668.4 113.4 L669.9 112.9 L671.4 112.4 L672.8 111.9 L674.3 111.4 L675.8 110.9 L677.3 110.4 L678.8 109.9 L680.3 109.3 L681.7 108.8 L683.2 108.3 L684.7 107.7 L686.2 107.2 L687.7 106.6 L689.2 106.0 L690.6 105.4 L692.1 104.9 L693.6 104.3 L695.1 103.6 L696.6 103.0 L698.1 102.4 L699.5 101.8 L701.0 101.1 L702.5 100.4 L704.0 99.8 L705.5 99.1 L707.0 98.4 L708.5 97.7 L709.9 97.0 L711.4 96.2 L712.9 95.5 L714.4 94.7 L715.9 93.9 L717.4 93.1 L718.8 92.3 L720.3 91.5 L721.8 90.7 L723.3 89.8 L724.8 88.9 L726.3 88.0 L727.7 87.1 L729.2 86.1 L730.7 85.2 L732.2 84.2 L733.7 83.2 L735.2 82.1 L736.6 81.0 L738.1 79.9 L739.6 78.8 L741.1 77.7 L742.6 76.5 L744.1 75.2 L745.5 74.0 L747.0 72.7 L748.5 71.4 L750.0 70.0 L751.5 68.6 L753.0 67.1 L754.5 65.6 L755.9 64.1 L757.4 62.5 L758.9 60.8 L760.4 59.1 L761.9 57.4 L763.4 55.6 L764.8 53.8 L766.3 52.0 L767.8 50.1 L769.3 48.2 L770.8 46.4 L772.3 44.5 L773.7 42.1 L775.2 39.6 L776.7 37.1 L778.2 34.6 L779.7 32.2 L781.2 30.0 L782.6 28.1 L784.1 26.6 L785.6 25.7 L787.1 25.3 L788.6 25.7 L790.1 26.6 L791.5 28.1 L793.0 30.0 L794.5 32.2 L796.0 34.6 L797.5 37.1 L799.0 39.6 L800.5 42.1 L801.9 44.5 L803.4 46.9 L804.9 49.2 L806.4 51.4 L807.9 53.5 L809.4 55.6 L810.8 57.5 L812.3 59.4 L813.8 61.2 L815.3 63.0 L816.8 64.6 L818.3 66.3 L819.7 67.8 L821.2 69.3 L822.7 70.8 L824.2 72.2 L825.7 73.5 L827.2 74.8 L828.6 76.1 L830.1 77.3 L831.6 78.5 L833.1 79.7 L834.6 80.8 L836.1 81.9 L837.5 83.0 L839.0 84.0 L840.5 85.0 L842.0 86.0 L843.5 87.0 L845.0 87.9 L846.5 88.9 L847.9 89.8 L849.4 90.6 L850.9 91.5 L852.4 92.3 L853.9 93.1 L855.4 93.9 L856.8 94.7 L858.3 95.5 L859.8 96.3 L861.3 97.0 L862.8 97.7 L864.3 98.4 L865.7 99.1 L867.2 99.8 L868.7 100.5 L870.2 101.2 L871.7 101.8 L873.2 102.5 L874.6 103.1 L876.1 103.7 L877.6 104.3 L879.1 104.9 L880.6 105.5 L882.1 106.1 L883.5 106.7 L885.0 107.3 L886.5 107.8 L888.0 108.4 L889.5 108.9 L891.0 109.4 L892.5 110.0 L893.9 110.5 L895.4 111.0 L896.9 111.5 L898.4 112.0 L899.9 112.5 L901.4 113.0 L902.8 113.5 L904.3 113.9 L905.8 114.4 L907.3 114.9 L908.8 115.3 L910.3 115.8 L911.7 116.2 L913.2 116.7 L914.7 117.1 L916.2 117.5 L917.7 118.0 L919.2 118.4 L920.6 118.8 L922.1 118.8 L923.6 118.7 L925.1 118.7 L926.6 118.7 L928.1 118.7 L929.5 118.7 L931.0 118.7 L932.5 118.7 L934.0 118.7 L935.5 118.7 L937.0 118.7 L938.5 118.7 L939.9 118.7 L941.4 118.8 L942.9 118.8 L944.4 118.8 L945.9 118.9 L947.4 118.9 L948.8 119.0 L950.3 119.0 L951.8 119.1 L953.3 119.1 L954.8 119.2 L956.3 119.3 L957.7 119.4 L959.2 119.4 L960.7 119.5 L962.2 119.6 L963.7 119.7 L965.2 119.8 L966.6 119.9 L968.1 120.0 L969.6 120.1 L971.1 120.2 L972.6 120.3 L974.1 120.5 L975.5 120.6 L977.0 120.7 L978.5 120.8 L980.0 121.0"/>
<line x1="60.0" x2="60.0" y1="230" y2="236" class="grid"/>
<line x1="208.4" x2="208.4" y1="230" y2="236" class="grid"/>
<line x1="356.8" x2="356.8" y1="230" y2="236" class="grid"/>
<line x1="520.0" x2="520.0" y1="230" y2="236" class="grid"/>
<line x1="668.4" x2="668.4" y1="230" y2="236" class="grid"/>
<line x1="920.6" x2="920.6" y1="230" y2="236" class="grid"/>
<text x="60" y="250" class="lb" text-anchor="middle">0 m</text>
<text x="208" y="250" class="lb" text-anchor="middle">20 m</text>
<text x="357" y="250" class="lb" text-anchor="middle">40 m</text>
<text x="505" y="250" class="lb" text-anchor="middle">60 m</text>
<text x="654" y="250" class="lb" text-anchor="middle">80 m</text>
<text x="802" y="250" class="lb" text-anchor="middle">100 m</text>
<text x="950" y="250" class="lb" text-anchor="middle">120 m</text>
<text x="60" y="274" class="lb">distance along the loop from checkpoint A (124 m); the gNB signal per resource element, dBm</text>
</svg></div>
      <div style="margin-top:12px">%COVERTABLE%</div>
      <p class="hint" style="max-width:110ch;margin:8px 0 0">The boards sit on one desk, so the radio itself never fades: the controller applies this model, stopping and starting each AGV&#x27;s UE exactly as a lost and regained connection would. The sidelink is assumed to reach through the store&#x27;s open door to the AGVs behind (8–16 m away, always nearer than the gNB); only the gNB&#x27;s signal is blocked. LEDs: <b style="color:var(--rremote)">red</b> sends over the sidelink (out of coverage or re-attaching; blinking while its UE attaches), <b style="color:var(--rpivot)">blue</b> in coverage and carrying another AGV&#x27;s traffic, <b style="color:var(--rrelay)">green</b> in coverage, its own UE only. The gNB board flashes green for NR slots and blue for the sidelink windows.</p>
    </div>
  </section>

  <section class="card" aria-label="Coverage runs compared" id="compare">
    <div class="hd"><h2>Coverage runs compared: towards no lost packets</h2><span class="sub">every step kept as a run in the replay above</span></div>
    <div class="bd">
      <p style="max-width:110ch;margin:0">Each change to the coverage scenario, run on the boards for 10–15 minutes with the first minute left out. Lost counts the AGVs&#x27; uplink messages (positioning and LiDAR) that never reached the edge; over 1 s are the ones that arrived, late. The hint under each run is the AGVs&#x27; pace: holding at the door costs laps.</p>
      %COMPARE%
      <ul class="hint" style="max-width:110ch;margin:0;padding-left:18px">%CMPNOTES%</ul>
    </div>
  </section>

  <section class="card" aria-label="Traffic flows" id="traffic">
    <div class="hd"><h2>Traffic flows</h2><span class="meta">per AGV · packet size and frequency · the same in every scenario</span></div>
    <div class="scroll"><table><thead><tr><th>Flow</th><th>Dir</th><th>Payload</th><th>IP packet</th><th>Air packets</th><th>Rate L1 · L2 · L3</th><th>Interval</th><th>kbit/s per AGV</th></tr></thead><tbody><tr><td><b>Positioning</b><br><span class="hint">position, heading, detections; ±10 % jitter</span></td><td>up</td><td class="mono">30–70 B</td><td class="mono">58–98 B</td><td class="mono">1</td><td class="mono">5 · 10 · 20 Hz</td><td class="mono">200 · 100 · 50 ms</td><td class="mono">3.1 · 6.2 · 12.5</td></tr><tr><td><b>LiDAR</b><br><span class="hint">a downsampled scan</span></td><td>up</td><td class="mono">752 B</td><td class="mono">780 B</td><td class="mono">4</td><td class="mono">1 · 2 · 4 Hz</td><td class="mono">1000 · 500 · 250 ms</td><td class="mono">6.2 · 12.5 · 25.0</td></tr><tr><td><b>Commands</b><br><span class="hint">stop/go from the edge, to each AGV</span></td><td>down</td><td class="mono">40 B</td><td class="mono">68 B</td><td class="mono">1</td><td class="mono">2 · 2 · 2 Hz</td><td class="mono">500 · 500 · 500 ms</td><td class="mono">1.1 · 1.1 · 1.1</td></tr><tr><td><b>Discovery announcement</b><br><span class="hint">PC5, relay to its peers; layer-3 relay only</span></td><td>PC5</td><td class="mono">15 B</td><td class="mono">43 B</td><td class="mono">1</td><td class="mono">2 Hz per link</td><td class="mono">500 ms</td><td class="mono">0.7 per link</td></tr><tr><td><b>Link set-up</b><br><span class="hint">PC5-S request, security mode command and complete, accept; then DHCPv4 discover, offer, request, ack; per link</span></td><td>PC5</td><td class="mono">6–32 B · 253 B</td><td class="mono">34–60 B · 281 B</td><td class="mono">1 · 2</td><td class="mono">once per link</td><td class="mono">–</td><td class="mono">–</td></tr><tr><td><b>Remote UE Report</b><br><span class="hint">relay to the edge up its relay PDU session, per lease and release</span></td><td>up</td><td class="mono">≈ 85 B</td><td class="mono">≈ 113 B</td><td class="mono">1</td><td class="mono">a few a minute</td><td class="mono">–</td><td class="mono">–</td></tr><tr><td><b>Uplink, one AGV</b></td><td>up</td><td></td><td></td><td></td><td></td><td></td><td class="mono">9.4 · 18.7 · 37.4</td></tr><tr><td><b>Uplink, the fleet</b></td><td>up</td><td></td><td></td><td></td><td></td><td></td><td class="mono">28 · 56 · 112</td></tr></tbody></table></div>
    <div class="bd scroll" style="border-top:1px solid var(--line)"><svg class="cvsvg" viewBox="0 0 1000 250" role="img" aria-label="One second of one AGV&#x27;s traffic at L1 and L2: positioning messages several times a second, one or two LiDAR scans of four air packets, and two commands down">
<text x="0" y="60" class="t">L1</text>
<text x="40" y="45" class="lb">Positioning</text>
<line x1="120" x2="980" y1="50" y2="50" class="grid"/>
<rect x="206.0" y="34" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="378.0" y="34" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="550.0" y="34" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="722.0" y="34" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="894.0" y="34" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<text x="40" y="71" class="lb">LiDAR</text>
<line x1="120" x2="980" y1="76" y2="76" class="grid"/>
<rect x="550.0" y="60" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 1 of 4</title></rect>
<rect x="571.5" y="60" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 2 of 4</title></rect>
<rect x="592.9" y="60" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 3 of 4</title></rect>
<rect x="614.4" y="60" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 4 of 4</title></rect>
<text x="40" y="97" class="lb">Commands</text>
<line x1="120" x2="980" y1="102" y2="102" class="grid"/>
<rect x="335.0" y="86" width="7.5" height="16" class="f2"><title>Commands: 68 B</title></rect>
<rect x="765.0" y="86" width="7.5" height="16" class="f2"><title>Commands: 68 B</title></rect>
<text x="0" y="170" class="t">L2</text>
<text x="40" y="155" class="lb">Positioning</text>
<line x1="120" x2="980" y1="160" y2="160" class="grid"/>
<rect x="163.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="249.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="335.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="421.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="507.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="593.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="679.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="765.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="851.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<rect x="937.0" y="144" width="8.6" height="16" class="f0"><title>Positioning: 58–98 B</title></rect>
<text x="40" y="181" class="lb">LiDAR</text>
<line x1="120" x2="980" y1="186" y2="186" class="grid"/>
<rect x="335.0" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 1 of 4</title></rect>
<rect x="356.4" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 2 of 4</title></rect>
<rect x="377.9" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 3 of 4</title></rect>
<rect x="399.4" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 4 of 4</title></rect>
<rect x="765.0" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 1 of 4</title></rect>
<rect x="786.5" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 2 of 4</title></rect>
<rect x="807.9" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 3 of 4</title></rect>
<rect x="829.4" y="170" width="20.2" height="16" class="f1"><title>LiDAR: 780 B, air packet 4 of 4</title></rect>
<text x="40" y="207" class="lb">Commands</text>
<line x1="120" x2="980" y1="212" y2="212" class="grid"/>
<rect x="335.0" y="196" width="7.5" height="16" class="f2"><title>Commands: 68 B</title></rect>
<rect x="765.0" y="196" width="7.5" height="16" class="f2"><title>Commands: 68 B</title></rect>
<text x="120" y="246" class="lb" text-anchor="middle">0 ms</text>
<text x="335" y="246" class="lb" text-anchor="middle">250 ms</text>
<text x="550" y="246" class="lb" text-anchor="middle">500 ms</text>
<text x="765" y="246" class="lb" text-anchor="middle">750 ms</text>
<text x="980" y="246" class="lb" text-anchor="end">1000 ms</text>
</svg></div>
    <div class="bd" style="border-top:1px solid var(--line)"><p class="hint" style="max-width:110ch;margin:0">IP packet = payload + 28 B (IPv4 + UDP). An air packet carries up to 255 B, so a LiDAR scan crosses each radio hop as 4 packets and a positioning message or a command as 1. Through the layer-3 relay a remote&#x27;s IP packet crosses the sidelink as it is; inside the relay AGV the GRE tunnel adds 24 B, off the air (the application relay of the dual runs added a 2 B hop header instead). Commands come from the edge every 500 ms to each AGV, to the address and port its last message came from: for a remote, its relay&#x27;s NAT address, which sends them on. PC5 signalling: announcements from every AGV allowed to relay, four PC5-S messages and four DHCPv4 messages to set up a link and two of each kind to end it, and a Remote UE Report up the relay&#x27;s relay PDU session for each lease and release.</p></div>
  </section>
  <section class="card" aria-label="Replay" id="replay">
    <div class="hd"><h2>Replay of the board runs</h2><span class="meta" id="rmeta"></span></div>
    <div class="ctl">
      <label for="run">Run <select id="run"></select></label>
      <button type="button" id="rplay">Pause</button>
      <label for="rspeed">Speed <select id="rspeed"><option value="1">1×</option><option value="2">2×</option><option value="5" selected>5×</option><option value="10">10×</option></select></label>
      <label for="rpos" style="flex:1 1 220px">Time <input type="range" id="rpos" min="0" max="1000" value="0" style="width:100%"></label>
      <span class="clock" id="rclock">t = 0 s</span>
    </div>
    <div class="simgrid">
      <div class="floorwrap">
        <canvas id="floor2" style="aspect-ratio:48/31" role="img" aria-label="Replay: AGV positions, roles, both gateways' Uu links and the remote's sidelink to the gateway it uses"></canvas>
        <div class="key" style="margin-top:8px"><span id="fkey">AGV colour = its board&#x27;s LED: red remote, blue the gateway serving it, green the other gateway</span><span id="fkey2">amber solid: the remote&#x27;s sidelink to the gateway serving it</span><span>dots: sidelink hops as the boards received them</span></div>
      </div>
      <div class="side" id="agvs2"></div>
    </div>
    <div class="charts">
      <div>
        <span class="label">Roles over the last 120 s</span>
        <canvas id="timeline2" style="height:132px" role="img" aria-label="Roles of the AGVs during the run"></canvas>
      </div>
    </div>
    <div class="bd" style="border-top:1px solid var(--line)">
      <span class="label">One-way delay by traffic flow, every message, last 60 s · colour = the sender&#x27;s LED when it was sent</span>
      <div class="flow3" id="flows2" style="margin-top:8px"></div>
    </div>
  </section>
</main>
<script>
%CONSTS%
const ROLES = ["pivot", "relay", "remote"];                 // chip classes and colours: blue, green, red
const RNAME = ["gateway, serving the remote", "gateway, NR only", "remote"];   // by LED: blue, green, red
const FL = ["det", "lidar", "cmd"];
const LEDN = ["blue", "green", "red"];
%LIB%

const RUNS = {};
let rep = null, rplaying = !matchMedia("(prefers-reduced-motion: reduce)").matches, rdrag = false;
function viaAt(r) {   // the gateway the remote's last delivered message went through, at r.t
  const M = r.tr.msgs, rem = r.chain[2], s = r.t / 1000;
  for (let j = lo(M, s) - 1; j >= Math.max(0, lo(M, s) - 3000); j--) { const m = M[j]; if (m[1] === rem && m[2] !== 2 && m[5] != null && m[5] >= 0 && m[5] !== rem && m[4] !== null && m[0] + m[4] / 1000 <= s) return m[5]; }
  return null;
}
function servingAt(tr, tms) {   // the gateway serving the remote at tms (ms), -1: none (its own UE)
  const S = tr.serve || []; let a = 0, b = S.length; const s = tms / 1000;
  while (a < b) { const m = (a + b) >> 1; if (S[m][0] <= s) a = m + 1; else b = m; }
  return a ? S[a - 1][1] : -1;
}
function chainAt(r, tms) { let c = r.history[0].chain; for (const e of r.history) { if (e.t > tms) break; c = e.chain; } return c; }
function ledRole(r, i, tms) {   // 0 blue (serving the remote), 1 green (NR only), 2 red (remote)
  if (r.tr.leds && r.tr.leds.length && isCover(r)) { const l = ledAt(r.tr, i, tms); return l && l[1] >= 0 ? l[1] : 1; }
  const c = tms === r.t ? r.chain : chainAt(r, tms);
  return c.indexOf(i) === 2 ? 2 : servingAt(r.tr, tms) === i ? 0 : 1;
}
const COLD = [32, 17, 48, 30], QSW = -100, QRLF = -110;
function rsrpAt(x, y) {
  const d = Math.max(1, Math.hypot(x - POLE[0], y - POLE[1])), dep = Math.min(x - COLD[0], y - COLD[1], COLD[2] - x, COLD[3] - y);
  return 10 - (46 + 35 * Math.log10(d)) - (dep <= 0 ? 0 : 32 * Math.min(1, dep / 3));
}
const isCover = r => !!(r && r.tr && r.tr.topology === "cover");
function ledAt(tr, i, tms) {   // [t, colour code 0 blue 1 green 2 red, blink] of board i's LED at tms, from the run's LED log
  const n = (tr.leds || []).length;
  if (!tr._led || tr._ledn !== n) { tr._led = [0, 1, 2].map(k => (tr.leds || []).filter(l => l[1] === k).map(l => [l[0], l[2], l[3]])); tr._ledn = n; }
  const L = tr._led;
  const X = L[i], s = tms / 1000; let a = 0, b = X.length;
  while (a < b) { const m = (a + b) >> 1; if (X[m][0] <= s) a = m + 1; else b = m; }
  return a ? X[a - 1] : null;
}
function viaOf(r, i) {   // the AGV through which i's last delivered uplink message went (i itself: its own UE)
  const M = r.tr.msgs, s = r.t / 1000;
  for (let j = lo(M, s) - 1, n = 0; j >= 0 && n < 4000; j--, n++) { const m = M[j]; if (m[1] === i && m[2] !== 2 && m[4] !== null && m[5] != null && m[5] >= 0) return m[5]; }
  return null;
}
function relayOf(r, i) {   // the AGV the controller has carrying i's uplink at the replay's time (falls back to the last message's path)
  const S = r.tr.serv;
  if (S && S.length) {
    const s = r.t / 1000;
    for (let j = S.length - 1; j >= 0; j--) { const e = S[j]; if (e[0] > s || e[1] !== i) continue; if (e[2] >= 0) return e[2]; break; }
  }
  return viaOf(r, i);
}
function holdAt(tr, i, tms) {
  let on = 0; const s = tms / 1000;
  for (const h of tr.holds || []) { if (h[0] > s) break; if (h[1] === i) on = h[2]; }
  return !!on;
}
function drawCoverage(g, X, Y, sc) {
  for (let x = 0; x < W; x += 1) for (let y = 0; y < H; y += 1) {
    const q = rsrpAt(x + .5, y + .5);
    if (q >= QSW) continue;
    g.fillStyle = COL[q < QRLF ? "--rremote" : "--pc5"]; g.globalAlpha = q < QRLF ? .16 : .14;
    g.fillRect(Math.round(X(x)), Math.round(Y(y)), Math.round(X(x + 1)) - Math.round(X(x)), Math.round(Y(y + 1)) - Math.round(Y(y)));
  }
  g.globalAlpha = 1; g.setLineDash([6, 4]); g.strokeStyle = COL["--rremote"]; g.lineWidth = 1.5;
  g.strokeRect(X(COLD[0]), Y(COLD[1]), (COLD[2] - COLD[0]) * sc, (COLD[3] - COLD[1]) * sc); g.setLineDash([]);
  g.fillStyle = COL["--rremote"]; g.font = `600 12px ${COL.disp}`; g.textAlign = "left";
  g.fillText("cold store · shielded: no gNB", X(COLD[0]) + 6, Y(COLD[1]) + 15);
}
const FLOWINFO = { det: ["Positioning", "58–98 B", [5, 10, 20], "up"], lidar: ["LiDAR", "780 B, 4 air packets", [1, 2, 4], "up"], cmd: ["Commands", "68 B", [2, 2, 2], "down"] };
function drawFlows(r, id) {
  const box = $(id); if (!box) return;
  if (!box.dataset.built) {
    box.innerHTML = ["det", "lidar", "cmd"].map(f => `<div class="flowp"><h3>${FLOWINFO[f][0]}</h3><div class="fs" id="${id}-${f}-s"></div><canvas id="${id}-${f}" role="img" aria-label="${FLOWINFO[f][0]}: one-way delay of every message, last 60 s, capped at 1 s"></canvas><div class="fq" id="${id}-${f}-q"></div></div>`).join("");
    box.dataset.built = 1;
  }
  const lv = { L1: 0, L2: 1, L3: 2 }[(r.tr && r.tr.level) || "L1"] ?? 0;
  for (const f of ["det", "lidar", "cmd"]) {
    const [nm, size, hz, dir] = FLOWINFO[f];
    $(`${id}-${f}-s`).textContent = `${size} · ${hz[lv]} Hz per AGV · ${dir} · capped at 1 s`;
    const cv = $(`${id}-${f}`), [g, w, h] = fit(cv);
    g.clearRect(0, 0, w, h);
    const x0 = 40, x1 = w - 6, y0 = 16, y1 = h - 18, span = 60000, t1 = Math.max(r.t, span), t0 = t1 - span;
    const X = t => x0 + (t - t0) / span * (x1 - x0), Y = v => y1 - Math.min(1000, v) / 1000 * (y1 - y0);
    g.strokeStyle = COL["--line"]; g.lineWidth = 1; g.fillStyle = COL["--ink-3"]; g.font = `10.5px ${COL.mono}`; g.textAlign = "right";
    [0, 250, 500, 750, 1000].forEach(v => { const y = Math.round(Y(v)) + .5; g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke(); g.fillText(v === 1000 ? "≥1 s" : v, x0 - 4, y + 3.5); });
    g.textAlign = "center"; for (let s2 = Math.ceil(t0 / 20000) * 20000; s2 <= t1; s2 += 20000) if (s2 >= 0) g.fillText(Math.round(s2 / 1000) + " s", X(s2), h - 4);
    g.textAlign = "left"; g.fillText("lost", x0 + 2, 10);
    const ds = []; let lost = 0, n = 0, over = 0;
    for (const rc of r.recs) {
      if (rc.flow !== f || rc.t < t0) continue;
      n++; const x = X(rc.t); g.fillStyle = g.strokeStyle = COL[RCOL[rc.role] || "--ink-3"];
      if (rc.lost) { lost++; g.lineWidth = 1.4; g.beginPath(); g.moveTo(x - 2.5, 3); g.lineTo(x + 2.5, 8); g.moveTo(x + 2.5, 3); g.lineTo(x - 2.5, 8); g.stroke(); continue; }
      ds.push(rc.d);
      if (rc.d > 1000) { over++; g.beginPath(); g.moveTo(x, y0 - 1); g.lineTo(x - 3, y0 + 4); g.lineTo(x + 3, y0 + 4); g.closePath(); g.fill(); continue; }
      const y = Y(rc.d);
      if (f === "lidar") { g.beginPath(); g.arc(x, y, 2.8, 0, 7); g.fill(); } else { g.globalAlpha = .75; g.fillRect(x - 1.3, y - 1.3, 2.6, 2.6); g.globalAlpha = 1; }
    }
    ds.sort((a, b) => a - b);
    const q = k => ds.length ? fmt(ds[Math.min(ds.length - 1, Math.floor(k * ds.length))]) : "–";
    $(`${id}-${f}-q`).textContent = n ? `last 60 s: p50 ${q(.5)} · p90 ${q(.9)} ms · lost ${(100 * lost / n).toFixed(1)} % · over 1 s ${(100 * over / n).toFixed(1)} %` : "no messages in the last 60 s";
  }
}
function drawTimelineDual(r, cv) {
  const [g, w, h] = fit(cv);
  g.clearRect(0, 0, w, h);
  const x0 = 26, x1 = w - 4, span = 120000, t1 = Math.max(r.t, span), t0 = t1 - span, X = t => x0 + (t - t0) / span * (x1 - x0);
  const rowH = 30, colR = [COL["--rpivot"], COL["--rrelay"], COL["--rremote"]];
  const cuts = [t0].concat(r.history.map(e => e.t), (r.tr.serve || []).map(e => e[0] * 1000), (r.tr.leds || []).map(e => e[0] * 1000)).filter(t => t >= t0 && t <= r.t).sort((a, b) => a - b);
  cuts.push(r.t);
  g.font = `600 12px ${COL.disp}`; g.textAlign = "left";
  AGV.forEach((a, i) => {
    const y = 6 + i * (rowH + 6);
    g.fillStyle = COL["--ink"]; g.fillText(a.n, 4, y + rowH / 2 + 4);
    for (let k = 0; k + 1 < cuts.length; k++) {
      const s0 = Math.max(cuts[k], 0), e0 = cuts[k + 1]; if (e0 <= s0) continue;
      g.fillStyle = colR[ledRole(r, i, s0 + 0.5)]; g.fillRect(X(s0), y, Math.max(1, X(e0) - X(s0)), rowH);
    }
  });
  g.fillStyle = COL["--ink-3"]; g.font = `10.5px ${COL.mono}`; g.textAlign = "center";
  if (!isCover(r)) r.history.forEach(e => { if (e.t > t0 && e.t > 0) g.fillRect(X(e.t) - .5, 2, 1, 3 * (rowH + 6)); });
  const yb = 6 + 3 * (rowH + 6) + 8;
  for (let s = Math.ceil(t0 / 30000) * 30000; s <= t1; s += 30000) if (s >= 0) g.fillText(Math.round(s / 1000) + " s", X(s), Math.min(h - 2, yb));
}
function attaching(r, i) { for (const [tc, a, tu] of r.tr.att || []) if (a === i && tc * 1000 <= r.t && (tu === null || tu * 1000 > r.t)) return true; return false; }
function drawFloorDual(r, cv) {
  const [g, w, h] = fit(cv);
  const pad = 6, sc = Math.min((w - 2 * pad) / W, (h - 2 * pad - 14) / H), ox = (w - W * sc) / 2, oy = pad;
  const X = x => ox + x * sc, Y = y => oy + y * sc;
  g.clearRect(0, 0, w, h);
  g.fillStyle = COL["--floor"]; g.strokeStyle = COL["--line"]; g.lineWidth = 1;
  g.fillRect(X(0), Y(0), W * sc, H * sc); g.strokeRect(X(0) + .5, Y(0) + .5, W * sc - 1, H * sc - 1);
  g.fillStyle = COL["--rack"]; RACKS.forEach(([x0, y0, x1, y1]) => g.fillRect(X(x0), Y(y0), (x1 - x0) * sc, (y1 - y0) * sc));
  if (isCover(r)) drawCoverage(g, X, Y, sc);
  g.strokeStyle = COL["--line"]; g.lineWidth = 6; g.lineJoin = "round"; g.beginPath(); ROUTE.forEach(([x, y], i) => i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y))); g.closePath(); g.stroke();
  g.font = `11px ${COL.mono}`; g.fillStyle = COL["--ink-3"]; g.textAlign = "center";
  CPV.forEach((v, k) => { const [x, y] = ROUTE[v]; g.beginPath(); g.arc(X(x), Y(y), 3.5, 0, 7); g.fill(); g.fillText(CPN[k], X(x), Y(y) + (y > 20 ? -9 : 16)); });
  g.setLineDash([3, 5]); g.strokeStyle = COL["--ink-3"]; g.lineWidth = 1; g.globalAlpha = .45;
  for (let rr = 5; rr <= 30; rr += 5) { g.beginPath(); g.arc(X(POLE[0]), Y(POLE[1]), rr * sc, 0, 7); g.stroke(); }
  g.globalAlpha = 1; g.setLineDash([]);
  const P = i => i < 0 ? [X(POLE[0]), Y(POLE[1])] : [X(r.agv[i].x), Y(r.agv[i].y)];
  const c = r.chain, sv = servingAt(r.tr, r.t), via = sv >= 0 ? sv : null;
  g.lineCap = "round";
  const link = (a, b, col, wdt, dash) => { const [x0, y0] = P(a), [x1, y1] = P(b); g.setLineDash(dash || []); g.strokeStyle = col; g.lineWidth = wdt; g.globalAlpha = .9; g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); g.globalAlpha = 1; g.setLineDash([]); };
  const cov = isCover(r), vias = [0, 1, 2].map(i => cov && ledRole(r, i, r.t) === 2 ? relayOf(r, i) : null);
  if (cov) {
    for (let i = 0; i < 3; i++) { const at = attaching(r, i); if (ledRole(r, i, r.t) !== 2 || at || vias[i] === i) link(-1, i, COL["--uu"], at ? 2.5 : 4, at ? [7, 6] : null); }
    vias.forEach((v, i) => { if (v !== null && v !== i) link(i, v, COL["--pc5"], 3.5); });
  } else {
    [c[0], c[1]].forEach(gw => link(-1, gw, COL["--uu"], attaching(r, gw) ? 2.5 : 4, attaching(r, gw) ? [7, 6] : null));
    [c[0], c[1]].forEach(gw => link(c[2], gw, COL["--pc5"], gw === via ? 3.5 : 1.6, gw === via ? null : [6, 6]));
  }
  for (const m of r.flying) {
    const u = m.cur; if (!u || r.t < u.t0 || r.t > u.t1) continue;
    const f = (r.t - u.t0) / Math.max(1, u.t1 - u.t0), [x0, y0] = P(u.a), [x1, y1] = P(u.b);
    g.fillStyle = COL[RCOL[ROLES.indexOf(m.role)]]; g.beginPath(); g.arc(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, m.flow === "lidar" ? 3.6 : 2.2, 0, 7); g.fill();
  }
  const [px, py] = P(-1);
  g.fillStyle = COL["--uu"]; g.beginPath(); g.moveTo(px, py - 9); g.lineTo(px + 8, py + 6); g.lineTo(px - 8, py + 6); g.closePath(); g.fill();
  g.fillStyle = COL["--ink"]; g.textAlign = "left"; g.font = `600 12px ${COL.disp}`; g.fillText("gNB pole", px + 11, py + 4);
  r.agv.forEach(a => {
    const [x, y] = P(a.i), role = ledRole(r, a.i, r.t);
    g.save(); g.translate(x, y); g.rotate(a.h);
    g.fillStyle = COL[RCOL[role]]; g.strokeStyle = COL["--panel"]; g.lineWidth = 1.5;
    g.beginPath(); g.roundRect ? g.roundRect(-0.9 * sc, -0.6 * sc, 1.8 * sc, 1.2 * sc, 3) : g.rect(-0.9 * sc, -0.6 * sc, 1.8 * sc, 1.2 * sc); g.fill(); g.stroke();
    g.restore();
    g.fillStyle = "#fff"; g.textAlign = "center"; g.font = `700 12px ${COL.disp}`; g.fillText(AGV[a.i].n, x, y + 4);
    g.fillStyle = COL["--ink"]; g.font = `600 11px ${COL.disp}`;
    const lab = cov ? (role === 2 && vias[a.i] === a.i ? "own UE · settling" : role === 2 ? "sidelink" + (vias[a.i] !== null && vias[a.i] !== a.i ? " via " + AGV[vias[a.i]].n : "") + (rsrpAt(a.x, a.y) < QSW ? " · no gNB" : attaching(r, a.i) ? " · attaching" : " · settling")
        : role === 0 ? "relays " + vias.map((v, k) => v === a.i && k !== a.i ? AGV[k].n : "").join("") : "own UE") + ` · ${rsrpAt(a.x, a.y).toFixed(0)} dBm`
      : (role === 2 ? "remote" + (via !== null ? " · via " + AGV[via].n : "") : role === 0 ? "gateway · serving " + AGV[c[2]].n : "gateway · NR only");
    g.fillText((cov && holdAt(r.tr, a.i, r.t) ? "holding at the door · " : "") + lab + (a.dwell > 0 ? " · stopped" : ""), x, y - 0.6 * sc - 7);
  });
  g.fillStyle = COL["--ink-3"]; g.textAlign = "left"; g.font = `11px ${COL.mono}`; g.fillText("0", X(0), Y(H) + 12); g.textAlign = "right"; g.fillText("48 m", X(W), Y(H) + 12);
}
function renderSide2(r) {
  const s = r.t / 1000, M = r.tr.msgs, a0 = lo(M, s - 60), a1 = lo(M, s), cnt = {};
  for (let j = a0; j < a1; j++) { const m = M[j]; if (m[3] === 2 && m[2] !== 2 && m[5] != null && m[5] >= 0 && m[5] !== m[1]) cnt[m[5]] = (cnt[m[5]] || 0) + 1; }
  const tot = Object.values(cnt).reduce((x, y) => x + y, 0) || 1;
  $("agvs2").innerHTML = r.agv.map(a => {
    const role = ledRole(r, a.i, r.t), L = r.last[a.i];
    const v = isCover(r) ? (role === 2 ? relayOf(r, a.i) : viaOf(r, a.i)) : null;
    const path = isCover(r) ? (role === 2 && v === a.i ? "its own UE (Uu), session settling" : role === 2 ? "PC5" + (v !== null && v !== a.i ? " to AGV " + AGV[v].n : "") + " (out of coverage or re-attaching)" : role === 0 ? "its own UE, with another AGV&#x27;s traffic" : "its own UE (Uu)")
      : role === 0 ? "its own UE (Uu), with the remote&#x27;s traffic" : role === 1 ? "its own UE (Uu)" : "PC5 to " + [r.chain[0], r.chain[1]].map(g => `${AGV[g].n} ${Math.round(100 * (cnt[g] || 0) / tot)} %`).join(", ") + " (last 60 s)";
    return `<div class="agv" style="--c:var(${RCOL[role]})"><span class="sw"></span><span class="nm">AGV ${AGV[a.i].n}</span><span class="chip ${ROLES[role]}">${isCover(r) ? ["relays", "own UE", "sidelink"][role] : RNAME[role]}</span>
      <span class="sub">${AGV[a.i].board} · <span class="ledg" style="--c:var(${RCOL[role]})"></span>LED ${LEDN[role]} · up through ${path}</span>
      <dl>${isCover(r) ? `<div><dt>gNB signal</dt><dd>${rsrpAt(a.x, a.y).toFixed(0)} dBm</dd></div>` : `<div><dt>to pole</dt><dd>${a.d.toFixed(1)} m</dd></div>`}<div><dt>pose</dt><dd>${fmt(L.det)}</dd></div><div><dt>LiDAR</dt><dd>${fmt(L.lidar)}</dd></div><div><dt>cmd</dt><dd>${fmt(L.cmd)}</dd></div></dl></div>`;
  }).join("") + `<p class="hint">Measured one-way delay of each flow&#x27;s last message, in ms.</p>`;
}
class DualReplay extends Replay {
  update() {
    super.update(); if (!this.tr || !this.tr.serve) return;
    // colours by LED: a message by its AGV's LED when it was sent, a hop by its sender's
    for (const rc of this.recs) rc.role = ledRole(this, rc.agv, rc.t);
    for (const m of this.flying) m.role = ROLES[ledRole(this, m.cur.a, this.t)];
  }
}
// ---------------------------------------------------------------- architecture, coloured by the replay
const AR = (() => {
  const OX = 150, VW = OX + 1660, VH = 780, W = 150, H = 72, NS = "http://www.w3.org/2000/svg";
  const ael = (tag, at, par) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); if (par) par.appendChild(e); return e; };
  const BX = [620, 960, 1300], SER = ["2HOKMMVEX1WMP", "GBAMNH4BQ3CSB", "GLNAOWUSDREB4"], RC = ["var(--rpivot)", "var(--rrelay)", "var(--rremote)"];
  const LANES = [[0, 140, "free5GC", ["v4.3.0", "core functions"], "--core"], [140, 270, "OpenAirInterface", ["gNB VNF + PNF,", "nrUE, sidelink UE"], "--uu"],
    [270, 500, "Host · Linux", ["nrlink Python,", "Docker sidecars"], "--ink-2"], [500, 620, "NXP", ["FRDM-MCXW72", "nrlink firmware"], "--pc5"],
    [620, 780, "Over the air", ["2402 MHz, one channel", "NR slots and", "sidelink windows"], "--ink-3"]];
  const N = {
    upf: { x: 30, y: 44, t: "UPF", s: "free5GC · gtp5g", s2: "N3 / N6 anchor", d: "User plane anchor. GTP-U from the gNB comes in here and leaves to ext-dn decapsulated. Two DNNs: internet (12.1.1.0/24, every UE&#x27;s own session) and relay (12.1.2.0/24, the relay sessions)." },
    amf: { x: 210, y: 44, t: "AMF · SMF", s: "free5GC v4.3.0", s2: "pool 12.1.1.0/24", d: "Registration and sessions for nrUE1–3. Each AGV&#x27;s UE gets its own address, so the edge can tell which UE a message came up on. Each nrUE also has a second session on DNN relay (pool 12.1.2.0/24): a relay carries its remotes&#x27; traffic in that one, apart from its own. A remote behind a layer-3 relay has no session of its own: the core sees only the relay&#x27;s relay session, and learns about the remote from the relay&#x27;s Remote UE Report: the relay&#x27;s UE sends it as NAS (UL NAS transport) on its relay session, and the SMF (patched: free5GC&#x27;s Remote UE context list decoder is a stub) keeps each relay&#x27;s remote UEs and answers with a Remote UE Report Response." },
    nfs: { x: 390, y: 44, t: "SBI NFs", s: "NRF AUSF UDM UDR", s2: "PCF NSSF NEF CHF", d: "The rest of the free5GC control plane." },
    dn: { x: 30, y: 172, t: "ext-dn", s: "OAI trf-gen", s2: "192.168.70.132", d: "The data network endpoint. The edge runs inside its network namespace." },
    vnf: { x: 210, y: 172, t: "gNB VNF", s: "OAI nr-softmodem", s2: "MAC RLC PDCP RRC", d: "The upper gNB (nFAPI VNF). Its MAC schedules the two gateways&#x27; UEs, and the remote&#x27;s while it is attached." },
    pnf: { x: 390, y: 172, t: "gNB PNF", s: "OAI + nrlink L1", s2: "nFAPI P5 / P7", d: "The lower gNB. nrlink&#x27;s L1 hands each slot to gnb_bridge and waits for its exchange over the air." },
    edge: { x: 30, y: 300, t: "edge", s: "agv_chain.py edge", s2: "agvc-edge · core clock", d: "Logs every message&#x27;s arrival on the core clock, and sends each AGV&#x27;s commands to the address and port its last message came from. Behind the layer-3 relay that is the relay UE&#x27;s address, rewritten by its NAT, so a remote&#x27;s commands go down the relay&#x27;s PDU session and its NAT sends them on over the sidelink. It also logs the relays&#x27; Remote UE Reports (which remote, its User Info ID, the address its relay leased it, connected or released), which arrive up the relay sessions." },
    live: { x: 210, y: 300, t: "live page", s: "agv_chain_live.py", s2: ":8090 · udp/5301", d: "Joins the controller&#x27;s events with the edge&#x27;s arrivals into one-way delays and serves the live page; the clock on udp/5298 gives the controller its offset to the core." },
    gbr: { x: 390, y: 300, t: "gnb_bridge", s: "--ues 1,2,3", s2: "--sl-window 25 · LED", d: "Carries the gNB&#x27;s slots to the gNB board: downlink to every attached UE, polls to each attached UE&#x27;s board in uplink slots. After slot 9 of each frame it announces a 25 ms sidelink window and stays off the air. It flashes the gNB board&#x27;s LED green for each stretch of NR slots and blue for each sidelink window (6 ms dark between), so the board shows the slot scheme as it runs." },
    gbd: { x: 390, y: 524, t: "gNB board", s: "nrlink v4, USB", s2: "3TJ2B15JGASV3", d: "The gNB&#x27;s radio, 1 Mbit/s GFSK on 2402 MHz. It is quiet during the sidelink windows. Its LED: green while it carries NR slots, blue during each sidelink window." },
    ctl: { x: 620, y: 410, w: 1000, t: "controller · agv_chain.py ue, cover topology, layer-3 relay", s: "the three AGVs&#x27; computers: motion, positioning and LiDAR data, the gNB signal by position, coverage loss, PC5 discovery and direct links, LEDs", s2: "also the UE boards&#x27; bridge: gives each attached nrUE its slots and answers polls; carries the sidelink UEs&#x27; slots in the windows", d: "One process on the UE PC for the three AGVs. Every AGV makes its own positioning messages and LiDAR scans and sends them up its own UE while its gNB signal is good. Below −100 dBm (threshHighRemote) it becomes a remote: from the relays&#x27; discovery announcements it picks the strongest above −90 dBm SD-RSRP, sets up a PC5-S direct link, gets an address by DHCPv4 and sends its packets as plain IP over the sidelink; the relay&#x27;s kernel does the rest. An AGV announces itself as a relay only while its Uu RSRP is within the relay thresholds and its relay PDU session is up. In the dual-gateway runs the controller relayed at the application instead. It sets each board&#x27;s LED from the roles." },
  };
  for (let i = 0; i < 3; i++) {
    const n = AGV[i].n, bx = BX[i];
    N["ue" + i] = { x: bx, y: 172, agv: i, t: `nrUE${i + 1} · AGV ${n}`, s: `OAI · oai-nrue${i + 1}`, s2: "", d: `AGV ${n}&#x27;s NR UE. In the coverage scenario it is stopped by a radio link failure in the cold store (below −110 dBm for 1 s) and started again 2 s after the signal is back (attach about 3.7 s, then 8 s to settle). It has two PDU sessions: its own (DNN internet, oaitun_ue1) and the relay session (DNN relay, oaitun_rly), which carries what AGV ${n} relays. While AGV ${n} relays, its netns is the relay&#x27;s NAT router. In the dual runs it was stopped when AGV ${n} became the remote, once both new gateways were ready.` };
    N["sl" + i] = { x: bx + 170, y: 172, agv: i, t: `SL UEs · AGV ${n}`, s: "OAI sidelink, mode 2", s2: "two: one per pair", d: `AGV ${n}&#x27;s two sidelink UEs, one in each pair it belongs to (A–B, B–C, C–A), each with its own IP over the sidelink (oaitun). With the layer-3 relay the remote&#x27;s packets go out on the one paired with its relay; the PC5 signalling (announcements, direct link request, accept, release) goes over the same IP, UDP port 6602.` };
    N["fw" + i] = { x: bx, y: 300, agv: i, t: `fwd ${n}`, s: `agvc-fwd${i + 1}`, s2: "2 sessions, NAT", d: `Sidecar in nrUE${i + 1}&#x27;s network: puts AGV ${n}&#x27;s own messages into the PDU session and hands down its commands. For the layer-3 relay, once the relay session is up, it sets up nrUE${i + 1}&#x27;s namespace as a NAT router: a GRE tunnel to each of AGV ${n}&#x27;s sidelink UEs over the AGV&#x27;s LAN, a policy route from those tunnels into the relay session (oaitun_rly), MASQUERADE, and each sidelink UE&#x27;s DHCPv4 pool routed back to it. It tells the controller the relay session is up (no announcements before that) and sends the Remote UE Reports up that session.` };
    N["sf" + i] = { x: bx + 170, y: 300, agv: i, t: `slfwd ${n}`, s: "agvc-slf sidecars", s2: "routing, DHCPv4", d: `Sidecars of AGV ${n}&#x27;s sidelink UEs. As a relay it runs a DHCPv4 server with its own pool (10.40.x.0/28), and the kernel in the sidelink UE&#x27;s namespace routes what comes in on oaitun through a GRE tunnel to nrUE${i + 1} and back. As a remote it runs the DHCPv4 client, puts the leased address on oaitun and binds AGV ${n}&#x27;s app socket to it, straight to the edge over the sidelink. The PC5 discovery (UDP 6602) and PC5-S (UDP 6603) sockets are here too.` };
    N["bd" + i] = { x: bx + 85, y: 524, agv: i, led: 1, t: `AGV ${n} board`, s: i === 2 ? "nrlink v6, via its Pico" : "nrlink v4, USB", s2: SER[i], d: `FRDM-MCXW72 for AGV ${n}: it carries nrUE${i + 1}&#x27;s slots and AGV ${n}&#x27;s sidelink UEs&#x27; slots on the same radio. ${i === 2 ? " Its host link is the Pico on LPUART0 (a C USB bridge, 1 Mbaud, as fast as the MCU-Link port); nrlink v6 listens on both ports and answers on the one that last spoke." : ""} Its LED: red when ${n} sends over the sidelink, blue when ${n} relays another AGV (in the dual runs: serves the remote), green on its own UE only; blinking while its UE attaches.` };
  }
  for (const n of Object.values(N)) n.w = n.w || W;
  const dq = t => t.replace(/&#x27;/g, "'");
  const pt = (id, side, f = 0.5) => { const n = N[id]; return side === "t" ? [n.x + n.w * f, n.y] : side === "b" ? [n.x + n.w * f, n.y + H] : side === "l" ? [n.x, n.y + H * f] : [n.x + n.w, n.y + H * f]; };
  const vl = (a, b, f = 0.5, g = f) => [pt(a, "b", f), pt(b, "t", g)];
  // dyn: how the link's colour and state follow the replay
  const L = [
    { id: "sbi", st: "ctl", pts: [pt("amf", "r"), pt("nfs", "l")], name: "SBI", cap: "HTTP/2", what: "Service-based interface inside the core." },
    { id: "n2", st: "ctl", pts: vl("amf", "vnf", .7), lbl: [325, 160, "start", "N2"], name: "N2", cap: "NGAP over SCTP", what: "gNB to AMF: UE contexts, PDU session resources." },
    { id: "n3", dyn: "core", pts: [pt("upf", "b", .8), [150, 152], [250, 152], [250, 172]], lbl: [160, 147, "start", "N3"], name: "N3", cap: "GTP-U", what: "UPF to gNB, for every attached UE." },
    { id: "n6", dyn: "core", pts: vl("upf", "dn", .3), lbl: [82, 140, "start", "N6"], name: "N6", cap: "IP, Docker bridge", what: "Decapsulated UE traffic between the UPF and ext-dn." },
    { id: "p7", dyn: "core", pts: [pt("vnf", "r"), pt("pnf", "l")], name: "nFAPI P7", cap: "UDP, per slot", what: "Slot messages between VNF and PNF." },
    { id: "pg", dyn: "core", pts: vl("pnf", "gbr"), lbl: [472, 262, "start", "UDP"], name: "PNF ↔ gnb_bridge", cap: "UDP, lockstep per slot", what: "Each slot is handed to the bridge, which answers when the exchange over the air is done." },
    { id: "gu", dyn: "core", pts: vl("gbr", "gbd"), lbl: [472, 494, "start", "USB 1 Mbaud"], name: "USB", cap: "1 Mbaud SLIP", what: "Serial link to the gNB board." },
    { id: "de", dyn: "core", pts: vl("dn", "edge"), lbl: [112, 262, "start", "netns"], name: "shared network", cap: "agvc-edge in oai-ext-dn&#x27;s namespace", what: "The edge receives what the UEs send to ext-dn and sends the commands from there." },
    { id: "el", st: "ctl", pts: [pt("edge", "r"), pt("live", "l")], name: "docker logs", cap: "JSON lines", what: "The live server follows the edge&#x27;s log." },
    { id: "cl", st: "ctl", pts: [pt("ctl", "l", .5), [300, 446], pt("live", "b", .6)], lbl: [310, 440, "start", "LAN · events udp/5301"], name: "controller events", cap: "UDP JSON over the LAN", what: "Everything the controller logs (positions, roles, hops, messages, LEDs, attaches) goes to the live page." },
  ];
  for (let i = 0; i < 3; i++) {
    const n = AGV[i].n, bx = BX[i];
    L.push({ id: "pdu" + i, dyn: "uu", agv: i, pts: vl("ue" + i, "fw" + i, .3), lbl: [bx + 52, 262, "start", "oaitun"], name: `PDU session, AGV ${n}`, cap: "IP in the UE&#x27;s namespace: oaitun_ue1, oaitun_rly", what: `AGV ${n}&#x27;s data, up and down, through its own UE&#x27;s first session; what it relays, through the second (DNN relay).` });
    L.push({ id: "fc" + i, dyn: "uu", agv: i, pts: vl("fw" + i, "ctl", .3, (bx + 45 - 620) / 1000), name: `fwd ${n} ↔ controller`, cap: "UDP on the UE PC", what: `AGV ${n}&#x27;s messages to its fwd sidecar, commands back.` });
    L.push({ id: "us" + i, dyn: "uu", agv: i, pts: [pt("ue" + i, "b", .8), [bx + 120, 262], [bx + 150, 262], [bx + 150, 380], [bx + 120, 380], [bx + 120, 410]], name: `nrUE${i + 1} slots`, cap: "UDP: slot items", what: `The controller hands nrUE${i + 1} its downlink slots from AGV ${n}&#x27;s board and returns its uplink when the gNB polls that board.` });
    L.push({ id: "sp" + i, dyn: "sl", agv: i, pts: vl("sl" + i, "sf" + i, .3), lbl: [bx + 222, 262, "start", "oaitun"], name: `sidelink IP, AGV ${n}`, cap: "IP over the sidelink UEs", what: `A remote&#x27;s IP packets to the edge and the commands back, the PC5 discovery and PC5-S messages, and DHCPv4, to and from AGV ${n}.` });
    L.push({ id: "sc" + i, dyn: "sl", agv: i, pts: vl("sf" + i, "ctl", .3, (bx + 215 - 620) / 1000), name: `slfwd ${n} ↔ controller`, cap: "Unix sockets on the UE PC", what: `AGV ${n}&#x27;s messages when it is a remote, and the PC5 signalling the controller sends and receives. What AGV ${n} relays never comes up here: the kernel routes it.` });
    L.push({ id: "lan" + i, dyn: "sl", agv: i, pts: [pt("fw" + i, "r"), pt("sf" + i, "l")], name: `AGV ${n}&#x27;s LAN`, cap: `agvc-lan-${"abc"[i]}, 10.30.${i}.0/24, GRE tunnels`, what: `Inside AGV ${n}: its nrUE (.2) and its sidelink UEs (.10 and up). A relayed packet goes from the sidelink UE to the nrUE in a GRE tunnel, since the internal Docker network drops packets addressed outside it.` });
    L.push({ id: "ss" + i, dyn: "sl", agv: i, pts: [pt("sl" + i, "b", .8), [bx + 290, 262], [bx + 316, 262], [bx + 316, 380], [bx + 290, 380], [bx + 290, 410]], name: `sidelink UE slots, AGV ${n}`, cap: "UDP: PSBCH, SCI, transport blocks", what: `The controller carries AGV ${n}&#x27;s sidelink UEs&#x27; slots to its board in the windows.` });
    L.push({ id: "ub" + i, dyn: "board", agv: i, pts: [[bx + 160, 482], [bx + 160, 524]], lbl: [bx + 168, 508, "start", i === 2 ? "USB · Pico" : "USB"], name: `USB, AGV ${n} board`, cap: i === 2 ? "1 Mbaud SLIP through the Pico (C bridge) to LPUART0" : "1 Mbaud SLIP, MCU-Link", what: `NR and sidelink slots of AGV ${n} share this link and the board.` });
    L.push({ id: "air" + i, dyn: "uu", agv: i, air: 1, pts: [[480 - 30 * i, 596], [480 - 30 * i, 650 + 24 * i], [bx + 115, 650 + 24 * i], [bx + 115, 596]], name: `Uu, AGV ${n}`, cap: "NR slots over nrlink, 2402 MHz", what: `The gNB board&#x27;s downlink to AGV ${n}&#x27;s board and the polls for its uplink, between the windows.` });
  }
  [[0, 1], [1, 2], [0, 2]].forEach(([a, b], k) => {
    const ya = 726 + 16 * k;
    L.push({ id: `pc5${a}${b}`, dyn: "pc5", pair: [a, b], pts: [[BX[a] + 205 + 8 * k, 596], [BX[a] + 205 + 8 * k, ya], [BX[b] + 190 - 8 * k, ya], [BX[b] + 190 - 8 * k, 596]], name: `PC5, AGV ${AGV[a].n} – AGV ${AGV[b].n}`, cap: "NR sidelink mode 2, in the 25 ms windows", what: `The sidelink between AGV ${AGV[a].n} and AGV ${AGV[b].n}: position beacons always, discovery announcements from an AGV allowed to relay; the PC5-S link set-up and DHCPv4 when one of them picks the other as its relay; then the remote&#x27;s IP packets up and its commands down.` });
  });
  let sel = { node: "ctl" }, svg = null, cur = null;
  function build() {
    svg = $("arch3"); if (!svg) return; svg.textContent = ""; svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
    const X = x => x + OX, P = p => p.map(([x, y]) => `${X(x)} ${y}`).join(" L ");
    LANES.forEach(([y0, y1, name, sub, tok], i) => {
      ael("rect", { x: 0, y: y0, width: VW, height: y1 - y0, fill: `color-mix(in srgb, var(${tok}) ${i % 2 ? 5 : 8}%, var(--panel-2))` }, svg);
      ael("rect", { x: 0, y: y0, width: 5, height: y1 - y0, fill: `var(${tok})` }, svg);
      if (i) ael("line", { x1: 0, x2: VW, y1: y0, y2: y0, stroke: "var(--line)", "stroke-width": 1.2 }, svg);
      ael("text", { class: "lanehd", x: 18, y: y0 + 28, fill: `var(${tok})` }, svg).textContent = name;
      sub.forEach((t, j) => { ael("text", { class: "lanesub", x: 18, y: y0 + 46 + j * 14 }, svg).textContent = t; });
    });
    ael("line", { x1: OX, x2: OX, y1: 0, y2: VH, stroke: "var(--line)" }, svg);
    for (const [x0, x1] of [[14, 560], [600, 1640]]) ael("rect", { class: "pcframe", x: X(x0), y: 8, width: x1 - x0, height: 604, rx: 10 }, svg);
    ael("text", { class: "pctitle", x: X(550), y: 34, "text-anchor": "end" }, svg).textContent = "CORE PC · claudepc";
    ael("text", { class: "pcsub", x: X(550), y: 160, "text-anchor": "end" }, svg).textContent = "";
    ael("text", { class: "pctitle", x: X(615), y: 34 }, svg).textContent = "UE PC · claudepc2";
    for (let i = 0; i < 3; i++) ael("text", { class: "pcsub", x: X(BX[i]), y: 160 }, svg).textContent = `AGV ${AGV[i].n}`;
    for (const l of L) {
      const g = ael("g", { class: "link " + (l.st || "up"), "data-id": l.id, tabindex: 0, role: "button", "aria-label": l.name }, svg);
      const d = "M" + P(l.pts);
      ael("path", { class: "hit", d }, g); l.base = ael("path", { class: "base", d, stroke: "var(--ink-2)" }, g); ael("path", { class: "flow", d }, g);
      if (l.lbl) { const [lx, ly, a, t] = l.lbl; ael("text", { class: "ltxt", x: X(lx), y: ly, "text-anchor": a }, g).textContent = t; }
      l.g = g;
      g.addEventListener("click", () => pick({ link: l.id }));
      g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick({ link: l.id }); } });
    }
    ael("text", { class: "ltxt", x: X(30), y: 660 }, svg).textContent = "Uu: NR slots between the windows";
    ael("text", { class: "ltxt", x: X(30), y: 744 }, svg).textContent = "PC5: sidelink, in the windows";
    for (const [id, n] of Object.entries(N)) {
      const g = ael("g", { class: "node", "data-id": id, tabindex: 0, role: "button", "aria-label": n.t }, svg);
      ael("rect", { class: "box", x: X(n.x), y: n.y, width: n.w, height: H, rx: 6 }, g);
      n.bar = n.agv !== undefined ? ael("rect", { x: X(n.x), y: n.y + 9, width: 3, height: H - 18, fill: "var(--line)" }, g) : null;
      ael("text", { class: "n1", x: X(n.x) + 12, y: n.y + 24 }, g).textContent = dq(n.t);
      ael("text", { class: "n2", x: X(n.x) + 12, y: n.y + 43 }, g).textContent = dq(n.s);
      n.t3 = ael("text", { class: "n3", x: X(n.x) + 12, y: n.y + 59 }, g); n.t3.textContent = dq(n.s2);
      if (n.led) n.ledel = ael("circle", { class: "led", cx: X(n.x) + n.w - 14, cy: n.y + 14, r: 7, fill: "var(--line)" }, g);
      n.g = g;
      g.addEventListener("click", () => pick({ node: id }));
      g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick({ node: id }); } });
    }
    pick(sel);
  }
  const RN = ["gateway, serving the remote (blue)", "gateway, NR only (green)", "remote (red)"], RNC = ["relaying another AGV (blue)", "own UE only (green)", "over the sidelink (red)"];
  function state(r) {   // roles by LED at the replay's time, the gateway serving the remote, who is attaching
    const role = [0, 1, 2].map(i => ledRole(r, i, r.t)), rem = role.indexOf(2);
    let srv = isCover(r) ? (rem >= 0 ? relayOf(r, rem) : -1) : servingAt(r.tr, r.t);
    if (srv === null || srv === rem) srv = -1;
    const att = [0, 1, 2].map(i => (r.tr.att || []).some(([tc, a, tu]) => a === i && tc * 1000 <= r.t && (tu === null || tu * 1000 > r.t)));
    const ueUp = [0, 1, 2].map(i => role[i] !== 2 || att[i] || (!isCover(r) && srv < 0) || (isCover(r) && relayOf(r, i) === i));
    return { role, srv, rem, att, ueUp, key: role.join() + srv + att.join() };
  }
  function apply(s) {
    for (const l of L) {
      let col = "var(--ink-2)", st = l.st || "up";
      if (l.dyn === "uu") { col = RC[s.role[l.agv]]; st = s.ueUp[l.agv] ? "up" : "idle"; if (s.role[l.agv] === 2 && st === "up") col = RC[2]; }
      else if (l.dyn === "sl") { col = RC[s.role[l.agv]]; st = s.role[l.agv] === 1 ? "idle" : (s.srv >= 0 ? "up" : "idle"); if (st === "idle") col = "var(--pc5)"; }
      else if (l.dyn === "board") col = RC[s.role[l.agv]];
      else if (l.dyn === "pc5") { const on = s.srv >= 0 && l.pair.includes(s.rem) && l.pair.includes(s.srv); col = on ? RC[2] : "var(--pc5)"; st = on ? "up" : "idle"; }
      if (l.st === "ctl") { st = "ctl"; col = "var(--ink-3)"; }
      l.g.setAttribute("class", `link ${st}` + (sel.link === l.id ? " sel" : "")); l.base.setAttribute("stroke", col);
    }
    for (const [id, n] of Object.entries(N)) {
      if (n.agv === undefined) continue;
      const rl = s.role[n.agv];
      n.bar.setAttribute("fill", RC[rl]);
      if (id.startsWith("ue")) { n.t3.textContent = s.ueUp[n.agv] ? (s.att[n.agv] ? "attaching" : rl === 2 ? "up: sends its own" : rl === 0 ? "up: own + remote's" : "up: its own data") : "stopped (remote)"; n.g.classList.toggle("stopped", !s.ueUp[n.agv]); }
      if (id.startsWith("bd") || id.startsWith("sl") || id.startsWith("fw") || id.startsWith("sf")) if (!n.led) n.t3.textContent = id.startsWith("fw") || id.startsWith("sf") ? n.s2 : n.s2;
      if (n.ledel) { n.ledel.setAttribute("fill", RC[rl]); n.ledel.classList.toggle("blink", s.att[n.agv]); n.t3.textContent = `${SER[n.agv]} · ${["blue", "green", "red"][rl]}`; }
    }
    const nm = i => "AGV " + AGV[i].n;
    $("archnow").textContent = `${window.ARLIVE ? "live" : "replay"} t = ${(cur.t / 1000).toFixed(0)} s · ${s.rem < 0 ? "all three in coverage, each on its own UE" : s.srv >= 0 ? nm(s.srv) + " carries " + nm(s.rem) + "&#x27;s traffic".replace("&#x27;", "'") : nm(s.rem) + (isCover(cur) ? " has no gateway yet" : " sends up its own UE")} · click any box or link`;
  }
  let lastKey = null;
  function update(r) {
    if (!svg || !r || !r.tr) return;
    cur = r; const s = state(r);
    if (s.key !== lastKey) { lastKey = s.key; apply(s); detail(); }
    else $("archnow").textContent = $("archnow").textContent.replace(/t = \d+ s/, `t = ${(r.t / 1000).toFixed(0)} s`);
  }
  function detail() {
    const box = $("adetail"); if (!box) return;
    const s = cur && cur.tr ? state(cur) : null;
    if (sel.link) {
      const l = L.find(x => x.id === sel.link);
      const now = !s ? "" : l.dyn === "pc5" ? (l.g.classList.contains("up") ? "carrying the remote&#x27;s data, and beacons" : "beacons only") : l.st === "ctl" ? "control" : l.g.classList.contains("up") ? "carrying data" : "idle";
      box.innerHTML = `<span class="label">Link</span><h3>${l.name}</h3><dl><dt>Carries</dt><dd>${l.cap}</dd>${now ? `<dt>Now</dt><dd>${now}</dd>` : ""}</dl><p>${l.what}</p>`;
    } else {
      const n = N[sel.node], pc = n.x >= 600 ? "UE PC, claudepc2" : "Core PC, claudepc";
      const now = s && n.agv !== undefined ? `<dt>Now</dt><dd>AGV ${AGV[n.agv].n}: ${(isCover(cur) ? RNC : RN)[s.role[n.agv]]}${s.att[n.agv] ? ", UE attaching" : ""}</dd>` : "";
      box.innerHTML = `<span class="label">${pc}</span><h3>${n.t}</h3><dl><dt>Is</dt><dd>${n.s}${n.s2 ? " · " + n.s2 : ""}</dd>${now}</dl><p>${n.d}</p>`;
    }
  }
  function pick(x) {
    sel = x;
    if (!svg) return;
    svg.querySelectorAll(".sel").forEach(e => e.classList.remove("sel"));
    svg.querySelector(`[data-id="${x.link || x.node}"]`)?.classList.add("sel");
    detail();
  }
  build();
  return { update };
})();
function seqTabs() {
  const ts = [...document.querySelectorAll('#sequences [role="tab"]')];
  const show = (b, focus) => { ts.forEach(t => { const on = t === b; t.setAttribute("aria-selected", on); t.tabIndex = on ? 0 : -1; $(t.getAttribute("aria-controls")).hidden = !on; }); if (focus) b.focus(); };
  ts.forEach((b, k) => {
    b.addEventListener("click", () => show(b));
    b.addEventListener("keydown", e => { const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key]; const n = d ? ts[(k + d + ts.length) % ts.length] : null; if (n) { e.preventDefault(); show(n, true); } });
  });
}
seqTabs();
function pickRun() {
  const tr = RUNS[$("run").value]; rep = new DualReplay(tr); rep.seek(30000);
  const cv = tr.topology === "cover";
  $("fkey").textContent = cv ? "AGV colour = its board's LED: red over the sidelink (no gNB, or re-attaching), blue relaying another AGV, green own UE only; shading: gNB signal below −100 dBm (amber) and −110 dBm (red)" : "AGV colour = its board's LED: red remote, blue the gateway serving it, green the other gateway";
  $("fkey2").textContent = cv ? "blue lines: Uu to the gNB (dashed while attaching) · amber: sidelink to the AGV carrying it" : "amber solid: the remote's sidelink to the gateway serving it";
  $("rmeta").textContent = `${tr.level} · sidelink window ${tr.window_ms} ms · ${tr.topology === "cover" ? "coverage by position: out of coverage or re-attaching, an AGV sends over the sidelink (" + (tr.relay === "l3" ? "layer-3 relay: discovery, direct link, routing and NAT" : tr.pick === "near" ? "nearest gateway, relayed by the app" : tr.pick + " pick, relayed by the app") + ")" : "remote picks " + (tr.pick === "first" ? "gateway 1 unless it is not ready" : "the less busy gateway at every beacon")}`;
}
fetch("agv-dual-runs.json").then(r => r.json()).then(d => {
  Object.assign(RUNS, d);
  $("run").innerHTML = Object.keys(RUNS).map(k => `<option value="${k}">${k}: ${(RUNS[k].pos.at(-1)[0] / 60).toFixed(0)} min, ${RUNS[k].topology === "cover" ? (RUNS[k].rlf || []).length + " coverage losses" : RUNS[k].chains.length + " role changes"}</option>`).join("");
  pickRun();
}).catch(() => { $("rmeta").textContent = "The recorded runs (agv-dual-runs.json) could not be loaded."; });
$("run").onchange = pickRun;
$("rplay").textContent = rplaying ? "Pause" : "Play";
$("rplay").onclick = () => { rplaying = !rplaying; $("rplay").textContent = rplaying ? "Pause" : "Play"; };
$("rpos").oninput = () => { if (rep) rep.seek($("rpos").value / 1000 * rep.T); };
$("rpos").onpointerdown = () => rdrag = true; $("rpos").onpointerup = () => rdrag = false;
let last = performance.now(), uiAt = 0;
function frame(now) {
  const dt = Math.min(100, now - last); last = now;
  if (rep) {
    if (rplaying && !rdrag) { rep.step(dt * +$("rspeed").value); if (rep.t >= rep.T) rep.seek(0); }
    drawFloorDual(rep, $("floor2")); if (!window.ARLIVE) AR.update(rep);
    if (now - uiAt > 250) { uiAt = now; drawTimelineDual(rep, $("timeline2")); drawFlows(rep, "flows2"); renderSide2(rep); $("rclock").textContent = `t = ${(rep.t / 1000).toFixed(0)} s`; if (!rdrag) $("rpos").value = Math.round(1000 * rep.t / rep.T); }
  }
  requestAnimationFrame(frame);
}
readCols();
{ const cs = getComputedStyle(document.documentElement); COL.disp = cs.getPropertyValue("--display").trim(); COL.mono = cs.getPropertyValue("--mono").trim(); }
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", readCols);
new MutationObserver(readCols).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
requestAnimationFrame(frame);
</script>
"""

if __name__ == "__main__":
    main()
