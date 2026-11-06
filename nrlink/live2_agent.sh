#!/bin/bash
cd /home/debian/oai/nrlink
pkill -f "[f]actory_dl.py agent"; pkill -f "[l]ive_agent.sh"; pkill -f "[f]actory2.py agent"
for c in fdl-live-rx-lidar fdl-live-rx-robots fdl-live-rx-cam1 fdl-live-rx-cam2 fdl2-robots fdl2-lidar; do docker rm -f $c >/dev/null 2>&1; done
mkdir -p logs/factory/live2 /dev/shm/fdl2
exec python3 -u apps/factory2.py agent --core 192.168.10.223
