# Reproducing the experiment

This guide rebuilds the setup from scratch: two Linux PCs, five FRDM-MCXW72 boards and two Raspberry Pi Picos.
The scripts use absolute paths under `/home/debian`. With another user name, either use the same paths
(a `debian` user) or search and replace `/home/debian` in `nrlink/`, `nxp-tools/` and `free5gc/`.

Addresses used throughout (change them in the places listed in step 9 if yours differ):

| | core PC | UE PC |
|---|---|---|
| name | claudepc | claudepc2 |
| LAN address | 192.168.10.223 | 192.168.10.232 |
| role | free5GC + N3IWF, OAI VNF/PNF, gNB board, Bluetooth access point (its own adapter, hci0) | nrUE1/2 with their boards, n3iwue1/2 with the BLE boards and Picos |

## 1. Hardware

| Item | Qty | Use |
|---|---|---|
| PC with Debian 13 (kernel 6.12), Docker, and a Bluetooth LE adapter on the core PC (we used an Intel 8265, BlueZ hci0) | 2 | core PC, UE PC |
| NXP FRDM-MCXW72 | 5 | gNB board, UE1 and UE2 boards (nrlink firmware), BLE1 and BLE2 boards (hci_bb firmware) |
| Raspberry Pi Pico (H) with MicroPython 1.24.1 | 2 | HCI bridge with RTS flow control for BLE1 and BLE2 |
| female-to-male jumper wires | 8 | Pico ↔ board (4 per board; a fifth, J2-9, is optional) |
| USB cables (USB-C for the boards, micro-USB data cables for the Picos) | 7 | |

Where the cables go:
- core PC: the gNB board, and (in our setup) BLE2's MCU-Link USB-C. BLE2's power and flashing therefore come from the core PC.
- UE PC: the UE1 and UE2 boards, BLE1's MCU-Link USB-C, and both Picos.

## 2. Software on both PCs

```sh
sudo apt install docker.io docker-compose-plugin bluez iperf3 git python3-venv build-essential cmake ninja-build
sudo usermod -aG docker,dialout $USER
python3 -m venv ~/nxp/venv
~/nxp/venv/bin/pip install pyserial pyocd spsdk west pyyaml pillow   # blhost comes with spsdk
```

Arm GNU toolchain (we used 14.2.rel1) unpacked under `~/nxp/toolchain/<version>/`. `firmware/build.sh` takes the first `bin/` it finds there.

## 3. Install the repository's files where the scripts expect them

```sh
git clone https://github.com/rpousa/BLE-5G.git ~/BLE-5G
mkdir -p ~/oai ~/nxp/images ~/free5gc
ln -s ~/BLE-5G/nrlink ~/oai/nrlink
ln -s ~/BLE-5G/deploy ~/oai/deploy
for f in ~/BLE-5G/nxp-tools/*; do ln -s "$f" ~/nxp/; done
cp ~/BLE-5G/firmware/images/* ~/nxp/images/
cp ~/BLE-5G/free5gc/install_modules.sh ~/free5gc/
mkdir -p ~/oai/nrlink/logs
```

## 4. Firmware

Use the prebuilt images in `firmware/images/`, or build them yourself.

### 4.1 Build

```sh
cd ~/nxp && west init -m https://github.com/nxp-mcuxpresso/mcuxsdk-manifests.git --mr v26.09.00 mcuxsdk
cd mcuxsdk && west update
~/BLE-5G/firmware/build.sh nrlink         # -> ~/nxp/mcuxsdk/build_nrlink/nrlink_bm.bin
~/BLE-5G/firmware/build.sh hci_bb         # release build (-Os), -> build_hcibb_rel/hci_bb_bm.bin
```

`build.sh` copies `firmware/sdk/` over the SDK tree first, so it changes these upstream files:
`hci_bb.c`, hci_bb's `app_preinclude.h` and `bm/CMakeLists.txt`, `board_debug_nbu.c`, `fsl_os_abstraction_bm.c` and `fsl_common_arm.h`.
It also adds the `nrlink`, `isp_agent` and `led_green` examples. The same changes, as diffs, are in `firmware/patches/`.

### 4.2 Flash

