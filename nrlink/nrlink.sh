#!/bin/bash
# Boot or stop the whole nrlink system, in order, waiting for each part to be ready.
#
#   ./nrlink.sh up        5G core -> board bridges -> gNB VNF -> gNB PNF -> 3 nrUEs -> dashboard
#   ./nrlink.sh down      everything, in reverse order (core included)
#   ./nrlink.sh down --keep-core   stop RAN, bridges and dashboard, leave the 5G core running
#   ./nrlink.sh restart   down (keeping the core) then up
#   ./nrlink.sh status    what is running
#   ./nrlink.sh boards    find the boards and assign gNB/UE roles (boards.json); bridges must be stopped
#   ./nrlink.sh flash <serial>   back up, then write the nrlink firmware to a board in bootloader mode
#
# Logs: ~/oai/nrlink/logs/. Not touched: the oai-dev build container; board firmware only via "flash".
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
HERE=$PWD
CN=(-f ~/oai/oai-cn5g-fed-master/docker-compose/docker-compose-basic-nrf.yaml -f ~/oai/deploy/nrlink/core-static-ips.yaml)
RAN=~/oai/deploy/nrlink/docker-compose.yaml
OLD_RAN=~/oai/deploy/docker-compose-ran.yaml   # phase 1 rfsim RAN, same container names
PY=~/nxp/venv/bin/python
LOG=$HERE/logs
PID=$LOG/pids
IMAGE=~/nxp/images/nrlink_v4.bin
mkdir -p "$LOG" "$PID"

# docker directly if this shell may use it, otherwise through the docker group
if docker info >/dev/null 2>&1; then dk() { docker "$@"; }
else dk() { sg docker -c "docker $(printf '%q ' "$@")"; }; fi

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '\033[31m   FAILED: %s\033[0m\n   logs in %s; clean up with: %s down\n' "$*" "$LOG" "$0" >&2; exit 1; }
# wait_for <seconds> <description> <command...>: retry the command every 2 s
wait_for() {
  local t=$1 what=$2; shift 2
  for ((i = 0; i < t; i += 2)); do "$@" >/dev/null 2>&1 && { ok "$what"; return 0; }; sleep 2; done
  die "$what (gave up after ${t}s)"
}
ue_ip() { dk exec "oai-nrue$1" ip -4 -o addr show oaitun_ue1 2>/dev/null | awk '{print $4}' | cut -d/ -f1; }

start_proc() {   # start_proc <name> <command...>: background process with a pid file
  local name=$1; shift
  nohup "$@" > "$LOG/$name.log" 2>&1 &
  echo $! > "$PID/$name.pid"
}
stop_proc() {    # stop_proc <name>: TERM, then KILL after 5 s
  local f=$PID/$1.pid p
  [ -f "$f" ] || return 0
  p=$(cat "$f")
  if kill -0 "$p" 2>/dev/null; then
    kill "$p" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$p" 2>/dev/null || break; sleep 1; done
    kill -9 "$p" 2>/dev/null
    ok "stopped $1 (pid $p)"
  fi
  rm -f "$f"
}
alive() { [ -f "$PID/$1.pid" ] && kill -0 "$(cat "$PID/$1.pid")" 2>/dev/null; }

