import sys, time, queue
sys.path.insert(0, "/home/debian/oai/nrlink")
import nrlink
a = nrlink.Board("2HOKMMVEX1WMP"); b = nrlink.Board("GBAMNH4BQ3CSB")
for x in (a, b):
    x.configure(0x5A, 0x1F)
def drain(x, t=0.3):
    out = []; end = time.time() + t
    while time.time() < end:
        try:
            r = x.rx.get(timeout=0.05); out.append(r)
        except queue.Empty:
            pass
    return out
for size in (100, 400, 700, 1400):
    for rep in range(3):
        drain(b, 0.05); drain(a, 0.05)
        frags = nrlink.fragments(4, 0, 1000 + rep, 0, bytes(size))
        t0 = time.time()
        for p in frags:
            a.tx(p)
        got = drain(b, 0.25)
        hdr = [nrlink.AIR_HDR.unpack_from(p) for _r, _t, p in got]
        l0 = sorted(h[4] for h in hdr if h[1] == 0); l1 = sorted(h[4] for h in hdr if h[1] == 1)
        print(f"{size} B, {len(frags)} frags: UE2 heard link0 {l0} link1 {l1}", flush=True)
