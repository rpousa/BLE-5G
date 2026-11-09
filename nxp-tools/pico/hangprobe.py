# run on the Pico after the bridge stopped: board RTS level, then HCI Read Version and diag 0xFDB2 without flow control
from machine import UART, Pin
import time
g2 = Pin(2, Pin.IN)
hi = n = 0; t = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t) < 1000:
    hi += g2.value(); n += 1
print('RTS (GP2) high in %d of %d samples over 1 s (high = board says wait)' % (hi, n))
u = UART(0, 1000000, tx=Pin(0), rx=Pin(1), rxbuf=4096, timeout=0)
time.sleep_ms(50); old = u.read()
print('bytes waiting from the board:', len(old) if old else 0)
for name, cmd in (('read version', b'\x01\x01\x10\x00'), ('diag 0xFDB2', b'\x01\xb2\xfd\x00')):
    u.write(cmd); time.sleep_ms(400); r = u.read()
    print(name, '->', r.hex() if r else 'NO REPLY')
