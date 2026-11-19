# nrlink Pico bridge

USB CDC <-> UART0 at 1 Mbaud, no flow control, for an FRDM-MCXW72 running nrlink v6 (host link also on LPUART0).

Wiring: Pico GP0 TX -> J1-1 (PTA16), GP1 RX <- J1-2 (PTA17), GND -> J2-4. GP2/GP3 stay inputs: on nrlink, PTA19 is
the green LED.

Build: `PICO_SDK_PATH=~/pico/pico-sdk PICO_TOOLCHAIN_PATH=<arm-gnu-toolchain-14.2> cmake -B build -DPICO_BOARD=pico && make -C build`
(pico-sdk 2.1.1). Flash: open the port at 1200 baud and close it (the Pico enters BOOTSEL), then copy
`nrlink_bridge.uf2` to the RPI-RP2 drive. The port shows up as `/dev/serial/by-id/usb-Raspberry_Pi_Pico_<ID>-if00`;
`nrlink/pico_ports.json` maps the board serial to that ID and `nrlink.py port_of()` prefers it.

Measured on GLNAOWUSDREB4 (19 Nov 2026): PING 0.39 ms p50 (VCOM 0.28), 255 B TX to TXDONE 8.2 ms (VCOM 8.0),
0 of 1000 failed. The MicroPython bridge (pico/main.py) took 53 ms for the same frame: it reads USB a byte at a time.
