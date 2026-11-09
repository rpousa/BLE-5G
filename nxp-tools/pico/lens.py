import serial, sys, time
s = serial.Serial(sys.argv[1], 1000000, timeout=0.6)
def cmd(op, p=b''):
    s.reset_input_buffer(); s.write(bytes([1, op & 0xFF, op >> 8, len(p)]) + p); time.sleep(0.6)
    return s.read(300).hex(' ')
print('reset          ', cmd(0x0C03))
print('le evmask  8 B ', cmd(0x2001, bytes(8)))
print('le rnd addr 6 B', cmd(0x2005, bytes([1, 2, 3, 4, 5, 0xC6])))
print('le advdata 32 B', cmd(0x2008, bytes([31]) + bytes(range(31))))
print('le advdata 32 B', cmd(0x2008, bytes([3, 2, 1, 6]) + bytes(28)))
print('name 248 B     ', cmd(0x0C13, b'nrlink'.ljust(248, b'\0'))[:60])
