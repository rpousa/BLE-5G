#!/bin/bash
# start the two sidelink nrUEs on the nrlink emulated PHY (NRLINK_SL), bridges on the host at 172.30.99.1:5600/5601
B=/oai-ran/build_sl; C=/oai-ran/targets/PROJECTS/NR-SIDELINK/CONF
run() { docker run -d --name $1 --network sl-net --ip $2 --cpuset-cpus $3 --cap-add NET_ADMIN --cap-add SYS_NICE --ulimit core=-1 --device /dev/net/tun -e NRLINK_SL=172.30.99.1:$5 --entrypoint bash -w $B -e LD_LIBRARY_PATH=$B -v $HOME/oai/sl-5g-nr:/oai-ran ran-base:local -c "$4 > /oai-ran/build_sl/$1.log 2>&1" >/dev/null; }
docker rm -f sl-syncref sl-nearby >/dev/null 2>&1
run sl-syncref 172.30.99.2 12-16 "./nr-uesoftmodem -O $C/sl_sync_ref_nofb.conf --sa --sl-mode 2 --sync-ref --thread-pool -1,-1,-1,-1 ${SL_EXTRA}" 5600
run sl-nearby 172.30.99.3 17-21 "./nr-uesoftmodem -O $C/sl_ue1_nofb.conf --sa --sl-mode 2 --thread-pool -1,-1,-1,-1 ${SL_EXTRA}" 5601
