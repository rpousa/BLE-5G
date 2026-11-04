#!/usr/bin/env python3
"""Talk raw HCI (H4) to a board running hci_bb, without BlueZ or root.

  hci_probe.py <serial>          HCI Reset, Read BD_ADDR, Read Local Version, LE Read Buffer Size
  hci_probe.py <serial> --isp    send the nrlink vendor command 0xFDB1: the board jumps to ROM ISP (blhost)
  hci_probe.py <serial> --set-addr   give the controller its nrlink address (02:4E:52 + 3 bytes from the serial;
                                     the firmware default 88:88:88:88:88:88 is the same on every board).
                                     NXP vendor command 0xFC22; kept across HCI Reset, lost on power-up/reset
  hci_probe.py <serial> --addr   just print the nrlink address of that board
  hci_probe.py <serial> --baud   print the HCI UART baud rate the board answers at (115200 for v2, 1000000 for v1)
"""
import struct
import sys
import time

import serial

BAUDS = (115200, 1_000_000)   # hci_bb_isp_v2 runs at 115200; v1 (1 Mbaud, loses bytes) still detected


def cmd(s, opcode, params=b"", timeout=2.0):
    s.write(struct.pack("<BHB", 0x01, opcode, len(params)) + params)
    t = time.time() + timeout
    buf = b""
    while time.time() < t:
        buf += s.read(64)
        while len(buf) >= 3:
            if buf[0] != 0x04:                      # not an event: drop a byte
                buf = buf[1:]
                continue
            code, ln = buf[1], buf[2]
            if len(buf) < 3 + ln:
                break
            ev, buf = buf[3:3 + ln], buf[3 + ln:]
            if code == 0x0E and struct.unpack_from("<H", ev, 1)[0] == opcode:   # command complete
                return ev[3:]
            if code == 0x0F and struct.unpack_from("<H", ev, 2)[0] == opcode:   # command status
                return bytes([ev[0]])
    return None


def reset(s, tries=3):
    """HCI Reset, resynchronising the board's H4 parser first if needed: stray bytes (e.g. an nrlink PING
    from boards.py) can leave it waiting for the rest of a packet; zero bytes complete or discard that."""
    for _ in range(tries):
        r = cmd(s, 0x0C03, timeout=0.6)
        if r is not None:
            return r
        s.write(bytes(300))
        time.sleep(0.2)
        s.reset_input_buffer()
    return None


def nrlink_addr(sn):
    """Deterministic, unique controller address for a board serial."""
    h = __import__("hashlib").sha1(sn.encode()).digest()
    return "02:4E:52:%02X:%02X:%02X" % (h[0], h[1], h[2])


def open_hci(sn):
    """(serial port, baud) of a board answering HCI Reset at one of BAUDS, else (None, None)."""
    port = __import__("glob").glob(f"/dev/serial/by-id/*{sn}*-if02")[0]
    for baud in BAUDS:
        s = serial.Serial(port, baud, timeout=0.05)
        s.reset_input_buffer()
        if reset(s, tries=4) == b"\x00":
            return s, baud
        s.close()
    return None, None


def main():
    sn = sys.argv[1]
    if "--addr" in sys.argv:
        print(nrlink_addr(sn))
        return 0
    s, baud = open_hci(sn)
    if s is None:
        print("no HCI answer at", "/".join(map(str, BAUDS)), "baud")
        return 1
    if "--baud" in sys.argv:
        print(baud)
        return 0
    if "--isp" in sys.argv:
        r = cmd(s, 0xFDB1)
        print("ISP command:", "ok, board in ROM ISP" if r == b"\x00" else f"no/bad reply {r!r}")
        return 0 if r == b"\x00" else 1
    if "--set-addr" in sys.argv:
        addr = nrlink_addr(sn)
        r = cmd(s, 0xFC22, bytes([0xFE, 6]) + bytes(int(x, 16) for x in reversed(addr.split(":"))))
        got = cmd(s, 0x1009)
        got = ":".join(f"{b:02X}" for b in reversed(got[1:7])) if got else None
        print(addr if got == addr else f"failed: set {r!r}, controller reports {got}")
        return 0 if got == addr else 1
    print(f"HCI Reset: ok ({baud} baud)")
    r = cmd(s, 0x1009)
    print("BD_ADDR:", ":".join(f"{b:02X}" for b in reversed(r[1:7])) if r else None)
    r = cmd(s, 0x1001)
    if r:
        st, hv, hrev, lv, manu, lsub = struct.unpack("<BBHBHH", r[:9])
        print(f"Version: HCI {hv} rev {hrev:#x}, LMP {lv}, manufacturer {manu:#06x}")
    r = cmd(s, 0x2002)
    if r:
        print("LE ACL buffers: %d x %d bytes" % (r[3], struct.unpack_from("<H", r, 1)[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
