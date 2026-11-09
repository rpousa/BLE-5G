# burst2.py <tty> [n]: parse every H4 event after a burst; then the hci_bb diag report (0xFDB2) for the H4 resync count
import serial, sys, time, collections, struct
n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
s = serial.Serial(sys.argv[1], 1000000, timeout=0.2)
def events(raw):
    out, i = [], 0
    while i + 3 <= len(raw):
        if raw[i] != 4: out.append(('junk', raw[i])); i += 1; continue
        L = raw[i + 2]; out.append(raw[i + 1:i + 3 + L]); i += 3 + L
    return out
def drain(sec):
    r = b''; t = time.time()
    while time.time() - t < sec:
        c = s.read(4096); r += c
        if not c and r: break
    return r
s.reset_input_buffer(); s.write(bytes([1, 3, 0x0c, 0])); drain(0.5)
pkt = bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))
s.write(pkt * n); s.flush()
ev = events(drain(4))
h = collections.Counter()
for e in ev:
    if isinstance(e, tuple): h['junk byte'] += 1; continue
    if e[0] == 0x0e: h['complete op=%02x%02x status=%02x' % (e[4], e[3], e[5])] += 1
    elif e[0] == 0x0f: h['cmd status op=%02x%02x status=%02x' % (e[5], e[4], e[2])] += 1
    else: h['event %02x' % e[0]] += 1
for k, v in sorted(h.items()): print('  %5d  %s' % (v, k))
s.write(bytes([1, 0xB2, 0xFD, 0])); r = drain(1)
for e in events(r):
    if not isinstance(e, tuple) and e[0] == 0xff and len(e) > 2 and e[2] == 0xf7:
        p = e[3:]; kind = p[0]; resyncs = struct.unpack_from('<I', p, 17)[0]
        print('diag kind %d, H4 resyncs %d' % (kind, resyncs))
