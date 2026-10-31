#!/usr/bin/env python3
"""Bridge test without OAI: fake PNF slots (multi-fragment DL TB + UL poll) and fake nrUEs, over the real boards."""
import socket, struct, sys, time
sys.path.insert(0, __import__("os").path.dirname(__file__))
from nrlink import MSG_HDR, ITEM_HDR, NRLINK_MAGIC, NRLINK_DL_SLOT, NRLINK_UL_SLOT, NRLINK_HELLO, IT_TB, IT_PUSCH, items
import config

ues = {}
for u, (_s, port, _c) in config.UES.items():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0)); s.settimeout(0.3)
    s.sendto(MSG_HDR.pack(NRLINK_MAGIC, NRLINK_HELLO, 0, 0, 0, 0x4600 + u, 0), ("127.0.0.1", port))
    ues[u] = (s, port)
time.sleep(0.3)
pnf = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); pnf.bind(("127.0.0.1", 0)); pnf.settimeout(2)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
tb = bytes(range(256)) * 2 + b"x" * 88
lat, ul_ok, dl_ok = [], 0, {u: 0 for u in ues}
for k in range(N):
    sfn, slot = 200 + k, 8
    for u, (s, port) in ues.items():
        pus = struct.pack("<HBH", 0x4600 + u, 1, 40) + bytes([k ^ u]) * 40
        s.sendto(MSG_HDR.pack(NRLINK_MAGIC, NRLINK_UL_SLOT, sfn, slot, 0, 0x4600 + u, 1) + ITEM_HDR.pack(IT_PUSCH, len(pus)) + pus,
                 ("127.0.0.1", port))
    time.sleep(0.01)
    body = struct.pack("<HH", 0x4601, len(tb)) + tb
    dl = MSG_HDR.pack(NRLINK_MAGIC, NRLINK_DL_SLOT, sfn, slot, 1, 0, 1) + ITEM_HDR.pack(IT_TB, len(body)) + body
    t0 = time.time(); pnf.sendto(dl, ("127.0.0.1", config.GNB_UDP_PORT))
    r, _ = pnf.recvfrom(65535); lat.append((time.time() - t0) * 1000)
    ul_ok += sum(1 for t, b in items(r) if t == IT_PUSCH)
    for u, (s, _p) in ues.items():
        try:
            dl_ok[u] += s.recvfrom(65535)[0] == dl
        except socket.timeout:
            pass
print(f"UL PUSCH received {ul_ok}/{N * len(ues)}, DL slots intact per UE {dl_ok} of {N}; "
      f"slot exchange (600 B DL + 3 polls) {min(lat):.1f}/{sum(lat) / len(lat):.1f}/{max(lat):.1f} ms min/avg/max")
