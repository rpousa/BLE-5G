#!/usr/bin/env python3
"""repeater.py: set an nrlink v5 board up as a daisy-chain repeater (option B) and report its counters.

The repeater forwards whole air messages from RAM: polls (kind 0x04) heard on link L go out on link L+1, replies
(kind 0x05) heard on L+1 go out on L; the sl_bridge.py ends run as for a host relay (master --link 0, peer --link 1).

  repeater.py <serial> --channel 0x5A [--link 0] [--gap-us 1200]   configure and watch (Ctrl-C leaves it repeating)
  repeater.py <serial> --off
"""
import argparse
import time

import nrlink

AIR_SLM, AIR_SLP = 0x04, 0x05


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("serial")
    ap.add_argument("--channel", type=lambda x: int(x, 0), default=0x5A)
    ap.add_argument("--power", type=lambda x: int(x, 0), default=0x1F)
    ap.add_argument("--link", type=int, default=0, help="link id of the hop above the repeater")
    ap.add_argument("--gap-us", type=int, default=0,
                    help="gap before and between repeated packets, 200-3000 us in 200 us steps (0: firmware default, 800)")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--stats", type=float, default=5.0)
    a = ap.parse_args()
    b = nrlink.Board(a.serial)
    ver = b.ping()
    print(a.serial, ver, flush=True)
    if not ver.startswith("nrlink v") or int(ver.split("v")[1].split()[0]) < 5:
        raise SystemExit("needs nrlink v5 or later")
    b.configure(a.channel, a.power)
    if a.off:
        print("repeater off:", b.repeat([]))
        return
    n = b.repeat([(AIR_SLM, a.link, a.link + 1), (AIR_SLP, a.link + 1, a.link)], gap_us=a.gap_us)
    print(f"repeating on channel {a.channel:#x}: link {a.link} <-> {a.link + 1} ({n} rules)", flush=True)
    last = None
    while True:
        time.sleep(a.stats)
        st = b.stats()
        if last and len(st) >= 9:
            d = [x - y for x, y in zip(st, last)]
            print(time.strftime("%H:%M:%S"), f"rx {d[0]} (crc err {d[1]}), tx {d[2]}, repeated {d[6]} packets in "
                  f"{d[8]} messages, queue full {d[7]}", flush=True)
        last = st


if __name__ == "__main__":
    main()
