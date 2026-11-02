#!/bin/sh
# Kernel modules for the free5GC core (run once with sudo; they are reloaded at every boot afterwards).
#   gtp5g          free5GC UPF data plane (built from ~/free5gc/gtp5g v0.10.2 for this kernel;
#                  free5GC v4.3.0's UPF needs 0.9.3 <= version < 0.10.3)
#   xfrm_*, esp4   IPsec tunnels between the N3IWF and non-3GPP UEs
#   ip_gre         GRE tunnels the N3IWF and n3iwue run inside IPsec for user data
set -e
KVER=$(uname -r)
KO=/home/debian/free5gc/gtp5g/gtp5g.ko
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ "$(modinfo -F vermagic "$KO" | cut -d' ' -f1)" = "$KVER" ] || { echo "gtp5g.ko was built for another kernel"; exit 1; }
grep -q '^gtp5g ' /proc/modules && rmmod gtp5g   # replace an older/newer build (stop the free5GC UPF first)
install -D -m 644 "$KO" "/lib/modules/$KVER/extra/gtp5g.ko"
depmod -a "$KVER"
modprobe -a gtp5g xfrm_user xfrm_interface esp4 ip_gre
printf '%s\n' gtp5g xfrm_user xfrm_interface esp4 ip_gre > /etc/modules-load.d/free5gc.conf
lsmod | grep -E '^(gtp5g|xfrm_interface|esp4|ip_gre)'
echo "ok: modules loaded, and listed in /etc/modules-load.d/free5gc.conf"
