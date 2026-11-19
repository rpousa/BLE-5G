#!/bin/bash
# Step C: the sidelink-release gNB (U2N relay) on the UE PC, next to the step-C free5GC (stepc-core); rfsim server on host port 4043.
cd "$(dirname "$0")/.."
docker rm -f sl-gnb >/dev/null 2>&1
docker run -d --name sl-gnb --network demo-oai-public-net --ip 192.168.70.160 -p 4043:4043 --cpuset-cpus 6-10 \
  --cap-add SYS_NICE --cap-add NET_ADMIN --ulimit core=-1 -e NRLINK_REMOTE_IP=12.1.1.200 \
  -e LD_LIBRARY_PATH=/oai-ran/build_sl -w /oai-ran/build_sl -v "$PWD":/oai-ran --entrypoint bash ran-base:local \
  -c "./nr-softmodem -O /oai-ran/stepc/gnb_sl_relay.conf --sa --rfsim --rfsimulator.serveraddr server --rfsimulator.serverport 4043 \
      --gNBs.[0].min_rxtxtime 6 --relay-type 1 --remote-ue-id 1 --ip-demo 1 > /oai-ran/build_sl/sl-gnb.log 2>&1" >/dev/null
echo started
