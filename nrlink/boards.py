#!/usr/bin/env python3
"""Find the FRDM-MCXW72 boards on USB and give each nrlink role (gNB, UE1-3) a board.

  boards.py            probe every MCU-Link, assign roles, write boards.json, print the table
  boards.py --check    same, but exit 1 when a role has no board (used by nrlink.sh up)

A board counts when its firmware answers the nrlink PING. Boards keep the role they had in
boards.json (or the defaults in config.py) when they are present; a missing role goes to any other
nrlink board. Boards that are connected but not running nrlink are listed with how to flash them.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.expanduser("~/nxp/venv/bin/python")
try:
    import serial  # noqa: F401
except ImportError:  # started with the system python: pyserial lives in the project venv
    if os.path.exists(VENV_PY) and sys.prefix != os.path.dirname(os.path.dirname(VENV_PY)):
        os.execv(VENV_PY, [VENV_PY] + sys.argv)
    raise
sys.path.insert(0, HERE)
import config  # noqa: E402  (defaults, before boards.json overrides them)
from nrlink import Board  # noqa: E402

ROLES = ["gnb", "ue1", "ue2", "ue3"]
CLASS = {"gnb": "gNB", "ue1": "UE1 eMBB", "ue2": "UE2 URLLC", "ue3": "UE3 mMTC"}
FILE = os.path.join(HERE, "boards.json")


def connected():
    """Serial numbers of all MCU-Link probes with a VCOM port."""
    out = []
    for link in sorted(glob.glob("/dev/serial/by-id/*MCU-LINK*-if02")):
        name = os.path.basename(link)
        out.append(name.rsplit("_", 1)[1][: -len("-if02")])
    return out


def probe(sn):
    """Firmware version string if the board runs nrlink, else None."""
    b = None
    try:
        b = Board(sn)
        for _ in range(2):
            try:
                return b.ping(timeout=0.7)
            except Exception:  # noqa: BLE001  no PONG: try once more, then give up
                pass
        return None
    except Exception:  # noqa: BLE001  port busy or gone
        return None
    finally:
        if b:
            b.close()


def previous():
    if os.path.exists(FILE):
        with open(FILE) as f:
            return json.load(f)
    return {"gnb": config.GNB_BOARD, **{f"ue{u}": v[0] for u, v in config.UES.items()}}


def main():
    check = "--check" in sys.argv
    found = {sn: probe(sn) for sn in connected()}
    nrlink = [sn for sn, v in found.items() if v and v.startswith("nrlink")]
    prev = previous()
    roles, used = {}, set()
    for r in ROLES:  # keep known boards in their roles
        if prev.get(r) in nrlink:
            roles[r] = prev[r]
            used.add(prev[r])
    spare = [sn for sn in nrlink if sn not in used]
    for r in ROLES:  # fill the gaps with other nrlink boards
        if r not in roles and spare:
            roles[r] = spare.pop(0)
    missing = [r for r in ROLES if r not in roles]

    for r in ROLES:
        sn = roles.get(r)
        moved = sn and prev.get(r) != sn
        print(f"  {CLASS[r]:<10} {sn or '— no board —':<15} {found.get(sn, '') or ''}"
              f"{'   (new for this role)' if moved else ''}")
    for sn in spare:
        print(f"  {'spare':<10} {sn:<15} {found[sn]}")
    other = [sn for sn, v in found.items() if sn not in nrlink]
    for sn in other:
        print(f"  {'no nrlink':<10} {sn:<15} not running the nrlink firmware")

    if not missing:
        with open(FILE, "w") as f:
            json.dump(roles, f, indent=2)
        return 0
    print(f"\n  missing a board for: {', '.join(CLASS[r] for r in missing)}")
    if other:
        print("  To use " + ", ".join(other) + ": hold SW3 on the board, press and release RESET (or replug it),"
              " release SW3,\n  then run:  ./nrlink.sh flash " + other[0])
    else:
        print("  Connect another FRDM-MCXW72 board.")
    return 1 if check else 0


if __name__ == "__main__":
    sys.exit(main())
