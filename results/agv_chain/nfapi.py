"""The 'nFAPI' tab: an animated presentation of how OAI's 5G stack runs over the FRDM-MCXW72 boards (nrlink): the gNB split
at nFAPI, the boards in place of the RF PHY, downlink per slot, uplink by polling, fragmentation, the slowed slot clock and
the tuning for slow slots. Uses the Sidelink tab's CSS (story.py). Facts from ~/oai/nrlink/README.md and the runs."""

NFAPI_CSS = """
#nfapi.present { position: fixed; inset: 0; z-index: 60; overflow: auto; border-radius: 0; font-size: 125%; }
#nfapi.present .st-p { font-size: 21px; } #nfapi.present .st-v { font-size: 38px; }
.nf-box rect { fill: #fff; stroke: var(--border); stroke-width: 1.5; transition: stroke .2s ease-out, fill .2s ease-out; }
.nf-box.on rect { stroke: var(--navy); stroke-width: 2.5; }
.nf-box.hot rect { fill: var(--bg-alt); stroke: var(--magenta); stroke-width: 2.5; }
.nf-box .t { fill: var(--navy); font: 600 14px var(--head); }
.nf-box .s { fill: var(--text-muted); font: 500 11.5px var(--display); }
.nf-ghost rect { fill: none; stroke: var(--idle); stroke-width: 1.5; stroke-dasharray: 5 4; }
.nf-ghost text { fill: var(--idle); font: 600 13px var(--display); }
.nf-x { stroke: var(--error); stroke-width: 2.5; }
.nf-ln { stroke: var(--text-muted); stroke-width: 1.5; }
.nf-air { stroke: var(--blue); stroke-width: 2; stroke-dasharray: 3 5; }
.nf-lab { fill: var(--text-muted); font: 500 11.5px var(--mono); }
.nf-col { fill: var(--navy); font: 600 12px var(--display); letter-spacing: .12em; }
.nf-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.nf-list li { border: 1px solid var(--border); border-radius: 4px; padding: 10px 14px; background: #fff; font-size: 15px; color: var(--navy);
              opacity: 0; transform: translateY(6px); transition: opacity .2s ease-out, transform .2s ease-out; }
.nf-list li.in { opacity: 1; transform: none; }
.nf-list li b { font-family: var(--head); }
.nf-list li span { display: block; font: 500 13px var(--mono); color: var(--text-muted); margin-top: 2px; }
@media (prefers-reduced-motion: reduce) { .nf-list li, .nf-box rect { transition: none; } }
"""

