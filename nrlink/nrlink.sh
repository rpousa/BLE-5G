#!/bin/bash
# Boot or stop the whole nrlink system, in order, waiting for each part to be ready.
#
#   ./nrlink.sh up        free5GC core (+N3IWF) -> board bridges -> gNB VNF -> gNB PNF -> 3 nrUEs
#                         -> non-3GPP UE through the N3IWF -> dashboard
#   ./nrlink.sh up --core oai      same with the OAI core instead (3GPP access only, no N3IWF)
#   ./nrlink.sh down      everything, in reverse order (core included)
#   ./nrlink.sh down --keep-core   stop RAN, UEs, bridges and dashboard, leave the 5G core running
#   ./nrlink.sh restart [--core oai|free5gc]   down (keeping the core) then up
#   ./nrlink.sh status    what is running
#   ./nrlink.sh boards    find the boards and assign this PC's roles (boards.json); bridges must be stopped.
#                         Without --role it uses the role of this PC's IP (UE_PC -> ue, CORE_PC -> core, else all)
#   ./nrlink.sh flash <serial>   back up, then write the nrlink firmware to a board in bootloader mode
#   ./nrlink.sh live start|stop|status   the live experiment: core PC serves http://<core>:8090/ and /arch
#                         (architecture); the UE PC runs its other half. Role from this PC's IP. Run it on both PCs.
#                         "up" starts it and "down" stops it.
#   ./nrlink.sh live enable|disable      start it at every boot (user crontab @reboot); it waits for the stack
#     --scenario factory|agv   which experiment (saved in logs/live_scenario with the options below, so the next
#                              start and the boot start use it again; default factory)
#       factory  the factory floor, closed loop (5 Nov - 10 Nov 2026): 4 AGVs, pole LiDAR on nrUE1, AGV reports and
#                commands on nrUE2, camera video to two operators over Bluetooth (n3iwue1/2); trials L1-L3
#       agv      one AGV, the same data on NR and Bluetooth at once (11 Nov 2026): its LiDAR on nrUE1 + n3iwue1,
#                camera detections and pose on nrUE2 + n3iwue2, checkpoint syncs back; simulated radio channel
#     --level L1|L2|L3          starting load (both scenarios)
#     --mode both|nr|ble        (agv) which technologies carry the data
#     --tx-dbm 0|-10|-20|-30    (agv) simulated transmit power for the channel model
#     --fading-drop             (agv) simulated outages also drop messages
#
#   --role all|core|ue   (up, down, restart, status) which PC of the split setup this is; default all
#     all   everything on this PC (the original single-PC setup)
#     core  core PC: free5GC + N3IWF, gNB board bridge, VNF, PNF, reference docker n3iwue, Bluetooth
#           access point for the non-3GPP UEs; no nrUEs, no dashboard; gNB telemetry goes to the UE PC
#           dashboard (NRLINK_TELEMETRY, default the UE PC 192.168.10.232:8091)
#     ue    UE PC: UE1 eMBB + UE2 URLLC boards and nrUEs (to the core PC gNB over the air), non-3GPP UEs
#           n3iwue1/2 (IMSI 041/042) to the core PC N3IWF
#   --transport lan|ble  (--role ue) how n3iwue1/2 reach the N3IWF: the interim LAN path (default; sudo ue_routing.sh
#                        here, core_routing.sh --lan there) or each over its own Bluetooth LE link (sudo ble_attach.sh first)
#
# Logs: ~/oai/nrlink/logs/. Not touched: the oai-dev build container; board firmware only via "flash".
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
HERE=$PWD
CN=(-f ~/oai/oai-cn5g-fed-master/docker-compose/docker-compose-basic-nrf.yaml -f ~/oai/deploy/nrlink/core-static-ips.yaml)
F5=~/oai/deploy/free5gc/docker-compose.yaml   # free5GC core + N3IWF + n3iwue, same network and IPs
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

CORE=free5gc
ROLE=all
CORE_PC=192.168.10.223   # claudepc: free5GC + N3IWF, gNB board, Bluetooth access point (hci0)
UE_PC=192.168.10.232     # claudepc2: nrUE boards, Bluetooth (hci_bb) boards, n3iwues
BLE_LINK=$HERE/ble_ip_link.py   # Bluetooth LE L2CAP <-> tun link for the non-3GPP UEs (from the UE PC session)
S=0; T=8
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

nstep() { step "$((++S))/$T $*"; }