The first time, a board has no firmware of ours and must enter the ROM bootloader by hand: hold SW3, press reset, then release SW3.
After that, `board.sh` puts it in the bootloader remotely: the firmware handles the `isp` text command on the VCOM, and hci_bb also takes HCI command 0xFDB1.

```sh
~/nxp/board.sh list                          # boards and their serial numbers
~/nxp/board.sh isp    <serial>               # into the ROM bootloader (skip if you used SW3)
~/nxp/board.sh backup <serial>               # 2 MB flash -> ~/nxp/backups/
~/nxp/board.sh flash  <serial> ~/nxp/images/nrlink_v4.bin          # gNB, UE1, UE2 boards
~/nxp/board.sh flash  <serial> ~/nxp/images/hci_bb_isp_v15r.bin    # BLE1, BLE2 boards
```

Flash each board from the PC its MCU-Link is plugged into. `~/oai/nrlink/nrlink.sh flash <serial>` does backup + flash for nrlink boards.

## 5. OpenAirInterface (core PC builds, both PCs run)

```sh
cd ~/oai
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git openairinterface5g-develop   # develop, v3.0.0 (Oct 2026)
cd openairinterface5g-develop && patch -p1 < ~/BLE-5G/oai/nrlink-oai.patch
docker build -f docker/Dockerfile.base.ubuntu -t ran-base:local .
docker run -d --name oai-dev -v ~/oai/openairinterface5g-develop:/oai-ran ran-base:local sleep infinity
~/BLE-5G/oai/build_dev.sh                                          # -> build_nrlink/ (nr-softmodem, nr-uesoftmodem)
docker build -f ~/oai/deploy/nrlink/Dockerfile.run -t nrlink-run:local ~/oai/deploy/nrlink
```

Copy the build tree and the `nrlink-run:local` image to the UE PC (`docker save nrlink-run:local | ssh <ue-pc> docker load`), at the same path.
The patch does nothing unless the environment variables `NRLINK_GNB` / `NRLINK_UE` are set, which the compose files do.

## 6. free5GC v4.3.0 with the N3IWF (core PC)

The UPF needs the gtp5g kernel module, version 0.9.3 to 0.10.2:

```sh
git clone -b v0.10.2 https://github.com/free5gc/gtp5g.git ~/free5gc/gtp5g && make -C ~/free5gc/gtp5g
sudo sh ~/free5gc/install_modules.sh       # also loads xfrm/esp4/ip_gre for the N3IWF, and at every boot
```

`deploy/free5gc/` holds our compose file and configuration. The TLS certificates under `deploy/free5gc/cert/` are not in the repository.
Generate them with free5GC's own script (free5gc-compose `cert/` or `make certs`), or copy them from a free5gc-compose checkout.
`nrlink.sh up` starts the core and provisions the subscribers (`f5gc_subscribers.py`): IMSIs 208950000000036–038 for the nrUEs, 040 for the reference n3iwue on the core PC, and 041/042 for n3iwue1/2 over Bluetooth.

## 7. Raspberry Pi Picos (UE PC)

1. Flash MicroPython 1.24.1 on each Pico: hold BOOTSEL, plug it in, copy the `.uf2`.
2. Wire each Pico to its board as in [HARDWARE.md](HARDWARE.md): GP0 → J1-1, GP1 ← J1-2, GND – J2-4, GP2 ← J1-5.
3. Install the bridge: `~/nxp/venv/bin/python -I ~/nxp/pico/mpy_run.py <pico tty> ~/nxp/pico/save_main.py`,
   then `mpy_run.py <pico tty> ~/nxp/pico/reset.py`. The bridge starts 2 s after every power-up.
4. Put each Pico's unique id into the `PICOS` map in `nxp-tools/hci_probe.py`, and into `P=` in `nrlink/ble1_loadtest.sh`.
   The tty is `/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_<id>-if00`.

## 8. Board roles

On each PC, with the bridges stopped, run `~/oai/nrlink/nrlink.sh boards`. It pings every board, finds the nrlink boards and writes `boards.json`.
On the UE PC, add the BLE boards by hand:

