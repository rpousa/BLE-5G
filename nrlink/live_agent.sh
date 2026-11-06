#!/bin/bash
cd /home/debian/oai/nrlink
mkdir -p logs/factory/live
exec python3 -u apps/factory_dl.py agent --core 192.168.10.223
