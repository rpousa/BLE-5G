#!/bin/bash
# UE PC, run as root: turn the hci_bb boards (boards.json ble1, ble2) into Linux Bluetooth adapters.
#   sudo ./ble_attach.sh          per board: set its unique address (hci_probe.py --set-addr), btattach (H4, at the baud the
#                                 board answers: 115200 for hci_bb_isp_v2, -N: the MCU-Link VCOM has no RTS/CTS),
#                                 LE on, power on; prints "ble<k> <serial> hci<N> <address>"
#   sudo ./ble_attach.sh --off    detach them again (stops the btattach processes)
# The adapters live as long as btattach runs. A board reset loses its address: run this again.
# bluetoothd is masked (--runtime, until reboot or --off): its GATT client reads the core's encrypted attributes,
# the failed pairing that follows makes the kernel drop the LE link. The links only need the kernel (L2CAP, mgmt).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
PY=/home/debian/nxp/venv/bin/python
PROBE=/home/debian/nxp/hci_probe.py
RUN=/run/nrlink-ble; mkdir -p "$RUN"

if [ "${1:-}" = --off ]; then
  systemctl unmask --runtime bluetooth.service >/dev/null 2>&1; systemctl start bluetooth.service 2>/dev/null
  for f in "$RUN"/btattach-*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null && echo "detached $(basename "$f" .pid)"; rm -f "$f"; done
  exit 0
fi

systemctl mask --runtime bluetooth.service >/dev/null 2>&1; systemctl stop bluetooth.service 2>/dev/null

hci_of() {   # hci_of <address>: the hciN with that address, if any
  for h in /sys/class/bluetooth/hci*; do
    [ -e "$h" ] || continue
    n=${h##*/}; btmgmt --index "${n#hci}" info 2>/dev/null | grep -qi "addr $1" && { echo "$n"; return; }
  done
}

for k in 1 2; do
  sn=$("$PY" -c "import json; print(json.load(open('boards.json')).get('ble$k', ''))" 2>/dev/null)
  [ -n "$sn" ] || { echo "ble$k: no board in boards.json (run ./nrlink.sh boards)"; continue; }
  tty=$(readlink -f /dev/serial/by-id/*"$sn"*-if02 2>/dev/null) || { echo "ble$k: board $sn not connected"; continue; }
  addr=$("$PY" -I "$PROBE" "$sn" --addr)
  h=$(hci_of "$addr")
  if [ -n "$h" ]; then echo "ble$k $sn $h $addr (already attached)"; continue; fi
  "$PY" -I "$PROBE" "$sn" --set-addr >/dev/null || { echo "ble$k: $sn does not answer HCI (hci_bb firmware?)"; continue; }
  baud=$("$PY" -I "$PROBE" "$sn" --baud)
  before=$(ls /sys/class/bluetooth | grep '^hci' | sort)
  nohup btattach -N -B "$tty" -P h4 -S "$baud" > "$RUN/btattach-ble$k.log" 2>&1 &
  echo $! > "$RUN/btattach-ble$k.pid"
  for _ in $(seq 20); do
    sleep 0.5
    h=$(comm -13 <(echo "$before") <(ls /sys/class/bluetooth | grep '^hci' | sort) | head -1)
    [ -n "$h" ] && break
  done
  [ -n "$h" ] || { echo "ble$k: btattach did not create an adapter (see $RUN/btattach-ble$k.log)"; continue; }
  sleep 1
  btmgmt --index "${h#hci}" le on >/dev/null 2>&1
  btmgmt --index "${h#hci}" bredr off >/dev/null 2>&1
  btmgmt --index "${h#hci}" bondable off >/dev/null 2>&1
  btmgmt --index "${h#hci}" power on >/dev/null 2>&1
  got=$(btmgmt --index "${h#hci}" info 2>/dev/null | awk '/addr/ {print $2; exit}')
  echo "ble$k $sn $h ${got:-?}$([ "${got^^}" = "$addr" ] || echo "  (expected $addr)")"
done
