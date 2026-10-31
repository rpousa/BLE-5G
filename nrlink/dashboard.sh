#!/bin/bash
# start the live dashboard (RAN must be up: ./run_nrlink.sh up). Open http://<this host>:8088
#   ./dashboard.sh              live
#   ./dashboard.sh --record 90  also save 90 s of events for the published artifact replay
#   ./dashboard.sh stop
cd "$(dirname "$0")"; mkdir -p logs/pids
[ -f logs/pids/dashboard.pid ] && kill "$(cat logs/pids/dashboard.pid)" 2>/dev/null && sleep 2
[ "$1" = stop ] && exit 0
nohup ~/nxp/venv/bin/python -u dashboard_server.py "$@" > logs/dashboard.log 2>&1 &
echo $! > logs/pids/dashboard.pid
sleep 4; cat logs/dashboard.log