```json
{ "ue1": "<UE1 serial>", "ue2": "<UE2 serial>", "ble1": "<BLE1 serial>", "ble2": "<BLE2 serial>" }
```

## 9. Addresses to adapt

- `nrlink/nrlink.sh`: `CORE_PC`, `UE_PC`, `CORE_BT` (the core adapter's public address, from `hciconfig hci0`).
- `nrlink/core_routing.sh`, `nrlink/ue_routing.sh`: the other PC's address.
- `nrlink/apps/factory*.py`: `--core` defaults.
- `nrlink/ble1_loadtest.sh`: `SN`, `ADDR`, `CORE_BT`, `P`, and the address bytes in the Read BD_ADDR check.
- The BLE boards get their addresses from `ble_attach.sh` (02:4E:52:65:B3:A1 and 02:4E:52:77:BF:5D, derived from the serial).

## 10. Start everything

Core PC:

```sh
cd ~/oai/nrlink
sudo ./core_routing.sh                    # forward the Bluetooth tun interfaces (ble+) to the N3IWF
./nrlink.sh up --role core                # free5GC + N3IWF, gnb_bridge, VNF, PNF, Bluetooth access point (ble_ip_link --listen)
```

`nrlink.sh` starts the Bluetooth access point with `sudo -n`. That needs a NOPASSWD sudoers rule for exactly
`~/nxp/venv/bin/python -u ~/oai/nrlink/ble_ip_link.py --listen --hci 0`. Without the rule, it prints the command to run by hand.

UE PC:

```sh
cd ~/oai/nrlink
sudo ./ble_attach.sh                      # BLE1/BLE2 -> Linux adapters (btattach through the Picos), addresses set
./nrlink.sh up --role ue --transport ble  # nrUE1/2 over the GenFSK boards, n3iwue1/2 over Bluetooth
sudo WATCH="1 2" setsid nohup ./ble_watchdog.sh >/dev/null 2>&1 &   # re-attaches a board after a reset, restarts its n3iwue
./nrlink.sh status
```

Check:
- the nrUEs have `oaitun_ue1` addresses 12.1.1.x;
- `docker exec nrlink-n3iwue1 ip addr show gretun-id-2-1` shows a 12.1.1.x address;
- `docker exec nrlink-n3iwue1 ping -I gretun-id-2-1 192.168.70.132` gets replies.

Live pages, from the core PC: `./nrlink.sh live start` on both PCs. Then open http://192.168.10.223:8090/ (factory closed loop) and `/arch` (architecture).
`./nrlink.sh live enable` adds a crontab `@reboot` entry.

Stop with `./nrlink.sh down` (`--keep-core` leaves free5GC running).

## 11. The experiments

### 11.1 Stress test (all four UEs)
```sh
core PC:  ./stress.py servers            # iperf3 servers in ext-dn's netns, ports 5221-5224
UE PC:    ./stress.py run                # per-UE RTT, TCP up/down, UDP sweeps, then all at once -> logs/stress/<time>.json
```

### 11.2 Factory floor, downlink only
```sh
core PC:  apps/factory_dl.py gen --out logs/factory/scen         # renders the scenes for each level
          apps/factory_dl.py clocksrv                            # clock offset service (the PCs have no NTP)
UE PC:    apps/factory_dl.py clock --host 192.168.10.223         # before and after the run
          apps/factory_dl.py recv --port P --dev IF --out FILE --secs S   # one per UE, in its netns
core PC:  apps/factory_dl.py send --scen logs/factory/scen --dst flow=ip ...   # in a sidecar in oai-ext-dn's netns
          apps/factory_report.py RUN_DIR SCEN_DIR OUT_DIR
```

### 11.3 Factory floor, closed loop (live)
This runs as part of `nrlink.sh live start`: `apps/factory2.py edge` on the core, and the robots, LiDAR and agent on the UE PC.
The page switches between Trials 1–3, which are traffic levels. Trial 3 is the maximum (LiDAR 4 Hz, reports 50 Hz, video 2 fps).
To set it without the page: `curl -X POST -d '{"level":"L3"}' http://<core>:8090/level`.
After Trial 2 or 3 the nrUEs keep an uplink backlog in RLC that outlives the demo: run `docker restart oai-nrue1 oai-nrue2`
on the UE PC before any other test.

### 11.3b NR against Bluetooth LE, same source and sink
Stop the live demo on both PCs (`./nrlink.sh live stop`), then on the UE PC:
```
cd ~/oai/nrlink
python3 compare_links.py --ues nrUE1,n3iwue1,nrUE2,n3iwue2 --out logs/compare/run.json
```
It runs the same tests against the same iperf3 servers in ext-dn (started by `stress.py servers` on the core), one UE at a time:
pings at 56/472/1372 B, a UDP sweep of 500 B datagrams from 10 to 120 kbit/s up and down (15 s each, queues drained in between),
then 30 s of TCP each way with pings alongside. About 8 minutes per UE.
To take ble_ip_link's own pacing out of the comparison, write `0` to `~/oai/nrlink/ble_rate_kbps` on **both** PCs (kbit/s; 0 = unpaced,
no file = the default: 150 down on the core, 50 up on the UE PC). Both ends re-read it within a second. Remove it again afterwards: unpaced uplink overload can make an n3iwue's IKE give up.
`--rates 20,40,..` and `--tests udp_dl,tcp_dl` narrow a run. The UE side of `ble_ip_link.py` asks for a connection interval after each connect:
15 ms for UE 1, 18.75 ms for UE 2 (`--interval <ms>` overrides, 0 = keep the 45 ms the link opens with); it logs `ble<k>: connection interval 15 ms`.
Never give both links the same interval: their events lock together on the Intel adapter and one link starves for minutes.

