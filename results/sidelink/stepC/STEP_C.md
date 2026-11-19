# Sidelink step C: mode 1 and a UE-to-network relay, over the nrlink boards (11-12 Nov 2026)

The remote UE has no Uu link. It reaches the 5G core through the relay UE over the sidelink (PC5); the relay UE is
connected to the gNB (Uu), runs sidelink mode 1 with the configured grant (CG type 1) it gets from the gNB, and carries
the remote UE's traffic in its own PDU session (SRAP, U2N relay of OAI release sl-release-2025w49).

```
remote UE --PC5 (SRAP)--> relay UE --Uu (DRB 2)--> gNB --N3 (relay's GTP tunnel)--> UPF --> ext-dn
  sl-remote                 sl-relay                 sl-gnb                          step-C free5GC
  mode 2, no Uu             mode 1, sync ref         --relay-type 1
```

## Setup (all on the UE PC)

| piece | where | notes |
|---|---|---|
| core | `stepc/docker-compose.yaml`, project `stepc-core` | free5GC v4.3.0 with the testbed's configs (no N3IWF); the live core on the core PC is untouched |
| relay subscriber | `relay_subscriber.py` | IMSI 208950000000039, nrUE key/OPc, static PDU address 12.1.1.200 |
| gNB | `stepc/gnb_start.sh`, `gnb_sl_relay.conf` | release gNB, rfsim server, PLMN 208/95, TAC 0xa000, slice 1/010203, PSFCH and CSI off in the SL pools |
| UEs | `sl_relay_start.sh` | relay: mode 1, sync ref, `--relay-type 1 --is-relay-ue 1`; remote: mode 2, `--relay-type 1` |
| sidelink air | `sl_relay_air.sh` | `AIR=udp` (bridges over UDP) or `AIR=board` (UE1 master / relay side, UE2 peer / remote side, channel 0x5A) |
| test | `sl_relay_measure.sh` | remote UE 12.1.1.200 <-> ext-dn 192.168.70.132: ping, UDP, TCP |

The step-C core runs on the UE PC because the core PC (2 cores) already carries the live gNB, and rfsim between the PCs
would stream about 2 Gbit/s of IQ samples over the 1 Gbit/s LAN.

**Addressing.** The release's gNB sends a downlink packet on the relay DRB when its destination ends in .100, and its UPF
patch makes the OAI UPF accept the remote's address. Instead, the remote UE uses the relay's own PDU address
(12.1.1.200): free5GC needs no patch, and the gNB patch below names that address (`NRLINK_REMOTE_IP`).

## Changes to the release (`sl-tree-stepC.patch`, on top of step B's `nrlink_sl.c`)

| file | change | why |
|---|---|---|
| `executables/nr-ue.c` | in mode 1, start `nrlink_sl_thread` when `NRLINK_SL` is set | the sidelink over the boards; Uu stays on rfsim |
| `nr_sdap_entity.c` (gNB) | `NRLINK_REMOTE_IP` picks the relay DRB by address | the ".100" rule and the UPF patch are not needed |
| `nr_nas_msg_sim.c` | full Registration Request with 5GMM capability in Security Mode Complete | free5GC: "Capability5GMM is nil", Registration Reject |
| `nr_nas_msg_sim.c` | PDU session request: security header type 2, one uplink NAS COUNT | free5GC: "wrong security header type", then "MAC verification failed" |
| `rrc_UE.c` | SI validity timers once per frame, not every slot | `nr_rrc_SI_timers` counts 10 ms per call but was called every slot, so SIB1 was freed after 9 minutes instead of 3 hours; the MAC's BWP still pointed into it (segfault in `get_pucch_tx_power_ue`). This is in the release itself |
| `rrc_UE.c` | ignore SI while connected | re-decoding SIB1 while connected asserted on the idle sub-state |
| `nrlink_sl.c` | in mode 1, leave the RRC timer trigger to the Uu loop | the timers ran twice |

## Problems found on the way

- The release's rfsim uses `serveraddrsl=server` for the whole device, so the relay (client to the gNB) must be the sidelink
  client and the remote the sidelink server, as in the README.
- With both Uu and sidelink on rfsim in one relay process, the free-running sidelink pair and the lock-step Uu link stall
  each other. With the sidelink on `nrlink_sl.c` (real-time paced) Uu runs alone on rfsim.
- My `--thread-pool` options were too small: the gNB with 2 workers and the UEs with 4 deadlock under load (DLSCH/LDPC
  sub-jobs queued to a pool whose workers all wait for them). The defaults (gNB and UE) fix it.
- Docker's port proxy in the rfsim path stalled the start; the relay now has a second interface on the gNB's network.

## Results

Remote UE 12.1.1.200 <-> ext-dn 192.168.70.132, through the relay. Pings are min / avg / max.

| test | sidelink over the boards | sidelink over UDP (no boards) |
|---|---|---|
| ping 56 B | 0 % loss, 77 / 81 / 86 ms | 0 % loss, 16 / 21 / 26 ms |
| ping 500 B | 0 % loss, 90 / 97 / 107 ms | 0 % loss, 17 / 25 / 35 ms |
| ping 1400 B | 0 % loss, 107 / 115 / 126 ms | 0 % loss, 19 / 26 / 35 ms |
| UDP up (remote -> network) | 100 / 200 / 300 kbit/s all delivered; 400 -> 316 | 3 / 6 Mbit/s all delivered; 10 -> 6.18 |
| UDP down (network -> remote) | 100 delivered; 200 / 300 / 400 -> 171 / 172 / 173 | 3 / 6 Mbit/s all delivered; 10 -> 5.93 |
| TCP up / down, 30 s | 171 / 175 kbit/s | 6.15 / 5.49 Mbit/s |
| ping 56 B under TCP up | 17 % loss, avg 205 ms | 0 % loss, avg 1.45 s (queue) |

Against step B (two UEs, no network): the relay hop adds Uu, gNB and core, about 50 ms more round trip over the boards.
Uplink keeps step B's ceiling (about 316 kbit/s). Downlink, where the relay transmits in mode 1, stops at about
172 kbit/s although the bridge then moves about 350 kbit/s of items. The relay sends one transport block per
configured-grant occasion at the grant's size, so partly empty blocks still cross the air nearly whole; mode 2 (the
remote's uplink) sizes each block to its data. This is the likely cause and has not been checked block by block.

The board run held for 14 minutes after the fixes below (the earlier runs died at about 9).

## Afterwards

UE1 and UE2 went back to the testbed (ue_bridge 1/2) after each board run. nrUE1 and nrUE2 exited each time their
bridges came back (139, `pthread_join` assert) and were restarted (last: 12.1.1.15 and 12.1.1.16); agv-fwd-nr1/2 restarted.
The step-C core (`docker compose -p stepc-core`) is left running on the UE PC, idle; `docker compose -p stepc-core down`
in `stepc/` stops it.

Rerun: `bash stepc/gnb_start.sh`, then `AIR=udp bash sl_relay_air.sh` (or `AIR=board` after stopping ue_bridge 1/2),
then `sl_relay_measure.sh` with the sudo password on stdin.
