# hci_ping.py <tty>: HCI Reset, Read Local Version, Read BD_ADDR through a Pico bridge; prints the replies
import serial, sys, time
s = serial.Serial(sys.argv[1], 1000000, timeout=0.3, rtscts=False)
time.sleep(0.1); s.reset_input_buffer()
def cmd(ogf, ocf, params=b''):
    op = (ogf << 10) | ocf
    s.write(bytes([1, op & 0xFF, op >> 8, len(params)]) + params)
    t = time.time(); r = b''
    while time.time() - t < 1.5:
        r += s.read(64)
        if len(r) >= 3 and len(r) >= 3 + r[2]: break
    return r
r = cmd(3, 3);      print('reset  ', r.hex(' ') or 'no reply')
r = cmd(4, 1);      print('version', r.hex(' ') or 'no reply')
r = cmd(4, 9);      print('bdaddr ', r.hex(' ') or 'no reply', ' ->', ':'.join('%02X' % b for b in reversed(r[7:13])) if len(r) >= 13 else '')