### 11.4 hci_bb uplink load test (one BLE board)
On the UE PC, as root:

```sh
sudo IMAGE=/home/debian/nxp/images/hci_bb_isp_v15r.bin RATES="30 40 50 60 70" SECS=120 ./ble1_loadtest.sh
```

Steps:
1. Stops the watchdog.
2. Optionally flashes the board (VCOM `isp`, then `board.sh flash`) and reads the breadcrumbs once.
3. Installs the Pico bridge, attaches BLE1 and restarts n3iwue1.
4. Sends UDP uplink through the IPsec tunnel at each rate, checking HCI every second. Before each step it checks that the tunnel works.
5. If the board hangs or resets itself, collects the Pico counters and the board state, then reads the breadcrumbs of the boot that hung (`pico/crumbs.py`, with symbols from `hci_bb_isp_v15r.syms`).
6. Restores everything, including the watchdog on both boards.

The log is `logs/ble1_load_<time>.log`.

### 11.5 Reading an hci_bb board's breadcrumbs by hand
```sh
sudo ./ble_attach.sh --off     # or stop btattach for that board: crumbs.py needs the Pico's tty
~/nxp/venv/bin/python -I ~/nxp/pico/crumbs.py /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_<id>-if00 ~/nxp/images/hci_bb_isp_v15r.syms
```

The output shows:
- the reset reason of this boot and of the previous one (WDOG0 = the hardware watchdog fired);
- the tick and main-loop counters;
- the last place the tick interrupted;
- any fault, with its address;
- the last 16 changes of the interrupt mask, with function names.

## 12. Known issues

- Under heavy uplink, n3iwue1's IPsec/GRE tunnel sometimes stops passing traffic even though its board and Bluetooth link are fine. Restarting n3iwue1 recovers it, and the load test does that before each step. Not investigated yet.
- Neither PC runs NTP and their clocks differ by hours. Only round trips are measured, except in the factory runs, which measure the clock offset.
- The board connected through the other PC's USB (BLE2 in our setup) can only be hardware-reset from that PC: `~/nxp/venv/bin/python -I ~/nxp/hwreset.py <serial>`.
- `bluetoothd` is masked on the UE PC while the links run (`ble_attach.sh` does it). Its GATT client triggers a failed pairing that drops the LE link.
- `gnb_bridge.py` can be left holding a dead serial handle (its log fills with `write failed: [Errno 5]`, 0 slots/s) while the gNB board answers fine. Restart gnb_bridge, then both `ue_bridge.py` on the UE PC, then the nrUEs.
- Never `pkill -f` a pattern that also matches your own shell's command line. Several scripts use bracket patterns for this reason.
