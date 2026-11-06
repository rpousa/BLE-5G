"""Host side of the nrlink board protocol (SLIP framed over the MCU-Link VCOM). See nrlink.c on the board."""
import glob
import json
import socket
import os
import struct
import threading
import time
import queue

import serial

SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
MSG_TX, MSG_LED, MSG_CFG, MSG_PING, MSG_STATS = 0x01, 0x02, 0x03, 0x04, 0x05
MSG_TXDONE, MSG_RX, MSG_CFGOK, MSG_PONG, MSG_STATSR = 0x81, 0x82, 0x83, 0x84, 0x85
OFF, RED, GREEN, BLUE = 0, 1, 2, 3


def port_of(serial_no):
    hits = glob.glob(f"/dev/serial/by-id/*{serial_no}-if02")
    if not hits:
        raise FileNotFoundError(f"board {serial_no} not connected")
    return os.path.realpath(hits[0])


class Board:
    def __init__(self, serial_no, baud=1_000_000):
        self.serial_no = serial_no
        self.ser = serial.Serial(port_of(serial_no), baud, timeout=0.05)
        self.rx = queue.Queue()        # (rssi, ts, payload)
        self.events = queue.Queue()    # (type, body) for non-RX messages
        self._stop = False
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def close(self):
        self._stop = True
        self._t.join(timeout=1)
        self.ser.close()

    def _send(self, msg_type, body=b""):
        out = bytearray([SLIP_END])
        for b in bytes([msg_type]) + body:
            if b == SLIP_END:
                out += bytes([SLIP_ESC, SLIP_ESC_END])
            elif b == SLIP_ESC:
                out += bytes([SLIP_ESC, SLIP_ESC_ESC])
            else:
                out.append(b)
        out.append(SLIP_END)
        self.ser.write(out)

    def _reader(self):
        frame, in_frame, esc = bytearray(), False, False
        while not self._stop:
            data = self.ser.read(self.ser.in_waiting or 1)
            for b in data:
                if b == SLIP_END:
                    if in_frame and frame:
                        self._dispatch(bytes(frame))
                    frame, in_frame, esc = bytearray(), True, False
                elif not in_frame:
                    continue  # boot banner / text output
                elif esc:
                    frame.append(SLIP_END if b == SLIP_ESC_END else SLIP_ESC if b == SLIP_ESC_ESC else b)
                    esc = False
                elif b == SLIP_ESC:
                    esc = True
                else:
                    frame.append(b)

    def _dispatch(self, f):
        if f[0] == MSG_RX and len(f) >= 6:
            rssi = struct.unpack("b", f[1:2])[0]
            ts = struct.unpack("<I", f[2:6])[0]
            self.rx.put((rssi, ts, f[6:]))
        else:
            self.events.put((f[0], f[1:]))

    def _wait(self, msg_type, timeout=1.0):
        while True:
            t, body = self.events.get(timeout=timeout)
            if t == msg_type:
                return body

    def ping(self, timeout=1.0):
        self._send(MSG_PING)
        return self._wait(MSG_PONG, timeout).decode()

    def tx(self, payload, wait=True, timeout=0.5):
        assert len(payload) <= 255
        self._send(MSG_TX, bytes(payload))
        if wait:
            return self._wait(MSG_TXDONE, timeout)[0]

    def led_solid(self, color, ul=False, dur_ms=0):
        self._send(MSG_LED, bytes([0, color, 1 if ul else 0]) + struct.pack("<H", dur_ms))

    def led_blink(self, color, count):
        self._send(MSG_LED, bytes([1, color, count, 0, 0]))

    def configure(self, channel, power):
        self._send(MSG_CFG, bytes([channel, power]))
        self._wait(MSG_CFGOK)

    def stats(self):
        self._send(MSG_STATS)
        b = self._wait(MSG_STATSR)
        return struct.unpack("<%dI" % (len(b) // 4), b)


# ---- air framing shared by the bridges ----------------------------------------------------------
# Every radio packet: kind, ue_id, sfn (le16), slot, frag, nfrag, then a chunk of a slot message.
AIR_DL, AIR_POLL, AIR_UL = 0x01, 0x02, 0x03
AIR_HDR = struct.Struct("<BBHBBB")
AIR_CHUNK = 255 - AIR_HDR.size

# slot message (see nfapi/oai_integration/nrlink_proto.h)
MSG_HDR = struct.Struct("<BBHBBHH")  # magic, type, sfn, slot, flags, rnti, n_items
ITEM_HDR = struct.Struct("<BH")
NRLINK_MAGIC = 0x4E
NRLINK_DL_SLOT, NRLINK_UL_SLOT, NRLINK_UL_RESULT, NRLINK_HELLO = 1, 2, 3, 4
IT_SSB, IT_DCI, IT_TB, IT_ULGRANT, IT_PUSCH, IT_UCI, IT_PRACH = 0x01, 0x02, 0x03, 0x04, 0x11, 0x12, 0x13


def fragments(kind, ue_id, sfn, slot, data):
    chunks = [data[i:i + AIR_CHUNK] for i in range(0, len(data), AIR_CHUNK)] or [b""]
    return [AIR_HDR.pack(kind, ue_id, sfn, slot, i, len(chunks)) + c for i, c in enumerate(chunks)]


class Reassembler:
    """Collects the fragments of one (kind, ue_id, sfn, slot) message."""

    def __init__(self):
        self.parts = {}

    def add(self, pkt):
        if len(pkt) < AIR_HDR.size:
            return None
        kind, ue_id, sfn, slot, frag, nfrag = AIR_HDR.unpack_from(pkt)
        key = (kind, ue_id, sfn, slot)
        if nfrag == 1:
            return key, pkt[AIR_HDR.size:]
        if frag >= nfrag:
            return None
        n, got = self.parts.get(key, (nfrag, {}))
        if n != nfrag or frag in got:  # leftover of an older message with the same key (SFN wrap): start over
            got = {}
        got[frag] = pkt[AIR_HDR.size:]
        self.parts[key] = (nfrag, got)
        if len(self.parts) > 32:  # forget incomplete messages
            for k in list(self.parts)[:-16]:
                del self.parts[k]
        if len(got) == nfrag:
            del self.parts[key]
            return key, b"".join(got[i] for i in range(nfrag))
        return None


def items(msg):
    """Yields (type, body) of a slot message."""
    if len(msg) < MSG_HDR.size:
        return
    n = MSG_HDR.unpack_from(msg)[6]
    off = MSG_HDR.size
    for _ in range(n):
        if off + ITEM_HDR.size > len(msg):
            return
        t, ln = ITEM_HDR.unpack_from(msg, off)
        off += ITEM_HDR.size
        yield t, msg[off:off + ln]
        off += ln


# ---- telemetry for the dashboard server (fire and forget, JSON over UDP; localhost unless
# NRLINK_TELEMETRY=host:port points it at a dashboard on another PC)
_tele_env = os.environ.get("NRLINK_TELEMETRY", "").strip()
TELEMETRY_ADDR = ((_tele_env.rsplit(":", 1)[0], int(_tele_env.rsplit(":", 1)[1])) if ":" in _tele_env
                  else ("127.0.0.1", 8091))
_tele_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def telemetry(**kw):
    kw.setdefault("t", time.time())
    try:
        _tele_sock.sendto(json.dumps(kw, separators=(",", ":")).encode(), TELEMETRY_ADDR)
    except OSError:
        pass
