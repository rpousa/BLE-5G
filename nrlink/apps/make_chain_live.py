#!/usr/bin/env python3
"""Builds the core PC's live pages, served by agv_chain_live.py, from the artifact pages: the same page with a live
card on top that reuses the page's floor, timeline and scatter drawing, fed by server-sent events. Run it again
whenever an artifact page changes.

  make_chain_live.py [chain|dual]
    chain: ~/BLE-5G/docs/pages/agv-relay-chain.html  -> apps/agv_chain_live.html   (the relay chain)
    dual:  ~/BLE-5G/docs/pages/agv-dual-gateway.html -> apps/agv_dual_live.html    (two gateways)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODE = sys.argv[1] if len(sys.argv) > 1 else "chain"
SRC = os.path.expanduser({"chain": "~/BLE-5G/docs/pages/agv-relay-chain.html", "dual": "~/BLE-5G/docs/pages/agv-dual-gateway.html",
                           "cover": "~/BLE-5G/docs/pages/agv-dual-gateway.html"}[MODE])
OUT = os.path.join(HERE, {"chain": "agv_chain_live.html", "dual": "agv_dual_live.html", "cover": "agv_cover_live.html"}[MODE])
TITLE = {"chain": "AGV Relay Chain", "dual": "AGV Dual Gateway", "cover": "AGV Coverage Relay"}[MODE]
LEDTXT = {"chain": '<b style="color:var(--rpivot)">blue pivot</b>, <b style="color:var(--rrelay)">green relay</b>, '
                   '<b style="color:var(--rremote)">red remote</b>',
          "dual": '<b style="color:var(--rremote)">red the remote</b>, <b style="color:var(--rpivot)">blue the gateway the remote '
                  'sends through now</b> (NR and sidelink), <b style="color:var(--rrelay)">green the other gateway</b> (NR to the gNB '
                  'only); the remote stays on one gateway at least 5 s',
          "cover": '<b style="color:var(--rremote)">red: out of coverage (or re-attaching), sending over the sidelink</b>, '
                   '<b style="color:var(--rpivot)">blue: in coverage and carrying another AGV&#x27;s traffic</b>, '
                   '<b style="color:var(--rrelay)">green: in coverage, its own UE only</b>; a board blinks while its UE attaches'}[MODE]

CSS = """
.lleds { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }
.lboard { display: flex; gap: 12px; align-items: center; padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); min-width: 0; }
.lboard .sub { color: var(--ink-2); font-size: 12.5px; }
.lboard .sn { font-family: var(--mono); font-size: 12px; color: var(--ink-3); }
.lled { width: 24px; height: 24px; border-radius: 50%; background: var(--c); box-shadow: 0 0 14px var(--c); flex: none; border: 2px solid var(--panel); }
.lled.blink { animation: lblink 1s steps(2, jump-none) infinite; }
.lled.off { box-shadow: none; }
@keyframes lblink { 50% { opacity: .18; box-shadow: none; } }
@media (prefers-reduced-motion: reduce) { .lled.blink { animation: none; outline: 2px dashed var(--c); outline-offset: 3px; } }
.lstate { display: inline-flex; align-items: center; gap: 6px; }
.lstate .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-3); }
.lstate.ok .dot { background: var(--rrelay); }
.lstate.bad .dot { background: var(--rremote); }
.lwait { padding: 14px 16px; color: var(--ink-2); }
.lev { font-family: var(--mono); font-size: 12px; color: var(--ink-2); max-height: 158px; overflow: auto; padding: 6px 10px 10px; margin: 0; list-style: none; }
.lev li { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
"""

CARD = """
  <section class="card" aria-label="Live on the boards" id="onboards">
    <div class="hd"><h2>Live on the boards</h2><span class="meta" id="lmeta">connecting to the core PC…</span></div>
    <div class="bd" style="border-bottom:1px solid var(--line)">
      <span class="label">The boards, with the colour their RGB LED shows now</span>
      <div class="lleds" id="lleds"></div>
      <p class="hint" style="margin-top:8px">Each AGV is one board. Its LED is set by the controller at every role change: %LEDTXT%; an LED blinks in its role's colour while that AGV's UE attaches. The LEDs here are the ones the controller last set on the boards, not the role it computes.</p>
    </div>
    <div class="lwait" id="lwait" hidden></div>
    <div class="simgrid">
      <div class="floorwrap">
        <canvas id="floor3" style="aspect-ratio:48/31" role="img" aria-label="Live: AGV positions, roles and the chain, with sidelink hops as the boards receive them"></canvas>
        <div class="key" style="margin-top:8px"><span>AGV colour = its role, as its board's LED shows it</span><span>dots: sidelink hops as the boards received them</span><span id="llag"></span></div>
      </div>
      <div class="side" id="agvs3"></div>
    </div>
    <div class="charts">
      <div>
        <span class="label">Roles over the last 120 s</span>
        <canvas id="timeline3" style="height:132px" role="img" aria-label="Live roles of the AGVs"></canvas>
      </div>
      <div>
        <span class="label">Events</span>
        <ul class="lev" id="lev"></ul>
      </div>
    </div>
    <div class="flow3" id="flows3" style="padding:0 14px 14px"></div>
    <div class="scroll" style="border-top:1px solid var(--line)">
      <table>
        <thead><tr><th>Role</th><th>Flow</th><th>Messages</th><th>p50</th><th>p90</th><th>Lost</th></tr></thead>
        <tbody id="lcmp"></tbody>
      </table>
      <p class="hint" style="padding:6px 10px 10px" id="lstats">Last 60 s, up to 10 s ago (a message counts as lost 10 s after it was sent). One-way delays on the core PC's clock, the UE PC's set to it by UDP clock pings.</p>
    </div>
  </section>
"""

JS = r"""
// ------------------------------------------------------------------ live: events from the core PC (agv_chain_live.py)
const DUALP = typeof drawFloorDual === "function", LNAME0 = DUALP ? RNAME : ROLES, SCN = DUALP ? "dual" : "chain";
const COVN = ["relays", "own UE", "sidelink"];
const isCov = () => !!(LV.start && LV.start.topology === "cover");
const LNAME = new Proxy(LNAME0, { get: (t, k) => (isCov() && /^[0-2]$/.test(String(k)) ? COVN[k] : t[k]) });
const LV = { serv: [], holds: [], ledlog: [], serve: [], t0: null, start: null, chain: [0, 1, 2], pos: [], chains: [], hops: [], msgs: [], att: [], leds: [null, null, null], stats: "", st: null, recvAt: 0, rep: null, dirty: false, evlog: [] };
const LEDV = { blue: "--rpivot", green: "--rrelay", red: "--rremote", off: "--line" };
const hm = t => new Date((t + (LV.st ? LV.st.off : 0)) * 1000).toTimeString().slice(0, 8);   // UE PC clock -> core PC clock
function lvLog(t, s) { LV.evlog.unshift(`${hm(t)}  ${s}`); LV.evlog.length = Math.min(LV.evlog.length, 60); }
function lvEv(e) {
  if (e.k === "start") { Object.assign(LV, { serv: [], holds: [], ledlog: [], serve: [], t0: e.t, start: e, chain: e.chain || [0, 1, 2], pos: [], chains: [], hops: [], msgs: [], att: [], rep: null }); lvLog(e.t, `controller started: ${e.level}, ${e.pc5 === "oai" ? "OAI sidelink" : "controller hops"}, chain ${(e.chain || []).map(i => AGV[i].n).join("-")}`); return; }
  if (e.k === "state") { LV.st = e; LV.recvAt = performance.now(); return; }
  if (LV.t0 === null) return;
  const t = e.t - LV.t0;
  switch (e.k) {
    case "pos": LV.pos.push(e.q ? [t, e.a, e.chain, e.q] : [t, e.a, e.chain]); LV.chain = e.chain; break;
    case "hold": LV.holds.push([t, e.agv, e.on]); lvLog(e.t, e.on ? `AGV ${AGV[e.agv].n}: the only one with a session, holding before the dead zone` : `AGV ${AGV[e.agv].n}: another AGV has a session again, moving on`); break;
    case "rlf": lvLog(e.t, `AGV ${AGV[e.agv].n}: radio link failure at ${e.q} dBm, its UE lost the gNB`); break;
    case "chain": LV.chains.push([t, e.new]); LV.chain = e.new; lvLog(e.t, `chain ${e.new.map(i => AGV[i].n).join("-")}: pivot ${AGV[e.new[0]].n}`); break;
    case "hop": LV.hops.push([t, e.a, e.b, e.f]); break;
    case "serve": if (e.r !== undefined) { LV.serv.push([t, e.r, e.g === null ? -1 : e.g]); break; } LV.serve.push([t, e.g === null ? -1 : e.g]); lvLog(e.t, e.g === null ? "no gateway serves the remote (it sends up its own UE)" : `AGV ${AGV[e.g].n} serves the remote: LED blue`); break;
    case "msg": { const r = [t, e.agv, e.f, DUALP ? lvLedRole(e.agv, e.role, t) : e.role, e.d, e.via == null ? -1 : e.via], k = lo(LV.msgs, t); LV.msgs.splice(k, 0, r); break; }
    case "cov": if (e.on) { if (DUALP || LV.chain[0] === e.agv) LV.att.push([t, e.agv, null]); lvLog(e.t, `AGV ${AGV[e.agv].n} in coverage: nrUE${e.ue} attaching`); } else lvLog(e.t, `AGV ${AGV[e.agv].n} out of coverage: nrUE${e.ue} stopped`); break;
    case "attach": if (e.ok) { const a = LV.att.findLast(x => x[1] === e.agv && x[2] === null); if (a) a[2] = t; lvLog(e.t, `nrUE${e.ue} attached (AGV ${AGV[e.agv].n}), session ${e.ip || ""}`); } else lvLog(e.t, `nrUE${e.ue} did not attach: ${e.why || ""}`); break;
    case "egress": lvLog(e.t, `fleet traffic leaves through ${e.agv === null ? "no UE" : "AGV " + AGV[e.agv].n + "'s nrUE" + (e.agv + 1)}`); break;
    case "pc5conn": lvLog(e.t, e.on ? `L3 relay: AGV ${AGV[e.agv].n} has a direct link to AGV ${AGV[e.relay].n}, DHCPv4 address ${e.ip} (${e.ms} ms from the request)` : `L3 relay: AGV ${AGV[e.agv].n} released its link to AGV ${AGV[e.relay].n}`); break;
    case "rur": lvLog(e.t, `AGV ${AGV[e.relay].n} reports remote UE AGV ${AGV[e.remote].n} to the network (${e.ev}${e.ip ? ", " + e.ip : ""})`); break;
    case "relayop": lvLog(e.t, `AGV ${AGV[e.agv].n} ${e.on ? "may act as a relay" : "stops relay operation"} (Uu ${e.q} dBm)`); break;
    case "rlysess": lvLog(e.t, `AGV ${AGV[e.agv].n}: relay PDU session (DNN relay) up, ${e.ip}`); break;
    case "disc": lvLog(e.t, `AGV ${AGV[e.agv].n} hears relay AGV ${AGV[e.relay].n}'s announcement, SD-RSRP ${e.sd} dBm`); break;
    case "pc5up": lvLog(e.t, `sidelink UE up on AGV ${AGV[e.agv].n} (link ${e.l}), ${e.ip}`); break;
    case "led": LV.leds[e.agv] = e; if (LV.ledlog.length > 5000) LV.ledlog.splice(0, 1000); LV.ledlog.push([t, e.agv, { blue: 0, green: 1, red: 2 }[e.c] ?? -1, e.blink]); lvLog(e.t, `AGV ${AGV[e.agv].n}'s board ${e.board}: LED ${e.c}${e.blink ? " blinking" : ""}`); break;
    case "stats": LV.stats = e.line; break;
  }
  LV.dirty = true;
}
function lvLedRole(agv, role, t) {   // dual: a message's colour by its AGV's LED when it was sent
  if (role === 2) return 2;
  return servingAt({ serve: LV.serve }, t * 1000) === agv ? 0 : 1;
}
function lvTrim() {
  const last = LV.pos.length ? LV.pos.at(-1)[0] : 0, cut = last - 200;
  { const j = lo(LV.serve, cut); if (j > 1) LV.serve.splice(0, j - 1); }
  for (const k of ["pos", "hops", "msgs"]) { const j = lo(LV[k], cut); if (j > 0) LV[k].splice(0, j); }
  const j = lo(LV.chains, cut); if (j > 1) LV.chains.splice(0, j - 1);
  LV.att = LV.att.filter(a => a[2] === null || a[2] > cut);
}
function lvConnect() {
  const es = new EventSource("events");
  es.onmessage = m => { const d = JSON.parse(m.data); if (d.snap) Object.assign(LV, { serv: [], holds: [], ledlog: [], serve: [], t0: null, start: null, pos: [], chains: [], hops: [], msgs: [], att: [], evlog: [] }); const st = d.ev.findLast(e => e.k === "state"); if (st) LV.st = st; d.ev.forEach(lvEv); lvTrim(); };
  es.onerror = () => { LV.st = null; $("lmeta").innerHTML = `<span class="lstate bad"><span class="dot"></span>no connection to the core PC's live server, retrying</span>`; };
}
function renderLeds() {
  const B = (LV.start && LV.start.boards) || [];
  $("lleds").innerHTML = [0, 1, 2].map(i => {
    const L = LV.leds[i], c = L ? L.c : "off", role = DUALP ? ({ blue: 0, green: 1, red: 2 }[c] ?? (LV.chain.indexOf(i) === 2 ? 2 : 1)) : LV.rep ? LV.rep.chain.indexOf(i) : LV.chain.indexOf(i);
    const what = !L ? "not set yet" : L.blink ? `blinking: its UE is attaching (${LNAME[role]})` : isCov()
      ? (c === "blue" ? "in coverage, its own UE, also relaying another AGV" : c === "green" ? "in coverage, its own UE only" : c === "red" ? "over the sidelink: out of coverage or re-attaching" : "")
      : DUALP
      ? (c === "blue" ? "gateway serving the remote: NR and sidelink" : c === "green" ? "gateway, NR only: its own traffic" : c === "red" ? "remote: one sidelink hop to the blue gateway" : "")
      : (c === "blue" ? "pivot: Uu to the gNB" : c === "green" ? "relay: one sidelink hop" : c === "red" ? "remote: two sidelink hops" : "");
    return `<div class="lboard"><span class="lled ${L && L.blink ? "blink" : ""} ${c === "off" ? "off" : ""}" style="--c:var(${LEDV[c]})" role="img" aria-label="LED ${c}"></span><div style="min-width:0"><b>AGV ${AGV[i].n}</b> <span class="chip ${ROLES[role]}">${LNAME[role]}</span><br><span class="sn">${B[i] || AGV[i].board}</span><br><span class="sub">LED ${c}${L && L.blink ? " (blinking)" : ""}: ${what}</span></div></div>`;
  }).join("") + `<div class="lboard"><span class="lled off" style="--c:var(--uu);background:transparent;border:3px solid var(--uu)" role="img" aria-label="gNB board"></span><div><b>gNB on the pole</b><br><span class="sn">core PC's board</span><br><span class="sub">its LED flashes blue and red with the UEs' slots</span></div></div>`;
}
function lvVia(r) { return DUALP && r.tr ? viaAt(r) : null; }
function renderSide3(r) {
  const B = (LV.start && LV.start.boards) || [];
  $("agvs3").innerHTML = r.agv.map(a => {
    const role = DUALP ? ledRole(r, a.i, r.t) : r.chain.indexOf(a.i), L = r.last[a.i], led = LV.leds[a.i], c = led ? led.c : "off";
    return `<div class="agv" style="--c:var(${RCOL[role]})"><span class="sw"></span><span class="nm">AGV ${AGV[a.i].n}</span><span class="chip ${ROLES[role]}">${LNAME[role]}</span>
      <span class="sub">${B[a.i] || AGV[a.i].board} · <span class="ledg" style="--c:var(${LEDV[c]})"></span>LED ${c}${led && led.blink ? " blinking" : ""} · ${isCov() ? (role === 2 ? (() => { const v = relayOf(r, a.i); return v === a.i ? "its own UE, session settling" : "over the sidelink" + (v !== null && v >= 0 ? " to AGV " + AGV[v].n : ""); })() : role === 0 ? "its own UE, also relaying another AGV" : "its own UE only") : DUALP ? (role === 0 ? "its own UE to the gNB, with the remote's traffic" : role === 1 ? "its own UE to the gNB" : "one PC5 hop" + (servingAt(r.tr, r.t) >= 0 ? " to AGV " + AGV[servingAt(r.tr, r.t)].n : "") + " + Uu") : role === 0 ? "Uu to the gNB" : role + " PC5 hop" + (role > 1 ? "s" : "") + " + Uu"}</span>
      <dl><div>${isCov() ? `<dt>gNB signal</dt><dd>${Math.round(rsrpAt(a.x, a.y))} dBm</dd>` : `<dt>to pole</dt><dd>${a.d.toFixed(1)} m</dd>`}</div><div><dt>positioning</dt><dd>${fmt(L.det)}</dd></div><div><dt>LiDAR</dt><dd>${fmt(L.lidar)}</dd></div><div><dt>cmd</dt><dd>${fmt(L.cmd)}</dd></div></dl><div class="rep">${a.rep ? `edge knows <b>(${a.rep.x.toFixed(1)}, ${a.rep.y.toFixed(1)}) m</b>, reported ${((r.t - a.rep.gen) / 1000).toFixed(2)} s ago` : "edge knows: no report yet"} · now (${a.x.toFixed(1)}, ${a.y.toFixed(1)}) m</div></div>`;
  }).join("") + `<p class="hint">Measured one-way delay of each flow&#x27;s last message, in ms.</p>`;
}
function renderLiveTable(r) {
  const s = r.t / 1000, a = lo(LV.msgs, s - 70), b = lo(LV.msgs, s - 10);
  let h = "";
  ROLES.forEach((role, k) => FL.forEach((f, q) => {
    const d = [], n = { all: 0 };
    for (let j = a; j < b; j++) { const m = LV.msgs[j]; if (m[3] === k && m[2] === q) { n.all++; if (m[4] !== null) d.push(m[4]); } }
    if (!n.all) return;
    const lost = 100 * (n.all - d.length) / n.all;
    h += `<tr><td>${q === 0 ? `<span class="chip ${role}">${LNAME[k]}</span>` : ""}</td><td>${FLOWN[f]}</td><td class="n">${n.all}</td><td class="n">${fmt(pct(d, .5))}</td><td class="n">${fmt(pct(d, .9))}</td><td class="n ${lost >= 1 ? "bad" : ""}">${lost.toFixed(1)} %</td></tr>`;
  }));
  $("lcmp").innerHTML = h || `<tr><td colspan="6" class="hint">No messages in the last minute yet.</td></tr>`;
}
function renderMeta() {
  const st = LV.st, s = LV.start;
  if (!st) return;
  const okc = st.ctl !== null && st.ctl < 3, run = s && LV.pos.length ? (LV.pos.at(-1)[0] / 60) : 0;
  const what = s && !s.late ? `${s.level} · ${s.topology === "dual" ? "two gateways, the remote picks " + (s.pick === "first" ? "gateway 1" : s.pick === "sticky" ? "one and stays on it (sticky)" : "the less busy one") + " · " : ""}${s.pc5 === "oai" ? "hops over OAI's NR sidelink (option 3)" : "hops: controller packets"} · ${s.uu === "per-agv" ? "a UE per AGV" : "one fleet UE"} · sidelink window ${s.window_ms} ms · ` : "";
  $("lmeta").innerHTML = `<span class="lstate ${okc ? "ok" : "bad"}"><span class="dot"></span>${okc ? "live" : st.ctl === null ? "no controller events yet" : `controller silent for ${st.ctl.toFixed(0)} s`}</span> · ${what}${run ? `running ${run.toFixed(0)} min · ` : ""}edge ${st.edge ? "up" : "down"}`;
  const w = $("lwait");
  if (!okc && !LV.pos.length) { w.hidden = false; w.innerHTML = `Waiting for the controller on the UE PC. Start it with <code>./nrlink.sh live start --scenario ${SCN}</code> on both PCs (the core PC first).`; }
  else w.hidden = true;
  $("lev").innerHTML = LV.evlog.map(x => `<li>${x}</li>`).join("");
  $("lstats").textContent = "Last 60 s, up to 10 s ago (a message counts as lost 10 s after it was sent). One-way delays on the core PC's clock, the UE PC's set to it by UDP clock pings." + (LV.stats ? "  Controller: " + LV.stats : "");
}
let lvUi = 0;
function liveFrame(now) {
  if (LV.pos.length >= 2) {
    if (LV.dirty || !LV.rep) { LV.rep = new (DUALP ? DualReplay : Replay)({ pos: LV.pos, chains: LV.chains, hops: LV.hops, msgs: LV.msgs, att: LV.att, serve: LV.serve, leds: LV.ledlog, holds: LV.holds, serv: LV.serv, topology: LV.start && LV.start.topology }); LV.dirty = false; }
    const head = LV.st ? (LV.st.now_ue - LV.t0) * 1000 + (now - LV.recvAt) : LV.rep.T;
    LV.rep.seek(Math.max(0, Math.min(LV.rep.T, head - 600)));
    if (DUALP) drawFloorDual(LV.rep, $("floor3")); else drawFloor(LV.rep, $("floor3"));
    if (DUALP && typeof AR !== "undefined" && LV.rep.tr) { window.ARLIVE = 1; AR.update(LV.rep); }
    if (now - lvUi > 250) { lvUi = now; if (DUALP) drawTimelineDual(LV.rep, $("timeline3")); else drawTimeline(LV.rep, $("timeline3")); drawFlows(LV.rep, "flows3"); renderSide3(LV.rep); renderLiveTable(LV.rep); renderLeds(); renderMeta(); $("llag").textContent = `shown ${((head - LV.rep.t) / 1000).toFixed(1)} s behind the AGVs`; }
  } else if (now - lvUi > 500) { lvUi = now; renderLeds(); renderMeta(); }
  requestAnimationFrame(liveFrame);
}
lvConnect();
requestAnimationFrame(liveFrame);
"""


def main():
    s = open(SRC, encoding="utf-8").read()

    def put(anchor, text, before=True, last=False):
        nonlocal s
        i = s.rfind(anchor) if last else s.find(anchor)
        assert i >= 0, anchor
        if not before:
            i += len(anchor)
        s = s[:i] + text + s[i:]
    put("</style>", CSS)
    put("</header>", CARD.replace("%LEDTXT%", LEDTXT), before=False)
    put("</script>", JS, last=True)
    s = s.replace(f"<title>{TITLE}</title>", f"<title>{TITLE} · live</title>", 1)
    s = s.replace('<span class="pill"><span class="dot"></span>', '<span class="pill"><span class="dot"></span><a href="#onboards">live on the boards, below</a> · ', 1)
    open(OUT, "w", encoding="utf-8").write(s)
    print(f"{OUT}: {len(s)} bytes")


if __name__ == "__main__":
    main()
