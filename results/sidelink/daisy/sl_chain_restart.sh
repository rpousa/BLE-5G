#!/bin/bash
# sidelink over a two-hop daisy chain (option A): master (sync-ref UE) -> relay node (no UE) -> peer (nearby UE)
#   AIR=udp    all three bridges on UDP
#   AIR=board  hop 0 over the boards UE1 (master) <-> UE2 (relay), hop 1 over UDP (relay <-> peer)
#   AIR=ends   only the ends: UE1 (master, link 0) and UE2 (peer, link 1); the middle node is elsewhere (a host relay
#              sl_bridge.py --role relay on a third board, or an nrlink v5 board set up by repeater.py)
AIR=${AIR:-udp}; CH=${CH:-0x5A}
docker rm -f sl-syncref sl-nearby >/dev/null 2>&1
cd ~/oai/nrlink
for p in $(pgrep -f "[s]l_bridge\.py"); do kill $p; done
sleep 1
PY=/home/debian/nxp/venv/bin/python; L=logs/sl
b() { r=$1; shift; nohup $PY -u sl_bridge.py --role $r "$@" > $L/chain_${AIR}_$r.log 2>&1 & }
case $AIR in
udp)
  b master --ue-port 5600 --link 0 --air udp --air-port 5700 --peer 127.0.0.1:5701
  b relay --link 0 --air udp --air-port 5701 --peer 127.0.0.1:5700 --down-air udp --down-air-port 5710 --down-peer 127.0.0.1:5711
  b peer --ue-port 5601 --link 1 --air udp --air-port 5711 --peer 127.0.0.1:5710 ;;
board)
  b master --ue-port 5600 --link 0 --board 2HOKMMVEX1WMP --channel $CH
  b relay --link 0 --board GBAMNH4BQ3CSB --channel $CH --down-air udp --down-air-port 5710 --down-peer 127.0.0.1:5711
  b peer --ue-port 5601 --link 1 --air udp --air-port 5711 --peer 127.0.0.1:5710 ;;
ends)
  b master --ue-port 5600 --link 0 --board 2HOKMMVEX1WMP --channel $CH $MASTER_EXTRA
  b peer --ue-port 5601 --link 1 --board GBAMNH4BQ3CSB --channel $CH $PEER_EXTRA ;;
esac
sleep 2
cd ~/oai/sl-5g-nr && bash sl_emu_start.sh
