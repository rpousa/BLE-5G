#!/bin/bash
# every 30 s: time, then per sidelink nr-uesoftmodem: pid rss_kB vsz_kB threads
while true; do
  printf "%s" "$(date +%s)"
  for p in $(pgrep -f "nr-uesoftmodem -O .*NR-SIDELINK"); do
    printf " %s:%s:%s:%s" $p $(awk "/VmRSS/{r=\$2}/VmSize/{v=\$2}/Threads/{t=\$2}END{print r, v, t}" /proc/$p/status 2>/dev/null)
  done
  printf " free_mb=%s\n" "$(free -m | awk "/Mem:/{print \$7}")"
  sleep 30
done
