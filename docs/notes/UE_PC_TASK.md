# UE PC task: nrlink split architecture, after the role swap

You are the Claude Code session on **claudepc2** (192.168.10.232). On 2026-11-04 the user **swapped the roles**:

- **This machine is now the UE PC.**
- The other PC, **claudepc** (192.168.10.223, tailscale 100.64.0.74), is now the **core PC**.

`~/oai/CORE_PC_TASK.md` is **superseded**. Keep it only as background: architecture, IP plan, gotchas (§5 there still applies). This file replaces it. Report progress in `~/oai/UE_PC_STATUS.md`.

Written by the claudepc session, which now runs the core.

## Why the swap
- claudepc has a Bluetooth adapter (Intel 8265, BT 4.2, `hci0`), which the access point needs. This PC has none.
- claudepc already ran the free5GC core with gtp5g.

Your `--role core` work wasn't wasted: claudepc runs exactly that code. Your core here was stopped with `./nrlink.sh down --role core`. Your `core_routing.sh --lan` rules and route were removed with `--off`, because 192.168.72.0/24 is now this PC's own n3iwue subnet.

## New layout

```
 UE PC = this machine (claudepc2, 192.168.10.232)          CORE PC (claudepc, 192.168.10.223)
 ───────────────────────────────────────────────            ─────────────────────────────────
 nrUE1 ─ ue_bridge ─ UE1 board ┐                              free5GC + N3IWF 192.168.70.139 + ext-dn
 nrUE2 ─ ue_bridge ─ UE2 board ┤ GenFSK over the air ───────► gNB board ─ gnb_bridge ─ PNF ─ VNF
 n3iwue1 ─ ble1 ─ BLE1 board (hci_bb) ┐ Bluetooth LE L2CAP ─► hci0 (Intel) ─ ble_ip_link --listen ─ N3IWF
 n3iwue2 ─ ble2 ─ BLE2 board (hci_bb) ┘ (PSM 0x0081)
```

**Boards**
- Any nrlink board can take any nrlink role; `boards.py` assigns them and saves `boards.json`.
- The **BLE boards are 3VWLILYT2A1AK and GLNAOWUSDREB4**, running `hci_bb_isp_v1`.
- The user is moving boards physically. When the move is done this PC should have:
  - 2 nrlink boards: UE1 and UE2, e.g. 2HOKMMVEX1WMP and GBAMNH4BQ3CSB;
  - 2 hci_bb boards: the two above.
- The gNB board (3TJ2B15JGASV3) goes to claudepc.

**Scripts:** all are already synced here and identical on both PCs.

| Script | Use |
|---|---|
| `nrlink.sh` | `CORE_PC=192.168.10.223`, `UE_PC=192.168.10.232`. Use `./nrlink.sh up\|down\|status --role ue` |
| `boards.py` | Run as `./nrlink.sh boards`. Lists UE1/UE2 (nrlink) and BLE1/BLE2 (hci_bb) |
| `ue_routing.sh` | Run with sudo. Interim LAN path: route 192.168.70.139/32 via 192.168.10.223, plus an accept rule into the `n3ue` bridge. Detects the LAN interface |
| `ble_attach.sh` | Run with sudo. Sets each BLE board's unique address (`~/nxp/hci_probe.py --set-addr`), runs `btattach -P h4 -S 1000000`, then LE on and power on |
| `ble_ip_link.py` | `--connect <core hci0 addr> --local <board addr> --index k [--netns <pid>] [--route 192.168.70.139/32]` |
| `~/nxp/hci_probe.py <serial>` | Raw HCI check. `--addr` / `--set-addr` / `--isp`. `~/nxp/board.sh isp` also handles hci_bb boards |

Facts for the scripts:
- **Core PC Bluetooth address:** **A0:C5:89:8D:1C:B9** (public).
- **BLE board addresses:**
  - 3VWLILYT2A1AK: `02:4E:52:65:B3:A1`
  - GLNAOWUSDREB4: `02:4E:52:77:BF:5D`
- **Board addresses reset:** a board reset reverts the address to 88:88:88:88:88:88, so re-run `ble_attach.sh`.
- **n3iwue containers:** `nrlink-n3iwue1` and `nrlink-n3iwue2` (`~/oai/deploy/nrlink/n3iwue-ue.yaml`).
  - IMSIs 208950000000041 and 042 are already provisioned on the core.
  - Their configs are generated into `~/oai/nrlink/logs/n3ue/` with unique IKE/IPsec SPIs.
  - They live on docker network `nrlink-n3ue-net`, 192.168.72.0/24, routed without NAT.
- **Telemetry:** the VNF/PNF telemetry default now points at this PC (`192.168.10.232:8091`). No dashboard runs here yet; that is harmless.

## Your tasks, in order
1. **Boards.** Once the user has moved them, run `./nrlink.sh boards`. Expect UE1, UE2, BLE1 and BLE2. If an nrlink board doesn't answer, use `~/nxp/board.sh isp <sn>`, then `backup`, then `flash` (always back up first).
2. **Interim LAN test (3GPP and non-3GPP).** Wait until the core PC says its core is up (claudepc runs `nrlink.sh up --role core` and `sudo core_routing.sh --lan`; the user will tell you, or check `ping 192.168.70.139` after step 3). Then:
   ```
   sudo ~/oai/nrlink/ue_routing.sh
   cd ~/oai/nrlink && ./nrlink.sh up --role ue
   ```
   Success: UE1/UE2 get 12.1.1.x on `oaitun_ue1` (over the air to the gNB board on claudepc), and n3iwue1/2 get 12.1.1.x on `gretun-id-*` (through the N3IWF over the LAN). Ping ext-dn 192.168.70.132 from each.
   - Note what fails. A likely first suspect is radio range between the two PCs' boards.
3. **Bluetooth.** Run `sudo ~/oai/nrlink/ble_attach.sh`; expect two new `hciN` with the addresses above. Then test the link to the core's access point (claudepc runs `ble_ip_link.py --listen` on hci0):
   ```
   sudo ~/nxp/venv/bin/python -u ~/oai/nrlink/ble_ip_link.py --connect A0:C5:89:8D:1C:B9 --local 02:4E:52:65:B3:A1 --index 1
   ping 10.70.0.1
   ```
4. **n3iwue over Bluetooth.** Make `--role ue` run each n3iwue over its `ble<k>` link instead of the LAN:
   - `ble_ip_link.py --netns <container pid> --route 192.168.70.139/32`;
   - n3iwue config `IPSecIfaceName: ble<k>` and `IPSecIfaceAddr: 10.70.0.1k`;
   - the container must start before the tun exists, so the n3iwue entrypoint waits for `ble<k>`.

   Keep the LAN path as `--transport lan`, for comparison.

## Coordination
- **Status file:** `~/oai/UE_PC_STATUS.md` (new). claudepc reads it over SSH.
- **Shared scripts:** if you change one, say exactly what in the status file. Keep both copies identical; claudepc syncs them.
- **Older notes:** `~/oai/UE_PC_NOTES.md` has claudepc's notes from before the swap.
- **Rules from the user:**
  - commit or push only if asked;
  - never write the sudo password into a file;
  - back up before overwriting firmware;
  - no destructive debug-mailbox commands;
  - never `pkill -f` a pattern that matches your own shell.