# ---- live experiment: factory (apps/factory_live2.py core, apps/factory2.py agent UE) or agv (apps/agv_live.py
# core, apps/agv.py ue UE)
LIVE_SIDECARS_CORE="fdl2-edge fdl-live-tx agv-edge"
LIVE_SIDECARS_UE="fdl2-robots fdl2-lidar fdl-live-rx-cam1 fdl-live-rx-cam2 fdl-live-rx-lidar fdl-live-rx-robots agv-fwd-nr1 agv-fwd-ble1 agv-fwd-nr2 agv-fwd-ble2"
live_role() { [ "$ROLE_SET" = 1 ] && echo "$ROLE" || host_role; }
live_scenario() {   # sets SCN and SCN_OPTS: from the command line (and saves them), else from logs/live_scenario
  if [ -n "$SCENARIO" ]; then
    SCN=$SCENARIO; SCN_OPTS=$LIVE_OPTS
    mkdir -p "$LOG"; printf '%s %s\n' "$SCN" "$SCN_OPTS" > "$LOG/live_scenario"
  elif [ -s "$LOG/live_scenario" ]; then
    read -r SCN SCN_OPTS < "$LOG/live_scenario"
    [ -n "$LIVE_OPTS" ] && SCN_OPTS=$LIVE_OPTS
  else
    SCN=factory; SCN_OPTS=$LIVE_OPTS
  fi
  case "$SCN" in factory | agv) ;; *) die "unknown scenario '$SCN' (factory or agv)" ;; esac
}
live_opt() { local o=$1; set -- $SCN_OPTS; while [ $# -gt 0 ]; do [ "$1" = "$o" ] && { echo "${2:-}"; return; }; shift; done; }
live_start() {
  local r lv; r=$(live_role); live_scenario
  lv=$(live_opt --level); lv=${lv:-L1}
  step "live experiment: $SCN ($r PC)"
  case "$r" in
    core)
      if alive live_server; then ok "live server already running (pid $(cat "$PID/live_server.pid")); live restart to change the scenario"; return 0; fi
      pkill -f "[f]actory_live2\.py" 2>/dev/null; pkill -f "[f]actory_live\.py" 2>/dev/null; pkill -f "[c]amsrc --truth" 2>/dev/null
      pkill -f "[a]gv_live\.py" 2>/dev/null
      if [ "$SCN" = agv ]; then
        local a=(--http 8090 --level "$lv") v
        v=$(live_opt --mode); [ -n "$v" ] && a+=(--mode "$v")
        v=$(live_opt --tx-dbm); [ -n "$v" ] && a+=(--tx-dbm "$v")
        [[ " $SCN_OPTS " == *" --fading-drop "* ]] && a+=(--fading-drop)
        mkdir -p "$LOG/agv"
        start_proc live_server python3 -u apps/agv_live.py "${a[@]}"
      else
        mkdir -p "$LOG/factory/live2_core"
        start_proc live_server python3 -u apps/factory_live2.py --http 8090 --level "$lv"
      fi
      wait_for 20 "live pages on :8090" curl -sf -o /dev/null http://127.0.0.1:8090/arch
      ok "http://$CORE_PC:8090/  ($SCN)   http://$CORE_PC:8090/arch  (architecture)" ;;
    ue)
      if alive live_agent; then ok "live agent already running (pid $(cat "$PID/live_agent.pid")); live restart to change the scenario"; return 0; fi
      pkill -f "[f]actory2\.py agent" 2>/dev/null; pkill -f "[f]actory_dl\.py agent" 2>/dev/null; pkill -f "[a]gv\.py ue" 2>/dev/null
      if [ "$SCN" = agv ]; then
        start_proc live_agent python3 -u apps/agv.py ue --core "$CORE_PC" --level "$lv"
      else
        mkdir -p "$LOG/factory/live2"
        start_proc live_agent python3 -u apps/factory2.py agent --core "$CORE_PC"
      fi
      sleep 2; alive live_agent && ok "$SCN: UE side feeding the core PC $CORE_PC:5300" || die "live agent exited (log $LOG/live_agent.log)" ;;
    *) printf '   skipped: the live pages need the two-PC split (core and UE PC)\n' ;;
  esac
}
live_stop() {
  local r c; r=$(live_role)
  step "live pages ($r PC)"
  case "$r" in
    core) stop_proc live_server; pkill -f "[f]actory_live2\.py" 2>/dev/null; pkill -f "[c]amsrc --truth" 2>/dev/null
          pkill -f "[a]gv_live\.py" 2>/dev/null
          for c in $LIVE_SIDECARS_CORE; do dk rm -f "$c" >/dev/null 2>&1; done ;;
    ue)   stop_proc live_agent; pkill -f "[f]actory2\.py agent" 2>/dev/null; pkill -f "[a]gv\.py ue" 2>/dev/null
          for c in $LIVE_SIDECARS_UE; do dk rm -f "$c" >/dev/null 2>&1; done ;;
  esac
  ok "stopped"
}
live_status() {
  local r; r=$(live_role); live_scenario
  printf 'scenario      %s %s\n' "$SCN" "$SCN_OPTS"
  case "$r" in
    core) alive live_server && printf 'live server   running (pid %s)  http://%s:8090/  /arch\n' "$(cat "$PID/live_server.pid")" "$CORE_PC" \
            || printf 'live server   stopped\n'; c=$LIVE_SIDECARS_CORE ;;
    ue)   alive live_agent && printf 'live agent    running (pid %s) -> %s\n' "$(cat "$PID/live_agent.pid")" "$CORE_PC" || printf 'live agent    stopped\n'
          c=$LIVE_SIDECARS_UE ;;
    *) printf 'live pages need the two-PC split\n'; return ;;
  esac
  for n in $c; do st=$(dk inspect -f '{{.State.Status}}' "$n" 2>/dev/null) && printf '  %-20s %s\n' "$n" "$st"; done
  crontab -l 2>/dev/null | grep -q "nrlink.sh live start" && printf 'at boot       enabled (crontab @reboot)\n' || printf 'at boot       disabled\n'
}
live_boot() {   # live_boot enable|disable
  local line="@reboot sleep 45 && $HERE/nrlink.sh live start >> $LOG/live_boot.log 2>&1"
  { crontab -l 2>/dev/null | grep -v "nrlink.sh live start"; [ "$1" = enable ] && echo "$line"; } | crontab -
  [ "$1" = enable ] && ok "live pages start at boot: $line" || ok "live pages no longer start at boot"
}
live() {
  case "$ARG" in
    start) live_start ;; stop) live_stop ;; status) live_status ;; restart) live_stop; live_start ;;
    enable | disable) live_boot "$ARG" ;;
    *) die "usage: $0 live start|stop|restart|status|enable|disable" ;;
  esac
}

