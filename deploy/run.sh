#!/bin/bash
# OAI 5G SA end-to-end: CN5G + gNB split over nFAPI (VNF <-> PNF/rfsim) + nrUE.
# Usage: ./run.sh up | test | down | logs <container>
# Needs membership of the docker group (or prefix with: sg docker -c "...").
set -e
CN=~/oai/oai-cn5g-fed-master/docker-compose
RAN=~/oai/deploy

case "$1" in
  up)
    docker compose -f $CN/docker-compose-basic-nrf.yaml -f $RAN/nrlink/core-static-ips.yaml up -d --wait
    docker compose -f $RAN/docker-compose-ran.yaml up -d oai-vnf
    sleep 5
    docker compose -f $RAN/docker-compose-ran.yaml up -d oai-pnf
    sleep 15
    docker compose -f $RAN/docker-compose-ran.yaml up -d oai-nr-ue
    echo "Waiting for PDU session..."
    for i in $(seq 60); do
      docker exec oai-nr-ue ip -4 -br addr show oaitun_ue1 2>/dev/null | grep -q 12.1.1 && break
      sleep 2
    done
    docker exec oai-nr-ue ip -4 -br addr show oaitun_ue1
    ;;
  test)
    UE_IP=$(docker exec oai-nr-ue ip -4 -o addr show oaitun_ue1 | awk '{print $4}' | cut -d/ -f1)
    DN_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-ext-dn)
    echo "UE $UE_IP -> DN $DN_IP (uplink)"; docker exec oai-nr-ue ping -I oaitun_ue1 -c 10 -i 0.3 $DN_IP | tail -2
    echo "DN -> UE $UE_IP (downlink)";      docker exec oai-ext-dn ping -c 10 -i 0.3 $UE_IP | tail -2
    ;;
  down)
    docker compose -f $RAN/docker-compose-ran.yaml down
    docker compose -f $CN/docker-compose-basic-nrf.yaml -f $RAN/nrlink/core-static-ips.yaml down
    ;;
  logs)
    docker logs -f "$2"
    ;;
  *) echo "usage: $0 up|test|down|logs <container>"; exit 1 ;;
esac
