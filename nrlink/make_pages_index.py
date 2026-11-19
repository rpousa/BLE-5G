#!/usr/bin/env python3
"""Builds docs/pages/index.html: a landing page with a thumbnail and the title of every page.

Thumbnails are screenshots of the top of each page (1280 x 800, scaled to 640 x 400), taken in a light and a
dark version with Playwright in Docker, from the pages server (pages_server.py) and the live AGV page.
Rerun it after a page changes:

  python3 make_pages_index.py [--dir /home/debian/BLE-5G/docs/pages] [--server http://127.0.0.1:8088]
                              [--live http://127.0.0.1:8090/] [--no-shots]
"""
import argparse
import datetime
import html
import json
import os
import re
import subprocess

IMAGE = "mcr.microsoft.com/playwright/python:v1.47.0-jammy"

# Short descriptions; a page not listed here shows its title only.
ABOUT = {
    "nrlink-two-pc-split.html": "The whole setup over two PCs: architecture, who is connected where, latency per hop, "
                                "slot schemes, packet sequences, stress tests and the NR against Bluetooth comparison.",
    "agv-testbed.html": "LiDAR scans over NR and detections over Bluetooth at once: campaign results by load, mode, "
                        "power and fade, with a replay of the tuned run.",
    "agv-relay-chain.html": "Three AGVs and a gNB pole: the AGV nearest the pole talks to the gNB and relays the "
                            "others over a sidelink chain, and the role moves as they drive. Simulated with the boards' "
                            "measured links.",
    "agv-dual-gateway.html": "Two AGVs connected to the gNB as gateways, the third one sidelink hop from either and "
                             "picking one per message: board runs at L1 and L2 across sidelink window widths, with a replay. "
                             "Now live as a platoon with coverage by position: AGVs lose the gNB in a shielded cold store, "
                             "relay through another AGV one at a time, now as a 3GPP layer-3 relay (configured thresholds, PC5-S with security, DHCPv4, a relay PDU session, QoS, Remote UE Report to the SMF), "
                             "with no relayed packets lost; every step compared; packet sequence, slot scheme, traffic flows.",
    "nr-tuning.html": "Each NR optimisation step measured on its own: slot rate and one-way delays, "
                      "from 643 ms to 144 ms for a LiDAR scan.",
    "factory-floor-downlink.html": "The factory demo: downlink traffic classes to the UEs and their one-way delays.",
    "mcxw72-hci-flow-control-wiring.html": "How each Bluetooth board's HCI UART is wired through a Raspberry Pi Pico "
                                           "with RTS/CTS flow control.",
}
ORDER = ["nrlink-two-pc-split.html", "agv-testbed.html", "agv-relay-chain.html", "agv-dual-gateway.html", "nr-tuning.html", "factory-floor-downlink.html",
         "mcxw72-hci-flow-control-wiring.html"]

SHOT = r'''
import asyncio, json, sys
from playwright.async_api import async_playwright
async def main(jobs):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for url, out, scheme in jobs:
            pg = await b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=0.5, color_scheme=scheme)
            try:
                await pg.goto(url, timeout=30000); await pg.wait_for_timeout(3000)
                await pg.screenshot(path=out, type="jpeg", quality=82)
                print("ok", out, flush=True)
            except Exception as e:
                print("fail", url, e, flush=True)
            await pg.close()
        await b.close()
asyncio.run(main(json.load(open(sys.argv[1]))))
'''