up() {
  step "1/7 checks"
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge dashboard; do alive $f && die "$f is already running, run: $0 down"; done
  dk info >/dev/null 2>&1 || die "docker is not usable from this account"
  ok "docker"
  "$PY" boards.py --check || die "not enough boards running nrlink (see above)"
  ok "boards found, roles saved in boards.json"

  step "2/7 5G core (OAI CN5G)"
  dk compose "${CN[@]}" up -d --wait >"$LOG/core.log" 2>&1 || die "core did not come up healthy (see core.log)"
  ok "AMF, SMF, UPF, NRF, UDM, UDR, AUSF, MySQL, ext-dn healthy"
  # IMSIs 037/038 get the SST 1 / DNN oai profile of 036 (the stock database gives them other slices)
  dk exec mysql mysql -uroot -plinux oai_db -e "
    CREATE TEMPORARY TABLE t AS SELECT singleNssai, dnnConfigurations FROM SessionManagementSubscriptionData WHERE ueid='208950000000036';
    UPDATE SessionManagementSubscriptionData s, t SET s.singleNssai=t.singleNssai, s.dnnConfigurations=t.dnnConfigurations
    WHERE s.ueid IN ('208950000000037','208950000000038');" >/dev/null 2>&1 || die "subscriber update failed"
  ok "subscribers 036-038 on SST 1 / DNN oai"

  step "3/7 board bridges (host)"
  dk compose -f "$OLD_RAN" down >/dev/null 2>&1
  dk compose -f "$RAN" down >/dev/null 2>&1
  start_proc gnb_bridge "$PY" -u gnb_bridge.py
  for u in 1 2 3; do start_proc "ue${u}_bridge" "$PY" -u ue_bridge.py "$u"; done
  wait_for 20 "gNB board answers" grep -q "listening on udp" "$LOG/gnb_bridge.log"
  for u in 1 2 3; do wait_for 20 "UE$u board answers" grep -q "bridge on udp" "$LOG/ue${u}_bridge.log"; done

  step "4/7 gNB VNF (MAC/RLC/PDCP/RRC)"
  dk compose -f "$RAN" up -d oai-vnf >/dev/null 2>&1 || die "VNF container did not start"
  wait_for 60 "VNF registered with the AMF (NG setup)" bash -c "$(declare -f dk); dk logs oai-vnf 2>&1 | grep -q 'Received NGSetupResponse'"

  step "5/7 gNB PNF (L1 emulation over the boards)"
  dk compose -f "$RAN" up -d oai-pnf >/dev/null 2>&1 || die "PNF container did not start"
  wait_for 60 "PNF running slots through the gNB board" bash -c "grep -E '^\[gnb\] +[1-9][0-9.]* slots/s' '$LOG/gnb_bridge.log'"
  wait_for 30 "VNF follows the PNF slot clock" bash -c "$(declare -f dk); ! dk logs --since 5s oai-vnf 2>&1 | grep -q 'time client: connection'"

  step "6/7 nrUEs (eMBB, URLLC, mMTC)"
  dk compose -f "$RAN" up -d oai-nrue1 oai-nrue2 oai-nrue3 >/dev/null 2>&1 || die "UE containers did not start"
  for u in 1 2 3; do
    wait_for 420 "UE$u PDU session" bash -c "$(declare -f dk ue_ip); [ -n \"\$(ue_ip $u)\" ]"
    printf '        oaitun_ue1 = %s\n' "$(ue_ip "$u")"
  done

  step "7/7 dashboard and demo apps"
  start_proc dashboard "$PY" -u dashboard_server.py
  wait_for 40 "dashboard answering on :8088" curl -sf http://127.0.0.1:8088/api/hello
  wait_for 40 "LiDAR, AGV and sensor traffic flowing" bash -c "timeout 5 curl -sN http://127.0.0.1:8088/events | grep -q '\"k\":\"scan\"'"

  printf '\n\033[1mUp.\033[0m Dashboard: http://%s:8088  (LAN)   http://100.64.0.74:8088  (Tailscale)\n' "$(hostname -I | awk '{print $1}')"
}

