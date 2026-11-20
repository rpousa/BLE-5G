"""The 'Slides' tab: the 8-slide deck (deck/, the same files as the Slides artifact) shown inside the page. Each 1920x1080
slide is scaled to the panel's width; the deck's CSS is scoped under .dk so it never touches the page."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DECK_URL = "https://claude.ai/artifact/7tsYLEEMSUPhv7MpGSX1Lh"

DECK_CSS = """
.dk-wrap { padding: 24px; display: grid; gap: 16px; }
.dk-stage { position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; border: 1px solid var(--border); border-radius: 4px; background: #F7FAFC; }
.dk .sl { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; transform-origin: 0 0; box-sizing: border-box;
          padding: 128px; background: #F7FAFC; display: none; flex-direction: column; gap: 40px; color: #022E57; font-family: "IBM Plex Sans", sans-serif; }
.dk .sl.on { display: flex; }
.dk .sl.alt { background: #EBF2F6; } .dk .sl.dark { background: #022E57; color: #F7FAFC; } .dk .sl.foot { padding: 128px 128px 160px; }
.dk h1, .dk h2, .dk h3 { font-family: "Space Grotesk", sans-serif; font-weight: 600; color: #022E57; margin: 0; letter-spacing: 0; text-transform: none; }
.dk h1 { font-size: 112px; line-height: 1.05; } .dk h2 { font-size: 72px; line-height: 1.1; } .dk h3 { font-size: 40px; line-height: 1.15; }
.dk .dark h2, .dk .dark h3 { color: #F7FAFC; }
.dk p { margin: 0; font-size: 32px; line-height: 1.4; max-width: none; }
.dk .eyebrow { font-family: "IBM Plex Mono", monospace; font-size: 26px; letter-spacing: 3px; color: #E20073; font-weight: 500; }
.dk .lead { font-size: 40px; line-height: 1.35; color: #4F6A84; max-width: 1400px; } .dk .dark .lead { color: #BFD6E8; }
.dk .row { display: flex; gap: 32px; align-items: stretch; }
.dk .card { flex: 1; background: #FFFFFF; border: 2px solid #D5E0E8; border-radius: 6px; padding: 36px; display: flex; flex-direction: column; gap: 16px; overflow: visible; }
.dk .card .txt { font-size: 28px; line-height: 1.4; color: #4F6A84; }
.dk .card .num { font-family: "IBM Plex Mono", monospace; font-size: 26px; color: #E20073; font-weight: 500; }
.dk .big { font-family: "Space Grotesk", sans-serif; font-size: 120px; font-weight: 600; line-height: 1; color: #022E57; } .dk .dark .big { color: #F7FAFC; }
.dk .unit { font-size: 32px; color: #4F6A84; } .dk .dark .unit { color: #BFD6E8; }
.dk .stat { flex: 1; display: flex; flex-direction: column; gap: 12px; border-top: 4px solid #2C93CC; padding-top: 28px; }
.dk .dark .stat { border-top-color: #83CFED; } .dk .stat.hl { border-top-color: #E20073; }
.dk .statlab { font-size: 28px; line-height: 1.35; color: #4F6A84; } .dk .dark .statlab { color: #BFD6E8; }
.dk .footer { position: absolute; left: 128px; bottom: 64px; width: 1664px; display: flex; justify-content: space-between; font-size: 24px; color: #4F6A84; }
.dk .dark .footer { color: #9FBBD3; }
.dk .bar { height: 72px; display: flex; align-items: center; justify-content: center; color: #F7FAFC; font-size: 26px; font-weight: 600; flex: none; }
.dk .barlab { font-family: "IBM Plex Mono", monospace; font-size: 26px; color: #4F6A84; width: 220px; flex: none; }
.dk .tl { font-size: 28px; color: #022E57; }
.dk .cover-logo { width: 242px; height: 132px; object-fit: contain; }
.dk table { border-collapse: collapse; } .dk th, .dk td { padding: 14px 18px; text-align: left; border-bottom: 1px solid rgba(255,255,255,.18); font-size: 28px; }
.dk th { font: 600 26px "IBM Plex Sans", sans-serif; color: #BFD6E8; text-transform: none; letter-spacing: 0; background: none; }
.dk tr:hover td { background: none; }
.dk .panel { flex: 1; display: flex; flex-direction: column; gap: 16px; border: 2px solid #D5E0E8; border-radius: 6px; padding: 28px; background: #FBFCFD; }
.dk .radio { width: 440px; display: flex; flex-direction: column; gap: 20px; justify-content: center; flex: none; }
.dk .pl { font-family: "IBM Plex Mono", monospace; font-size: 26px; letter-spacing: 3px; color: #4F6A84; font-weight: 500; }
.dk .box { flex: 1; border-radius: 6px; padding: 18px 24px; display: flex; align-items: center; justify-content: center; border: 3px solid #2C93CC; background: #FFFFFF; }
.dk .box p { font-family: "Space Grotesk", sans-serif; font-size: 32px; font-weight: 600; color: #022E57; text-align: center; }
.dk .box.core { background: #022E57; border-color: #022E57; } .dk .box.core p { color: #F7FAFC; }
.dk .box.brd { border-color: #E20073; flex: none; height: 96px; } .dk .radio .row .box.brd { flex: 1; }
.dk .box.ue { background: #83CFED; border-color: #83CFED; }
.dk .air { border-left: 4px dashed #2C93CC; margin-left: 40px; padding: 18px 0 18px 20px; }
.dk .air p { font-family: "IBM Plex Mono", monospace; font-size: 26px; color: #0D659E; white-space: nowrap; }
.dk .rise { animation: dkrise 600ms ease-out both; animation-delay: calc(var(--i, 0) * 140ms); }
@keyframes dkrise { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: none; } }
.dk .grow { animation: dkgrow 900ms ease-out both; animation-delay: calc(var(--i, 0) * 200ms); }
@keyframes dkgrow { from { opacity: 0; transform: translateX(-40px); } to { opacity: 1; transform: none; } }
.dk-note { margin: 0; font-size: 15px; line-height: 1.5; color: var(--text-muted); max-width: 110ch; min-height: 3em; }
#slides.present { position: fixed; inset: 0; z-index: 60; overflow: auto; border-radius: 0; }
#slides.present .dk-stage { max-width: calc((100vh - 140px) * 16 / 9); margin: 0 auto; }
@media (prefers-reduced-motion: reduce) { .dk .rise, .dk .grow { animation: none; } }
"""

DECK_JS = r"""
// ---------------------------------------------------------------- Slides tab
(function deck() {
  const sec = document.getElementById("slides"); if (!sec) return;
  const st = sec.querySelector(".dk-stage"), S = [...sec.querySelectorAll(".dk .sl")], note = document.getElementById("dk-note");
  let k = 0;
  const fitAll = () => { const s = st.clientWidth / 1920; S.forEach(x => x.style.transform = `scale(${s})`); };
  function go(i) {
    k = (i + S.length) % S.length;
    S.forEach((x, j) => x.classList.toggle("on", j === k));
    document.getElementById("dk-n").textContent = `${String(k + 1).padStart(2, "0")} / ${String(S.length).padStart(2, "0")}`;
    [...document.getElementById("dk-dots").children].forEach((b, j) => { b.removeAttribute("aria-current"); if (j === k) b.setAttribute("aria-current", "step"); });
    note.textContent = S[k].dataset.note || "";
    fitAll();
  }
  S.forEach((x, i) => { const b = document.createElement("button"); b.type = "button"; b.textContent = String(i + 1).padStart(2, "0"); b.setAttribute("aria-label", "Slide " + (i + 1)); b.onclick = () => go(i); document.getElementById("dk-dots").appendChild(b); });
  document.getElementById("dk-prev").onclick = () => go(k - 1);
  document.getElementById("dk-next").onclick = () => go(k + 1);
  st.onclick = () => go(k + 1);
  document.getElementById("dk-pres").onclick = () => { const on = sec.classList.toggle("present"); const b = document.getElementById("dk-pres"); b.setAttribute("aria-pressed", on); b.textContent = on ? "Exit presentation" : "Presentation"; requestAnimationFrame(fitAll); };
  document.addEventListener("keydown", e => {
    if (sec.hidden || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) || (e.target.closest && e.target.closest("#seg"))) return;
    if (e.key === "Escape" && sec.classList.contains("present")) document.getElementById("dk-pres").click();
    if (e.key === "ArrowRight" || e.key === "PageDown" || (e.key === " " && sec.classList.contains("present"))) { e.preventDefault(); go(k + 1); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(k - 1); }
  });
  window.addEventListener("resize", fitAll);
  new ResizeObserver(fitAll).observe(st);
  go(0);
})();
"""


def deck_html(logo_uri):
    d = os.path.join(HERE, "deck")
    order = json.load(open(os.path.join(d, "deck.json")))["order"]
    out = []
    for n, sid in enumerate(order, 1):
        h = open(os.path.join(d, "slides", sid + ".html"), encoding="utf-8").read().strip()
        m = re.search(r"<aside>(.*?)</aside>", h, re.S)
        note = m.group(1).strip() if m else ""
        h = re.sub(r"\s*<aside>.*?</aside>", "", h, flags=re.S)
        h = re.sub(r'<section id="[^"]*"( class="([^"]*)")?([^>]*)>',
                   lambda mm: f'<div class="sl {mm.group(2) or ""}"{mm.group(3).replace(" data-transition=\"fade\"", "")} data-note="{note.replace(chr(34), "&quot;")}" role="group" aria-roledescription="slide" aria-label="Slide {n}">', h, 1)
        h = h.replace("</section>", "</div>").replace('<span class="pg"></span>', f"<span>{n}</span>")
        h = re.sub(r'src="/_blob/[0-9a-f]+"', f'src="{logo_uri}"', h)
        out.append(h)
    return f"""
  <section class="card" aria-label="Presentation" id="slides" data-tab="slides">
    <div class="hd"><h2>The testbed in 8 slides</h2><span class="meta"><span id="dk-n">01 / 08</span> · click the slide or use ← → · <a href="{DECK_URL}" target="_blank" rel="noopener">open as a deck</a></span></div>
    <div class="dk-wrap">
      <div class="dk-stage dk">{"".join(out)}</div>
      <p class="dk-note" id="dk-note" aria-live="polite"></p>
    </div>
    <div class="st-ctl">
      <button type="button" id="dk-prev" aria-label="Previous slide">‹</button>
      <button type="button" class="primary" id="dk-next" aria-label="Next slide">Next ›</button>
      <div class="st-dots" id="dk-dots"></div>
      <span class="st-sp"></span>
      <button type="button" id="dk-pres" aria-pressed="false">Presentation</button>
    </div>
  </section>
"""
