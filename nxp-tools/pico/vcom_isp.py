# vcom_isp.py <tty> [seconds]: send "isp" on the MCU-Link VCOM until the board answers "ISP" (hci_bb v10)
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=0.05); r = b''; t = time.time()
lim = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
while time.time() - t < lim and b'ISP' not in r:
    s.write(b'isp'); r += s.read(16)
print('reply:', r, 'after %.2f s' % (time.time() - t))
