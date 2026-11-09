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
 live pages :8090 (factory closed loop, architecture)        live agent (telemetry, health)
```

## Headline results

| What | Result |
|---|---|
| NR over the boards | about 44 kbit/s up and 90 kbit/s down clean (2 UEs); about 94 slots/s, set by the over-the-air exchange |
| Bluetooth LE non-3GPP access | n3iwue1/2 registered and carrying traffic through the N3IWF; 20–30 kbit/s clean per link, about 32 kbit/s at saturation |
| Factory demo | closed loop live: LiDAR and AGV reports uplink over NR, stop/go commands downlink, camera video to operators over Bluetooth |
| hci_bb board hang under uplink load | **root cause found and fixed** (v15): a BusFault in the radio-core interrupt handler while the radio domain slept |

Full numbers: [docs/RESULTS.md](docs/RESULTS.md).

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
| `results/` | raw results: stress test, factory runs, hci_bb load tests | |
| `docs/` | reproduction guide, hardware, results, history, the published pages | |

## History

The commits follow the order in which the work was done, from 30 Oct to 9 Nov 2026. See [docs/HISTORY.md](docs/HISTORY.md),
including how the history was put together.
