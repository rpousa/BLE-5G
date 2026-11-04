# UE PC status (claudepc2, 192.168.10.232)

Last update: 2026-11-05 18:15, UE PC Claude session (after the role swap). CORE_PC_STATUS.md is history now.

## Summary
| Task | State |
|---|---|
| 1. Boards | **done**: `./nrlink.sh boards` (now picks the ue role from this PC's IP; `--role ue` also works) → UE1 eMBB **2HOKMMVEX1WMP**, UE2 URLLC **GBAMNH4BQ3CSB** (both nrlink v4), BLE1 **3VWLILYT2A1AK**, BLE2 **GLNAOWUSDREB4** (hci_bb). boards.json has these 4 roles. No flashing. |
| 2. Interim LAN test | **done, passes** (2026-11-05 18:12). One clean restart each of n3iwue1/2 (fresh config, lan). The earlier n3iwue1 "invalid checksum" was the double start; it didn't come back. Ping ext-dn 192.168.70.132, 5/5 each: **nrUE1 12.1.1.3 avg 733 ms, nrUE2 12.1.1.2 avg 722 ms** (over the air via the claudepc gNB), **n3iwue1 12.1.1.7 avg 1.7 ms, n3iwue2 12.1.1.8 avg 1.9 ms** (N3IWF over the LAN). The nrUEs stayed attached for 18 h. Earlier, n3iwue2's first session ended after 23 min with "Read TCP connection failed: EOF" (the N3IWF closed NWu-CP at 2026-11-05 00:25 UTC): claudepc, did your N3IWF restart then? |
| 3. Bluetooth attach + link test | **radio link OK**. hci0 = 02:4E:52:65:B3:A1 (BLE1), hci1 = 02:4E:52:77:BF:5D (BLE2), both UP. As a normal user (no tun), each board opened L2CAP PSM 0x0081 to A0:C5:89:8D:1C:B9 and got its hello back (`NRLK\x01` in 2.6 s, `NRLK\x02` in 4.0 s), so the claudepc listener sees both. The full `sudo ble_ip_link.py --connect …` + `ping 10.70.0.1` needs root, waiting on the user's terminal. |
| 4. n3iwue over Bluetooth | **implemented**, not tested yet (needs 3). `--transport ble`; **default is now `lan`**, so the step 2 command works as written. |

## Changes to shared files (claudepc: please sync)
Backups here: `nrlink.sh.preue`, `ble_ip_link.py.orig`, `~/oai/deploy/nrlink/n3iwue-ue.yaml.orig`.

1. **nrlink.sh**
   - `boards` honours `--role`: `ue` → `boards.py --need ue1,ue2`; `core` → `--need gnb`; otherwise all roles as before. Without this, plain `boards` on the UE PC tried to fill gNB/UE3 and gave the gNB role to a UE board.
   - New `--transport lan|ble` (role ue only). Default is **lan** (changed from ble at 19:00 so the task's step 2 command works as written).
   - `boards` without `--role` uses `host_role()`: this PC's IP is UE_PC → ue, CORE_PC → core, else all. An explicit `--role` wins.
   - `n3ue_conf`: ble → `IPSecIfaceName: ble<k>`, `IPSecIfaceAddr: 10.70.0.1k`; lan → `eth0`, `192.168.72.1k` (as before). It also checks that both substitutions happened.
   - New `up_n3ue_ble`, used for the ble transport:
     - per k: board ble<k> from boards.json; its address from `hci_probe.py --addr`; it must already be an attached `hciN` (otherwise "sudo ble_attach.sh");
     - `sudo -v` (on a terminal) or `sudo -n true`;
     - containers start with `N3UE<k>_IF=ble<k>` and wait;
     - then `sudo -n $PY -u ble_ip_link.py --connect A0:C5:89:8D:1C:B9 --local <board addr> --index k --netns <container pid> --route 192.168.70.139/32`, logging to `logs/ble<k>.log`;
     - waits for `ble<k>: up`, then for the PDU sessions.
   - The lan path is unchanged apart from `dk rm -f` of old n3iwue containers first. The registration wait is shared (`n3ue_wait`).
   - `down --role ue` stops the links first: `sudo -n pkill -TERM -f "[b]le_ip_link.py --connect"`, or prints that command.
   - `status --role ue` adds a ble1/ble2 link line.
   - New variable `CORE_BT=A0:C5:89:8D:1C:B9`.
   - `hci_of()` uses `hciconfig <hciN>` instead of `btmgmt info`: without a terminal, btmgmt hung (no output after 120 s) or printed nothing, so the lookup failed. (`ble_attach.sh` still uses btmgmt; fine on a terminal, but it would hang if run without one.)
2. **~/oai/deploy/nrlink/n3iwue-ue.yaml**: the command is a block script. It sets the default route as before; then, if `WAIT_IF` is set (`environment: WAIT_IF: ${N3UE<k>_IF:-}`), it waits until `ip route get 192.168.70.139` goes via `dev $WAIT_IF`, then `exec ./n3iwue`. With WAIT_IF empty (lan) it behaves as before.
3. **ble_ip_link.py** (`connect`): it also catches `subprocess.CalledProcessError`. Before, a failing `ip`/`nsenter` (e.g. the container restarted) killed the reconnect loop with a traceback. With `--netns` it now exits cleanly once the target pid is gone.

## Notes / questions for claudepc
- **Two sessions at once (19:00):** another session ran `nrlink.sh up --role ue --transport lan` here while I was editing nrlink.sh (my edit landed mid-run). Then we both fixed `:ro` and restarted the n3iwues within seconds of each other. To avoid this: only one session drives the UE PC containers. I'll stop and leave them to you unless the user says otherwise.
- **n3iwue-ue.yaml:** the config mount is writable now; the n3iwue writes its SQN back into the file.
- **SSH:** claudepc → claudepc2 works as before. claudepc2 → claudepc fails on host-key verification; I didn't override it. If you want me to check your status myself, the user can accept claudepc's host key here (`ssh debian@192.168.10.223` once, interactively).
- **Bluetooth on the core:** please confirm `ble_ip_link.py --listen` is running on hci0 with LE advertising on, and `core_routing.sh` (`ble+ <-> demo-oai`) is applied.
- **Root on this PC:** the BLE links need it, and sudo here asks for a password. Through my tool there is no tty, so sudo can't prompt or reuse a cached ticket. For the ble transport, the user either runs `./nrlink.sh up --role ue` in their own terminal or adds a NOPASSWD sudoers entry.
- **Link drops:** if a BLE link drops, ble_ip_link reconnects and recreates `ble<k>` in the container, but the n3iwue has `AutoReRegistration: false` and won't re-register on its own. Restart it with `./nrlink.sh down --role ue` then `up` (or set AutoReRegistration).

## 2026-11-05 17:15: n3iwue outages over Bluetooth, and RTS/CTS (UE PC session, read-only checks)
- **Outages since the BLE start (16:12):** 2 at the start (IKE retransmit limit, 16:15:54 / 16:16:00). After that the watchdog (`logs/ble_watchdog.log`) restarted **n3iwue1 20 times and n3iwue2 2 times**.
  - Each restart follows "BLE<k> board … does not answer HCI": **BLE1 (3VWLILYT2A1AK) 21 times, BLE2 (GLNAOWUSDREB4) once**.
  - 16:22–16:34: a reattach loop (the board didn't come back; ble1 got "No route to host").
  - Since 16:37: BLE1 hangs every ~2–15 min (up 16:37:31→16:46:32, 16:46:45→17:02:22, 17:02:30→17:04:43, 17:05:03→17:06:54, 17:07:06→17:08:29).
  - n3iwue2's last container was killed at ~17:01 (exit 137, while the watchdog was in "boards: 1" mode).
- **RTS/CTS: none on these ports.**
  - All four are MCU-Link VCOM ports (cdc_acm, interface 2), with ACM bmCapabilities 0x02 ("line coding and serial state").
  - CDC-ACM SERIAL_STATE has no CTS bit, and cdc_acm always reports CTS asserted (TIOCMGET shows DTR RTS CTS on ttyACM1/4), so `crtscts` can't throttle the host.
  - The RTS line goes out only as SET_CONTROL_LINE_STATE; the MCU-Link VCOM bridges only TXD/RXD to the MCXW72.
  - Current settings: BLE ports 115200 `-crtscts` (btattach -N), UE ports 1 Mbaud `-crtscts`.
  - So the H4 link between the host and hci_bb has no hardware flow control. A UART overrun on the board side can break H4 framing, which fits "does not answer HCI". For real RTS/CTS, use an external USB-UART wired to the MCXW72 LPUART RTS/CTS pins (check the FRDM-MCXW72 schematic), or add flow control in software.
