> **Superseded 2026-11-04:** roles swapped, this machine is now the UE PC. See ~/oai/UE_PC_TASK.md.

# Core PC task: nrlink split architecture

You are the Claude Code session on the **core PC**, `claudepc2`. A second PC (the **UE PC**, where this setup was first built) handed this work over. Read the whole file before acting. Write progress to `~/oai/CORE_PC_STATUS.md` (see the last section).

Written 2026-11-04 by the UE PC session.

---

## 1. What this project is

nrlink carries OAI 5G NR between real radios. The radios are FRDM-MCXW72 boards using GenFSK on the BLE 1M PHY. The NR L1 is emulated at the FAPI boundary:

- `nrlink_gnb.c` in the OAI PNF talks over UDP to `gnb_bridge.py`. The bridge talks over serial to the gNB board.
- The gNB board talks over 2.4 GHz to the UE boards.
- Each UE board connects to its `ue_bridge.py`, which connects to `nrlink_ue.c` in the OAI nrUE.

On top of that, a free5GC core with an N3IWF serves both kinds of access at the same time:

- **3GPP access:** OAI nrUEs over the boards and nFAPI.
- **Non-3GPP access:** UEs registering with IKEv2/EAP-5G through the N3IWF.

Until now everything ran on one PC. The user wants it split across two.

## 2. Agreed architecture

```
 UE PC (192.168.10.223, tailscale 100.64.0.74)          CORE PC = this machine (192.168.10.232, tailscale 100.64.0.77)
 ─────────────────────────────────────────────          ───────────────────────────────────────────────────────────
 nrUE1 ─ ue_bridge ─ UE board A ┐                         free5GC v4.3.0 core (AMF, SMF, UPF, ...) + oai-ext-dn
 nrUE2 ─ ue_bridge ─ UE board B ┤ GenFSK over the air      N3IWF
                                └──────────────────────► gNB board 3TJ2B15JGASV3 ─ gnb_bridge.py ─ OAI PNF ─nFAPI─ OAI VNF ─ N2/N3 ─ core
 n3iwue1 ─ BLE board C (hci_bb) ┐
 n3iwue2 ─ BLE board D (hci_bb) ┤ Bluetooth LE L2CAP CoC ─► Bluetooth adapter here ─ ble_ip_link (tun) ─ routed ─► N3IWF 192.168.70.139
                                └─────────────────────────►
```

- **Core PC (you):** free5GC core, N3IWF, OAI VNF and PNF, `gnb_bridge.py` with the gNB board, and the Bluetooth access point for the non-3GPP UEs.
- **UE PC:** two nrlink UE boards with their nrUEs, two Bluetooth boards running NXP `hci_bb` firmware (each a Linux `hciN` controller), the n3iwue clients, and the dashboard.
- The only link between the PCs is over the air: GenFSK for 3GPP access, Bluetooth for non-3GPP access. Two exceptions over the LAN:
  - **Telemetry:** `gnb_bridge.py` sends telemetry to the dashboard on the UE PC (see §6.4).
  - **Interim test:** before Bluetooth works, the UE PC's n3iwue can reach the N3IWF over the LAN (§6.6).

## 3. Already done on this machine (by the UE PC session, over SSH)

