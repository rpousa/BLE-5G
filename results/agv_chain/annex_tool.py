import re
def element(s, i):
    """(start, end) of the balanced element whose opening tag starts at s[i]."""
    tag = re.match(r"<(\w+)", s[i:]).group(1)
    pat = re.compile(rf"<{tag}\b|</{tag}>")
    d, j = 0, i
    for m in pat.finditer(s, i):
        d += 1 if m.group(0)[1] != "/" else -1
        if d == 0:
            return i, m.end()
    raise ValueError(tag)
def text(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip()

ANNEX_CSS = """
.full { display: none; scroll-margin-top: 140px; }
body.onepage .full { display: block; }
body.onepage .brief, .axhead { display: none; }
body.onepage .axhead { display: block; }
.axl { font-size: 14px; color: var(--blue-deep); white-space: nowrap; }
.short { margin: 0; color: var(--navy); font-size: 15px; line-height: 1.5; max-width: 75ch; }
"""

# (text that starts or sits in the element, what stays in its place, the annex heading)
MOVES = [
    ('<div class="sub">', "Three AGVs and one gNB. An AGV the gNB cannot reach sends through another AGV over the sidelink: a 3GPP layer-3 relay, on gNB-scheduled sidelink.", "Overview"),
    ("Moving dots mark links", "Moving dots: links carrying data now.", "Architecture: legend"),
    ("As a layer-3 UE-to-network relay", "Layer-3 relay (TS 23.304): discovery, a PC5 link, an IP address from the relay, then routing and NAT into its PDU session.", "Packet sequence: the layer-3 relay"),
    ("Mode 1 as in TS", "Mode 1 (TS 38.321): the gNB gives each AGV a slice of the window from its buffer report, and a pool to an AGV out of coverage.", "Slot scheme: sidelink mode 1"),
    ("<b>Fixed window</b>", "Fixed window: after slot 9 of each frame the gNB stays off the air for 25 ms of sidelink.", "Slot scheme: the fixed window"),
    ("These runs relayed at the application", "Application-level relay runs, each above the relay chain at the same load. Red: 5 % lost or more.", "Board runs: reading the table"),
    ('<ul class="plan"', "Two gateways carried L2 with at most 3.4 % lost; the relay chain lost up to 57 %.", "Board runs: findings"),
    ("The AGVs drive the same loop", "The AGVs drive the loop as a platoon. In the shielded cold store the gNB signal falls to −118 dBm; below −100 dBm an AGV switches to the sidelink.", "Coverage: the model"),
    ("The boards sit on one desk", "", "Coverage: on the desk"),
    ("Each change to the coverage scenario", "Each change, 10–15 min on the boards. Lost: uplink messages that never reached the edge.", "Coverage runs: method"),
    ('<ul class="hint" style="max-width:110ch;margin:0;padding-left:18px">', "", "Coverage runs: notes on each change"),
    ("IP packet = payload", "IP packet = payload + 28 B; one air packet carries up to 255 B.", "Traffic flows: sizes and signalling"),
    ('<ol class="steps2"', "The remote sends each message through either gateway, its pick per message.", "Two gateways: step by step"),
    ("In these runs the gateways relay", "These runs relay at the application; the coverage scenario relays at layer 3.", "Two gateways: application relay"),
]


def annexify(s, moves=MOVES):
    """Each long element stays where it was, hidden in the tabbed views and shown in the Annex view (the whole page on one
    tab, as it was before the tabs); in its place the tabbed views show a short version."""
    for n, (key, short, head) in enumerate(moves):
        i = s.find(key)
        if i < 0:
            print("annex: not found:", key[:40]); continue
        if not key.startswith("<"):
            i = s.rfind("<", 0, i + 1)
            while not s[i:].startswith(("<p", "<div", "<ul", "<ol", "<span")):
                i = s.rfind("<", 0, i)
        a, b = element(s, i)
        el = s[a:b]
        aid = f"ax-{n + 1}"
        more = f' <a class="axl" href="#{aid}">More in the Annex ›</a>'
        if el.startswith("<span"):
            repl = f'<span class="hint brief">{short}{more}</span><span class="full" id="{aid}">{el}</span>'
        else:
            brief = f'<p class="short brief">{short}{more}</p>' if short else f'<p class="short brief"><a class="axl" href="#{aid}">{head} · in the Annex ›</a></p>'
            repl = f'{brief}<div class="full" id="{aid}">{el}</div>'
        s = s[:a] + repl + s[b:]
    head = ('\n  <section class="card axhead" aria-label="Annex" id="annex" data-tab="annex"><div class="hd"><h2>Annex: the whole page</h2>'
            '<span class="meta">every section on one tab, with the full text and today\'s numbers</span></div></section>\n')
    return s.replace('<main class="wrap">', '<main class="wrap">' + head, 1)
