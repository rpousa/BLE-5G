# pingpong.py <tty> [n]: n LE Set Advertising Data commands, each sent after the previous Command Complete
import serial, sys, time
n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
s = serial.Serial(sys.argv[1], 1000000, timeout=0.5)
s.reset_input_buffer(); s.write(bytes([1, 3, 0x0c, 0])); time.sleep(0.4); s.read(256)
ok = bad = lost = 0; buf = b''; t0 = time.time(); worst = 0
want = bytes([0x04, 0x0e, 0x04, 0x01, 0x08, 0x20, 0x00])
for i in range(n):
    payload = bytes((i + k) & 0xFF for k in range(31))
    t = time.time(); s.write(bytes([1, 0x08, 0x20, 32, 31]) + payload)
    while len(buf) < 7:
        c = s.read(7 - len(buf))
        if not c: break
        buf += c
    dt = time.time() - t; worst = max(worst, dt)
    if buf[:7] == want: ok += 1
    elif not buf: lost += 1
    else: bad += 1; s.read(256)
    buf = b''
el = time.time() - t0
print('%d commands: %d OK, %d wrong reply, %d no reply; %.1f ms avg round trip, worst %.1f ms' % (n, ok, bad, lost, el / n * 1000, worst * 1000))
