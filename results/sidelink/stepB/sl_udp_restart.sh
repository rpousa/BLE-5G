#!/bin/bash
# restart the two sl_bridge.py (UDP air) and the two emulated sidelink UEs
docker rm -f sl-syncref sl-nearby >/dev/null 2>&1
cd ~/oai/nrlink
for p in $(pgrep -f "[s]l_bridge\.py"); do kill $p; done
sleep 1
PY=/home/debian/nxp/venv/bin/python
nohup $PY -u sl_bridge.py --role master --ue-port 5600 --air udp --air-port 5700 --peer 127.0.0.1:5701 > logs/sl/master_udp.log 2>&1 &
nohup $PY -u sl_bridge.py --role peer --ue-port 5601 --air udp --air-port 5701 --peer 127.0.0.1:5700 > logs/sl/peer_udp.log 2>&1 &
sleep 1
cd ~/oai/sl-5g-nr && bash sl_emu_start.sh
