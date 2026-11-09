# burst.py <tty> [n]: n back-to-back LE Set Advertising Data commands (36 bytes each), count the Command Completes
import serial, sys, time
n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
s = serial.Serial(sys.argv[1], 1000000, timeout=0.2)
s.reset_input_buffer()
s.write(bytes([1, 0x03, 0x0c, 0])); time.sleep(0.3); s.read(64)          # HCI Reset
pkt = bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))                     # LE Set Advertising Data
t = time.time(); s.write(pkt * n); s.flush(); tw = time.time() - t
r = b''; t2 = time.time()
while time.time() - t2 < 3:
    c = s.read(4096); r += c
    if not c and r.count(bytes([0x04, 0x0e, 0x04, 0x01, 0x08, 0x20, 0x00])) >= n: break
ok = r.count(bytes([0x04, 0x0e, 0x04, 0x01, 0x08, 0x20, 0x00]))
bad = r.count(bytes([0x04, 0x0e])) - ok
print('sent %d x %d B = %d B in %.2f s (%.0f kbit/s); complete OK %d, other events %d' % (n, len(pkt), n * len(pkt), tw, n * len(pkt) * 8 / tw / 1000, ok, bad))
