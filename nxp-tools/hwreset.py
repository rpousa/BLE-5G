# Pulse the target nRESET line through the MCU-Link (CMSIS-DAP SWJ_Pins), no SWD access needed.
import sys, time
from pyocd.probe.pydapaccess import DAPAccess
uid = sys.argv[1]
dev = next(d for d in DAPAccess.get_connected_devices() if d.get_unique_id() == uid)
dev.open()
try:
    dev.assert_reset(True); time.sleep(0.1); dev.assert_reset(False)
finally:
    dev.close()
print(uid, "reset pulsed")
