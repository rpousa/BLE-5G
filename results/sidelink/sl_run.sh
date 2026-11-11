#!/bin/bash
# Step A: OAI NR sidelink mode 2 (EpiSci release sl-release-2025w49) over rfsim, two UEs in containers on the UE PC.
#   ./sl_run.sh start   sync-reference UE (10.0.0.1) and nearby UE (10.0.0.100), PSFCH and CSI off (the *_nofb.conf copies)
#   ./sl_run.sh ping    20 pings nearby -> sync-reference over the sidelink (needs sudo for nsenter)
#   ./sl_run.sh stop
# Build: docker container sl-dev (ran-base:local) with this tree at /oai-ran, cmake + ninja in build_sl/.
cd "$(dirname "$0")"
B=/oai-ran/build_sl; C=/oai-ran/targets/PROJECTS/NR-SIDELINK/CONF
run() { docker run -d --name $1 --network sl-net --ip $2 --cpuset-cpus $3 --cap-add NET_ADMIN --cap-add SYS_NICE \
          --device /dev/net/tun --entrypoint bash -w $B -e LD_LIBRARY_PATH=$B -v "$PWD":/oai-ran ran-base:local \
          -c "$4 > $B/$1.log 2>&1" >/dev/null; }
case "$1" in
start)
  docker network inspect sl-net >/dev/null 2>&1 || docker network create --subnet 172.30.99.0/24 sl-net >/dev/null
  docker rm -f sl-syncref sl-nearby >/dev/null 2>&1
  run sl-syncref 172.30.99.2 12-16 "./nr-uesoftmodem -O $C/sl_sync_ref_nofb.conf --sa --sl-mode 2 --sync-ref --rfsim --thread-pool -1,-1,-1,-1 --rfsimulator.serveraddrsl server --rfsimulator.serverportsl 4048"
  sleep 8
  run sl-nearby 172.30.99.3 17-21 "./nr-uesoftmodem -O $C/sl_ue1_nofb.conf --sa --sl-mode 2 --rfsim --thread-pool -1,-1,-1,-1 --rfsimulator.serveraddrsl 172.30.99.2 --rfsimulator.serverportsl 4048"
  for i in $(seq 60); do grep -q "successfully configured" build_sl/sl-nearby.log 2>/dev/null && grep -q "Sync Achieved" build_sl/sl-nearby.log && { echo "sidelink up"; exit 0; }; sleep 2; done
  echo "nearby UE did not sync; see build_sl/sl-nearby.log"; exit 1 ;;
ping) sudo nsenter -t "$(docker inspect -f '{{.State.Pid}}' sl-nearby)" -n ping -c 20 -i 0.5 -I oaitun_ue2 10.0.0.1 ;;
stop) docker rm -f sl-syncref sl-nearby sl-iperf-srv >/dev/null 2>&1; echo stopped ;;
*) sed -n 2,6p "$0" ;;
esac
