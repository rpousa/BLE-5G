#!/bin/bash
# nrlink end to end: 5G core + OAI gNB (VNF/PNF) + 3 OAI nrUEs, NR L1 carried by the FRDM-MCXW72 boards.
#   ./run_nrlink.sh up       same as ./nrlink.sh up (core, bridges, gNB, UEs, dashboard)
#   ./run_nrlink.sh status   UE IPs / bridge stats
#   ./run_nrlink.sh test     iperf3 per traffic class between each UE's oaitun_ue1 and oai-ext-dn
#   ./run_nrlink.sh down     same as ./nrlink.sh down --keep-core
set -e
cd "$(dirname "$0")"
D=~/oai/deploy/nrlink
PY=~/nxp/venv/bin/python
LOG=$PWD/logs
DC="docker compose -f $D/docker-compose.yaml"
dk() { sg docker -c "$*"; }

ue_ip() { dk "docker exec oai-nrue$1 ip -4 -o addr show oaitun_ue1" 2>/dev/null | awk '{print $4}' | cut -d/ -f1; }
DN_IP=192.168.70.132

case "$1" in
  up)
    exec ./nrlink.sh up ;;
  status)
    for u in 1 2 3; do echo "UE$u oaitun_ue1: $(ue_ip $u)"; done
    tail -n 2 "$LOG"/gnb_bridge.log; for u in 1 2 3; do tail -n 1 "$LOG/ue${u}_bridge.log"; done ;;
  test)
    T=${T:-30}
    dk "docker exec oai-ext-dn sh -c 'pkill iperf3; sleep 1; for p in 5201 5202 5203; do iperf3 -s -D -p \$p; done'"
    U1=$(ue_ip 1); U2=$(ue_ip 2); U3=$(ue_ip 3)
    echo "== eMBB  UE1 $U1: bulk TCP uplink, then downlink (window sized to the ~150 kb/s link)"
    dk "docker exec oai-nrue1 iperf3 -c $DN_IP -p 5201 -B $U1 --bind-dev oaitun_ue1 -t $T -i 5 -w 16K" | tail -n 4
    dk "docker exec oai-nrue1 iperf3 -c $DN_IP -p 5201 -B $U1 --bind-dev oaitun_ue1 -t $T -i 5 -w 16K -R" | tail -n 4
    echo "== URLLC UE2 $U2: 32 B every 20 ms both ways + RTT"
    dk "docker exec oai-nrue2 iperf3 -c $DN_IP -p 5202 -B $U2 --bind-dev oaitun_ue1 -u -l 32 -b 12.8k -t $T" | tail -n 3
    dk "docker exec oai-nrue2 iperf3 -c $DN_IP -p 5202 -B $U2 --bind-dev oaitun_ue1 -u -l 32 -b 12.8k -t $T -R" | tail -n 3
    dk "docker exec oai-nrue2 ping -I oaitun_ue1 -c 20 -i 0.2 -s 32 $DN_IP" | tail -n 2
    echo "== mMTC  UE3 $U3: 20 B every 1 s uplink (sensor reports)"
    dk "docker exec oai-nrue3 iperf3 -c $DN_IP -p 5203 -B $U3 --bind-dev oaitun_ue1 -u -l 20 -b 160 -t $T" | tail -n 3 ;;
  down)
    exec ./nrlink.sh down --keep-core ;;
  *) echo "usage: $0 up|status|test|down"; exit 1 ;;
esac