# RAN configs for the free5GC core, generated from the OAI-core ones in ~/oai/deploy/nrlink:
# the gNB also advertises slice 1/010203 and the nrUEs ask for it with DNN internet
ran_conf_free5gc() {
  local D=~/oai/deploy/nrlink G=~/oai/deploy/nrlink/free5gc u
  mkdir -p "$G"
  cp "$D/pnf.conf" "$G/"
  sed 's/snssaiList = ({ sst = 1 }/snssaiList = ({ sst = 1; sd = 0x010203 }, { sst = 1 }/' "$D/vnf.conf" > "$G/vnf.conf"
  grep -q 'sd = 0x010203' "$G/vnf.conf" || die "could not add slice 1/010203 to vnf.conf"
  for u in 1 2 3; do
    sed 's/pdu_sessions = ({ dnn = "oai"; nssai_sst = 1; });/pdu_sessions = ({ dnn = "internet"; nssai_sst = 1; nssai_sd = 0x010203; });/' "$D/ue$u.conf" > "$G/ue$u.conf"
    grep -q 'nssai_sd = 0x010203' "$G/ue$u.conf" || die "could not set the free5GC slice in ue$u.conf"
  done
}

core_oai() {
  nstep "5G core (OAI CN5G)"
  dk compose -f "$F5" down >/dev/null 2>&1   # one core at a time: both own demo-oai-public-net
  dk compose "${CN[@]}" up -d --wait >"$LOG/core.log" 2>&1 || die "core did not come up healthy (see core.log)"
  ok "AMF, SMF, UPF, NRF, UDM, UDR, AUSF, MySQL, ext-dn healthy"
  # IMSIs 037/038 get the SST 1 / DNN oai profile of 036 (the stock database gives them other slices)
  dk exec mysql mysql -uroot -plinux oai_db -e "
    CREATE TEMPORARY TABLE t AS SELECT singleNssai, dnnConfigurations FROM SessionManagementSubscriptionData WHERE ueid='208950000000036';
    UPDATE SessionManagementSubscriptionData s, t SET s.singleNssai=t.singleNssai, s.dnnConfigurations=t.dnnConfigurations
    WHERE s.ueid IN ('208950000000037','208950000000038');" >/dev/null 2>&1 || die "subscriber update failed"
  ok "subscribers 036-038 on SST 1 / DNN oai"
  export NRLINK_CONF=./
}

core_free5gc() {
  nstep "5G core (free5GC + N3IWF)"
  grep -q '^gtp5g ' /proc/modules || die "the gtp5g kernel module (free5GC UPF) is not loaded: run  sudo ~/free5gc/install_modules.sh"
  case "$(cat /sys/module/gtp5g/version 2>/dev/null)" in
    0.9.[3-9]* | 0.10.[0-2]) ;;   # what free5GC v4.3.0's UPF accepts
    *) die "gtp5g $(cat /sys/module/gtp5g/version) is loaded, the free5GC v4.3.0 UPF needs 0.9.3 to 0.10.2: run  sudo sh ~/free5gc/install_modules.sh" ;;
  esac
  dk compose "${CN[@]}" down >/dev/null 2>&1   # one core at a time: both own demo-oai-public-net
  dk compose -f "$F5" up -d db nrf amf ausf nssf pcf smf udm udr chf nef webui upf oai-ext-dn >"$LOG/core.log" 2>&1 \
    || die "free5GC containers did not start (see core.log)"
  wait_for 60 "webconsole up, subscribers 036-038 (nrUEs), 040 (docker n3iwue) and 041-042 (UE PC n3iwues) provisioned" "$PY" f5gc_subscribers.py
  wait_for 60 "SMF associated with the UPF (PFCP)" bash -c "$(declare -f dk); dk logs f5gc-smf 2>&1 | grep -qi 'association.*\(success\|accept\)'"
  wait_for 60 "ext-dn routes 12.1.1.0/24 to the UPF" bash -c "$(declare -f dk); [ \"\$(dk inspect -f '{{.State.Health.Status}}' oai-ext-dn)\" = healthy ]"
  dk compose -f "$F5" up -d n3iwf >>"$LOG/core.log" 2>&1 || die "N3IWF did not start"
  wait_for 60 "N3IWF registered with the AMF (NG setup)" bash -c "$(declare -f dk); dk logs f5gc-n3iwf 2>&1 | grep -qi 'NG *Setup *Response'"
  ran_conf_free5gc
  ok "RAN configs for slice 1/010203, DNN internet in ~/oai/deploy/nrlink/free5gc"
  export NRLINK_CONF=./free5gc
}