NFAPI_HTML = """
  <section class="card" aria-label="nFAPI over the boards, step by step" id="nfapi" data-tab="nfapi">
    <div class="hd"><h2>5G over the boards: the nFAPI adaptation</h2><span class="meta"><span class="chip"><span class="dot idle"></span>Illustration · measured numbers</span></span></div>
    <div class="story">
      <svg viewBox="0 0 640 525" role="img" aria-labelledby="nf-h" id="nfsv">
        <text class="nf-col" x="20" y="14">CORE PC</text><text class="nf-col" x="400" y="14">UE PC</text>
        <g id="nfln"></g>
        <line class="nf-air" x1="240" y1="315" x2="400" y2="315"/>
        <text class="nf-lab" x="320" y="306" text-anchor="middle">2.4 GHz GenFSK</text>
        <text class="nf-lab" x="320" y="334" text-anchor="middle">1 Mbit/s · ≤255 B</text>
        <g class="nf-ghost" id="nfghost"><rect x="262" y="118" width="116" height="56" rx="4"/><text x="320" y="111" text-anchor="middle">RF PHY · not used</text>
          <line class="nf-x" id="nfx1" x1="290" y1="130" x2="350" y2="162"/><line class="nf-x" id="nfx2" x1="350" y1="130" x2="290" y2="162"/></g>
        <g id="nfboxes"></g>
        <circle class="sv-pk" id="nfp0" r="7"/><circle class="sv-pk" id="nfp1" r="7"/>
        <rect class="sv-pk" id="nff0" width="11" height="11"/><rect class="sv-pk" id="nff1" width="11" height="11"/>
        <rect class="sv-pk" id="nff2" width="11" height="11"/><rect class="sv-pk" id="nff3" width="11" height="11"/>
        <text class="nf-lab" id="nfpl" text-anchor="middle"></text>
        <text class="sv-t" x="0" y="384">One TDD period: 10 slots</text>
        <text class="sv-labm" x="0" y="415">Real NR</text><g id="nfreal"></g><line class="sv-ph" id="nfph0" y1="398" y2="426"/>
        <text class="sv-labm" x="0" y="453" id="nfl2">nrlink</text><g id="nfslow"></g><line class="sv-ph" id="nfph1" y1="436" y2="464"/>
        <g id="nfaxis"></g>
        <text class="sv-t" x="340" y="516" text-anchor="middle">Time (ms)</text>
      </svg>
      <div class="st-side" aria-live="polite">
        <div class="st-n" id="nf-n">01 / 07</div>
        <h3 class="st-h" id="nf-h"></h3>
        <p class="st-p" id="nf-p"></p>
        <div class="st-tiles" id="nf-tiles">
          <div class="st-tile"><span class="label">Slot length</span><div class="st-v" id="nf-slot">0.5<small>ms</small></div></div>
          <div class="st-tile"><span class="label">Slots per second</span><div class="st-v" id="nf-sps">2000</div></div>
          <div class="st-tile"><span class="label">Radio</span><div class="st-v">1<small>Mbit/s</small></div></div>
          <div class="st-tile"><span class="label">Air packet</span><div class="st-v">255<small>B max</small></div></div>
        </div>
        <ul class="nf-list" id="nf-list" hidden>
          <li><b>Poll only attached UEs</b><span>gnb_bridge --ues · 48 → 220 slots/s</span></li>
          <li><b>Skip empty slots</b><span>gnb_bridge --skip-empty · ~240 slots/s</span></li>
          <li><b>UL grant every frame</b><span>ulsch_max_frame_inactivity = 0</span></li>
          <li><b>Room between DL and UL</b><span>min_rxtxtime = 4 · TDD 7 DL, 1 S, 2 UL kept</span></li>
          <li><b>LiDAR scan, one-way</b><span>p50 643 → 144 ms</span></li>
        </ul>
      </div>
    </div>
    <div class="st-ctl">
      <button type="button" id="nf-prev" aria-label="Previous step">‹</button>
      <button type="button" class="primary" id="nf-play">Pause</button>
      <button type="button" id="nf-next" aria-label="Next step">›</button>
      <div class="st-dots" id="nf-dots"></div>
      <span class="st-sp"></span>
      <button type="button" id="nf-reset">Reset</button>
      <button type="button" id="nf-pres" aria-pressed="false">Presentation</button>
    </div>
  </section>
"""

