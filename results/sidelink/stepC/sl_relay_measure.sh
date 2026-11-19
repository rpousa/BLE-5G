#!/bin/bash
# Step C measurements: remote UE (12.1.1.200, behind the relay UE) <-> ext-dn 192.168.70.132 of the step-C core.
# Password on stdin (nsenter). RATES / RATES_DN: UDP offered loads up / down.
read -r PW
P=$(docker inspect -f "{{.State.Pid}}" sl-remote)
n() { echo "$PW" | sudo -S -p "" nsenter -t $P -n "$@"; }
n ip addr add 12.1.1.200/32 dev oaitun_ue2 2>/dev/null; n ip route replace 192.168.70.132/32 dev oaitun_ue2 src 12.1.1.200
docker exec oai-ext-dn pkill iperf3 2>/dev/null; docker exec -d oai-ext-dn iperf3 -s -B 192.168.70.132; sleep 1
docker rm -f sl-iperf-cl >/dev/null 2>&1
cl() { timeout 70 docker run --rm --name sl-iperf-cl --network container:sl-remote --entrypoint iperf3 nrlink-run:local -c 192.168.70.132 -B 12.1.1.200 "$@" 2>&1 | grep -E "receiver|sender|error" | tail -2; docker rm -f sl-iperf-cl >/dev/null 2>&1; sleep 4; }
for sz in 56 500 1400; do echo "== ping $sz B"; n ping -c 20 -i 0.5 -s $sz -I 12.1.1.200 192.168.70.132 | tail -2; done
for b in ${RATES:-100k 200k 300k 400k}; do echo "== UDP up $b"; cl -u -b $b -l 1000 -t 15; done
for b in ${RATES_DN:-100k 200k 300k 400k}; do echo "== UDP down $b"; cl -u -b $b -l 1000 -t 15 -R; done
echo "== TCP up 30 s"; cl -t 30
echo "== TCP down 30 s"; cl -t 30 -R
echo "== ping 56 B under TCP up"; (cl -t 25 > /dev/null &) ; sleep 5; n ping -c 30 -i 0.5 -I 12.1.1.200 192.168.70.132 | tail -2; sleep 8
docker exec oai-ext-dn pkill iperf3 2>/dev/null