down() {
  step "dashboard and demo apps"
  stop_proc dashboard
  if [ -n "$(dk ps -aq -f name=^nrlink-dn-apps$ 2>/dev/null)" ]; then dk rm -f nrlink-dn-apps >/dev/null 2>&1 && ok "removed ext-dn app sidecar"; fi
  step "nrUEs, gNB PNF, gNB VNF"
  for c in oai-nrue1 oai-nrue2 oai-nrue3 oai-pnf oai-vnf; do dk stop -t 5 "$c" >/dev/null 2>&1 && ok "stopped $c"; done
  dk compose -f "$RAN" down >/dev/null 2>&1
  dk compose -f "$OLD_RAN" down >/dev/null 2>&1
  step "board bridges"
  for f in ue1_bridge ue2_bridge ue3_bridge gnb_bridge; do stop_proc $f; done
  # bridges started before pid files existed (older start scripts)
  for p in $(pgrep -f "python -u (gnb|ue)_bridge\.py"); do kill "$p" 2>/dev/null && ok "stopped stray bridge (pid $p)"; done
  if [ "${1:-}" = "--keep-core" ]; then
    step "5G core left running"
  else
    step "5G core"
    dk compose "${CN[@]}" down >/dev/null 2>&1 && ok "core stopped"
  fi
  printf '\n\033[1mDown.\033[0m\n'
}

status() {
  printf '%-14s %s\n' PROCESS STATE
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge dashboard; do
    alive $f && printf '%-14s running (pid %s)\n' $f "$(cat "$PID/$f.pid")" || printf '%-14s stopped\n' $f
  done
  printf '\n'
  dk ps --format '{{.Names}}\t{{.Status}}' | grep -E 'oai-|mysql|nrlink' | sort | column -t -s $'\t'
  printf '\n'
  for u in 1 2 3; do printf 'UE%s oaitun_ue1: %s\n' "$u" "$(ue_ip "$u")"; done
  [ -f "$LOG/gnb_bridge.log" ] && grep '^\[gnb\]' "$LOG/gnb_bridge.log" | tail -1
}

boards() {
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge; do alive $f && die "the bridges hold the serial ports, run: $0 down --keep-core"; done
  "$PY" boards.py
}

flash() {
  local sn=${1:-} tty
  [ -n "$sn" ] || die "usage: $0 flash <serial>   (serials: $0 boards)"
  tty=$(readlink -f /dev/serial/by-id/*"$sn"*-if02 2>/dev/null) || die "board $sn is not connected"
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge; do alive $f && die "the bridges hold the serial ports, run: $0 down --keep-core"; done
  [ -f "$IMAGE" ] || die "firmware image $IMAGE not found"
  step "flash $sn ($tty) with $(basename "$IMAGE")"
  # in the ROM bootloader already (SW3 held at reset)? otherwise ask nrlink firmware to jump there
  if ~/nxp/venv/bin/blhost -p "$tty",115200 -t 2000 get-property 1 2>/dev/null | grep -q Success; then ok "board is in the bootloader"
  elif "$PY" -c "import sys; sys.path.insert(0,'.'); import boards; sys.exit(0 if boards.probe('$sn') else 1)" \
       && ~/nxp/board.sh isp "$sn" >/dev/null 2>&1; then ok "nrlink firmware switched to the bootloader"
  else die "board $sn is not in the bootloader. Hold SW3, press and release RESET (or replug the USB cable), release SW3, and run this again"; fi
  ~/nxp/board.sh backup "$sn" | tail -1 && ok "old firmware saved in ~/nxp/backups/" || die "backup failed, nothing was written"
  # capture everything first: piping into grep -q would close the pipe and kill board.sh mid-flash
  local res; res=$(~/nxp/board.sh flash "$sn" "$IMAGE" 2>&1)
  grep -q "verify OK" <<<"$res" || { printf '%s\n' "$res" | sed 's/^/   /'; die "flashing failed"; }
  ok "nrlink written and verified"
  sleep 2
  "$PY" -c "import sys; sys.path.insert(0,'.'); from nrlink import Board; b=Board('$sn'); print('   ok: board answers:', b.ping())" || die "board does not answer after flashing"
  "$PY" boards.py
}

case "${1:-}" in
  up) up ;;
  boards) boards ;;
  flash) flash "${2:-}" ;;
  down) down "${2:-}" ;;
  restart) down --keep-core; up ;;
  status) status ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
