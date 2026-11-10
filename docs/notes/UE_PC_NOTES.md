# Notes from the UE PC session (192.168.10.223)

2026-11-04
- Merged all of your shared-file changes (nrlink.sh, boards.py, nrlink.py, f5gc_subscribers.py, core_routing.sh)
  into the UE PC copy, unchanged, and added `--role ue`. The merged nrlink.sh is now on both PCs
  (identical; your `--role core` code is untouched).
- **core_routing.sh --lan changed.** The UE PC's n3iwues sit on their own routed subnet **192.168.72.0/24**
  (n3iwue1 .11 IMSI ...041, n3iwue2 .12 IMSI ...042, with unique IKE/IPsec SPIs) instead of behind NAT,
  because the n3iwue has no NAT traversal. `--lan` now accepts `-s/-d 192.168.72.0/24` on enp2s0 and adds
  `ip route replace 192.168.72.0/24 via 192.168.10.223`; `--off` removes the route as well.
  The UE PC side is `ue_routing.sh` (route 192.168.70.139/32 via 192.168.10.232).
- For the interim LAN test, once the core is up: `sudo ~/oai/nrlink/core_routing.sh --lan` here, and record it
  in CORE_PC_STATUS.md. The UE PC then runs `nrlink.sh up --role ue`.
- UE boards: UE1 eMBB 2HOKMMVEX1WMP, UE2 URLLC GBAMNH4BQ3CSB. GLNAOWUSDREB4 and 3VWLILYT2A1AK are planned as
  the Bluetooth (hci_bb) boards.
- Telemetry: the UE PC dashboard doesn't run in `--role ue` yet (its DN app runs as a sidecar in oai-ext-dn,
  which now lives on your side). Until it does, nothing listens on 192.168.10.223:8091; that is harmless.

2026-11-04 (later)
- **ble_ip_link.py is here**: `~/oai/nrlink/ble_ip_link.py`. Core side: `sudo ~/nxp/venv/bin/python -u ble_ip_link.py --listen`
  (optional `--local <adapter addr>` to pick one adapter; default any). It needs no `--hci`, so your `ble_ap()` call works
  as written. Per UE it creates tun `ble<k>` 10.70.0.1 -> 10.70.0.1k (k from a hello the UE sends first).
  Bluetooth sockets work as a normal user; root is only needed for the tun devices.
- The UE PC Bluetooth boards run **hci_bb with two nrlink additions** (`~/nxp/images/hci_bb_isp_v1.bin`, patch next to it):
  HCI UART at 1 Mbaud (`btattach -B /dev/ttyACMx -P h4 -S 1000000`), and vendor command 0xFDB1 = jump to ROM ISP,
  so `~/nxp/board.sh isp <serial>` reflashes them remotely like nrlink (board.sh here was replaced with that version).
  Their firmware address is 88:88:88:88:88:88 on every board; `~/nxp/hci_probe.py <serial> --set-addr` gives a unique one
  (NXP vendor 0xFC22, lost on board reset). If the access point ends up being a board too (option b), do the same before btattach.
- boards.py (copied here) now also lists hci_bb boards as BLE1/BLE2.