n3ue_ip() { dk exec f5gc-n3iwue ip -4 -o addr 2>/dev/null | awk '$4 ~ /^12\.1\.1\./ {print $2, $4}' | head -1; }

# Bluetooth access point for the non-3GPP UEs (core role): routing from ble+ to the N3IWF, then the
# L2CAP listener. Root parts are in core_routing.sh (run once with sudo); here only checks and start.
ble_ap() {
  nstep "Bluetooth access point for the non-3GPP UEs"
  if [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ]; then ok "IPv4 forwarding on"
  else printf '   warning: IPv4 forwarding is off, run:  sudo %s/core_routing.sh\n' "$HERE"; fi
  local hci; hci=$(ls /sys/class/bluetooth 2>/dev/null | grep -m1 '^hci')
  if [ -z "$hci" ]; then
    printf '   skipped: no Bluetooth adapter (plug in a dongle, or btattach an hci_bb board)\n'; return 0
  fi
  if [ ! -f "$BLE_LINK" ]; then
    printf '   skipped: %s not here yet (the UE PC session provides it)\n' "$BLE_LINK"; return 0
  fi
  if ! sudo -n true 2>/dev/null; then
    printf '   skipped: the listener needs root. Run:\n     sudo %s -u %s --listen --hci %s   (it turns on connectable LE advertising itself)\n' \
      "$PY" "$BLE_LINK" "${hci#hci}"; return 0
  fi
  nohup sudo -n "$PY" -u "$BLE_LINK" --listen --hci "${hci#hci}" > "$LOG/ble_link.log" 2>&1 &
  echo $! > "$PID/ble_link.pid"
  wait_for 10 "L2CAP listener on PSM 0x0081 ($hci)" bash -c "kill -0 $! && ! grep -qi 'error\|traceback' '$LOG/ble_link.log'"
}

# ---- UE PC role: nrUE1/2 over the boards to the core PC's gNB, n3iwue1/2 to the core PC's N3IWF
N3NET=nrlink-n3ue-net
N3UE_YAML=~/oai/deploy/nrlink/n3iwue-ue.yaml
N3IWF_IP=192.168.70.139
TRANSPORT=lan
CORE_BT=A0:C5:89:8D:1C:B9   # core PC access point adapter (hci0, public address), ble_ip_link.py --listen
# sudo for the Bluetooth links (tun, netns): asks for the password on a terminal, else needs cached/NOPASSWD sudo
if [ -t 0 ]; then SUDO_AUTH="sudo -v"; else SUDO_AUTH="sudo -n true"; fi

n3ue_conf() {   # n3ue_conf <n>: config for n3iwue<n>, IMSI 20895000000004<n>; IKE from 192.168.72.1<n> on eth0
  mkdir -p "$LOG/n3ue"   # (lan) or from 10.70.0.1<n> on ble<n> (ble)
  local ifn=eth0 ifa=192.168.72.1$1
  [ "$TRANSPORT" = ble ] && ifn=ble$1 ifa=10.70.0.1$1
  sed -e "s/MSIN: 0000000040/MSIN: 000000004$1/" \
      -e "s/IPSecIfaceName: eth0/IPSecIfaceName: $ifn/" \
      -e "s/IPSecIfaceAddr: 192.168.70.160/IPSecIfaceAddr: $ifa/" \
      -e "s/IkeSaSPI: 0x000000000006f708/IkeSaSPI: 0x000000000006f74$1/" \
      -e "s/IPSecSA3gppControlPlaneSPI: 0x00000002/IPSecSA3gppControlPlaneSPI: 0x0000004$1/" \
      ~/oai/deploy/free5gc/config/n3uecfg.yaml > "$LOG/n3ue/n3ue$1.yaml"
  grep -q "MSIN: 000000004$1" "$LOG/n3ue/n3ue$1.yaml" && grep -q "IkeSaSPI: 0x000000000006f74$1" "$LOG/n3ue/n3ue$1.yaml" \
    && grep -q "IPSecIfaceName: $ifn " "$LOG/n3ue/n3ue$1.yaml" && grep -q "IPSecIfaceAddr: $ifa " "$LOG/n3ue/n3ue$1.yaml" \
    || die "could not generate the n3iwue$1 config"
}
n3ue_ip_n() { dk exec "nrlink-n3iwue$1" ip -4 -o addr 2>/dev/null | awk '$4 ~ /^12\.1\.1\./ {print $2, $4}' | head -1; }

