"""Run history (Runs tab) and how a route is chosen now (Gateways tab) for the AGV dual-gateway page."""
import datetime
import html

CHANGED = {
    "dual_L1_w25": ("Two gateways", "The first dual run: two AGVs on the gNB; the remote picks the less busy gateway per message. L1, 25 ms window."),
    "dual_L2_w25": ("Double load", "L2: twice the positioning and LiDAR rate."),
    "dual_L2_w35": ("Wider window", "Sidelink window 25 → 35 ms."),
    "dual_L2_w50": ("Wider window", "Sidelink window 35 → 50 ms."),
    "dual_L2_w35_first": ("One gateway", "35 ms; the remote always uses gateway 1."),
    "dual_L2_w25_first": ("One gateway", "25 ms; the remote always uses gateway 1."),
    "cover_L1_w25": ("Coverage by position", "A shielded cold store; the AGVs spread out; a sticky gateway pick."),
    "cover_near_L1_w25": ("Nearest relay", "The nearest AGV with a session relays."),
    "cover_p5_L1_w25": ("Platoon", "The AGVs drive as a platoon, 5 m apart; a relay only if nearer than the gNB."),
    "cover_p8last_L1_w25": ("Last link holds", "8 m gaps; the last AGV with a session stops at the door."),
    "cover_p8lastq_L1_w25": ("Uplink queued", "Up to 5 s of uplink queued while an AGV re-attaches."),
    "cover_p8onespread_L1_w25": ("One sidelink at a time", "Only one AGV on the sidelink; only the AGVs behind wait."),
    "cover_p8one_L1_w25": ("Platoon kept together", "An AGV with a session waits for the one behind."),
    "cover_p8sl_L1_w25": ("Sidelink until NR is up", "An AGV keeps its relay until its own NR link is up."),
    "cover_l3_L1_w25": ("Layer-3 relay", "Discovery, a direct link, IP routing and NAT in the relay (TS 23.304)."),
    "cover_l3b_L1_w25": ("3GPP procedures", "SIB12-style thresholds, PC5-S set-up, DHCPv4, a relay PDU session."),
    "cover_l3e_L1_w25": ("Secured relay", "PC5 security, measured SD-RSRP, QoS, Remote UE Report to the SMF."),
    "cover_m1_L1_w25": ("Sidelink mode 1", "The gNB schedules the sidelink: a slice per AGV, a pool for the remote."),
}
PHASES = [("dual_", "Two gateways · application relay · 12–13 Nov"), ("cover_l3", "Layer-3 relay · 19 Nov"),
          ("cover_m1", "Sidelink mode 1 · 20 Nov"), ("cover_", "Coverage by position · 13–16 Nov")]


def phase(trace):
    return next(n for p, n in PHASES if trace.startswith(p))


def run_history(runs, dual):
    rows, last = [], None
    items = [(runs[k]["t0"], k, tr) for k, tr, *_ in dual if k in runs]
    for t0, k, tr in sorted(items):
        sm = runs[k]["summary"]
        up = [(v["n"], v["lost"]) for q, v in sm.items() if q.split("|")[1] in ("det", "lidar") and v.get("n")]
        lost = sum(n * l for n, l in up) / max(1, sum(n for n, _ in up))
        rd = sm.get("remote|det") or {}
        ph = phase(tr)
        if ph != last:
            rows.append(f'<tr class="rh-ph"><th colspan="6">{ph}</th></tr>')
            last = ph
        bad = lambda v: ' class="n bad"' if v >= 5 else ' class="n"'
        what, why = CHANGED.get(tr, (k, ""))
        rows.append(f'<tr><td class="n">{datetime.datetime.fromtimestamp(t0).strftime("%-d %b %H:%M")}</td>'
                    f'<td><b>{html.escape(what)}</b></td><td>{html.escape(why)}</td>'
                    f'<td{bad(lost)}>{lost:.1f} %</td><td class="n">{rd.get("p50", 0):.0f} ms</td><td{bad(rd.get("lost", 0))}>{rd.get("lost", 0):.1f} %</td></tr>')
    return f"""
  <section class="card" aria-label="Run history" id="history" data-tab="runs">
    <div class="hd"><h2>Every run so far</h2><span class="meta">{len(items)} board runs, oldest first · each row: what changed from the one before</span></div>
    <div class="scroll"><table class="rh"><thead><tr><th>Started</th><th>Change</th><th>What changed</th><th>Uplink lost</th><th>Remote positioning p50</th><th>Remote lost</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table></div>
    <div class="bd"><p class="short">Uplink lost: positioning and LiDAR of all three AGVs. Remote: the AGV sending over the sidelink. Red: 5 % or more.</p></div>
  </section>
"""


