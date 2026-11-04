#!/bin/bash
# UE PC, run as root (idempotent): interim LAN path for the non-3GPP UEs to the core PC's N3IWF.
#   sudo ./ue_routing.sh        route 192.168.70.139 (N3IWF) via the core PC, accept its replies into the n3ue bridge
#   sudo ./ue_routing.sh --off  remove both again
# The core PC needs the mirror image: sudo ./core_routing.sh --lan (route 192.168.72.0/24 via this PC).
# Not persistent across reboots. Not needed once the n3iwues run over Bluetooth.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
CORE_PC=192.168.10.223 N3IWF=192.168.70.139 BR=n3ue
LAN=$(ip route get "$CORE_PC" | grep -oP 'dev \K\S+')   # interface towards the core PC
iptables -nL DOCKER-USER >/dev/null 2>&1 || { echo "no DOCKER-USER chain: start docker first" >&2; exit 1; }
RULE="-i $LAN -s $N3IWF -o $BR -j ACCEPT"
if [ "${1:-}" = --off ]; then
  ip route del "$N3IWF/32" via "$CORE_PC" 2>/dev/null || true
  while iptables -C DOCKER-USER $RULE 2>/dev/null; do iptables -D DOCKER-USER $RULE; done
else
  ip route replace "$N3IWF/32" via "$CORE_PC" dev "$LAN"
  iptables -C DOCKER-USER $RULE 2>/dev/null || iptables -I DOCKER-USER $RULE
fi
ip route get "$N3IWF" | head -1
iptables -nvL DOCKER-USER
