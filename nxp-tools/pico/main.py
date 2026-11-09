# nrlink Pico HCI bridge v1: USB serial <-> UART0 with RTS/CTS, for an FRDM-MCXW72 running hci_bb v9.
# Wiring: GP0 TX -> J1-1 (PTA16), GP1 RX <- J1-2 (PTA17), GP2 CTS <- J1-5 (PTA19 RTS_b), GP3 RTS -> J2-9 (PTA18 CTS_b),
# GND -> J2-4. The host side needs no flow control: when the board holds CTS, the UART write blocks, this loop stops
# reading USB and USB itself paces the PC. Sending MAGIC drops to the MicroPython REPL (for updates).
import sys, select, time, micropython
from machine import UART, Pin
BAUD = 1000000
MAGIC = b'\xfe\xfe#nrlink-repl#\xfe\xfe'
time.sleep(2)                       # Ctrl-C still works during these 2 s
micropython.kbd_intr(-1)            # from now on 0x03 is data
u = UART(0, BAUD, tx=Pin(0), rx=Pin(1), cts=Pin(2), rts=Pin(3), flow=UART.RTS | UART.CTS,
         txbuf=2048, rxbuf=8192, timeout=0, timeout_char=0)
inp = sys.stdin.buffer
out = sys.stdout.buffer
pl = select.poll()
pl.register(sys.stdin, select.POLLIN)
buf = bytearray(256)
mv = memoryview(buf)
ML = len(MAGIC)

def run():
    m = 0
    while True:
        n = u.any()
        if n:
            out.write(u.read(n if n < 1024 else 1024))
        k = 0
        while k < 256 and pl.poll(0):
            c = inp.read(1)[0]
            buf[k] = c
            k += 1
            if c == MAGIC[m]:
                m += 1
                if m == ML:
                    return
            else:
                m = 1 if c == MAGIC[0] else 0
        if k:
            w = mv[:k]
            while len(w):
                s = u.write(w) or 0
                w = w[s:]

run()
micropython.kbd_intr(3)
print('nrlink bridge stopped')