RH_CSS = """
.rh td, .rh th { vertical-align: top; }
.rh td.n, .rh th:nth-child(n+4) { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.rh td.bad { color: var(--error); font-weight: 600; }
.rh tr.rh-ph th { background: var(--bg-alt); color: var(--navy); font: 600 14px var(--head); letter-spacing: 0; text-transform: none; text-align: left !important; padding: 10px 12px; }
.rt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 16px; padding: 24px; }
.rt-step { border: 1px solid var(--border); border-radius: 4px; padding: 16px; background: #fff; display: grid; gap: 8px; align-content: start; }
.rt-step .st-n { font-size: 13px; }
.rt-step h3 { margin: 0; font: 600 18px var(--head); color: var(--navy); }
.rt-step ul { margin: 0; padding-left: 18px; font-size: 14.5px; line-height: 1.45; color: var(--navy); }
.rt-step code { font-size: 13px; }
.rt-cmp { margin: 0 24px 24px; }
"""

ROUTES = """
  <section class="card" aria-label="How a route is chosen now" id="routes" data-tab="gateways">
    <div class="hd"><h2>How an AGV picks its route now</h2><span class="meta">layer-3 relay, TS 23.304 / 38.304 · per link, not per message</span></div>
    <div class="rt-grid">
      <div class="rt-step"><span class="st-n">01</span><h3>Who may relay</h3><ul><li>Uu RSRP between −95 and −31 dBm, 2 dB hysteresis</li><li>Own session settled, relay PDU session up</li><li>Then it announces itself on every PC5 link, every 0.5 s</li></ul></div>
      <div class="rt-step"><span class="st-n">02</span><h3>Who needs a relay</h3><ul><li>Own Uu RSRP below −100 dBm</li><li>Above it: its own UE; while re-attaching it queues</li></ul></div>
      <div class="rt-step"><span class="st-n">03</span><h3>Which relay</h3><ul><li>Announcements heard in the last 1.5 s</li><li>SD-RSRP above −90 dBm, measured per board pair</li><li>The strongest; it switches only for 2 dB more</li><li>A relay whose set-up failed: barred for 3 s</li></ul></div>
      <div class="rt-step"><span class="st-n">04</span><h3>The route</h3><ul><li>PC5-S link, secured; an address by DHCPv4</li><li>Plain IP to the edge from that address</li><li>Relay kernel: GRE to its UE, NAT into the relay PDU session</li><li>Commands come back to that NAT address</li></ul></div>
      <div class="rt-step"><span class="st-n">05</span><h3>Back to its own UE</h3><ul><li>Keeps the relay until its own NR session is up</li><li>Stops driving before leaving its relay's reach</li><li>Releases address and link; the relay tells the SMF</li></ul></div>
    </div>
    <div class="scroll rt-cmp"><table><thead><tr><th></th><th>Two-gateway runs (12–13 Nov)</th><th>Now</th></tr></thead><tbody>
      <tr><td><b>Who decides</b></td><td>the remote's program</td><td>the remote, from discovery (TS 38.304 8.2)</td></tr>
      <tr><td><b>How often</b></td><td>every message</td><td>when a link is set up; kept while good</td></tr>
      <tr><td><b>Rule</b></td><td><code>--pick</code> load · sticky · split · near · first</td><td>thresholds, strongest SD-RSRP, 2 dB to switch</td></tr>
      <tr><td><b>Relaying</b></td><td>the gateway's program re-sends</td><td>the relay's kernel routes and NATs</td></tr>
    </tbody></table></div>
  </section>
"""
