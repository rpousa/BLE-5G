# Core PC status (claudepc2, 192.168.10.232)

Last update: 2026-11-04, core PC Claude session.

## Summary
| Task | State |
|---|---|
| 6.1 root setup (docker, headers, bluez, groups) | **done** by the user: docker 26.1.5, compose 2.26.1, headers, gcc, pahole; `debian` in sudo, docker, dialout (sudo still asks for a password) |
| 6.2 load images, pull free5GC | **done**: nrlink-run:local, ran-base:local, free5GC v4.3.0 images, n3iwue:latest, mongo 4.4, trf-gen-cn5g |
| 6.3 gtp5g build + install | **done**: built v0.10.2 with BTF (plain `make`), installed by the user. `/sys/module/gtp5g/version` = 0.10.2; xfrm_interface, esp4, ip_gre loaded. (`install_modules.sh` wasn't executable; fixed with chmod +x.) |
| 6.4 gNB board check | **done**: `3TJ2B15JGASV3` on ttyACM0 answers `nrlink v4`. No flash needed, nothing written to the board. |
| 6.4 `--role core` in nrlink.sh | **done and running**: `nrlink.sh up --role core` passed 7/7 (2026-11-04 23:16 UTC). AMF: NG-Setup response to the VNF 192.168.70.140 (gNB name is `gNB-Eurecom-5GNRBox` from the shared vnf.conf; `gnb-nrlink` in the task file isn't set anywhere, so I left the name unchanged). PNF got PNF_START and runs ~199 slots/s through the gNB board with `rnti->ue {}`, i.e. idle and waiting for UEs. |
| 6.4 telemetry override | **done**: `NRLINK_TELEMETRY=host:port`, role core defaults to 192.168.10.223:8091 |
| 6.5 Bluetooth AP | no adapter yet (`/sys/class/bluetooth` missing); user hasn't chosen (a) dongle or (b) hci_bb board. `ble_ip_link.py` not here yet. |
| 6.6 routing | **applied** by the user: `sudo core_routing.sh --lan`. ip_forward=1; DOCKER-USER accepts ble+ <-> demo-oai and 192.168.10.223 over enp2s0 <-> demo-oai. **The UE PC can start the interim LAN n3iwue test now.** |
| IMSIs 041/042 | **added and provisioned** in free5GC (webconsole), same K/OPc as 040 |

## Subscribers (f5gc_subscribers.py)
- 208950000000036–038: nrUEs, K `0C0A34601D4F07677303652C0462535B`, OPc `63bfa50ee6523365ff14c1f45f88737d`
- 208950000000040: docker n3iwue (reference)
- **208950000000041, 208950000000042**: BLE n3iwue1/n3iwue2 on the UE PC. Same as 040: K `b73a90cbcf3afb622dba83c58a8415df`, OPc `df0c67868fa25f748b7044c6e7c245b8`, SQN 16f3b3f70fc2, slice 1/010203, DNN internet.
  → The UE PC n3iwue configs need these IMSIs (MSIN 0000000041 / 0000000042).

## Changes to shared files (for the UE PC to merge)
Originals are saved next to each file as `*.orig`; `diff -u X.orig X` shows each change.

1. **nrlink.py**: `TELEMETRY_ADDR` comes from env `NRLINK_TELEMETRY=host:port`. Default unchanged (127.0.0.1:8091).
2. **boards.py**: new `--need gnb,ue1,...`. Only those roles are assigned, required by `--check`, and written to boards.json. Default is all four roles, as before. On the core PC, boards.json now has just `{"gnb": "3TJ2B15JGASV3"}`.
3. **f5gc_subscribers.py**: IMSIs 041 and 042 added.
4. **nrlink.sh**:
   - Argument parsing is now a loop, so flags work in any order: `--core X`, `--role X`, `--keep-core`, plus a positional serial for `flash`. `down --keep-core` and `restart` behave as before (`KEEP_CORE` variable instead of `$1`).
   - New `ROLE` (default `all`). `--role ue` currently exits with "implemented on the UE PC's copy". When merging, drop that line and add your ue branch.
   - `up --role core`:
     - 7 steps: checks (`boards.py --check --need gnb`); core_free5gc unchanged; gnb_bridge only; VNF; PNF; docker n3iwue; then the new `ble_ap()`.
     - No nrUEs, no ue bridges, no dashboard.
     - Exports `NRLINK_TELEMETRY` (default `192.168.10.223:8091`).
   - `ble_ap()`:
     - warns if ip_forward is off;
     - skips with a message if there's no `hci*` adapter or no `ble_ip_link.py`;
     - with passwordless sudo it runs `btmgmt le on` / `advertising on` and starts `sudo $PY -u ble_ip_link.py --listen` (log `logs/ble_link.log`), otherwise it prints the commands.
   - `down --role core` also stops the L2CAP listener (`sudo pkill -f "[b]le_ip_link.py --listen"`).
5. **New: `core_routing.sh`** (core PC only, run as root, idempotent): `ip_forward=1` plus the DOCKER-USER rules for `ble+ <-> demo-oai`. `--lan` adds the interim UE PC 192.168.10.223 over enp2s0 rules (both directions); `--off` removes them all. The rules don't persist across reboots.

## Notes / for the UE PC
- `ble_ip_link.py`: nrlink.sh calls it as `ble_ip_link.py --listen` with no other arguments and expects it to pick the adapter itself. Tell me here if it needs e.g. `--hci N`.
- Debian 13 has docker.io 26.1.5 and docker-compose 2.26.1 (that one provides the `docker compose` plugin), so Docker's own repo shouldn't be needed. Docker ≥28 would block routing to container IPs from other interfaces; 26.x doesn't.
- Interim LAN test: **ready**. Core up, LAN rules in place. UE PC: `sudo ip route add 192.168.70.139/32 via 192.168.10.232`, then run an n3iwue with IMSI 041 or 042 against N3IWF 192.168.70.139.
- Reference docker n3iwue (IMSI 040) on the core PC: registered over non-3GPP access, PDU session 12.1.1.1 on gretun-id-2-1, `ping -I vrf-pdu-1 192.168.70.132` 3/3, rtt avg 0.62 ms.
- 3GPP: gNB waits for the UE PC nrUEs over the air (channel 0x2A). Its telemetry goes to 192.168.10.223:8091.

## Open items
- 6.5 Bluetooth adapter: the user still has to choose (a) a USB dongle or (b) a spare FRDM-MCXW72 with hci_bb. `ble_ip_link.py` from the UE PC session is still missing. Once both are here, `nrlink.sh up --role core` starts the listener, or prints the sudo commands for it.
- The firewall rules from core_routing.sh don't survive a reboot: run `sudo ~/oai/nrlink/core_routing.sh --lan` again after each one.
