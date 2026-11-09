#!/bin/bash
# BLE1 uplink load test (root, on the UE PC). Log: /home/debian/oai/nrlink/logs/ble1_load_<time>.log
cd /home/debian/oai/nrlink || exit 1
PY=/home/debian/nxp/venv/bin/python; NX=/home/debian/nxp; YAML=/home/debian/oai/deploy/nrlink/n3iwue-ue.yaml
P=/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e661640843676727-if00
SN=3VWLILYT2A1AK; ADDR=02:4E:52:65:B3:A1; CORE_BT=A0:C5:89:8D:1C:B9; DN=192.168.70.132
RATES=${RATES:-20 30 40 50 60}; SECS=${SECS:-60}
LOG=logs/ble1_load_$(date +%Y%m%d-%H%M%S).log
say() { echo "$(date '+%T') $*" | tee -a "$LOG"; }
hci_of() { for h in /sys/class/bluetooth/hci*; do hciconfig "${h##*/}" 2>/dev/null | grep -qi "BD Address: $ADDR" && { echo "${h##*/}"; return; }; done; }
stop_link1() { p=$(pgrep -f "ble_ip_link.py --connect .* --index 1 " | head -1); [ -n "$p" ] && kill "$p"
  f=/run/nrlink-ble/btattach-ble1.pid; [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; sleep 1; }
start_ue1() {
  docker rm -f nrlink-n3iwue1 >/dev/null 2>&1
  N3UE1_IF=ble1 N3UE2_IF=ble2 docker compose -f "$YAML" up -d --no-deps n3iwue1 >/dev/null 2>&1
  pid=$(docker inspect -f '{{.State.Pid}}' nrlink-n3iwue1)
  setsid nohup "$PY" -u ble_ip_link.py --connect "$CORE_BT" --local "$ADDR" --index 1 --netns "$pid" \
    --route 192.168.70.139/32 >> logs/ble1.log 2>&1 < /dev/null &
  for i in $(seq 60); do A=$(docker exec nrlink-n3iwue1 ip -4 -o addr show gretun-id-2-1 2>/dev/null | grep -o '12\.1\.1\.[0-9]*'); [ -n "$A" ] && break; sleep 3; done
}
attach1() { "$PY" -I "$NX/hwreset.py" "$SN" >/dev/null; sleep 3; ONLY=1 timeout 120 ./ble_attach.sh </dev/null >> "$LOG" 2>&1; }
watchdog() { for p in $(pgrep -f 'ble_watchdog\.sh'); do kill "$p"; done; sleep 1
  WATCH="$1" setsid nohup ./ble_watchdog.sh > /dev/null 2>&1 < /dev/null & }

say "== setup: watchdog stopped; bridge v2 on BLE1's Pico; BLE1 reset and attached; n3iwue1 restarted"
for p in $(pgrep -f 'ble_watchdog\.sh'); do kill "$p"; done
stop_link1
if [ -n "$IMAGE" ]; then
  say "== flashing $IMAGE onto BLE1"
  V=$(readlink -f /dev/serial/by-id/*${SN}*-if02)
  timeout 8 "$PY" -I "$NX/pico/vcom_isp.py" "$V" >> "$LOG" 2>&1 || { "$PY" -I "$NX/hwreset.py" "$SN" >/dev/null; sleep 0.05; timeout 8 "$PY" -I "$NX/pico/vcom_isp.py" "$V" >> "$LOG" 2>&1; }
  sleep 0.5; (cd "$NX" && HOME=/home/debian timeout 300 ./board.sh flash "$SN" "$IMAGE") 2>&1 | tail -3 | tee -a "$LOG"
  sleep 3
  say "== after flashing: one software reset, then a hardware reset; breadcrumbs of the boot before the hardware reset"
  "$PY" -I "$NX/hwreset.py" "$SN" >/dev/null; sleep 4
  timeout 20 "$PY" -I "$NX/pico/crumbs.py" "$P" "${SYMS:-$NX/images/hci_bb_isp_v13r.syms}" | tee -a "$LOG"
fi
"$PY" -I "$NX/pico/mpy_run.py" "$P" "$NX/pico/save_main.py" 2>&1 | grep -a saved | tee -a "$LOG"
"$PY" -I "$NX/pico/mpy_run.py" "$P" "$NX/pico/reset.py" >/dev/null 2>&1; sleep 4
attach1; start_ue1
[ -n "$A" ] || { say "no session at the first try (stale IKE?), restarting n3iwue1 once"; start_ue1; }
H=$(hci_of); say "BLE1 = $H, n3iwue1 = ${A:-NO SESSION}"
[ -n "$A" ] || { say "no session, giving up"; watchdog "1 2"; exit 1; }
say "baseline: $(docker exec nrlink-n3iwue1 ping -c10 -W3 -I gretun-id-2-1 $DN 2>&1 | tail -2 | tr '\n' ' ')"

HUNG=0
for R in $RATES; do
  say "== UDP uplink $R kbit/s for $SECS s (500 B datagrams)"
  timeout $((SECS + 60)) docker run --rm --network container:nrlink-n3iwue1 --entrypoint timeout nrlink-run:local \
    $((SECS + 50)) iperf3 -c $DN -p 5223 -B "$A" --bind-dev gretun-id-2-1 -u -b ${R}k -l 500 -t $SECS -J \
    > /tmp/ble1_iperf.json 2>&1 &
  IP=$!; fails=0; worst=0; n=0; slow=0
  while kill -0 $IP 2>/dev/null; do
    s=$(date +%s%N); out=$(timeout 3 hcitool -i "$H" cmd 0x04 0x0009 2>/dev/null); rc=$?
    ms=$(( ($(date +%s%N) - s) / 1000000 )); n=$((n + 1))
    if [ $rc -eq 0 ] && ! echo "$out" | tr -s ' \n' ' ' | grep -qi "01 09 10 00 A1 B3 65 52 4E 02"; then
      say "!! BLE1 answers with another address: it reset itself (watchdog)"; HUNG=3; break; fi
    if [ $rc -ne 0 ] && [ -z "$(hci_of)" ]; then say "!! adapter for BLE1 is gone (not a board hang)"; HUNG=2; break; fi
    if [ $rc -ne 0 ]; then fails=$((fails + 1)); say "  HCI read-version failed (rc $rc, $ms ms), $fails in a row"
    else fails=0; [ $ms -gt $worst ] && worst=$ms; [ $ms -gt 500 ] && slow=$((slow + 1)); fi
    [ $fails -ge 3 ] && { HUNG=1; break; }
    sleep 1
  done
  [ $HUNG = 2 ] && { kill $IP 2>/dev/null; break; }
  [ $HUNG = 3 ] && { say "!! at $R kbit/s"; kill $IP 2>/dev/null; break; }
  [ $HUNG = 1 ] && { say "!! BLE1 stopped answering HCI at $R kbit/s"; kill $IP 2>/dev/null; docker ps -q --filter ancestor=nrlink-run:local --filter network=container:nrlink-n3iwue1 | xargs -r docker kill >/dev/null 2>&1; break; }
  wait $IP
  res=$("$PY" -I -c "
import json,sys
try: j=json.load(open('/tmp/ble1_iperf.json'))
except Exception as e: print('iperf3 output unreadable'); sys.exit()
if 'error' in j: print('iperf3 error:', j['error']); sys.exit()
s=j['end'].get('sum_received') or j['end'].get('sum',{})
print('received %.1f kbit/s, loss %.1f %%, jitter %.1f ms' % (s.get('bits_per_second',0)/1000, s.get('lost_percent',0), s.get('jitter_ms',0)))")
  say "  $res · HCI checks: $n, worst $worst ms, $slow over 500 ms"
done

say "== Pico bridge counters"
stop_link1
[ $HUNG = 1 ] && { say "== hci_bb stall reports (6 s of raw H4 through the bridge)"; "$PY" -I "$NX/pico/diagread.py" "$P" 6 | tee -a "$LOG"; }
"$PY" -I "$NX/pico/stats.py" "$P" | tee -a "$LOG"
if [ $HUNG = 1 ]; then
  say "== board state while hung (bridge stopped, probing from the Pico)"
  dmesg -T | grep -iE "bluetooth: $H" | tail -4 | tee -a "$LOG"
  "$PY" -I "$NX/pico/mpy_run.py" "$P" "$NX/pico/hangprobe.py" 2>&1 | grep -aE "RTS|bytes|->" | tee -a "$LOG"
  V=$(readlink -f /dev/serial/by-id/*${SN}*-if02)
  say "VCOM isp probe (answers only if the main loop runs): $(timeout 6 "$PY" -I "$NX/pico/vcom_isp.py" "$V" 2>&1 | tail -1)"
  say "board did not reset itself: hardware reset"; "$PY" -I "$NX/hwreset.py" "$SN" >/dev/null; sleep 3
fi
if [ $HUNG = 1 ] || [ $HUNG = 3 ]; then
  "$PY" -I "$NX/pico/mpy_run.py" "$P" "$NX/pico/reset.py" >/dev/null 2>&1; sleep 4
  say "== breadcrumbs of the boot that hung"
  timeout 20 "$PY" -I "$NX/pico/crumbs.py" "$P" "${SYMS:-$NX/images/hci_bb_isp_v13r.syms}" | tee -a "$LOG"
fi
say "== restore: bridge restarted, BLE1 reset and attached, n3iwue1 restarted, watchdog on both"
"$PY" -I "$NX/pico/mpy_run.py" "$P" "$NX/pico/reset.py" >/dev/null 2>&1; sleep 4
attach1; start_ue1; say "n3iwue1 = ${A:-NO SESSION}"
watchdog "1 2"
say "done"