NFAPI_JS = r"""
// ---------------------------------------------------------------- nFAPI tab: the presentation
(function nfapi() {
  const S = [
    ["The gNB is split at nFAPI", "OAI's VNF runs MAC, RLC, PDCP, RRC and the scheduler; the PNF stands for the radio. nFAPI P5 configures it, P7 carries every slot."],
    ["The boards replace the RF PHY", "A hook in the PNF's slot handler and one in each nrUE send the slot's contents to an FRDM-MCXW72 board instead of a radio front end."],
    ["Downlink: each slot over the air", "The PNF hands over what the VNF scheduled (MIB, DCIs, PDSCH blocks); the UE boards pass it to their nrUEs, whose MAC runs as with a real PHY."],
    ["Uplink: the gNB board polls", "In each UL slot the gNB board polls every UE board; the answer becomes CRC, RX_DATA, UCI and RACH indications. A missing answer counts as DTX."],
    ["Packets cut to fit the radio", "A transport block crosses the air in packets of at most 255 B and is put back together on the other side."],
    ["Time is slowed down", "A slot lasts as long as its exchange over the air. The PNF's time server sets the slot clock, so every NR timer slows with it: about 11× slower."],
    ["Tuned for slow slots", "Fewer polls, no empty slots and earlier uplink grants made the slow clock usable."],
  ];
  const DUR = 6500, HOLD = 1200, CYC = 2600;
  const $s = id => document.getElementById(id), NS = "http://www.w3.org/2000/svg";
  if (!$s("nfsv")) return;
  const el = (tag, at, parent) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); parent.appendChild(e); return e; };
  const BX = [["vnf", 20, 30, "OAI VNF", "MAC · RLC · PDCP · RRC · scheduler"], ["pnf", 20, 118, "OAI PNF", "nrlink hook in the slot handler"],
              ["gbr", 20, 206, "gnb_bridge", "polls the UEs · fragments"], ["gbd", 20, 290, "gNB board", "FRDM-MCXW72"],
              ["ue", 400, 30, "3 nrUEs", "MAC runs as with a real PHY"], ["uel1", 400, 118, "nrlink_ue", "in place of the nrUE's PHY"],
              ["ubr", 400, 206, "ue_bridge × 3", "answers the polls"], ["ubd", 400, 290, "UE boards × 3", "FRDM-MCXW72"]];
  const B = {};
  for (const [id, x, y, t, s] of BX) {
    const g = el("g", { class: "nf-box", id: "nf-" + id }, $s("nfboxes"));
    el("rect", { x, y, width: 220, height: 50, rx: 4 }, g);
    el("text", { x: x + 12, y: y + 21, class: "t" }, g).textContent = t;
    el("text", { x: x + 12, y: y + 39, class: "s" }, g).textContent = s;
    B[id] = g;
  }
  const LN = [[130, 80, 130, 118, "nFAPI P5 · P7"], [130, 168, 130, 206, "UDP"], [130, 256, 130, 290, "serial, 1 Mbaud"],
              [510, 80, 510, 118, ""], [510, 168, 510, 206, "UDP"], [510, 256, 510, 290, "serial"]];
  for (const [x1, y1, x2, y2, lab] of LN) {
    el("line", { x1, y1, x2, y2, class: "nf-ln" }, $s("nfln"));
    if (lab) el("text", { x: x1 + 8, y: (y1 + y2) / 2 + 4, class: "nf-lab" }, $s("nfln")).textContent = lab;
  }
  // the two clocks: 10 slots (7 D, 1 S, 2 U) in 5 ms (mu = 1) and in 57 ms (measured with the AGVs)
  const X0 = 70, PX = 9, SLC = ["", "", "", "", "", "", "", "s", "u", "u"];
  const strip = (g, y, ms) => { g.innerHTML = ""; for (let i = 0; i < 10; i++) el("rect", { x: X0 + i * ms / 10 * PX, y, width: Math.max(.6, ms / 10 * PX - 1), height: 24, class: "sv-slot " + SLC[i] }, g); };
  strip($s("nfreal"), 400, 5);
  for (let t = 0; t <= 60; t += 10) {
    el("line", { x1: X0 + t * PX, x2: X0 + t * PX, y1: 470, y2: 476, class: "sv-grid" }, $s("nfaxis"));
    el("text", { x: X0 + t * PX, y: 492, class: "sv-labm", "text-anchor": "middle" }, $s("nfaxis")).textContent = t;
  }
  const DLP = [[130, 55], [130, 143], [130, 231], [130, 315], [400, 315], [510, 315], [510, 231], [510, 143], [510, 55]];
  const ULP = [[400, 315], [240, 315], [130, 315], [130, 231], [130, 143]];
  function along(P, f) {
    const L = []; let T = 0;
    for (let i = 1; i < P.length; i++) { const d = Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]); L.push(d); T += d; }
    let r = f * T;
    for (let i = 0; i < L.length; i++) { if (r <= L[i]) { const q = r / L[i]; return [P[i][0] + (P[i + 1][0] - P[i][0]) * q, P[i][1] + (P[i + 1][1] - P[i][1]) * q]; } r -= L[i]; }
    return P.at(-1);
  }
  const dot = (id, p, col) => { const c = $s(id); if (!p) { c.style.display = "none"; return; } c.style.display = ""; c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); c.style.fill = col; };
  const sq = (id, p, col) => { const c = $s(id); if (!p) { c.style.display = "none"; return; } c.style.display = ""; c.setAttribute("x", p[0] - 5.5); c.setAttribute("y", p[1] - 5.5); c.style.fill = col; };
  const ease = p => p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
  let step = 0, t0 = performance.now(), playing = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  function render(now) {
    const k = step, p = Math.min(1, (now - t0) / DUR), e = ease(p), ph = (now % CYC) / CYC;
    const hot = { 0: ["vnf", "pnf"], 1: ["pnf", "uel1"], 2: [], 3: ["gbd", "ubd"], 4: ["gbr"], 5: ["pnf"], 6: ["gbr"] }[k] || [];
    for (const id in B) { B[id].classList.toggle("hot", hot.includes(id)); B[id].classList.toggle("on", k >= 1); }
    $s("nfghost").style.opacity = k === 1 ? 1 : k > 1 ? .45 : .9;
    $s("nfx1").style.display = $s("nfx2").style.display = k >= 1 && (k > 1 || e > .3) ? "" : "none";
    const lab = $s("nfpl"); lab.textContent = "";
    ["nfp0", "nfp1"].forEach(i => dot(i, null)); ["nff0", "nff1", "nff2", "nff3"].forEach(i => sq(i, null));
    if (k === 0) { dot("nfp0", along([[130, 80], [130, 118]], ph), "var(--blue)"); lab.setAttribute("x", 200); lab.setAttribute("y", 104); lab.textContent = ph < .5 ? "slot request" : "TX_DATA · DL_TTI"; }
    if (k === 2 || k >= 5) dot("nfp0", along(DLP, ph), "var(--blue)");
    if (k === 3) {
      if (ph < .35) { dot("nfp0", along([[240, 315], [400, 315]], ph / .35), "var(--idle)"); lab.setAttribute("x", 320); lab.setAttribute("y", 290); lab.textContent = "poll UE 1"; }
      else { dot("nfp1", along(ULP, (ph - .35) / .65), "var(--navy)"); lab.setAttribute("x", 320); lab.setAttribute("y", 194); lab.textContent = "CRC · RX_DATA · UCI · RACH"; }
    }
    if (k === 4) {
      for (let j = 0; j < 4; j++) { const f = (ph - j * .12) / .55; sq("nff" + j, f >= 0 && f <= 1 ? along([[130, 231], [130, 315], [400, 315], [510, 315], [510, 231]], f) : null, "var(--navy)"); }
      lab.setAttribute("x", 320); lab.setAttribute("y", 360); lab.textContent = "780 B LiDAR scan = 4 air packets";
    }
    // clocks
    const slow = k < 5 ? 5 : k === 5 ? 5 + 52 * e : 57;
    strip($s("nfslow"), 438, slow);
    $s("nfl2").textContent = k < 5 ? "nrlink" : "nrlink";
    const rx = X0 + ((now % (CYC * 5 / 57)) / (CYC * 5 / 57)) * 5 * PX, sx = X0 + ph * slow * PX;
    $s("nfph0").setAttribute("x1", rx); $s("nfph0").setAttribute("x2", rx); $s("nfph1").setAttribute("x1", sx); $s("nfph1").setAttribute("x2", sx);
    const sl = slow / 10;
    $s("nf-slot").innerHTML = `${sl.toFixed(1)}<small>ms</small>`;
    $s("nf-sps").textContent = Math.round(1000 / sl);
    const list = $s("nf-list"); list.hidden = k !== 6; $s("nf-tiles").hidden = k === 6;
    [...list.children].forEach((li, i) => li.classList.toggle("in", k === 6 && p > i * .14));
  }
  function show(k) {
    step = (k + S.length) % S.length; t0 = performance.now();
    $s("nf-n").textContent = `${String(step + 1).padStart(2, "0")} / ${String(S.length).padStart(2, "0")}`;
    $s("nf-h").textContent = S[step][0]; $s("nf-p").textContent = S[step][1];
    [...$s("nf-dots").children].forEach((b, i) => { b.removeAttribute("aria-current"); if (i === step) b.setAttribute("aria-current", "step"); });
    if (!playing) t0 = performance.now() - DUR;
  }
  S.forEach((s, i) => { const b = document.createElement("button"); b.type = "button"; b.textContent = String(i + 1).padStart(2, "0"); b.setAttribute("aria-label", `Step ${i + 1}: ${s[0]}`); b.onclick = () => show(i); $s("nf-dots").appendChild(b); });
  const setPlay = on => { playing = on; $s("nf-play").textContent = on ? "Pause" : "Play"; if (on) t0 = performance.now(); };
  $s("nf-play").onclick = () => setPlay(!playing);
  $s("nf-prev").onclick = () => show(step - 1); $s("nf-next").onclick = () => show(step + 1);
  $s("nf-reset").onclick = () => { setPlay(!matchMedia("(prefers-reduced-motion: reduce)").matches); show(0); };
  $s("nf-pres").onclick = () => { const on = $s("nfapi").classList.toggle("present"); $s("nf-pres").setAttribute("aria-pressed", on); $s("nf-pres").textContent = on ? "Exit presentation" : "Presentation"; };
  document.addEventListener("keydown", e => {
    if ($s("nfapi").hidden) return;
    if (e.key === "Escape" && $s("nfapi").classList.contains("present")) $s("nf-pres").click();
    if (!(e.target.closest && e.target.closest("#nfapi"))) return;
    if (e.key === "ArrowRight") show(step + 1);
    if (e.key === "ArrowLeft") show(step - 1);
  });
  show(0);
  (function loop(now) {
    if (!$s("nfapi").hidden) {
      if (playing && now - t0 > DUR + HOLD) show(step + 1);
      render(now);
    }
    requestAnimationFrame(loop);
  })(performance.now());
})();
"""
