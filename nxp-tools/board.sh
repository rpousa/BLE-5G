#!/bin/bash
# Remote board management for the FRDM-MCXW72 boards (no SW3 needed once firmware supports the 'isp' command).
# Usage:
#   board.sh list                       show boards (probe serial -> tty)
#   board.sh isp    <serial>            ask running firmware (nrlink or hci_bb) to enter ROM ISP, then verify with blhost
#   board.sh backup <serial>            read full 2 MB flash to ~/nxp/backups/<serial>_<date>.bin (board must be in ISP)
#   board.sh flash  <serial> <file.bin> erase+write+verify image at 0x0, then reset into it (board must be in ISP)
#   board.sh reset  <serial>            reset from ISP into the application
set -e
BL=~/nxp/venv/bin/blhost
tty_of() { readlink -f /dev/serial/by-id/*"$1"-if02 2>/dev/null || { echo "board $1 not found" >&2; exit 1; }; }
blh() { $BL -p "$(tty_of "$1")",115200 -t 10000 "${@:2}"; }

isp_once() {
    t=$(tty_of "$1")
    # nrlink (460800 baud) needs a slow filler to leave SLIP frame mode before the text command
    "$HOME/nxp/venv/bin/python" -I -c "
import serial,time,sys
for baud in (460800,1000000,115200):
    s=serial.Serial(sys.argv[1],baud,timeout=0.1)
    for i in range(60): s.write(b'x'*10); time.sleep(0.003)
    time.sleep(0.05); s.reset_input_buffer(); s.write(b'\\r\\nisp\\r\\n'); time.sleep(0.3)
    hit=b'ISP' in s.read(4096); s.close()
    if hit: break  # the ROM is now autobauding: send it nothing else
" "$t"
    sleep 0.3
    blh "$1" get-property 1 | grep -q Success
}

case "$1" in
  list)
    for l in /dev/serial/by-id/*MCU-LINK*-if02; do s=${l##*V3.167_}; echo "${s%-if02} $(readlink -f "$l")"; done ;;
  isp)
    # hci_bb (Bluetooth controller) boards: vendor HCI command 0xFDB1, see hci_probe.py
    { "$HOME/nxp/venv/bin/python" -I "$HOME/nxp/hci_probe.py" "$2" --isp >/dev/null 2>&1 && sleep 0.3 && blh "$2" get-property 1 2>/dev/null | grep -q Success; } ||
    isp_once "$2" >/dev/null 2>&1 || { "$HOME/nxp/venv/bin/python" -I "$HOME/nxp/hwreset.py" "$2" >/dev/null; sleep 0.5; isp_once "$2" >/dev/null 2>&1; } \
      && echo "$2: in ISP" || { echo "$2: no ISP response" >&2; exit 1; } ;;
  hwreset)
    "$HOME/nxp/venv/bin/python" -I "$HOME/nxp/hwreset.py" "$2" ;;
  backup)
    mkdir -p ~/nxp/backups; f=~/nxp/backups/$2_$(date +%Y%m%d-%H%M%S).bin
    blh "$2" read-memory 0 0x200000 "$f" | tail -1; sha256sum "$f" ;;
  flash)
    n=$(stat -c %s "$3"); sec=$(( (n + 0x1FFF) / 0x2000 * 0x2000 ))
    blh "$2" flash-erase-region 0 $sec | tail -1
    blh "$2" write-memory 0 "$3" | tail -1
    v=$(mktemp); blh "$2" read-memory 0 "$n" "$v" > /dev/null; cmp "$3" "$v" && echo "verify OK"; rm -f "$v"
    blh "$2" reset | tail -1 ;;
  reset)
    blh "$2" reset | tail -1 ;;
  *) sed -n 2,9p "$0"; exit 1 ;;
esac
