#!/bin/bash
# Core PC, run as root (idempotent): route the non-3GPP UEs to the N3IWF (192.168.70.139).
#   sudo ./core_routing.sh          forwarding + Bluetooth tun interfaces (ble+) <-> demo-oai
#   sudo ./core_routing.sh --lan    also the interim test: UE PC n3iwue subnet 192.168.72.0/24 (routed via
#                                   the UE PC 192.168.10.232, no NAT) over the LAN interface <-> demo-oai; the UE PC runs ue_routing.sh
#   sudo ./core_routing.sh --off    remove all of these rules again
# Needs docker running (it creates the DOCKER-USER chain). Not persistent across reboots.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
UE_PC=192.168.10.232 UE_NET=192.168.72.0/24 BR=demo-oai
LAN=$(ip route get "$UE_PC" | grep -oP 'dev \K\S+')   # interface towards the UE PC
iptables -nL DOCKER-USER >/dev/null 2>&1 || { echo "no DOCKER-USER chain: start docker first" >&2; exit 1; }
RULES=("-i ble+ -o $BR -j ACCEPT" "-i $BR -o ble+ -j ACCEPT")
[ "${1:-}" = --lan ] || [ "${1:-}" = --off ] && RULES+=("-i $LAN -s $UE_NET -o $BR -j ACCEPT" "-i $BR -o $LAN -d $UE_NET -j ACCEPT")
for r in "${RULES[@]}"; do
  if [ "${1:-}" = --off ]; then
    while iptables -C DOCKER-USER $r 2>/dev/null; do iptables -D DOCKER-USER $r; done
  else
    iptables -C DOCKER-USER $r 2>/dev/null || iptables -I DOCKER-USER $r
  fi
done
[ "${1:-}" = --off ] || sysctl -qw net.ipv4.ip_forward=1
[ "${1:-}" = --lan ] && ip route replace "$UE_NET" via "$UE_PC" dev "$LAN"
[ "${1:-}" = --off ] && ip route del "$UE_NET" via "$UE_PC" 2>/dev/null || true
iptables -nvL DOCKER-USER
echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward)"
