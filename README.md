# BLE-5G: a 5G standalone network whose radio links are NXP FRDM-MCXW72 boards

This repository is the complete work of the **nrlink** project: a 5G SA network (OpenAirInterface RAN,
free5GC core) in which no 5G radio is used. Every air interface is a 2.4 GHz link between NXP FRDM-MCXW72 boards:

1. **3GPP access over GenFSK ("nrlink").** OAI's gNB and nrUEs run unchanged from MAC upward. Their L1 is
   emulated at the FAPI boundary, and each slot's MIB, DCIs and transport blocks travel over the air between
   a gNB board and one board per UE. The boards run our own SLIP-framed GenFSK modem firmware at 1 Mbit/s.
2. **Untrusted non-3GPP access over Bluetooth LE.** Two non-3GPP UEs (free5GC `n3iwue`, IKEv2/EAP-5G, GRE in IPsec)
   reach the core's **N3IWF** over an IPv4 link carried in a Bluetooth LE L2CAP connection-oriented channel.
   On the UE side the Bluetooth controllers are FRDM-MCXW72 boards running NXP's `hci_bb` controller firmware
   (heavily modified here). On the core side the access point is the PC's own Bluetooth adapter.

Everything is split over two PCs, as a core site and a UE site:

```
 core PC (claudepc, 192.168.10.223)                         UE PC (claudepc2, 192.168.10.232)
 ───────────────────────────────────                        ─────────────────────────────────
 free5GC v4.3.0 + N3IWF + UPF + ext-dn                       nrUE1 eMBB  ─ ue_bridge ─ USB ─ UE1 board ┐
 OAI VNF (MAC..RRC) ─nFAPI─ OAI PNF ─ gnb_bridge ─ USB ─ gNB board ~~~~~~ GenFSK 2402 MHz ~~~~~~~~~~~~┤
                                                             nrUE2 URLLC ─ ue_bridge ─ USB ─ UE2 board ┘
 N3IWF ─ ble_ip_link --listen ─ Intel hci0 ~~~~~~ Bluetooth LE ~~~~~~ BLE1 board ─ Pico ─ USB ─ btattach ─ ble_ip_link ─ n3iwue1
                                           ~~~~~~ Bluetooth LE ~~~~~~ BLE2 board ─ Pico ─ USB ─ btattach ─ ble_ip_link ─ n3iwue2
 live pages :8090 (AGV testbed or factory, architecture)    live agent (telemetry, health), AGV simulation
```

## Headline results

