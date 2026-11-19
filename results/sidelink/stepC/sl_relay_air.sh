#!/bin/bash
# Step C with the sidelink on the nrlink emulated PHY: AIR=udp (no boards) or AIR=board (UE1 master/relay side, UE2 peer/remote side).
# Uu (relay UE <-> sl-gnb) stays on rfsim. Start stepc/gnb_start.sh first. Stop ue_bridge 1/2 before AIR=board.
AIR=${AIR:-udp}; CH=${CH:-0x5A}
docker rm -f sl-relay sl-remote >/dev/null 2>&1
cd ~/oai/nrlink; mkdir -p logs/sl
for p in $(pgrep -f "[s]l_bridge\.py"); do kill $p; done
sleep 1
PY=/home/debian/nxp/venv/bin/python
if [ "$AIR" = board ]; then
  nohup $PY -u sl_bridge.py --role master --ue-port 5600 --board 2HOKMMVEX1WMP --channel $CH > logs/sl/relay_master_board.log 2>&1 &
  nohup $PY -u sl_bridge.py --role peer --ue-port 5601 --board GBAMNH4BQ3CSB --channel $CH > logs/sl/relay_peer_board.log 2>&1 &
  sleep 2
else
  nohup $PY -u sl_bridge.py --role master --ue-port 5600 --air udp --air-port 5700 --peer 127.0.0.1:5701 > logs/sl/relay_master_udp.log 2>&1 &
  nohup $PY -u sl_bridge.py --role peer --ue-port 5601 --air udp --air-port 5701 --peer 127.0.0.1:5700 > logs/sl/relay_peer_udp.log 2>&1 &
  sleep 1
fi
cd ~/oai/sl-5g-nr && SL_AIR=$AIR bash sl_relay_start.sh
