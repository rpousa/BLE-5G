# History

The repository's commits follow the steps of the work in order. Each firmware version, script and page is committed at the step where it changed.

## How this history was put together

The work happened directly on the two PCs, and this repository was assembled from them afterwards, on 9 Nov 2026.
Author dates are the days the steps happened; commit dates show when the repository was assembled.

- **Firmware (hci_bb v1–v15)** is exact. Every version's SDK files were rebuilt from upstream SDK v26.09.00 plus that version's saved patch or source snapshot.
  The result for v15 is byte-identical to the tree the v15 image was built from. The images are the ones actually flashed.
- **Host scripts** use the snapshots that were kept at each change (`.orig`, `pre-merge/`, `.pre-*` copies on both PCs).
  Where no snapshot existed, a file appears at the step that introduced it, in the oldest form available.
- **Load test and watchdog for v12–v15** were rebuilt by reversing the recorded edits. The reconstructed v13 watchdog, once fixed, is byte-identical to the deployed one.
- **The nrlink firmware and the OAI patch** appear in their final form (nrlink v4; OAI patch with the 2 Nov SSB fix). The intermediate versions were not kept.
- The coordination notes in `docs/notes/` are the final versions of the hand-off and status files shared between the two PCs' sessions.

## Steps

| Date | Step |
|---|---|
| 2026-10-30 | Add nrlink board firmware and remote board tools |
| 2026-10-30 | OAI: emulate the NR L1 over the nrlink boards (PNF and nrUE) |
| 2026-10-30 | Host side: board bridges, discovery, nrlink.sh and the dashboard |
| 2026-11-02 | free5GC v4.3.0 with the N3IWF as the default core |
| 2026-11-04 | Split across two PCs: core site and UE site |
| 2026-11-04 | hci_bb v1: FRDM-MCXW72 as a Bluetooth LE controller, and the BLE IP link |
| 2026-11-04 | hci_bb v2: 115200 baud |
| 2026-11-05 | n3iwue1/2 over Bluetooth LE end to end |
| 2026-11-05 | Stress test of the four UEs, and a watchdog for the BLE boards |
| 2026-11-05 | hci_bb v3: 4 KB UART ring and H4 resync |
| 2026-11-05 | hci_bb v4: diagnostic reports to the PC (vendor event 0xF7) |
| 2026-11-05 | hci_bb v5: interrupt state, stuck-receive report, report on demand (0xFDB2) |
| 2026-11-05 | hci_bb v6: detect interrupts left masked in the main loop |
| 2026-11-05 | ble_ip_link: keep each tun across reconnects |
| 2026-11-06 | hci_bb v7: 8 KB stack and a stack high-water mark |
| 2026-11-06 | Factory floor downlink test |
| 2026-11-06 | Live closed-loop factory demo and live architecture page |
| 2026-11-06 | Live factory page: step-by-step view per level and flow |
| 2026-11-06 | hci_bb v8: steady green LED on the BLE boards |
| 2026-11-06 | hci_bb v9: HCI on LPUART0 with RTS/CTS, for a Raspberry Pi Pico bridge |
| 2026-11-09 | hci_bb v10 and the Pico HCI bridge |
| 2026-11-09 | hci_bb v11: put PTA19 back to LPUART0_RTS_b after PLATFORM_InitLcl() |
| 2026-11-09 | Pico bridge v2 and the BLE1 uplink load test |
| 2026-11-09 | hci_bb v12: release build, every other interrupt below the tick |
| 2026-11-09 | hci_bb v13: breadcrumbs that survive a reset, and a hardware watchdog |
| 2026-11-09 | Watchdog and load test: decide only on a real Read BD_ADDR reply |
| 2026-11-09 | hci_bb v14: record faults and what each tick interrupted |
| 2026-11-09 | hci_bb v15: keep the radio core's power domain awake (fixes the hang) |
| 2026-11-09 | Docs: how to reproduce the experiment, hardware, results, history; the published pages |