def title_of(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        m = re.search(r"<title>([^<]*)</title>", f.read(200000))
    return html.unescape(m.group(1).strip()) if m else os.path.basename(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="/home/debian/BLE-5G/docs/pages")
    ap.add_argument("--server", default="http://127.0.0.1:8088")
    ap.add_argument("--live", default="http://127.0.0.1:8090/")
    ap.add_argument("--no-shots", action="store_true", help="keep the existing thumbnails")
    a = ap.parse_args()

    files = sorted((f for f in os.listdir(a.dir) if f.endswith(".html") and f != "index.html"),
                   key=lambda f: (ORDER.index(f) if f in ORDER else len(ORDER), f))
    pages = [{"file": f, "slug": f[:-5], "title": title_of(os.path.join(a.dir, f)), "about": ABOUT.get(f, ""),
              "mtime": os.path.getmtime(os.path.join(a.dir, f))} for f in files]
    os.makedirs(os.path.join(a.dir, "thumbs"), exist_ok=True)

    if not a.no_shots:
        jobs = []
        for p in pages:
            for sc in ("light", "dark"):
                jobs.append([f"{a.server}/{p['file']}", f"/pages/thumbs/{p['slug']}-{sc}.jpg", sc])
        if a.live:
            for sc in ("light", "dark"):
                jobs.append([a.live, f"/pages/thumbs/agv-live-{sc}.jpg", sc])
        with open(os.path.join(a.dir, "thumbs", ".jobs.json"), "w") as f:
            json.dump(jobs, f)
        with open(os.path.join(a.dir, "thumbs", ".shot.py"), "w") as f:
            f.write(SHOT)
        subprocess.run(["docker", "run", "--rm", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
                        "-e", "HOME=/tmp", "-v", f"{a.dir}:/pages", IMAGE, "bash", "-c",
                        "pip install -q --user playwright==1.47.0 >/dev/null 2>&1; "
                        "python3 /pages/thumbs/.shot.py /pages/thumbs/.jobs.json"], check=False)
        for f in (".jobs.json", ".shot.py"):
            os.remove(os.path.join(a.dir, "thumbs", f))

    live = os.path.exists(os.path.join(a.dir, "thumbs", "agv-live-light.jpg"))
    cards = []

    def card(href, slug, title, about, meta, extra=""):
        cards.append(f'''    <a class="card" href="{href}"{extra}>
      <picture>
        <source srcset="thumbs/{slug}-dark.jpg" media="(prefers-color-scheme: dark)">
        <img src="thumbs/{slug}-light.jpg" alt="" width="640" height="400" loading="lazy">
      </picture>
      <span class="txt"><span class="t">{html.escape(title)}</span>
        <span class="a">{html.escape(about)}</span>
        <span class="m">{meta}</span></span>
    </a>''')

    if live:
        card("http://192.168.10.223:8090/", "agv-live", "AGV Testbed, live",
             "The running testbed: change the load, mode and radio power, and watch both paths as it happens.",
             '<span class="dot"></span>live · port 8090', ' data-port="8090"')
    for p in pages:
        d = datetime.datetime.fromtimestamp(p["mtime"]).strftime("%-d %b %Y")
        card(p["file"], p["slug"], p["title"], p["about"], f"updated {d}")

    built = datetime.datetime.now().strftime("%-d %b %Y, %H:%M")
    out = PAGE.replace("{{CARDS}}", "\n".join(cards)).replace("{{BUILT}}", built)
    with open(os.path.join(a.dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)
    print(f"index.html: {len(cards)} pages")


PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BLE-5G Pages</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {
  --bg: #eef1f4; --panel: #ffffff; --line: #d3d9e0; --ink: #17202b; --ink-2: #4b5866; --ink-3: #7a8693;
  --signal: #b86e00; --ok: #0f7a55; --r: 6px; color-scheme: light;
  --display: "Barlow Semi Condensed", "Arial Narrow", "Roboto Condensed", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #0e141b; --panel: #151d26; --line: #2a3644; --ink: #e6ebf0; --ink-2: #a9b4bf; --ink-3: #74808c;
          --signal: #f0a830; --ok: #3dbb72; color-scheme: dark; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.4 var(--display); }
.wrap { max-width: 1480px; margin: 0 auto; padding: 22px 16px 40px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; margin-bottom: 18px; }
h1 { margin: 0; font-size: 1.7rem; font-weight: 600; }
header p { margin: 0; color: var(--ink-2); max-width: 80ch; }
header .built { margin-left: auto; font: 12px var(--mono); color: var(--ink-3); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap: 16px; }
.card { display: grid; grid-template-rows: auto 1fr; background: var(--panel); border: 1px solid var(--line);
        border-radius: var(--r); overflow: hidden; color: inherit; text-decoration: none;
        transition: border-color .15s, transform .15s; }
.card:hover, .card:focus-visible { border-color: var(--signal); transform: translateY(-2px); outline: none; }
.card img { display: block; width: 100%; height: auto; aspect-ratio: 16 / 10; object-fit: cover; object-position: top;
            border-bottom: 1px solid var(--line); background: var(--bg); }
.txt { display: grid; gap: 4px; padding: 11px 14px 13px; align-content: start; }
.t { font-size: 1.15rem; font-weight: 600; }
.a { color: var(--ink-2); font-size: .9rem; }
.m { font: 11.5px var(--mono); color: var(--ink-3); display: inline-flex; align-items: center; gap: 6px; margin-top: 2px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .3; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } .card { transition: none; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>BLE-5G Pages</h1>
    <p>5G NR over nrlink and Bluetooth LE non-3GPP access on FRDM-MCXW72 boards, free5GC and OpenAirInterface: every page of the testbed, served from the core PC.</p>
    <span class="built">built {{BUILT}}</span>
  </header>
  <main class="grid">
{{CARDS}}
  </main>
</div>
<script>
// The live page runs on its own port: point it at whatever host this page was opened from (LAN or Tailscale).
for (const a of document.querySelectorAll("a[data-port]")) {
  a.href = location.protocol + "//" + location.hostname + ":" + a.dataset.port + "/";
}
</script>
</body>
</html>
'''

if __name__ == "__main__":
    main()
