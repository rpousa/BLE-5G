# diag.py <tty>: optional 36-byte command, then hci_bb v10 diag (0xFDB2): HCI UART STAT, MODIR, PCR19, WATER
import serial, sys, time, struct
s = serial.Serial(sys.argv[1], 1000000, timeout=0.5)
def rd(t=0.6):
    r = b''; t0 = time.time()
    while time.time() - t0 < t: r += s.read(512)
    return r
def diag(label):
    s.reset_input_buffer(); s.write(bytes([1, 0xB2, 0xFD, 0])); r = rd()
    i = r.find(bytes([4, 0xff]))
    if i < 0: print(label, 'no diag', r.hex()); return
    p = r[i + 5:]
    stat, modir, pcr, water, resyncs = struct.unpack_from('<IIIII', p, 0)
    print('%-22s STAT=%08x (OR=%d) MODIR=%08x (RXRTSE=%d TXCTSE=%d RTSWATER=%d) PCR19=%08x (MUX=%d) WATER=%08x RXCOUNT=%d resyncs=%d' % (
        label, stat, (stat >> 19) & 1, modir, (modir >> 3) & 1, modir & 1, (modir >> 8) & 7, pcr, (pcr >> 8) & 0xF, water, (water >> 24) & 0xF, resyncs))
diag('fresh')
s.write(bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))); print('advdata reply:', rd().hex() or 'none')
diag('after 36-byte command')
for i in range(3):
    s.write(bytes([1, 0x08, 0x20, 32, 31]) + bytes(range(31))); rd(0.3)
diag('after 3 more')