ble_sn() { "$PY" -c "import json; print(json.load(open('boards.json')).get('ble$1', ''))" 2>/dev/null; }
ble_addr() { "$PY" -I ~/nxp/hci_probe.py "$1" --addr; }
hci_of() {   # hci_of <address>: the hciN adapter with that address, if attached
  local h n
  for h in /sys/class/bluetooth/hci*; do
    [ -e "$h" ] || continue
    # hciconfig, not btmgmt: btmgmt hangs or prints nothing without a terminal
    n=${h##*/}; timeout 5 hciconfig "$n" </dev/null 2>/dev/null | grep -qi "BD Address: $1" && { echo "$n"; return; }
  done
}
ble_pid() { pgrep -f "^python.* -u $HERE/[b]le_ip_link.py --connect .* --index $1 " 2>/dev/null | head -1; }

n3ue_wait() {
  for n in 1 2; do
    wait_for 120 "n3iwue$n registered over non-3GPP access, PDU session up" bash -c "$(declare -f dk n3ue_ip_n); [ -n \"\$(n3ue_ip_n $n)\" ]"
    printf '        %s\n' "$(n3ue_ip_n $n)"
  done
}

# n3iwue<k> over its own Bluetooth LE link: board ble<k> (hci_bb, attached by ble_attach.sh) -> core PC hci0.
# The container waits for ble<k>; ble_ip_link.py (root) connects, moves the tun into it and routes the N3IWF.
up_n3ue_ble() {
  local k sn addr h pid
  declare -A A
  for k in 1 2; do
    sn=$(ble_sn $k); [ -n "$sn" ] || die "no BLE$k board in boards.json: $0 boards --role ue"
    addr=$(ble_addr "$sn"); A[$k]=$addr
    h=$(hci_of "$addr"); [ -n "$h" ] || die "BLE$k board $sn ($addr) is not attached as an adapter: sudo $HERE/ble_attach.sh"
    ok "BLE$k board $sn is $h ($addr)"
    [ -n "$(ble_pid $k)" ] && die "a ble$k link is already running: $0 down --role ue"
  done
  $SUDO_AUTH 2>/dev/null || die "the Bluetooth links need root (tun, netns). Run this from a terminal (sudo asks once), or: sudo -v; $0 up --role ue"
  for k in 1 2; do n3ue_conf $k; done
  dk rm -f nrlink-n3iwue1 nrlink-n3iwue2 >/dev/null 2>&1
  N3UE1_IF=ble1 N3UE2_IF=ble2 dk compose -f "$N3UE_YAML" up -d >/dev/null 2>&1 || die "n3iwue containers did not start"
  for k in 1 2; do
    pid=$(dk inspect -f '{{.State.Pid}}' "nrlink-n3iwue$k")
    [ "${pid:-0}" -gt 0 ] || die "nrlink-n3iwue$k is not running"
    nohup sudo -n "$PY" -u "$HERE/ble_ip_link.py" --connect "$CORE_BT" --local "${A[$k]}" --index $k \
      --netns "$pid" --route "$N3IWF_IP/32" > "$LOG/ble$k.log" 2>&1 &
    wait_for 60 "ble$k: Bluetooth link ${A[$k]} -> $CORE_BT up, 10.70.0.1$k in nrlink-n3iwue$k" grep -q "ble$k: up" "$LOG/ble$k.log"
  done
  n3ue_wait
}

up_ue() {
  T=5
  nstep "checks"
  for f in ue1_bridge ue2_bridge; do alive $f && die "$f is already running, run: $0 down --role ue"; done
  dk info >/dev/null 2>&1 || die "docker is not usable from this account"
  ok "docker"
  [ -n "$(dk ps -q -f name=^f5gc-amf$ -f name=^oai-amf$)" ] && die "a 5G core is running on this PC; in the split setup it runs on the core PC: $0 down"
  "$PY" boards.py --check --need ue1,ue2 || die "need two boards running nrlink for UE1/UE2 (see above)"
  ok "UE boards found, roles saved in boards.json"
  ping -c1 -W2 "$CORE_PC" >/dev/null 2>&1 && ok "core PC $CORE_PC reachable" || printf '   warning: core PC %s does not answer ping\n' "$CORE_PC"

  nstep "docker networks and RAN configs"
  dk network inspect demo-oai-public-net >/dev/null 2>&1 || dk network create --subnet 192.168.70.128/26 --gateway 192.168.70.129 \
      -o com.docker.network.bridge.name=demo-oai demo-oai-public-net >/dev/null || die "could not create demo-oai-public-net"
  ok "demo-oai-public-net (nrUE containers, local only)"
  dk network inspect $N3NET >/dev/null 2>&1 || dk network create --subnet 192.168.72.0/24 --gateway 192.168.72.1 \
      -o com.docker.network.bridge.name=n3ue -o com.docker.network.bridge.enable_ip_masquerade=false $N3NET >/dev/null \
      || die "could not create $N3NET"
  ok "$N3NET 192.168.72.0/24 (non-3GPP UEs, routed without NAT)"
  ran_conf_free5gc
  export NRLINK_CONF=./free5gc
  ok "nrUE configs for slice 1/010203, DNN internet"

  nstep "UE board bridges and nrUEs (eMBB, URLLC) to the core PC gNB, over the air"
  dk compose -f "$RAN" down >/dev/null 2>&1
  for u in 1 2; do start_proc "ue${u}_bridge" "$PY" -u ue_bridge.py "$u"; done
  for u in 1 2; do wait_for 20 "UE$u board answers" grep -q "bridge on udp" "$LOG/ue${u}_bridge.log"; done
  dk compose -f "$RAN" up -d oai-nrue1 oai-nrue2 >/dev/null 2>&1 || die "UE containers did not start"
  for u in 1 2; do
    wait_for 420 "UE$u PDU session (needs the core PC up: nrlink.sh up --role core)" bash -c "$(declare -f dk ue_ip); [ -n \"\$(ue_ip $u)\" ]"
    printf '        oaitun_ue1 = %s\n' "$(ue_ip "$u")"
  done

  nstep "non-3GPP UEs n3iwue1/2 (IMSI 041/042) to the core PC N3IWF, over $TRANSPORT"
  if [ "$TRANSPORT" = ble ]; then
    up_n3ue_ble
  elif ip route get "$N3IWF_IP" 2>/dev/null | grep -q "via $CORE_PC"; then
    for n in 1 2; do n3ue_conf $n; done
    dk rm -f nrlink-n3iwue1 nrlink-n3iwue2 >/dev/null 2>&1
    dk compose -f "$N3UE_YAML" up -d >/dev/null 2>&1 || die "n3iwue containers did not start"
    n3ue_wait
  else
    printf '   skipped: no route to the N3IWF yet. Bluetooth is not there yet, so use the LAN:\n'
    printf '     here:     sudo %s/ue_routing.sh\n     core PC:  sudo ~/oai/nrlink/core_routing.sh --lan\n' "$HERE"
  fi

  ROLE_SET=1 live_start
  printf '\n\033[1mUp (UE PC).\033[0m nrUE1 eMBB, nrUE2 URLLC over the boards; n3iwue1/2 via the core PC N3IWF (%s).\n' "$TRANSPORT"
}

down_ue() {
  ROLE_SET=1 live_stop
  step "non-3GPP UEs"
  if [ -n "$(ble_pid 1)$(ble_pid 2)" ]; then   # root processes: stop them through sudo
    sudo -n pkill -TERM -f "[b]le_ip_link.py --connect" 2>/dev/null && ok "stopped the Bluetooth links ble1/ble2" \
      || printf '   stop the Bluetooth links as root:  sudo pkill -f "[b]le_ip_link.py --connect"\n'
  fi
  for n in 1 2; do dk rm -f "nrlink-n3iwue$n" >/dev/null 2>&1 && ok "removed nrlink-n3iwue$n"; done
  step "nrUEs"
  for c in oai-nrue1 oai-nrue2; do dk stop -t 5 "$c" >/dev/null 2>&1 && ok "stopped $c"; done
  dk compose -f "$RAN" down >/dev/null 2>&1
  step "board bridges"
  for f in ue1_bridge ue2_bridge; do stop_proc $f; done
  printf '\n\033[1mDown (UE PC).\033[0m\n'
}

status_ue() {
  printf '%-14s %s\n' PROCESS STATE
  for f in ue1_bridge ue2_bridge; do
    alive $f && printf '%-14s running (pid %s)\n' $f "$(cat "$PID/$f.pid")" || printf '%-14s stopped\n' $f
  done
  printf '\n'
  dk ps --format '{{.Names}}\t{{.Status}}' | grep -E 'oai-nrue|nrlink-n3iwue' | sort | column -t -s $'\t'
  printf '\n'
  for u in 1 2; do printf 'UE%s oaitun_ue1: %s\n' "$u" "$(ue_ip "$u")"; done
  for n in 1 2; do printf 'n3iwue%s:       %s\n' "$n" "$(n3ue_ip_n $n)"; done
  printf 'N3IWF route:   %s   (LAN transport)\n' "$(ip route get "$N3IWF_IP" 2>/dev/null | head -1)"
  for k in 1 2; do
    printf 'ble%s link:     %s\n' "$k" "$([ -n "$(ble_pid $k)" ] && grep -E "ble$k: (up|link|conn)" "$LOG/ble$k.log" 2>/dev/null | tail -1 || echo stopped)"
  done
}

up() {
  [ "$ROLE" = ue ] && { up_ue; return; }
  [ "$CORE" = oai ] && T=7
  if [ "$ROLE" = core ]; then
    [ "$CORE" = free5gc ] || die "--role core needs the free5GC core (the N3IWF serves the Bluetooth UEs)"
    T=7
    export NRLINK_TELEMETRY=${NRLINK_TELEMETRY:-$UE_PC:8091}
  fi
  nstep "checks"
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge dashboard; do alive $f && die "$f is already running, run: $0 down --role $ROLE"; done
  dk info >/dev/null 2>&1 || die "docker is not usable from this account"
  ok "docker"
  if [ "$ROLE" = core ]; then
    "$PY" boards.py --check --need gnb || die "the gNB board is not running nrlink (see above)"
    ok "gNB board found, saved in boards.json"
  else
    "$PY" boards.py --check || die "not enough boards running nrlink (see above)"
    ok "boards found, roles saved in boards.json"
  fi

  echo "$CORE" > "$PID/core"
  core_$CORE

  nstep "board bridges (host)"
  dk compose -f "$OLD_RAN" down >/dev/null 2>&1
  dk compose -f "$RAN" down >/dev/null 2>&1
  # core PC: only nrUE1/2 are on the air (the UE PC has no UE3 board); polling an absent UE costs 2 x 25 ms a UL slot.
  # --skip-empty (dynamic slotting): empty downlink-only slots stay off the air, the nrUEs fill them in (11 Nov 2026)
  if [ "$ROLE" = core ]; then start_proc gnb_bridge "$PY" -u gnb_bridge.py --ues 1,2 --skip-empty; else start_proc gnb_bridge "$PY" -u gnb_bridge.py; fi
  [ "$ROLE" = core ] || for u in 1 2 3; do start_proc "ue${u}_bridge" "$PY" -u ue_bridge.py "$u"; done
  wait_for 20 "gNB board answers" grep -q "listening on udp" "$LOG/gnb_bridge.log"
  [ "$ROLE" = core ] && ok "gNB telemetry to $NRLINK_TELEMETRY (UE PC dashboard)"
  [ "$ROLE" = core ] || for u in 1 2 3; do wait_for 20 "UE$u board answers" grep -q "bridge on udp" "$LOG/ue${u}_bridge.log"; done

  nstep "gNB VNF (MAC/RLC/PDCP/RRC)"
  dk compose -f "$RAN" up -d oai-vnf >/dev/null 2>&1 || die "VNF container did not start"
  wait_for 60 "VNF registered with the AMF (NG setup)" bash -c "$(declare -f dk); dk logs oai-vnf 2>&1 | grep -q 'Received NGSetupResponse'"

  nstep "gNB PNF (L1 emulation over the boards)"
  dk compose -f "$RAN" up -d oai-pnf >/dev/null 2>&1 || die "PNF container did not start"
  wait_for 60 "PNF running slots through the gNB board" bash -c "grep -E '^\[gnb\] +[1-9][0-9.]* slots/s' '$LOG/gnb_bridge.log'"
  wait_for 30 "VNF follows the PNF slot clock" bash -c "$(declare -f dk); ! dk logs --since 5s oai-vnf 2>&1 | grep -q 'time client: connection'"

  if [ "$ROLE" != core ]; then
  nstep "nrUEs (eMBB, URLLC, mMTC)"
  dk compose -f "$RAN" up -d oai-nrue1 oai-nrue2 oai-nrue3 >/dev/null 2>&1 || die "UE containers did not start"
  for u in 1 2 3; do
    wait_for 420 "UE$u PDU session" bash -c "$(declare -f dk ue_ip); [ -n \"\$(ue_ip $u)\" ]"
    printf '        oaitun_ue1 = %s\n' "$(ue_ip "$u")"
  done
  fi

  if [ "$CORE" = free5gc ]; then
    nstep "non-3GPP UE (IKEv2/EAP-5G to the N3IWF, IPsec over IP for now)"
    dk compose -f "$F5" up -d n3iwue >/dev/null 2>&1 || die "n3iwue container did not start"
    wait_for 120 "n3iwue registered over non-3GPP access, PDU session up" bash -c "$(declare -f dk n3ue_ip); [ -n \"\$(n3ue_ip)\" ]"
    printf '        %s\n' "$(n3ue_ip)"
  fi

  if [ "$ROLE" = core ]; then
    ble_ap
    ROLE_SET=1 live_start
    printf '\n\033[1mUp (core PC).\033[0m gNB waiting for the UE PC nrUEs over the air; non-3GPP UEs via the N3IWF 192.168.70.139.\n'
    printf '   gNB telemetry -> %s (dashboard on the UE PC)\n' "$NRLINK_TELEMETRY"
    return 0
  fi

  nstep "dashboard and demo apps"
  start_proc dashboard "$PY" -u dashboard_server.py
  wait_for 40 "dashboard answering on :8088" curl -sf http://127.0.0.1:8088/api/hello
  wait_for 40 "LiDAR, AGV and sensor traffic flowing" bash -c "timeout 5 curl -sN http://127.0.0.1:8088/events | grep -q '\"k\":\"scan\"'"

  printf '\n\033[1mUp (%s core).\033[0m Dashboard: http://%s:8088  (LAN)   http://100.64.0.74:8088  (Tailscale)\n' "$CORE" "$(hostname -I | awk '{print $1}')"
}

down() {
  [ "$ROLE" = ue ] && { down_ue; return; }
  [ "$ROLE" = core ] && ROLE_SET=1 live_stop
  if [ "$ROLE" = core ] && [ -f "$PID/ble_link.pid" ]; then
    step "Bluetooth access point"
    sudo -n pkill -TERM -f "[b]le_ip_link.py --listen" 2>/dev/null && ok "stopped the L2CAP listener" \
      || printf '   stop it as root:  sudo pkill -f "[b]le_ip_link.py --listen"\n'
    rm -f "$PID/ble_link.pid"
  fi
  step "dashboard and demo apps"
  stop_proc dashboard
  if [ -n "$(dk ps -aq -f name=^nrlink-dn-apps$ 2>/dev/null)" ]; then dk rm -f nrlink-dn-apps >/dev/null 2>&1 && ok "removed ext-dn app sidecar"; fi
  step "UEs, gNB PNF, gNB VNF"
  dk stop -t 5 f5gc-n3iwue >/dev/null 2>&1 && dk rm -f f5gc-n3iwue >/dev/null 2>&1 && ok "stopped f5gc-n3iwue"
  for c in oai-nrue1 oai-nrue2 oai-nrue3 oai-pnf oai-vnf; do dk stop -t 5 "$c" >/dev/null 2>&1 && ok "stopped $c"; done
  dk compose -f "$RAN" down >/dev/null 2>&1
  dk compose -f "$OLD_RAN" down >/dev/null 2>&1
  step "board bridges"
  for f in ue1_bridge ue2_bridge ue3_bridge gnb_bridge; do stop_proc $f; done
  # bridges started before pid files existed (older start scripts)
  for p in $(pgrep -f "python -u (gnb|ue)_bridge\.py"); do kill "$p" 2>/dev/null && ok "stopped stray bridge (pid $p)"; done
  if [ "$KEEP_CORE" = 1 ]; then
    step "5G core left running"
  else
    step "5G core"
    [ -n "$(dk ps -q -f name=^oai-amf$)" ] && dk compose "${CN[@]}" down >/dev/null 2>&1 && ok "OAI core stopped"
    [ -n "$(dk ps -aq -f name=^f5gc-)" ] && dk compose -f "$F5" down >/dev/null 2>&1 && ok "free5GC core stopped"
    rm -f "$PID/core"
  fi
  printf '\n\033[1mDown.\033[0m\n'
}

status() {
  [ "$ROLE" = ue ] && { status_ue; return; }
  printf '%-14s %s\n' PROCESS STATE
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge dashboard; do
    alive $f && printf '%-14s running (pid %s)\n' $f "$(cat "$PID/$f.pid")" || printf '%-14s stopped\n' $f
  done
  printf '\n'
  printf '\ncore: %s\n' "$(cat "$PID/core" 2>/dev/null || echo '?')"
  dk ps --format '{{.Names}}\t{{.Status}}' | grep -E 'oai-|mysql|nrlink|f5gc-' | sort | column -t -s $'\t'
  printf '\n'
  for u in 1 2 3; do printf 'UE%s oaitun_ue1: %s\n' "$u" "$(ue_ip "$u")"; done
  [ -n "$(dk ps -q -f name=^f5gc-n3iwue$)" ] && printf 'non-3GPP UE:    %s\n' "$(n3ue_ip)"
  [ -f "$LOG/gnb_bridge.log" ] && grep '^\[gnb\]' "$LOG/gnb_bridge.log" | tail -1
}

host_role() {   # role of this PC from its addresses: ue, core, or all (single-PC setup)
  local ips=" $(hostname -I 2>/dev/null) "
  case "$ips" in *" $UE_PC "*) echo ue ;; *" $CORE_PC "*) echo core ;; *) echo all ;; esac
}