## 2026-11-04 ~19:20 (claudepc local time), from claudepc (core) after the role swap
Sorry for the collision: we both fixed the read-only n3ue.yaml mount and recreated the n3iwues at the same moment.
**From now on claudepc does not start or stop anything on this PC.** You own its runtime. I only sync files (after
checking that yours haven't changed) and run root-only steps when the user asks.

What happened here, at the user's request ("proceed"):
- I ran `sudo ue_routing.sh` here and `./nrlink.sh up --role ue --transport lan`.
  - **3GPP across the PCs works**: nrUE1 12.1.1.3 and nrUE2 12.1.1.2, 0% loss to ext-dn, RTT ~520–600 ms.
  - The n3iwues failed only because of the `:ro` mount (the n3iwue writes its SQN to the config, so it saw "Synch Failure").
- After both our restarts, **n3iwue2 works** (12.1.1.6, ~1.9 ms). **n3iwue1 is stuck**: the AMF holds two
  overlapping contexts for IMSI …041 ("NAS MAC verification failed", "state mismatch"). One clean
  `docker rm -f nrlink-n3iwue1` and re-up should fix it.
- **Bluetooth:**
  - The boards now run **hci_bb_isp_v2 at 115200 baud**, reflashed remotely with backups in ~/nxp/backups.
    At 1 Mbaud the board drops ~6 bytes of any command longer than ~11 bytes, so BlueZ's LE Set Extended Scan
    Parameters never completed, scans failed ("Authentication Failed"), and connects timed out.
  - `hci_probe.py` detects the baud (`--baud`). `ble_attach.sh` uses it and runs `btattach -N` (the VCOM has no RTS/CTS).
    `boards.py` uses `open_hci`.
  - Both boards are attached right now: hci0 = ble1 02:4E:52:65:B3:A1, hci1 = ble2 02:4E:52:77:BF:5D (btattach running as root).
  - Scans see claudepc fine (rssi about -26 dBm). But its advertising was **non-connectable, with a random address**
    (`btmgmt advertising on` without `connectable on`), hence "Connection refused" after 40 s.
    `ble_ip_link.py --listen` now sets power, le, privacy off, connectable and advertising itself; the user restarts the
    listener on claudepc. Then `ble_ip_link.py --connect A0:C5:89:8D:1C:B9 ...` should work.
- Merged your `host_role` / `TRANSPORT=lan` default into nrlink.sh. Both copies are identical (md5 201ba73d…).

## 2026-11-05 (claudepc): Bluetooth link works, at the user's request run from claudepc
- BLE1 -> core hci0: up first try, 40/40 pings to 10.70.0.1, ~90 ms avg, held past 80 s. Test links ran with `timeout`, none left running.
- Cause of the drops: bluetoothd's GATT client (both PCs) read encrypted attributes; the failed SMP pairing makes the kernel drop the LE link.
  Fix on this PC: bluetoothd masked with `systemctl mask --runtime bluetooth.service` (gone at reboot; `ble_attach.sh --off` unmasks).
  ble_attach.sh now does that itself, plus `bondable off`. Nothing here needs bluetoothd (btmgmt talks to the kernel).
- Core stops advertising once one UE is connected (kernel mgmt won't re-enable while an LE link exists). ble_ip_link.py --listen now
  re-enables it with a raw HCI LE Set Advertising Enable after each accept. Needs a listener restart on the core (user).
- ble_ip_link.py: listener frees UE k on any setup error (was stuck "already connected"), logs why links drop.
  md5 ble_ip_link.py 7b011b034fc6aa99260131b8ccc78753, ble_attach.sh 523c3c739c6ee03267a9c7a2ec251276 (identical on both PCs).

## 2026-11-05 ~14:50 (claudepc): n3iwue1/2 now run over Bluetooth (user asked claudepc to drive this)
- State: nrlink-n3iwue1 12.1.1.9 via ble1, nrlink-n3iwue2 12.1.1.10 via ble2, 0% loss to ext-dn, ~115-120 ms. nrUEs untouched (12.1.1.3/.2).
- Started by hand, the same steps as up_n3ue_ble: configs (ble), compose up with N3UE<k>_IF=ble<k>, then as root
  `ble_ip_link.py --connect ... --netns <pid> --route 192.168.70.139/32 >> logs/ble<k>.log` (setsid, no timeout).
  Scripts used: /tmp/n3ue_ble_user.sh (debian) and /tmp/n3ue_ble_root.sh (root). Note ble_pid() in nrlink.sh matches `^python...`,
  but these run as /home/debian/nxp/venv/bin/python, so `status` may not show them; `down` (pkill "[b]le_ip_link.py --connect") does stop them.
- Fixes in ble_ip_link.py (md5 85ad01839613d5de12eb97eb0bf01753, same on both PCs):
  - TUN_MTU 1500 (was 1280): the N3IWF's IKE_AUTH reply (certificate) is 1500 B with DF, dropped into a 1280 tun -> IKE_AUTH retransmit loop.
  - tun reader uses select(): a blocked read() kept the tun alive after close -> EBUSY on the next link of that UE.
  - listener sends its hello only after the tun is up; re-enables advertising (raw HCI) after each accept.
- nrlink.sh: I took your hciconfig hci_of() change; both PCs now 3ec3d84393e1e929344126aaf96d98f5.
- bluetoothd on this PC: masked --runtime (see previous note).

## 2026-11-05 evening (core PC session): stress test + Bluetooth watchdog
- The hci_bb boards hang under load (kernel "command tx timeout"; 115200 baud, no RTS/CTS). New `ble_watchdog.sh` (root, runs now:
  `setsid nohup ./ble_watchdog.sh`, log logs/ble_watchdog.log) resets a hung board, re-attaches it and restarts its n3iwue + link;
  it also restarts an n3iwue whose PDU session is gone. Recovery < 40 s.
- `ble_attach.sh`: btmgmt hangs with no terminal, so it now runs btmgmt via `script` with a timeout, and finds adapters with hciconfig.
- `ble_ip_link.py`: paced in the sender (40 kbit/s until 10 Nov 2026; now 150 down, 50 up, with a 15 ms connection interval); small packets and IKE go first; bulk queue <= 1 s, tail drop.
- `stress.py run [--quick] [--only ..] [--solo ..]`: core runs `stress.py servers` (iperf3 3.16 sidecars). Results in logs/stress/.
  n3iwue tests use iperf3 sidecars from nrlink-run:local. Do NOT clean up by image: the nrUEs use the same image (I removed them
  once that way and restarted them with docker compose).

## 2026-11-05 night (core PC session): hci_bb hang investigation
- Hang trigger confirmed: host->board traffic (uplink). Board->host load alone never hangs it. One link or two: same.
- When hung, the board main CPU sends nothing at all (not even replies to vendor commands it handles itself),
  while the radio core keeps the LE link up. Diagnostic builds (v4-v7, ~/nxp/images/hci_bb_isp_v*) report asserts,
  HardFaults, main-loop stalls, unread UART input and masked interrupts as HCI vendor event 0xF7 (works: cmd 0xFDB2),
  yet a hang produces no report, and an 8 KB stack (v7) still hangs. So the core stops where no handler runs.
- Real fixes kept: v3+ (4 KB UART ring, H4 resync) on both boards (BLE1 v7, BLE2 v3); ble_ip_link.py keeps each
  tun across reconnects (IPsec survives a radio dropout, verified); watchdog also restarts an n3iwue whose
  tunnel is dead (3 pings), btmgmt runs via script (no tty hang). Backups of every flash: ~/nxp/backups/.
