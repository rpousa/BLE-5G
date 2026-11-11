#!/bin/bash
# sidelink over the nrlink boards UE1 (master, sync reference side) and UE2 (peer, nearby side)
CH=${CH:-0x5A}
docker rm -f sl-syncref sl-nearby >/dev/null 2>&1
cd ~/oai/nrlink
for p in $(pgrep -f "[s]l_bridge\.py"); do kill $p; done
sleep 1
PY=/home/debian/nxp/venv/bin/python
nohup $PY -u sl_bridge.py --role master --ue-port 5600 --board 2HOKMMVEX1WMP --channel $CH $MASTER_EXTRA > logs/sl/master_board.log 2>&1 &
nohup $PY -u sl_bridge.py --role peer --ue-port 5601 --board GBAMNH4BQ3CSB --channel $CH > logs/sl/peer_board.log 2>&1 &
sleep 2
cd ~/oai/sl-5g-nr && bash sl_emu_start.sh
