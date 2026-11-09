# pace.py <tty> <gap_ms> [n]: n LE Set Advertising Data commands with a gap after each; count good completes
import serial, sys, time
gap = float(sys.argv[2]) / 1000; n = int(sys.argv[3]) if len(sys.argv) > 3 else 300
s = serial.Serial(sys.argv[1], 1000000, timeout=0.2)
s.reset_input_buffer(); s.write(bytes([1, 3, 0x0c, 0])); time.sleep(0.4); s.read(256)
pkt = bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))
for i in range(n):
    s.write(pkt)
    if gap: time.sleep(gap)
r = b''; t = time.time()
while time.time() - t < 3:
    c = s.read(4096); r += c
    if not c and r: break
ok = r.count(bytes([0x04, 0x0e, 0x04, 0x01, 0x08, 0x20, 0x00])); tot = r.count(bytes([0x04, 0x0e]))
print('gap %4.1f ms: %d sent, %d OK, %d other' % (gap * 1000, n, ok, tot - ok))
