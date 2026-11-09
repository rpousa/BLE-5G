# diagread.py <tty> [secs]: read raw H4 from the board (bridge running, btattach stopped) and decode hci_bb's
# vendor reports (event 0xFF, tag 0xF7): kind 2 HardFault, 3 main loop stalled, 5 RX not serviced, 7 active IRQs
import serial, sys, time, struct
s = serial.Serial(sys.argv[1], 1000000, timeout=0.2)
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6
r = b''; t = time.time()
while time.time() - t < secs: r += s.read(4096)
KIND = {1: 'assert', 2: 'HardFault', 3: 'main loop stalled', 4: 'on request', 5: 'HCI RX not serviced', 6: 'IRQs masked', 7: 'active IRQs'}
i = n = 0
while True:
    i = r.find(b'\x04\xff', i)
    if i < 0 or i + 3 > len(r): break
    L = r[i + 2]; p = r[i + 3:i + 3 + L]
    if len(p) >= 22 and p[0] == 0xF7:
        k = p[1]; a, b, c, d, rs = struct.unpack_from('<IIIII', p, 2)
        if k == 7:
            print('kind 7 active IRQs: IABR0=%08x IABR1=%08x IABR2=%08x ICSR=%08x (VECTACTIVE %d)' % (a, b, c, d, d & 0x1ff))
        else:
            print('kind %d %s: pc=%08x lr=%08x xpsr=%08x (IPSR %d) info=%08x resyncs=%d' % (k, KIND.get(k, '?'), a, b, c, c & 0x1ff, d, rs))
        n += 1
    i += 2
print('%d reports in %d bytes' % (n, len(r)))
