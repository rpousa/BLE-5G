# nrlink Pico HCI bridge v2: USB serial <-> UART0, for an FRDM-MCXW72 running hci_bb v11.
# Wiring: GP0 TX -> J1-1 (PTA16), GP1 RX <- J1-2 (PTA17), GP2 CTS <- J1-5 (PTA19 RTS_b), GND -> J2-4.
# GP3 (RTS -> J2-9) is optional: v11 ignores its CTS input.
# When the board holds RTS, the UART write waits; this loop stops reading USB, so USB paces the PC.
# v2: while it waits, board -> PC data keeps flowing (v1 held back the board's events, including command replies),
# and it counts how long the board held it. Sending MAGIC drops to the REPL and prints those counters.
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
T0 = time.ticks_ms()
st = {'up': 0, 'down': 0, 'holds': 0, 'held_ms': 0, 'max_hold_ms': 0, 'holds_over_1s': 0}

def fwd():
    n = u.any()
    if n:
        d = u.read(n if n < 1024 else 1024)
        out.write(d)
        st['down'] += len(d)

def run():
    m = 0
    while True:
        fwd()
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
            st['up'] += k
            w = mv[:k]
            t = None
            while len(w):
                s = u.write(w) or 0
                if s:
                    w = w[s:]
                    continue
                if t is None:
                    t = time.ticks_ms()
                fwd()
            if t is not None:
                d = time.ticks_diff(time.ticks_ms(), t)
                st['holds'] += 1
                st['held_ms'] += d
                if d > st['max_hold_ms']:
                    st['max_hold_ms'] = d
                if d > 1000:
                    st['holds_over_1s'] += 1

run()
micropython.kbd_intr(3)
st['uptime_s'] = time.ticks_diff(time.ticks_ms(), T0) // 1000
print('nrlink bridge stopped', st)
