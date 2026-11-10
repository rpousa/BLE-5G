# Hardware and wiring

## Boards

| Role | Firmware | Serial (ours) | USB on | LED |
|---|---|---|---|---|
| gNB board | nrlink v4 | 3TJ2B15JGASV3 | core PC | RGB = class of the UE served this slot (eMBB blue, URLLC red); LED1 on during uplink |
| UE1 board (nrUE1, eMBB) | nrlink v4 | 2HOKMMVEX1WMP | UE PC | blue, 1 blink when served |
| UE2 board (nrUE2, URLLC) | nrlink v4 | GBAMNH4BQ3CSB | UE PC | red, 2 blinks when served |
| BLE1 board (n3iwue1) | hci_bb v15 | 3VWLILYT2A1AK | MCU-Link on the UE PC, HCI via Pico e661640843676727 on the UE PC | green = RTS (on while it can take bytes) |
| BLE2 board (n3iwue2) | hci_bb v15 | GLNAOWUSDREB4 | MCU-Link on the core PC, HCI via Pico e6616408439b6727 on the UE PC | green = RTS |

The nrlink boards talk to their PC over the MCU-Link VCOM, using SLIP at 1 Mbaud.

The BLE boards' HCI (H4, 1 Mbaud) runs on LPUART0, which has hardware flow control. A Raspberry Pi Pico bridges it to USB.
The MCU-Link USB-C cable stays connected. It powers the board and carries flashing and the `isp` recovery command on the VCOM.

## Pico ↔ FRDM-MCXW72 (one Pico per BLE board)

| Pico pin | Board header | MCU pin | Function | Carries |
|---|---|---|---|---|
| 1 · GP0 UART0 TX | J1-1 | PTA16 | LPUART0_RX (Alt6) | host → board |
| 2 · GP1 UART0 RX | J1-2 | PTA17 | LPUART0_TX (Alt6) | board → host |
| 3 · GND | J2-4 | – | ground | |
| 4 · GP2 UART0 CTS | J1-5 | PTA19 | LPUART0_RTS_b (Alt3) | the board pauses the Pico |
| 5 · GP3 UART0 RTS | J2-9 | PTA18 | LPUART0_CTS_b | optional: hci_bb v11+ turns its CTS input off |
| 3V3 / VSYS / VBUS | not connected | | | each side is powered from its own USB |

- Both sides are 3.3 V, so no level shifting is needed. The board needs no rework: no cuts, no solder bridges.
- PTA19 also drives the green LED. The LED is on while the board can take bytes and flickers off when it asks the Pico to pause.
- The header pin numbers follow the FRDM-MCXW72 Quick Start Guide's tables. J1-1, J1-2 and J1-5 were confirmed in use.
- Check your wiring with `nxp-tools/pico/gp2.py`, which reads GP2 with pull-up and pull-down, and with a burst of long HCI commands (`pico/burst2.py`).
  A GP2 wire on the wrong pin shows up as every HCI command longer than about 12 bytes being lost.

## Firmware facts that matter for the wiring

- The common bare-metal `main.c` calls `PLATFORM_InitLcl()` after the board's pin setup. That call switches PTA18–20 to the radio's debug outputs (RF_GPO, Alt6), which silently takes RTS off J1-5.
  hci_bb v11 and later call `BOARD_InitPinLPUART0_RTS()` again on the first pass of the main loop.
- LPUART0 MODIR: receive RTS on (RTSWATER 4), transmit CTS off.
- The VCOM (LPUART1, 115200 baud) only listens for the text `isp`. It answers `ISP` and jumps to the ROM bootloader, so `board.sh flash` works without pressing SW3.

## Bluetooth access point (core PC)

The core PC uses its built-in adapter, an Intel 8265 (Bluetooth 4.2) at hci0. Its LE advertising is connectable on 2402/2426/2480 MHz.
Each UE board connects to it with an L2CAP CoC on PSM 0x0081, at LE 1M. The link opens at a 45 ms connection interval, and
`ble_ip_link.py` on the UE then asks for 15 ms on BLE1 and 18.75 ms on BLE2 (HCI LE Connection Update, after every connect;
the intervals must differ, see RESULTS.md 7b).
The adapter is the limit on the air side: it has no LE 2M PHY and no data length extension, so link-layer packets stay at 27 B
(the hci_bb boards support both: LE features 0xFF 0x79, max data length 251 B).
