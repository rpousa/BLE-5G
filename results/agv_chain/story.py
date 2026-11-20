"""The 'Sidelink' tab of the AGV dual-gateway page: an animated, step-by-step presentation of why and how the AGVs use
the sidelink (gNB coverage, losing it in the cold store, a relay, the sidelink window, mode-1 scheduling, PC5 traffic).
An illustration: positions and timings are drawn to scale only roughly; the numbers shown are the measured ones."""

STORY_CSS = """
.story { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(280px, 1fr); gap: 24px; padding: 24px; align-items: start; }
@media (max-width: 900px) { .story { grid-template-columns: 1fr; padding: 16px; } }
.story svg { width: 100%; height: auto; display: block; }
.sv-floor { fill: var(--bg-alt); }
.sv-ring { fill: none; stroke: var(--blue-light); stroke-width: 1.2; stroke-dasharray: 2 4; }
.sv-cold { fill: rgba(226, 0, 115, .07); stroke: var(--magenta); stroke-width: 1.4; stroke-dasharray: 6 4; }
.sv-route { fill: none; stroke: var(--border); stroke-width: 10; stroke-linecap: round; stroke-linejoin: round; }
.sv-gnb { fill: var(--navy); } .sv-pole { stroke: var(--navy); stroke-width: 3; }
.sv-uu { stroke: var(--blue); stroke-width: 2.5; }
.sv-pc5 { stroke: var(--magenta); stroke-width: 3; }
.sv-pulse { fill: none; stroke: var(--magenta); stroke-width: 2; }
.sv-agv rect { stroke: #fff; stroke-width: 2; transition: fill .2s ease-out; }
.sv-agv text { fill: #fff; font: 600 15px var(--head); text-anchor: middle; dominant-baseline: central; }
.sv-lab { fill: var(--navy); font: 500 13px var(--mono); }
.sv-labm { fill: var(--text-muted); font: 500 12px var(--display); }
.sv-t { fill: var(--navy); font: 600 13px var(--display); }
.sv-pk { stroke: #fff; stroke-width: 1.5; }
.sv-slot { fill: var(--blue-deep); } .sv-slot.e { fill: var(--border); } .sv-slot.s { fill: var(--blue); } .sv-slot.u { fill: var(--navy); }
.sv-win { fill: var(--magenta); } .sv-gr0 { fill: var(--blue); } .sv-gr1 { fill: var(--blue-light); } .sv-pool { fill: rgba(226, 0, 115, .45); }
.sv-ph { stroke: var(--navy); stroke-width: 2; }
.sv-grid { stroke: var(--border); stroke-width: 1; }
.sv-gap { fill: none; }
.st-side { display: grid; gap: 16px; align-content: start; }
.st-n { font: 600 14px var(--mono); color: var(--magenta); letter-spacing: .08em; }
.st-h { font: 600 clamp(24px, 2.4vw, 32px)/1.15 var(--head); color: var(--navy); margin: 0; }
.st-p { font-size: 17px; line-height: 1.5; color: var(--navy); margin: 0; min-height: 3em; }
.st-tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.st-tile { border: 1px solid var(--border); border-radius: 4px; padding: 12px 14px; background: #fff; }
.st-tile .label { display: block; }
.st-v { font: 600 30px/1.1 var(--head); color: var(--navy); font-variant-numeric: tabular-nums; }
.st-v small { font: 500 15px var(--display); color: var(--text-muted); margin-left: 4px; }
.st-ctl { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 0 24px 24px; }
.st-ctl button { height: 44px; min-width: 44px; padding: 0 16px; border: 1px solid var(--border); background: #fff; color: var(--navy);
                 font: 600 14px var(--display); border-radius: 3px; cursor: pointer; transition: background .15s ease-out; }
.st-ctl button:hover { background: var(--bg-alt); }
.st-ctl button.primary { background: var(--navy); border-color: var(--navy); color: #fff; min-width: 96px; }
.st-dots { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 8px; }
.st-dots button { min-width: 44px; padding: 0; font: 500 13px var(--mono); }
.st-dots button[aria-current="step"] { border-color: var(--navy); box-shadow: inset 0 -2px 0 var(--magenta); font-weight: 600; }
.st-sp { flex: 1; }
#story.present { position: fixed; inset: 0; z-index: 60; overflow: auto; border-radius: 0; font-size: 125%; }
#story.present .st-p { font-size: 21px; } #story.present .st-v { font-size: 38px; }
@media (prefers-reduced-motion: reduce) { .sv-agv rect, .st-ctl button { transition: none; } }
"""

