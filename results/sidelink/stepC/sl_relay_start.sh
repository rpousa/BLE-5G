#!/bin/bash
# Step C: U2N relay. gNB = sl-gnb (stepc/gnb_start.sh, rfsim server on host port 4043) with the step-C free5GC (stepc/docker-compose.yaml).
#   relay UE  sl-relay  172.30.99.2  mode 1, sync reference, IMSI 208950000000039 (PDU 12.1.1.200)
#   remote UE sl-remote 172.30.99.3  mode 2, reaches the network through the relay (SRAP)
# SL_AIR=rfsim (default): the sidelink over rfsim. SL_AIR=board|udp: over nrlink_sl.c + sl_bridge.py (bridges started separately).
cd "$(dirname "$0")"
B=/oai-ran/${SL_BUILD:-build_sl}; C=/oai-ran/targets/PROJECTS/NR-SIDELINK/CONF; AIR=${SL_AIR:-rfsim}
RC=${SL_RELAY_CONF:-sl_sync_ref_nofb.conf}; NC=${SL_REMOTE_CONF:-sl_ue1_nofb.conf}
run() { docker run -d --name $1 --network sl-net --ip $2 --cpuset-cpus $3 --cap-add NET_ADMIN --cap-add SYS_NICE --ulimit core=-1 \
          --device /dev/net/tun --entrypoint bash -w $B -e LD_LIBRARY_PATH=$B $5 -v "$PWD":/oai-ran ran-base:local \
          -c "$4 > $B/$1.log 2>&1" >/dev/null; }
docker network inspect sl-net >/dev/null 2>&1 || docker network create --subnet 172.30.99.0/24 sl-net >/dev/null
docker rm -f sl-relay sl-remote sl-syncref sl-nearby >/dev/null 2>&1
UU="--rfsim --rfsimulator.serveraddr 192.168.70.160 --rfsimulator.serverport 4043"  # direct, via a second interface on demo-oai-public-net
SIM="--uicc0.imsi 208950000000039 --uicc0.key 0C0A34601D4F07677303652C0462535B --uicc0.opc 63bfa50ee6523365ff14c1f45f88737d --uicc0.dnn internet --uicc0.nssai_sst 1 --uicc0.nssai_sd 66051"
if [ "$AIR" = rfsim ]; then
  # in this tree serveraddrsl=server makes the whole rfsim device a server, so the remote UE serves the sidelink
  SLR="--rfsimulator.serveraddrsl 172.30.99.3 --rfsimulator.serverportsl 4148"; SLN="--rfsim --rfsimulator.serveraddrsl server --rfsimulator.serverportsl 4148"; ER=""; EN=""
else
  SLR=""; SLN=""; ER="-e NRLINK_SL=172.30.99.1:5600"; EN="-e NRLINK_SL=172.30.99.1:5601"
fi
run sl-remote 172.30.99.3 17-21 "./nr-uesoftmodem -O $C/$NC --sa --sl-mode 2 $SLN --relay-type 1" "$EN"
sleep 3
run sl-relay 172.30.99.2 12-16 "./nr-uesoftmodem -O $C/$RC -r 106 --numerology 1 --band 78 -C 3619200000 $SIM --sa --sl-mode 1 --sync-ref $UU $SLR --relay-type 1 --is-relay-ue 1" "$ER"
docker network connect --ip 192.168.70.161 demo-oai-public-net sl-relay
echo "started (sidelink over $AIR); logs ${SL_BUILD:-build_sl}/sl-relay.log, ${SL_BUILD:-build_sl}/sl-remote.log"
