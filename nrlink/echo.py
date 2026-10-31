#!/usr/bin/env python3
"""UDP echo test: one source sends numbered, timestamped packets; a reflector sends each one straight back.

  core PC, in ext-dn's netns:  echo.py reflect [--port 5230]
  UE side, in the UE's netns:  echo.py send --dst 192.168.70.132 --bind <UE addr> --dev <PDU iface> --rate 10,20,30

Every time is taken on the sender's clock, so the two PCs' clock offset does not matter. The reflector counts the
packets of each run and the sender asks for that count at the end, which splits the loss into uplink (lost before
the reflector) and downlink (lost on the way back). Output: one JSON object per rate on stdout.
"""
import argparse
import json
import os
import socket
import statistics
import struct
import sys
import threading
import time

HDR = struct.Struct("!4sIIdI")        # magic, run id, seq, send time (sender clock), reflector count
MAGIC, STAT = b"ECHO", b"STAT"


def reflect(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    counts = {}
    print(f"reflector on udp {port}", flush=True)
    while True:
        p, a = s.recvfrom(4096)
        if len(p) >= HDR.size and p[:4] == MAGIC:
            m, run, seq, t, _ = HDR.unpack_from(p)
            counts[run] = counts.get(run, 0) + 1
            if len(counts) > 1000:
                counts.pop(next(iter(counts)))
            s.sendto(HDR.pack(m, run, seq, t, counts[run]) + p[HDR.size:], a)
        elif len(p) >= 8 and p[:4] == STAT:
            run = struct.unpack_from("!I", p, 4)[0]
            s.sendto(STAT + struct.pack("!II", run, counts.get(run, 0)), a)


def pct(v, q):
    return round(v[min(len(v) - 1, int(q * len(v)))] * 1000, 1) if v else None


def send_one(a, rate):
    run = struct.unpack("!I", os.urandom(4))[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if a.dev:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, a.dev.encode())
    s.bind((a.bind or "0.0.0.0", 0))
    s.settimeout(0.2)
    dst = (a.dst, a.port)
    rtt, back, stat = {}, {}, {}
    stop = threading.Event()

    def rx():
        while not stop.is_set():
            try:
                p = s.recv(4096)
            except socket.timeout:
                continue
            now = time.monotonic()
            if p[:4] == MAGIC and len(p) >= HDR.size:
                _, r, seq, t, n = HDR.unpack_from(p)
                if r == run and seq not in rtt:
                    rtt[seq] = now - t
                    back[seq] = n
            elif p[:4] == STAT and len(p) >= 12:
                r, n = struct.unpack_from("!II", p, 4)
                if r == run:
                    stat["count"] = n

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    pad = b"\0" * max(0, a.size - HDR.size)
    gap = a.size * 8 / (rate * 1000)
    t0 = time.monotonic()
    sent = {}
    seq = 0
    while True:
        due = t0 + seq * gap
        now = time.monotonic()
        if due - t0 >= a.secs:
            break
        if due > now:
            time.sleep(due - now)
        try:
            s.sendto(HDR.pack(MAGIC, run, seq, time.monotonic(), 0) + pad, dst)
        except OSError:
            pass                       # a full local queue counts as uplink loss
        sent[seq] = due - t0
        seq += 1
    time.sleep(a.drain)                # late echoes still count
    for _ in range(10):                # reflector's count for this run
        s.sendto(STAT + struct.pack("!I", run), dst)
        time.sleep(1)
        if "count" in stat:
            break
    stop.set()
    th.join()
    n, got = seq, len(rtt)
    refl = stat.get("count")
    v = sorted(rtt.values())
    order = sorted(rtt)
    seqs_by_arrival = sorted(rtt, key=lambda k: sent[k] + rtt[k])
    reordered = sum(1 for i in range(1, len(seqs_by_arrival)) if seqs_by_arrival[i] < seqs_by_arrival[i - 1])
    jit = [abs(rtt[order[i]] - rtt[order[i - 1]]) for i in range(1, len(order)) if order[i] == order[i - 1] + 1]
    per_s = []                         # per second of sending: share echoed back and median RTT
    for sec in range(int(a.secs)):
        ks = [k for k, t in sent.items() if sec <= t < sec + 1]
        r = sorted(rtt[k] for k in ks if k in rtt)
        per_s.append({"s": sec, "back_pct": round(100 * len(r) / max(1, len(ks))),
                      "rtt_ms": round(statistics.median(r) * 1000) if r else None})
    out = {"rate_kbps": rate, "size": a.size, "secs": a.secs, "sent": n, "reflected": refl, "back": got,
           "loss_pct": round(100 * (n - got) / max(1, n), 1),
           "up_loss_pct": round(100 * (n - refl) / max(1, n), 1) if refl is not None else None,
           "down_loss_pct": round(100 * (refl - got) / max(1, refl), 1) if refl else None,
           "up_kbps": round(refl * a.size * 8 / a.secs / 1000, 1) if refl is not None else None,
           "down_kbps": round(got * a.size * 8 / a.secs / 1000, 1),
           "rtt_ms": {"min": pct(v, 0), "p50": pct(v, .5), "p90": pct(v, .9), "p99": pct(v, .99),
                      "max": pct(v, 1)},
           "jitter_ms": round(statistics.mean(jit) * 1000, 1) if jit else None,
           "reordered": reordered, "per_second": per_s}
    s.close()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["reflect", "send"])
    ap.add_argument("--port", type=int, default=5230)
    ap.add_argument("--dst")
    ap.add_argument("--bind")
    ap.add_argument("--dev", help="bind to this interface (the UE's PDU tunnel)")
    ap.add_argument("--rate", default="10,20,30,40", help="kbit/s of UDP payload, comma-separated")
    ap.add_argument("--size", type=int, default=500, help="UDP payload bytes")
    ap.add_argument("--secs", type=float, default=20)
    ap.add_argument("--drain", type=float, default=8, help="s to wait for late echoes")
    ap.add_argument("--pause", type=float, default=5, help="s between rates")
    a = ap.parse_args()
    if a.what == "reflect":
        return reflect(a.port)
    for i, r in enumerate(int(x) for x in a.rate.split(",")):
        if i:
            time.sleep(a.pause)
        print(json.dumps(send_one(a, r)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
