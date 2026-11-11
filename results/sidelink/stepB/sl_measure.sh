#!/bin/bash
# measurements over the sidelink (nearby UE 10.0.0.100 -> sync reference 10.0.0.1); needs the password on stdin
read -r PW
docker rm -f sl-iperf-srv sl-iperf-cl >/dev/null 2>&1
docker run -d --name sl-iperf-srv --network container:sl-syncref --entrypoint iperf3 nrlink-run:local -s -B 10.0.0.1 >/dev/null
sleep 1
cl() { timeout 70 docker run --rm --name sl-iperf-cl --network container:sl-nearby --entrypoint iperf3 nrlink-run:local -c 10.0.0.1 -B 10.0.0.100 "$@" 2>&1 | grep -E "receiver|sender|error" | tail -2; docker rm -f sl-iperf-cl >/dev/null 2>&1; sleep 4; }
P=$(docker inspect -f "{{.State.Pid}}" sl-nearby)
for sz in 56 500 1400; do echo "== ping $sz B"; echo "$PW" | sudo -S -p "" nsenter -t $P -n ping -c 20 -i 0.5 -s $sz -I oaitun_ue2 10.0.0.1 | tail -2; done
for b in ${RATES:-100k 200k 300k 400k 600k}; do echo "== UDP up $b"; cl -u -b $b -l 1000 -t 15; done
for b in ${RATES_DN:-200k 300k 400k}; do echo "== UDP down $b"; cl -u -b $b -l 1000 -t 15 -R; done
echo "== TCP up 30 s"; cl -t 30
echo "== TCP down 30 s"; cl -t 30 -R
echo "== ping 56 B under TCP up"; (cl -t 25 > /dev/null &) ; sleep 5; echo "$PW" | sudo -S -p "" nsenter -t $P -n ping -c 30 -i 0.5 -I oaitun_ue2 10.0.0.1 | tail -2; sleep 8
docker rm -f sl-iperf-srv >/dev/null 2>&1