| What | Result |
|---|---|
| NR over the boards | about 44 kbit/s up and 90 kbit/s down clean (2 UEs, 9 Nov). Since the 11 Nov tuning (poll only live UEs, uplink pre-grant, `min_rxtxtime` 4, dynamic slotting) about 240 slots/s, was 94; the slot rate is set by the over-the-air exchange |
| Bluetooth LE non-3GPP access | n3iwue1/2 registered and carrying traffic through the N3IWF; since 10 Nov at 15 / 18.75 ms connection intervals: 30–40 kbit/s up and 60–80 down clean per link (was 30 / 30 at 45 ms) |
| NR against Bluetooth, same source and sink | NR 40 up / 80 down clean, about 500 ms idle RTT; Bluetooth 30–40 up / 60–80 down clean, 76–94 ms idle RTT (2M and 251 B packets: not supported by the core's Intel 8265) |
| AGV testbed, the same data over NR and Bluetooth at once (11 Nov) | nothing lost at any load; with the tuned NR, LiDAR scans (752 B) arrive first over NR (142 against 172 ms median), detections (≤ 80 B) first over Bluetooth (50 against 59–74 ms); no coexistence penalty |
| Factory demo | closed loop live: LiDAR and AGV reports uplink over NR, stop/go commands downlink, camera video to operators over Bluetooth (all frames through at Trials 1 and 2 since 10 Nov) |
| hci_bb board hang under uplink load | **root cause found and fixed** (v15): a BusFault in the radio-core interrupt handler while the radio domain slept |

Full numbers: [docs/RESULTS.md](docs/RESULTS.md). Published pages (HTML copies in `docs/pages/`): the two-PC split
with its live architecture, the AGV testbed replay, the NR tuning steps, the factory floor, and the Pico wiring.

## How to reproduce

[docs/REPRODUCE.md](docs/REPRODUCE.md) walks through hardware, building every piece, wiring, starting
the two PCs and running each experiment. The wiring of the Pico bridges is in [docs/HARDWARE.md](docs/HARDWARE.md).

## Layout

| Path | What | Installed on the PCs as |
|---|---|---|
| `firmware/sdk/` | our files in the NXP MCUXpresso SDK v26.09.00 tree (new examples `genfsk/nrlink`, `isp_agent`, `led_green`; modified `hci_bb`, OSA, `fsl_common_arm.h`) | copied over `~/nxp/mcuxsdk/mcuxsdk/` |
| `firmware/patches/` | the same SDK changes as patches against upstream | |
| `firmware/images/` | prebuilt images, with SHA256SUMS | `~/nxp/images/` |
| `firmware/build.sh` | applies the overlay and builds nrlink / hci_bb | |
| `oai/` | patch for OpenAirInterface (develop, v3.0.0, Oct 2026) and its build script | `~/oai/openairinterface5g-develop/` |
| `nrlink/` | host side: board bridges, BLE IP link, orchestration (`nrlink.sh`), tests, demo apps, live pages | `~/oai/nrlink/` |
| `deploy/` | docker compose files and configs: OAI RAN, free5GC + N3IWF, n3iwues | `~/oai/deploy/` |
| `nxp-tools/` | board tools (flash, reset, HCI probe) and the Raspberry Pi Pico bridge (`pico/`) | `~/nxp/` |
| `free5gc/` | gtp5g kernel module install script | `~/free5gc/` |
| `results/` | raw results: stress test, factory runs, NR against Bluetooth (`compare/`), AGV testbed (`agv/`), NR tuning (`nr_tuning/`), hci_bb load tests | |
| `docs/` | reproduction guide, hardware, results, history, the published pages | |

## Where the code is

| Piece | Source in this repository | Runs as |
|---|---|---|
| gNB board firmware (and the UE1/UE2 boards: the same image) | `firmware/sdk/examples/wireless_examples/genfsk/nrlink/nrlink.c` (+ board files in `firmware/sdk/examples/_boards/frdmmcxw72/wireless_examples/genfsk/nrlink/`) | `firmware/images/nrlink_v4.bin`, flashed on 3TJ2B15JGASV3 (gNB), 2HOKMMVEX1WMP (UE1), GBAMNH4BQ3CSB (UE2) |
| BLE1 / BLE2 board firmware | `firmware/sdk/examples/wireless_examples/ble_controller/hci_bb/hci_bb.c` (+ `_boards/frdmmcxw72/.../hci_bb/`, and the OSA / `fsl_common_arm.h` changes); as one patch: `firmware/patches/hci_bb.patch` | `firmware/images/hci_bb_isp_v15r.bin` (+ `.syms`), flashed on 3VWLILYT2A1AK (BLE1) and GLNAOWUSDREB4 (BLE2) |
| `gnb_bridge.py` | `nrlink/gnb_bridge.py`, with `nrlink/nrlink.py` (board protocol, air framing) and `nrlink/config.py` (board roles) | core PC, `~/oai/nrlink/gnb_bridge.py --ues 1,2 --skip-empty`, started by `nrlink.sh up --role core` |
| `ue_bridge.py` | `nrlink/ue_bridge.py` | UE PC, one per nrUE |
| `ble_ip_link.py` | `nrlink/ble_ip_link.py` | core PC `--listen --hci 0` (the access point); UE PC `--connect <core address> --local <board address> --index 1` / `--index 2`, one per BLE board, started by `ble_watchdog.sh` |
| OAI side of nrlink (PNF hook, nrUE L1 emulation) | `oai/nrlink-oai.patch` (`nfapi/oai_integration/nrlink_gnb.c`, `executables/nrlink_ue.c`, …) | built into `nr-softmodem` / `nr-uesoftmodem` |

Build the firmware with `firmware/build.sh nrlink` or `firmware/build.sh hci_bb` (docs/REPRODUCE.md, section 4).

## History

The commits follow the order in which the work was done, from 30 Oct to 11 Nov 2026. See [docs/HISTORY.md](docs/HISTORY.md),
including how the history was put together.