| Done | Where |
|---|---|
| Claude Code installed | `~/.local/bin/claude` (2.1.318) |
| nrlink code: bridges, boards.py, nrlink.sh, dashboard, apps | `~/oai/nrlink/` (same paths as the UE PC) |
| RAN compose and confs (VNF, PNF, UE) | `~/oai/deploy/nrlink/` |
| free5GC compose and configs, already adapted (see §5) | `~/oai/deploy/free5gc/` |
| OAI source tree **with built binaries** in `build_nrlink/` (includes the nrlink hooks and the SSB fix) | `~/oai/openairinterface5g-develop/` |
| OAI incremental build script (needs the `oai-dev` container) | `~/oai/build_dev.sh` |
| Docker images `nrlink-run:local` and `ran-base:local` (saved, not loaded: Docker isn't installed) | `~/images/nrlink-run_ran-base.tar.zst` |
| gtp5g v0.10.2 source (git checkout), not built for this kernel | `~/free5gc/gtp5g/` |
| Kernel module install script | `~/free5gc/install_modules.sh` |
| Board tools: `board.sh` (ISP, backup, flash), `hwreset.py`, nrlink firmware images | `~/nxp/` |
| Python venv with blhost (spsdk 3.12), pyocd 0.46, pyserial (made with uv, because `python3-venv` isn't installed) | `~/nxp/venv/` |
| `debian` added to the `dialout` group (serial access to `/dev/ttyACM*`) | takes effect at the next login |

The paths deliberately match the UE PC (`/home/debian/...`), so the compose files and scripts work unchanged.

## 4. Facts you need

**Docker network:** `demo-oai-public-net`, 192.168.70.128/26, Linux bridge `demo-oai`. The host is 192.168.70.129.

| IP | Container |
|---|---|
| .130 | NRF |
| .131 | MongoDB |
| .132 | oai-ext-dn |
| .133 | UPF |
| .134 | SMF |
| .135 | AMF |
| .136 | AUSF |
| .137 | UDM |
| .138 | UDR |
| .139 | N3IWF |
| .140 | OAI VNF |
| .141 | OAI PNF |
| .143 | NSSF |
| .144 | PCF |
| .145 | CHF |
| .146 | NEF |
| .147 | webui (http://192.168.70.147:5000, admin / free5gc) |
| .151–.153 | nrUEs (on the UE PC now) |
| .160 | docker n3iwue (reference UE over plain IP) |

**Subscribers:** `~/oai/nrlink/f5gc_subscribers.py` provisions them idempotently.

| | Value |
|---|---|
| PLMN | 208/95 |
| TAC | 0xa000 |
| Slice | SST 1, SD 010203 |
| DNN | `internet` |
| UE pool | 12.1.1.0/24 |
| nrUE IMSIs | 208950000000036–038 |
| n3iwue IMSI | 208950000000040 |
| IPsec inner pool (N3IWF) | 10.0.0.0/24, N3IWF 10.0.0.1 |

**Other facts**
- **Boards:** the gNB board is **3TJ2B15JGASV3**, plugged in here at `/dev/serial/by-id/*3TJ2B15JGASV3-if02` (ttyACM0). Serial runs at 1 Mbaud with the nrlink SLIP protocol (`~/oai/nrlink/nrlink.py`, class `Board`). Radio channel 0x2A, power 0x1F (`config.py`).
- **PNF to bridge:** the PNF finds `gnb_bridge.py` through the `NRLINK_GNB=192.168.70.129:5000` env var in `~/oai/deploy/nrlink/docker-compose.yaml`.
- **nrlink.sh:** today it brings up **everything** on one machine:
  - `up [--core oai|free5gc]`, `down [--keep-core]`, `status`, `boards`, `flash <serial>`;
  - `NRLINK_CONF=./free5gc` selects the generated free5GC confs (vnf.conf with SD 0x010203; ue*.conf with DNN `internet`).

## 5. Gotchas already solved (don't re-debug them)

- **gtp5g version:** the free5GC v4.3.0 UPF needs **0.9.3 ≤ gtp5g < 0.10.3**. v0.10.3 fails at UPF start. The checkout is at v0.10.2.
- **One core at a time:** the OAI core compose and the free5GC compose both own `demo-oai-public-net`. This PC only needs free5GC, so you can ignore the OAI core (`--core oai`) entirely.
- **N3IWF ready line:** it logs "NG Setup Response" with spaces. Wait for it with `grep 'NG *Setup *Response'`.
- **n3iwue config format:** `free5gc/n3iwue:latest` needs the newer config format, including `IkeRetransmit`. The config in `config/n3uecfg.yaml` is already correct.
  - Its PDU session appears as GRE `gretun-id-2-1` inside VRF `vrf-pdu-1`. Test it with `ping -I vrf-pdu-1 192.168.70.132`.
  - Its built-in ping self-test fails (the cgroup mount is read-only). That is harmless.
- **SSB fix:** `nrlink_gnb.c` must compute `ssb_start_subcarrier` from the SSB PDU. It's already fixed and built. Without the fix, nrUEs die with exit 139 on "Invalid CSET0 start PRB".
- **pkill:** never `pkill -f` a pattern that also matches your own shell. Use pid files, or `[_]bridge.py`-style patterns. `nrlink.sh` keeps pids in `~/oai/nrlink/logs/pids/`.
- **sudo:** asks for a password. Ask the user to run root steps, or to give you the password in the session. Don't write the password into any file.

## 6. Your tasks, in order

Report each milestone in `~/oai/CORE_PC_STATUS.md`.

### 6.1 Root setup (needs sudo, so ask the user)
```
sudo apt-get update
sudo apt-get install -y docker.io docker-compose linux-headers-$(uname -r) build-essential dwarves bluez
sudo usermod -aG docker debian        # then log out and in again (dialout also needs a re-login)
```
- Check `docker compose version`. If Debian's `docker-compose` package doesn't provide the `compose` plugin, install `docker-compose-plugin` from Docker's repo instead.
- The scripts use `sg docker -c ...`, so group membership is required.

### 6.2 Load images and pull free5GC
```
zstd -dc ~/images/nrlink-run_ran-base.tar.zst | sg docker -c 'docker load'
sg docker -c 'docker compose -f ~/oai/deploy/free5gc/docker-compose.yaml pull'
```
The pull includes `trf-gen-cn5g`; the free5GC tags are pinned at v4.3.0.

The `oai-dev` build container is only needed if you change the OAI C code:
```
docker run -d --name oai-dev -v ~/oai/openairinterface5g-develop:/oai-ran ran-base:local sleep infinity
~/oai/build_dev.sh
```

### 6.3 gtp5g kernel module
```
cd ~/free5gc/gtp5g && make clean && make     # this kernel 6.12.113 has apt headers; with dwarves installed BTF works
sudo ~/free5gc/install_modules.sh            # also loads xfrm_user xfrm_interface esp4 ip_gre, persists in /etc/modules-load.d
```
- If `make` fails on `pahole`, use `make CONFIG_DEBUG_INFO_BTF_MODULES=` (that's how the UE PC built it).
- Check that `cat /sys/module/gtp5g/version` shows 0.10.2.

### 6.4 gNB board check, then a `--role core` for nrlink.sh
Check the board first, after re-login so `dialout` applies:
```
~/nxp/venv/bin/python -c "import sys; sys.path.insert(0,'/home/debian/oai/nrlink'); from nrlink import Board; print(Board('3TJ2B15JGASV3').ping())"
```
It should print `nrlink v4`.
- If it doesn't answer, run `~/nxp/board.sh isp 3TJ2B15JGASV3`, then `~/nxp/board.sh backup 3TJ2B15JGASV3`, then `~/nxp/board.sh flash 3TJ2B15JGASV3 ~/nxp/images/nrlink_v4.bin`. **Always back up before flashing.**
- Never use destructive debug-mailbox commands (erase, program-lifecycle, set-bricked-mode).

Then add `--role core|ue|all` to `~/oai/nrlink/nrlink.sh`. Keep `all` as the default: it is the current single-PC behaviour. The UE PC session is adding the `ue` role on its own copy. For `core`:
- **Step 1 (boards):** only the gNB board is required. `boards.py --check` currently requires the gNB plus 3 UEs, so give it a role filter, e.g. `--need gnb`. Write `boards.json` with just `gnb` here.
- **Step 2 (core):** `core_free5gc` unchanged: core, subscribers, UPF association, ext-dn, N3IWF NG Setup.
- **Step 3 (gNB):** start `gnb_bridge.py` and the `oai-vnf` and `oai-pnf` services only. Don't start any `ue_bridge.py` or `oai-nrue*`.
- **Step 4 (optional):** the docker `n3iwue`, as a reference non-3GPP UE over plain IP. Keep it, since it gives the baseline numbers.
- **Step 5:** the Bluetooth access point (§6.5) and its routing (§6.6).
- **No dashboard here:** it runs on the UE PC.
- **Telemetry:** `nrlink.py` sends to `TELEMETRY_ADDR = ("127.0.0.1", 8091)`. Make it overridable with `NRLINK_TELEMETRY=host:port`, and in role core set it to `192.168.10.223:8091` so the UE PC dashboard still gets gNB stats.
- **down:** `down --role core` stops the same pieces.

Success means `oai-vnf` shows the PNF connected and the AMF shows NG Setup from gNB `gnb-nrlink`, with the gNB board's LEDs idle and waiting for UEs. UEs only attach once the UE PC brings its nrUEs up and the boards are in radio range (same room).

### 6.5 Bluetooth access point
**This PC has no Bluetooth adapter** (`/sys/class/bluetooth` is empty). The user still has to choose one of:
- **(a)** plug in a USB Bluetooth dongle;
- **(b)** use a spare FRDM-MCXW72 flashed with NXP `hci_bb` (HCI over UART), attached with `sudo btattach -B /dev/ttyACMx -P h4 -S 1000000`. The UE PC session can build that image; the SDK is on the UE PC under `~/nxp/mcuxsdk/mcuxsdk/examples/wireless_examples/ble_controller/hci_bb`.

Link protocol (both PCs implement the same thing; the UE PC session writes `~/oai/nrlink/ble_ip_link.py` with `--listen` (core) and `--connect` (UE) modes and will copy it here):
- **Channel:** a Bluetooth LE L2CAP connection-oriented channel, PSM **0x0081**. Python `socket.AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP`, binding with `bdaddr_type` LE public.
- **Packets:** one IPv4 packet per SDU, with tun MTU 1280.
- **Addressing:** link subnet **10.70.0.0/24**. The core side is 10.70.0.1. BLE UE1 is 10.70.0.11 and BLE UE2 is 10.70.0.12, assigned by connection order or by peer bdaddr. Each connection gets its own tun on the core side (`ble1`, `ble2`) as a /32 point-to-point link.
- **Permissions:** the listener needs `CAP_NET_ADMIN` (for tun) and `CAP_NET_RAW`, so run it with sudo or setcap. It also needs LE advertising on, e.g. `btmgmt --index N le on; btmgmt advertising on` (`bluez` package).

### 6.6 Routing non-3GPP UEs to the N3IWF
The non-3GPP UEs reach the N3IWF (192.168.70.139, UDP 500/4500, then ESP/GRE inside) through this host:
```
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -I DOCKER-USER -i ble+ -o demo-oai -j ACCEPT
sudo iptables -I DOCKER-USER -i demo-oai -o ble+ -j ACCEPT
```
- Docker's FORWARD chain otherwise drops traffic from non-Docker interfaces.
- The N3IWF's default gateway is the host (.129), so return traffic to 10.70.0.0/24 goes back out of `bleN` without any extra route.

**Interim test, before Bluetooth exists.** Also accept from the LAN interface `enp2s0`:
```
sudo iptables -I DOCKER-USER -i enp2s0 -s 192.168.10.223 -o demo-oai -j ACCEPT
```
Add the matching return rule too. The UE PC then adds `ip route add 192.168.70.139/32 via 192.168.10.232` and runs an n3iwue against the N3IWF over the LAN. That proves the remote non-3GPP path end to end before Bluetooth is in the loop.
- The N3IWF and SMF need nothing else.
- Every non-3GPP UE needs its own IMSI and subscriber. Add IMSIs **…041** and **…042** to `f5gc_subscribers.py` for the two BLE UEs, with the same K/OPc as …040 unless the UE PC says otherwise. Record what you used in the status file.

## 7. Coordination with the UE PC

- **Status file:** keep `~/oai/CORE_PC_STATUS.md` up to date: what's done, what failed, IMSIs, and anything the UE PC must change. The UE PC session reads it over SSH (`ssh debian@192.168.10.232`).
- **This document:** don't edit it. Add notes to the status file instead.
- **UE PC files:** don't touch anything on the UE PC.
- **Shared code:** the code here is a copy. If you change a shared file (`nrlink.sh`, `boards.py`, `nrlink.py`, `config.py`), describe the change in the status file so the UE PC can merge it, or keep the changes behind `--role core`.
- **Rules from the user:**
  - commit or push only if asked;
  - don't send the user's email anywhere;
  - don't read `~/.bash_history`;
  - back up before overwriting firmware.