STORY_HTML = """
  <section class="card" aria-label="Sidelink, step by step" id="story" data-tab="sidelink">
    <div class="hd"><h2>Why the AGVs need a sidelink</h2><span class="meta"><span class="chip"><span class="dot idle"></span>Illustration · measured numbers</span></span></div>
    <div class="story">
      <svg viewBox="0 0 640 545" role="img" aria-labelledby="st-h" id="sv">
        <defs><clipPath id="svclip"><rect x="0" y="0" width="640" height="400" rx="4"/></clipPath></defs>
        <rect class="sv-floor" x="0" y="0" width="640" height="400" rx="4"/>
        <g clip-path="url(#svclip)"><circle class="sv-ring" cx="120" cy="260" r="150"/><circle class="sv-ring" cx="120" cy="260" r="300"/><circle class="sv-ring" cx="120" cy="260" r="450"/></g>
        <rect class="sv-cold" x="440" y="225" width="170" height="160" rx="4"/>
        <text class="sv-labm" x="452" y="376">Cold store · shielded</text>
        <path class="sv-route" d="M50 130 H600 M520 130 V335"/>
        <line class="sv-uu" id="svuA"/><line class="sv-uu" id="svuB"/><line class="sv-uu" id="svuC"/>
        <line class="sv-pc5" id="svpc5"/>
        <circle class="sv-pulse" id="svpulse" r="0"/>
        <line class="sv-pole" x1="120" y1="260" x2="120" y2="300"/>
        <path class="sv-gnb" d="M120 228 L106 262 H134 Z"/>
        <text class="sv-t" x="120" y="320" text-anchor="middle">gNB</text>
        <g class="sv-agv" id="svA"><rect x="-21" y="-14" width="42" height="28" rx="4"/><text>A</text></g>
        <g class="sv-agv" id="svB"><rect x="-21" y="-14" width="42" height="28" rx="4"/><text>B</text></g>
        <g class="sv-agv" id="svC"><rect x="-21" y="-14" width="42" height="28" rx="4"/><text>C</text></g>
        <text class="sv-lab" id="svClab" text-anchor="middle"></text>
        <text class="sv-lab" id="svBlab" text-anchor="middle"></text>
        <circle class="sv-pk" id="pk0" r="6"/><circle class="sv-pk" id="pk1" r="6"/><circle class="sv-pk" id="pk2" r="6"/><circle class="sv-pk" id="pk3" r="6"/>
        <text class="sv-labm" id="pklab" text-anchor="middle"></text>
        <text class="sv-t" x="0" y="432">One cycle on the air</text>
        <text class="sv-labm" x="0" y="474">NR</text>
        <g id="svslots"></g><g id="svwin"></g>
        <text class="sv-labm" id="svwlab" y="449"></text>
        <line class="sv-ph" id="svph" y1="448" y2="488"/>
        <g id="svaxis"></g>
        <text class="sv-t" x="340" y="532" text-anchor="middle">Time in the cycle (ms)</text>
      </svg>
      <div class="st-side" aria-live="polite">
        <div class="st-n" id="st-n">01 / 07</div>
        <h3 class="st-h" id="st-h"></h3>
        <p class="st-p" id="st-p"></p>
        <div class="st-tiles">
          <div class="st-tile"><span class="label">AGV C · gNB signal</span><div class="st-v" id="st-rsrp">−83<small>dBm</small></div></div>
          <div class="st-tile"><span class="label">Sidelink window</span><div class="st-v" id="st-win">0<small>ms</small></div></div>
          <div class="st-tile"><span class="label">AGV C's data</span><div class="st-v" id="st-path" style="font-size:22px">own NR link</div></div>
          <div class="st-tile"><span class="label">Cycles</span><div class="st-v" id="st-cyc">–<small>per s</small></div></div>
        </div>
      </div>
    </div>
    <div class="st-ctl">
      <button type="button" id="st-prev" aria-label="Previous step">‹</button>
      <button type="button" class="primary" id="st-play">Pause</button>
      <button type="button" id="st-next" aria-label="Next step">›</button>
      <div class="st-dots" id="st-dots"></div>
      <span class="st-sp"></span>
      <button type="button" id="st-reset">Reset</button>
      <button type="button" id="st-pres" aria-pressed="false">Presentation</button>
    </div>
  </section>
"""

