# crumbs.py <tty> [syms]: ask hci_bb v13 for the previous boot's breadcrumbs (vendor command 0xFDB3) through the
# Pico bridge (btattach stopped) and decode them. syms: `arm-none-eabi-nm -n -S` output of the same image.
import serial, sys, time, struct, bisect
SRS = {0: 'WAKEUP', 1: 'POR', 2: 'LVD', 3: 'HVD', 4: 'WARM', 5: 'FATAL', 8: 'PIN', 9: 'DAP', 10: 'RSTACK', 11: 'LPACK',
       12: 'SCG', 13: 'WDOG0', 14: 'SW', 15: 'LOCKUP', 25: 'WDOG1', 30: 'SECVIO'}
def srs(v): return '+'.join(n for b, n in SRS.items() if v >> b & 1) or '0x%x' % v
syms = []
if len(sys.argv) > 2:
    for l in open(sys.argv[2]):
        f = l.split()
        if len(f) >= 4: syms.append((int(f[0], 16), int(f[1], 16), f[3]))
addrs = [s[0] for s in syms]
def sym(a):
    a &= ~1
    i = bisect.bisect_right(addrs, a) - 1
    if i >= 0 and a < syms[i][0] + max(syms[i][1], 2): return '%s+0x%x' % (syms[i][2], a - syms[i][0])
    return '0x%08x' % a
s = serial.Serial(sys.argv[1], 1000000, timeout=0.2)
s.reset_input_buffer(); s.write(b'\x01\xb3\xfd\x00')
r = b''; t = time.time()
while time.time() - t < 1.5: r += s.read(4096)
i = r.find(b'\x04\xff')
while i >= 0 and not (i + 4 < len(r) and r[i + 3] == 0xF8): i = r.find(b'\x04\xff', i + 1)
if i < 0: print('no breadcrumb report (%d bytes back: %s)' % (len(r), r[:40].hex())); sys.exit(1)
p = r[i + 3:i + 3 + r[i + 2]]
cur_srs, hz = struct.unpack_from('<II', p, 2)
w = struct.unpack_from('<48I', p, 10)
print('this boot: reset by %s; watchdog clock measured %d Hz%s' % (srs(cur_srs), hz, '' if hz else ' (watchdog OFF)'))
if w[0] != 0x4E524331: print('previous boot: no breadcrumbs (magic %08x): power-up or first v13 boot' % w[0]); sys.exit(0)
print('previous boot #%d: reset by %s; ticks %d ms, main loops %d, tick at the last main loop %d -> loop stalled %d ms before the reset%s' % (
    w[1], srs(w[2]), w[3], w[4], w[5], w[3] - w[5], '' if w[3] else ' (tick never ran)'))
EXC = {2: 'NMI', 3: 'HardFault', 4: 'MemManage', 5: 'BusFault', 6: 'UsageFault', 7: 'SecureFault', 12: 'DebugMonitor', 14: 'PendSV', 15: 'SysTick'}
def exc(n): return 'thread (main loop)' if n == 0 else EXC.get(n, 'IRQ %d' % (n - 16))
if w[3]: print('last tick interrupted %s in %s' % (sym(w[11]), exc(w[12] & 0x1FF)))
if w[13]: print('FAULT %s at %s (lr %s), CFSR %08x HFSR %08x -> reset' % (exc(w[13] & 0x1FF), sym(w[14]), sym(w[15]), w[8], w[9]))
elif w[6] or w[8] or w[9]: print('HardFault: pc %s lr %s CFSR %08x HFSR %08x' % (sym(w[6]), sym(w[7]), w[8], w[9]))
n = w[10]
TAG = {1: 'MASK  ', 2: 'unmask', 3: 'restore-masked'}
print('last %d of %d interrupt mask changes, oldest first:' % (min(n, 16), n))
for k in range(max(0, n - 16), n):
    pc, lr = w[16 + 2 * (k % 16)], w[17 + 2 * (k % 16)]
    print('  %-14s at %-44s called from %s' % (TAG.get(pc & 3, '?%d' % (pc & 3)), sym(pc & ~3), sym(lr)))
if n and (w[16 + 2 * ((n - 1) % 16)] & 3) in (1, 3): print('=> interrupts were left MASKED by the last entry')
