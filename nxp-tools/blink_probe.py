# Blink the MCU-Link status LEDs (CMSIS-DAP DAP_HostStatus) of one probe, to find the board physically.
import sys, time
from pyocd.probe.pydapaccess import DAPAccess
uid, secs = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 60
dev = next(d for d in DAPAccess.get_connected_devices() if d.get_unique_id() == uid)
dev.open()
end, on = time.time() + secs, True
try:
    while time.time() < end:
        dev._protocol.set_led(0, on)   # connect LED
        dev._protocol.set_led(1, on)   # running LED
        on = not on
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 0.25)
finally:
    dev._protocol.set_led(0, False); dev._protocol.set_led(1, False); dev.close()
print("done")
