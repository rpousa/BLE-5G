import serial, sys, time, struct
s = serial.Serial(sys.argv[1], 1000000, timeout=0.3)
def rd(t=0.6):
    r = b''; t0 = time.time()
    while time.time() - t0 < t: r += s.read(512)
    return r
def diag(label):
    s.reset_input_buffer(); s.write(bytes([1, 0xB2, 0xFD, 0])); r = rd(); i = r.find(bytes([4, 0xff]))
    if i < 0: print(label, 'no diag'); return
    now, last, ts, modir, resyncs = struct.unpack_from('<IIIII', r, i + 5)
    print('%-12s resyncs=%d  at resync: now=%u last=%u (gap %d)  timestamp now=%u' % (label, resyncs, now, last, (now - last) & 0xFFFFFFFF, ts))
diag('fresh'); time.sleep(0.2); diag('0.2 s later')
s.write(bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))); print('advdata reply:', rd().hex() or 'none')
diag('after long')
