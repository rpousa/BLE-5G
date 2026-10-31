"""Board roles for the nrlink setup."""
GNB_BOARD = "3TJ2B15JGASV3"
GNB_UDP_PORT = 5000                      # the PNF (nrlink_gnb.c) talks to this port

# ue_id: (board serial, UDP port for the nrUE emulator, traffic class)
UES = {
    1: ("2HOKMMVEX1WMP", 5001, "embb"),
    2: ("GBAMNH4BQ3CSB", 5002, "urllc"),
    3: ("3VWLILYT2A1AK", 5003, "mmtc"),
}

# LED identity of each traffic class: (RGB colour, number of blinks on the UE board)
CLASS_LED = {"embb": (3, 1), "urllc": (1, 2), "mmtc": (2, 3)}  # blue x1, red x2, green x3

RADIO_CHANNEL = 0x2A
RADIO_POWER = 0x1F

# boards.json (written by boards.py when nrlink.sh up finds the boards) overrides the serials above
_f = __import__("os").path.join(__import__("os").path.dirname(__file__), "boards.json")
if __import__("os").path.exists(_f):
    _b = __import__("json").load(open(_f))
    GNB_BOARD = _b.get("gnb", GNB_BOARD)
    UES = {u: (_b.get(f"ue{u}", sn), port, cls) for u, (sn, port, cls) in UES.items()}
