# run a MicroPython snippet on a Pico via raw REPL: mpy_run.py <port> <file.py>
# The nrlink bridge (main.py) is stopped first with its escape sequence.
import serial, sys, time
MAGIC = b'\xfe\xfe#nrlink-repl#\xfe\xfe'
p = serial.Serial(sys.argv[1], 115200, timeout=0.5)
p.write(MAGIC); time.sleep(0.3)
p.write(b'\r\x03\x03'); time.sleep(0.3); p.reset_input_buffer()
p.write(b'\x01'); time.sleep(0.3); p.read(p.in_waiting or 1)
code = open(sys.argv[2], 'rb').read()
p.write(code + b'\x04')
out = b''; t = time.time()
while time.time() - t < 30:
    out += p.read(512)
    if out.count(b'\x04') >= 2: break
p.write(b'\x02'); p.close()
o = out.split(b'\x04')
print(o[0].lstrip(b'OK').decode(errors='replace'))
if len(o) > 1 and o[1].strip(): print('ERR:', o[1].decode(errors='replace'))