boards() {
  [ "$ROLE_SET" = 1 ] || { ROLE=$(host_role); [ "$ROLE" = all ] || printf '   this PC is %s: %s role\n' "$(hostname)" "$ROLE"; }
  for f in gnb_bridge ue1_bridge ue2_bridge ue3_bridge; do alive $f && die "the bridges hold the serial ports, run: $0 down --keep-core"; done
  case "$ROLE" in   # only the nrlink roles this PC owns (the BLE boards are always listed)
    ue) "$PY" boards.py --need ue1,ue2 ;;
    core) "$PY" boards.py --need gnb ;;
    *) "$PY" boards.py ;;
  esac
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

cmd=${1:-}; shift || true
KEEP_CORE=0; ARG=; ROLE_SET=0; SCENARIO=; LIVE_OPTS=
while [ $# -gt 0 ]; do
  case "$1" in
    --core) case "${2:-}" in oai | free5gc) CORE=$2 ;; *) die "--core takes oai or free5gc" ;; esac; shift ;;
    --role) case "${2:-}" in all | core | ue) ROLE=$2; ROLE_SET=1 ;;
              *) die "--role takes all, core or ue" ;; esac; shift ;;
    --keep-core) KEEP_CORE=1 ;;
    --scenario) case "${2:-}" in factory | agv) SCENARIO=$2 ;; *) die "--scenario takes factory or agv" ;; esac; shift ;;
    --level) case "${2:-}" in L1 | L2 | L3) LIVE_OPTS="$LIVE_OPTS --level $2" ;; *) die "--level takes L1, L2 or L3" ;; esac; shift ;;
    --mode) case "${2:-}" in both | nr | ble) LIVE_OPTS="$LIVE_OPTS --mode $2" ;; *) die "--mode takes both, nr or ble" ;; esac; shift ;;
    --tx-dbm) case "${2:-}" in 0 | -10 | -20 | -30) LIVE_OPTS="$LIVE_OPTS --tx-dbm $2" ;; *) die "--tx-dbm takes 0, -10, -20 or -30" ;; esac; shift ;;
    --fading-drop) LIVE_OPTS="$LIVE_OPTS --fading-drop" ;;
    --transport) case "${2:-}" in ble | lan) TRANSPORT=$2 ;; *) die "--transport takes ble or lan" ;; esac; shift ;;
    *) ARG=$1 ;;
  esac
  shift
done

case "$cmd" in
  up) up ;;
  boards) boards ;;
  flash) flash "$ARG" ;;
  down) down ;;
  restart) KEEP_CORE=1; down; KEEP_CORE=0; up ;;
  status) status; [ "$ROLE" = all ] || { printf '\n'; ROLE_SET=1 live_status; } ;;
  live) live ;;
  *) sed -n '2,39p' "$0"; exit 1 ;;
esac
