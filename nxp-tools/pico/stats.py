# stats.py <tty>: stop the bridge (MAGIC) and print the counters bridge v2 prints when it stops
import serial, sys, time
MAGIC = b'\xfe\xfe#nrlink-repl#\xfe\xfe'
p = serial.Serial(sys.argv[1], 115200, timeout=0.2)
p.reset_input_buffer(); p.write(MAGIC)
r = b''; t = time.time()
while time.time() - t < 2:
    r += p.read(4096)
    if b'stopped' in r and r.rstrip().endswith(b'}'): break
i = r.find(b'nrlink bridge stopped')
print(r[i:].decode(errors='replace').strip() if i >= 0 else 'bridge did not stop (no answer to MAGIC): Pico loop stuck')