STORY_JS = r"""
// ---------------------------------------------------------------- Sidelink tab: the presentation
(function story() {
  const S = [
    ["The gNB serves every AGV", "Each AGV has its own NR link: downlink and uplink slots in every frame."],
    ["AGV C drives into the cold store", "Its gNB signal falls below −100 dBm, then −110 dBm: the NR link is lost."],
    ["C needs a sidelink", "C hears AGV B announce itself as a relay and sets up a direct link to it."],
    ["The gNB makes room on the air", "After slot 9 of every frame the gNB stays quiet for a 25 ms sidelink window."],
    ["The gNB schedules the window", "B reports its sidelink buffer; the gNB gives each AGV a slice and C a shared pool: 16.4 ms on average."],
    ["The AGVs talk to each other", "C's packets reach B over PC5 in the pool; B sends them up in its own NR slots, and the commands come back the same way."],
    ["Back in coverage", "C leaves the store, its own NR link is up again after 3.8 s (median), and the sidelink is released."],
  ];
  const DUR = 6500, HOLD = 1200, CYC = 2600;                 // ms per step, pause at its end, ms per drawn cycle
  const $s = id => document.getElementById(id), NS = "http://www.w3.org/2000/svg";
  if (!$s("sv")) return;
  const GNB = [120, 245], A = [220, 130], B = [420, 130], COUT = [520, 130], CIN = [520, 330];
  const X0 = 40, PX = 7;                                       // frame strip: x of 0 ms, px per ms
  const SL = [[2.7, ""], [0.7, "e"], [0.7, "e"], [0.7, "e"], [0.7, "e"], [0.7, "e"], [2.7, ""], [11, "s"], [11, "u"], [11, "u"]];
  const K = 57 / SL.reduce((a, s) => a + s[0], 0); SL.forEach(s => s[0] *= K);   // the NR part of a cycle as measured: 57 ms
  const NR = 57;
  const el = (tag, at, parent) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); parent.appendChild(e); return e; };
  let x = X0;
  SL.forEach(([ms, c], i) => { el("rect", { x: x, y: 456, width: Math.max(1, ms * PX - 1), height: 26, class: "sv-slot " + c }, $s("svslots")); if (ms > 2) el("text", { x: x + ms * PX / 2, y: 473, class: "sv-labm", "text-anchor": "middle", fill: "#fff" }, $s("svslots")).textContent = ["D0", "", "", "", "", "", "D6", "S7", "U8", "U9"][i]; x += ms * PX; });
  for (const t of [0, 10, 20, 30, 40, 50, 60, 70, 80]) {
    el("line", { x1: X0 + t * PX, x2: X0 + t * PX, y1: 488, y2: 494, class: "sv-grid" }, $s("svaxis"));
    el("text", { x: X0 + t * PX, y: 508, class: "sv-labm", "text-anchor": "middle" }, $s("svaxis")).textContent = t;
  }
  [...$s("svslots").querySelectorAll("text")].forEach(t => t.setAttribute("style", "fill:#fff;font-weight:600"));
  const win = el("g", {}, $s("svwin"));
  let step = 0, t0 = performance.now(), playing = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ease = p => p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
  const lerp = (a, b, f) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  const cl = (v, a, b) => Math.max(a, Math.min(b, v));
  const ROLE = { own: "var(--ok)", relay: "var(--blue)", remote: "var(--error)" };
  function line(id, a, b, show, dash) { const l = $s(id); l.setAttribute("x1", a[0]); l.setAttribute("y1", a[1]); l.setAttribute("x2", b[0]); l.setAttribute("y2", b[1]); l.style.display = show ? "" : "none"; l.setAttribute("stroke-dasharray", dash ? "6 5" : ""); }
  function agv(id, p, role) { const g = $s(id); g.setAttribute("transform", `translate(${p[0]},${p[1]})`); g.querySelector("rect").style.fill = ROLE[role]; }
  function pk(i, a, b, f, col, show) { const c = $s("pk" + i); if (!show || f < 0 || f > 1) { c.style.display = "none"; return; } const q = lerp(a, b, f); c.style.display = ""; c.setAttribute("cx", q[0]); c.setAttribute("cy", q[1]); c.style.fill = col; }
  function drawWin(slices, total) {                          // slices: [[ms, class, label]]
    win.innerHTML = ""; let x = X0 + NR * PX;
    for (const [ms, c, lab] of slices) { el("rect", { x: x, y: 456, width: Math.max(0, ms * PX - 1), height: 26, class: c }, win); if (lab && ms * PX > 22) { const t = el("text", { x: x + ms * PX / 2, y: 473, class: "sv-labm", "text-anchor": "middle" }, win); t.textContent = lab; t.setAttribute("style", "fill:#fff;font-weight:600"); } x += ms * PX; }
    const wl = $s("svwlab"); wl.setAttribute("x", X0 + NR * PX); wl.textContent = total > 0.5 ? `sidelink window · ${total.toFixed(1)} ms` : "";
  }
  function render(now) {
    const p = cl((now - t0) / DUR, 0, 1), e = ease(p), k = step;
    // AGV C's position and gNB signal (dBm), from the path-loss model's values at the door and inside the store
    const cpos = k === 1 ? lerp(COUT, CIN, e) : k >= 2 && k <= 5 ? CIN : k === 6 ? lerp(CIN, COUT, e) : COUT;
    const fin = (cpos[1] - COUT[1]) / (CIN[1] - COUT[1]);
    const rsrp = -83 - 35 * fin;
    const cLost = rsrp < -110 || (k >= 2 && k <= 5) || (k === 6 && p < .55);
    const sl = (k === 2 && p > .45) || (k >= 3 && k <= 5) || (k === 6 && p < .75);
    agv("svA", A, "own"); agv("svB", B, sl ? "relay" : "own"); agv("svC", cpos, cLost || rsrp < -100 ? "remote" : "own");
    line("svuA", GNB, A, true); line("svuB", GNB, B, true); line("svuC", GNB, cpos, !cLost, rsrp < -100);
    line("svpc5", B, cpos, sl, false);
    const pu = $s("svpulse");                                  // relay discovery announcements from B
    if (k === 2 && p < .6) { const f = (now % 1100) / 1100; pu.setAttribute("cx", B[0]); pu.setAttribute("cy", B[1]); pu.setAttribute("r", 10 + f * 150); pu.style.opacity = 1 - f; pu.style.display = ""; } else pu.style.display = "none";
    $s("svClab").setAttribute("x", cpos[0] - 62); $s("svClab").setAttribute("y", cpos[1] + 5); $s("svClab").textContent = `${Math.round(rsrp)} dBm`;
    const bl = $s("svBlab"); bl.setAttribute("x", B[0]); bl.setAttribute("y", B[1] - 26); bl.textContent = sl ? "relay" : "";
    // the sidelink window after slot 9: none, then the fixed 25 ms, then mode 1 (measured means of the mode-1 run)
    let slices = [], wms = 0;
    const M1 = [[1.0, "sv-gap", ""], [3.2, "sv-gr0", "A"], [0.8, "sv-gap", ""], [3.3, "sv-gr1", "B"], [0.8, "sv-gap", ""], [6.8, "sv-pool", "pool C"], [0.5, "sv-gap", ""]];
    const m1 = M1.reduce((a, s) => a + s[0], 0);
    if (k === 3) { wms = 25 * e; slices = [[wms, "sv-win", "window"]]; }
    else if (k === 4) { wms = 25 + (m1 - 25) * e; slices = e < .5 ? [[wms, "sv-win", "window"]] : M1.map(([ms, c, l]) => [ms * wms / m1, c, l]); }
    else if (k === 5 || (k === 6 && p < .75)) { wms = m1; slices = M1; }
    else if (k === 6) { wms = m1 * (1 - (p - .75) / .25); slices = M1.map(([ms, c, l]) => [ms * wms / m1, c, l === "pool C" ? "" : l]); }
    drawWin(slices, wms);
    // a cycle playing on the strip, and what is on the air at that moment
    const cyc = NR + wms, ph = ((now % CYC) / CYC) * cyc, at = (a, b) => ph >= a && ph < b ? (ph - a) / (b - a) : -1;
    $s("svph").setAttribute("x1", X0 + ph * PX); $s("svph").setAttribute("x2", X0 + ph * PX);
    const dl = at(0, 6.4 + 2.7), u = at(NR - 22, NR), w = at(NR, NR + Math.max(wms, .01));
    const lab = $s("pklab"); lab.textContent = "";
    pk(0, GNB, A, dl, "var(--blue)", dl >= 0); pk(1, A, GNB, u, "var(--navy)", u >= 0);
    pk(2, cLost ? B : cpos, GNB, u, k === 5 ? "var(--magenta)" : "var(--navy)", u >= 0);
    if (k === 4 && u >= 0) { lab.setAttribute("x", (B[0] + GNB[0]) / 2); lab.setAttribute("y", 175); lab.textContent = "SL-BSR"; }
    if (k === 5 && u >= 0) { lab.setAttribute("x", (B[0] + GNB[0]) / 2); lab.setAttribute("y", 175); lab.textContent = "C's data, in B's NR slot"; }
    if (k >= 3 && sl && w >= 0) {                                // inside the window: C → B in the pool, B → C in B's slice
      const pool0 = (1 + 3.2 + .8 + 3.3 + .8) / m1;
      if (k >= 5 && w > pool0) { pk(3, cpos, B, (w - pool0) / (1 - pool0), "var(--magenta)", true); lab.setAttribute("x", (B[0] + cpos[0]) / 2 + 52); lab.setAttribute("y", (B[1] + cpos[1]) / 2); lab.textContent = "PC5"; }
      else if (k >= 5 && w > (1 + 3.2 + .8) / m1 && w < (1 + 3.2 + .8 + 3.3) / m1) pk(3, B, cpos, (w - (5 / m1)) / (3.3 / m1), "var(--blue)", true);
      else pk(3, B, cpos, 0, "", false);
    } else pk(3, B, cpos, 0, "", false);
    // side panel
    $s("st-rsrp").innerHTML = `${Math.round(rsrp).toString().replace("-", "−")}<small>dBm</small>`;
    $s("st-win").innerHTML = `${wms.toFixed(wms > 0 && wms < 10 ? 1 : wms ? 1 : 0)}<small>ms</small>`;
    $s("st-path").textContent = cLost ? (sl ? "via B (PC5)" : "no way up") : "own NR link";
    $s("st-cyc").innerHTML = `${(1000 / (NR + wms)).toFixed(1)}<small>per s</small>`;
  }
  function show(k, keep) {
    step = (k + S.length) % S.length; t0 = performance.now();
    $s("st-n").textContent = `${String(step + 1).padStart(2, "0")} / ${String(S.length).padStart(2, "0")}`;
    $s("st-h").textContent = S[step][0]; $s("st-p").textContent = S[step][1];
    [...$s("st-dots").children].forEach((b, i) => b.toggleAttribute("aria-current", false) || (i === step && b.setAttribute("aria-current", "step")));
    if (!playing) { t0 = performance.now() - DUR; render(performance.now()); }
  }
  S.forEach((s, i) => { const b = document.createElement("button"); b.type = "button"; b.textContent = String(i + 1).padStart(2, "0"); b.setAttribute("aria-label", `Step ${i + 1}: ${s[0]}`); b.onclick = () => show(i); $s("st-dots").appendChild(b); });
  const setPlay = on => { playing = on; $s("st-play").textContent = on ? "Pause" : "Play"; if (on) t0 = performance.now(); };
  $s("st-play").onclick = () => setPlay(!playing);
  $s("st-prev").onclick = () => show(step - 1); $s("st-next").onclick = () => show(step + 1);
  $s("st-reset").onclick = () => { setPlay(!matchMedia("(prefers-reduced-motion: reduce)").matches); show(0); };
  $s("st-pres").onclick = () => { const on = $s("story").classList.toggle("present"); $s("st-pres").setAttribute("aria-pressed", on); $s("st-pres").textContent = on ? "Exit presentation" : "Presentation"; };
  document.addEventListener("keydown", e => {
    if ($s("story").hidden || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === "Escape" && $s("story").classList.contains("present")) $s("st-pres").click();
    if (e.target.closest && e.target.closest("#seg")) return;
    if (e.key === "ArrowRight" && e.target.closest && e.target.closest("#story")) show(step + 1);
    if (e.key === "ArrowLeft" && e.target.closest && e.target.closest("#story")) show(step - 1);
  });
  show(0);
  (function loop(now) {
    if (!$s("story").hidden) {
      if (playing && now - t0 > DUR + HOLD) show(step + 1);
      render(playing ? now : Math.max(now, t0 + DUR));
    }
    requestAnimationFrame(loop);
  })(performance.now());
})();
"""
