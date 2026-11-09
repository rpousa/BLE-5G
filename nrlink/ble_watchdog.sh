#!/bin/bash
# UE PC, run as root: keeps n3iwue<k> running over its Bluetooth link ble<k>.
#   sudo ./ble_watchdog.sh          (foreground; log in logs/ble_watchdog.log)
# Every 5 s, for k = 1, 2:
#   1. the BLE<k> board must answer HCI. The hci_bb boards sometimes stop answering the host (kernel: "command tx
#      timeout") while their radio link stays up; then nothing gets through. Fix: hardware reset (MCU-Link nRESET),
#      ble_attach.sh, restart n3iwue<k> and its link.
#   2. n3iwue<k> must be running with a PDU address. After a stall its IKE gives up and the container exits
#      (AutoReRegistration false). Fix: restart n3iwue<k> and its link (after a grace period).
# Log lines start with the epoch time, so stress.py can list the outages that fell inside a test.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
PY=/home/debian/nxp/venv/bin/python
W=logs/ble_watchdog.log
CORE_BT=A0:C5:89:8D:1C:B9
YAML=/home/debian/oai/deploy/nrlink/n3iwue-ue.yaml
WATCH=${WATCH:-1 2}  # boards to look after: WATCH=1 for a single-link test (BLE2 left alone)
GRACE=60          # s a fresh n3iwue gets to register before it counts as down
BOARD_GRACE=30    # s after a board reset before its HCI is checked again
DN=192.168.70.132 # ext-dn: pinged through each PDU tunnel every 30 s; 3 misses in a row = dead tunnel

say() { echo "$(date +%s) $(date '+%F %T') $*" | tee -a "$W"; }
sn_of() { "$PY" -c "import json; print(json.load(open('boards.json')).get('ble$1', ''))"; }
addr_of() { "$PY" -I /home/debian/nxp/hci_probe.py "$1" --addr; }
hci_of() {   # hciN with that address (hciconfig: btmgmt hangs without a terminal)
  local h; for h in /sys/class/bluetooth/hci*; do
    [ -e "$h" ] && timeout 5 hciconfig "${h##*/}" </dev/null 2>/dev/null | grep -qi "BD Address: $1" && { echo "${h##*/}"; return; }
  done
}
# alive <hciN> <address>: the board answers Read BD_ADDR with its own address. hci_bb v13 resets itself (hardware
# watchdog) when it hangs and then comes back with the default 88:88:88:88:88:88: that counts as down too.
alive() { local r; r=$(echo "$2" | tr ':' '\n' | tac | tr '\n' ' ')
  timeout 4 hcitool -i "$1" cmd 0x04 0x0009 2>/dev/null | tr -s ' \n' ' ' | grep -qi "01 09 10 00 ${r% }"; }
link_pid() { pgrep -f "ble_ip_link.py --connect .* --index $1 " | head -1; }
tunnel_ok() { docker exec "nrlink-n3iwue$1" ping -c1 -W4 -I gretun-id-2-1 "$DN" >/dev/null 2>&1; }
session() { docker exec "nrlink-n3iwue$1" ip -4 -o addr show gretun-id-2-1 2>/dev/null | grep -o '12\.1\.1\.[0-9]*'; }

restart_ue() {   # restart_ue k: n3iwue<k> container (config kept: it holds the SQN) and its Bluetooth link
  local k=$1 pid p
  p=$(link_pid "$k"); [ -n "$p" ] && kill "$p"
  docker rm -f "nrlink-n3iwue$k" >/dev/null 2>&1
  N3UE1_IF=ble1 N3UE2_IF=ble2 docker compose -f "$YAML" up -d --no-deps "n3iwue$k" >/dev/null 2>&1
  pid=$(docker inspect -f '{{.State.Pid}}' "nrlink-n3iwue$k" 2>/dev/null)
  [ "${pid:-0}" -gt 0 ] || { say "n3iwue$k: container did not start"; return 1; }
  setsid nohup "$PY" -u ble_ip_link.py --connect "$CORE_BT" --local "${ADDR[$k]}" --index "$k" \
    --netns "$pid" --route 192.168.70.139/32 >> "logs/ble$k.log" 2>&1 < /dev/null &
  STARTED[$k]=$(date +%s)
  say "n3iwue$k: restarted with its ble$k link"
}

reset_board() {   # reset_board k: hardware reset of BLE<k>, attach again, restart its n3iwue
  local k=$1 p f=/run/nrlink-ble/btattach-ble$1.pid
  p=$(link_pid "$k"); [ -n "$p" ] && kill "$p"
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"
  sleep 1
  "$PY" -I /home/debian/nxp/hwreset.py "${SN[$k]}" >/dev/null
  sleep 2
  ONLY=$k timeout 120 ./ble_attach.sh </dev/null >/dev/null 2>&1
  RESET[$k]=$(date +%s); FAILS[$k]=0
  say "BLE$k board ${SN[$k]}: reset and attached as $(hci_of "${ADDR[$k]}")"
  restart_ue "$k"
}

declare -A SN ADDR STARTED RESET FAILS PINGED MISSES
for k in 1 2; do SN[$k]=$(sn_of $k); ADDR[$k]=$(addr_of "${SN[$k]}"); STARTED[$k]=$(date +%s); RESET[$k]=0; FAILS[$k]=0; PINGED[$k]=0; MISSES[$k]=0; done
say "watchdog started (boards: $WATCH): BLE1 ${SN[1]} ${ADDR[1]}, BLE2 ${SN[2]} ${ADDR[2]}"
while true; do
  for k in $WATCH; do
    h=$(hci_of "${ADDR[$k]}")
    if [ $(( $(date +%s) - RESET[$k] )) -gt $BOARD_GRACE ] && { [ -z "$h" ] || ! alive "$h" "${ADDR[$k]}"; }; then
      FAILS[$k]=$(( FAILS[$k] + 1 ))      # two misses in a row (10 s): one command can be slow under load
      if [ "${FAILS[$k]}" -ge 2 ]; then
        say "BLE$k board ${SN[$k]}: ${h:-no adapter} does not answer HCI"
        reset_board "$k"
      fi
      continue
    fi
    FAILS[$k]=0
    if [ -z "$(session "$k")" ] && [ $(( $(date +%s) - STARTED[$k] )) -gt $GRACE ]; then
      say "n3iwue$k: no PDU session ($(docker inspect -f '{{.State.Status}}' "nrlink-n3iwue$k" 2>/dev/null || echo missing))"
      restart_ue "$k"; MISSES[$k]=0
    elif [ $(( $(date +%s) - PINGED[$k] )) -ge 30 ] && [ $(( $(date +%s) - STARTED[$k] )) -gt $GRACE ]; then
      PINGED[$k]=$(date +%s)
      # a session can outlive its tunnel (address kept, IPsec dead); a loaded link may drop one ping, not three
      if tunnel_ok "$k"; then MISSES[$k]=0; else MISSES[$k]=$(( MISSES[$k] + 1 )); fi
      if [ "${MISSES[$k]}" -ge 3 ]; then
        say "n3iwue$k: PDU session up but $DN unreachable through it 3 times in a row"
        restart_ue "$k"; MISSES[$k]=0
      fi
    fi
  done
  sleep 5
done
