# nrlink: OAI 5G NR over FRDM-MCXW72 GenFSK boards

```
 oai-ext-dn ── UPF ── AMF/SMF (CN5G)
                 │ N2/N3
             OAI VNF (MAC/RLC/PDCP/RRC, scheduler)
                 │ nFAPI P5/P7
             OAI PNF ── nrlink_gnb.c ──UDP── gnb_bridge.py ──serial── gNB board (3TJ2B15JGASV3)
                                                                         ~~ 2.4 GHz GenFSK 1 Mb/s ~~
 nrUE1 eMBB  ── nrlink_ue.c ──UDP── ue_bridge.py 1 ──serial── UE board 2HOKMMVEX1WMP  (blue  x1)
 nrUE2 URLLC ── nrlink_ue.c ──UDP── ue_bridge.py 2 ──serial── UE board GBAMNH4BQ3CSB  (red   x2)
 nrUE3 mMTC  ── nrlink_ue.c ──UDP── ue_bridge.py 3 ──serial── UE board 3VWLILYT2A1AK  (green x3)
```

Since the two-PC split (4 Nov 2026) only nrUE1 and nrUE2 run: UE3's board became the Bluetooth board BLE1
(`hci_bb` firmware, see the top-level README). On the core PC `nrlink.sh` starts the bridge as `gnb_bridge.py --ues 1,2 --skip-empty`.

The boards are the radio: the NR PHY is emulated at the FAPI boundary. Per slot the PNF sends what the
VNF scheduled (MIB, DCIs, PDSCH transport blocks) over the gNB board; each UE board hands it to its nrUE,
whose MAC runs exactly as with a real PHY. For UL slots the gNB board polls every UE board, which sends
what its MAC put on PUSCH/PUCCH/PRACH; the PNF turns that into CRC/RX_DATA/UCI/RACH indications. A slot
lasts as long as its over-the-air exchange (about 1 ms for a downlink slot to 10 ms for one with uplink, two PCs,
11 Nov 2026), and every NR timer follows the slot clock.

## Pieces
- Firmware `nrlink` (MCUXpresso SDK, `~/nxp/mcuxsdk/mcuxsdk/examples/wireless_examples/genfsk/nrlink`):
  SLIP framed modem on the MCU-Link VCOM at 1 Mbaud, LED patterns, `isp` (ROM bootloader) kept so the
  boards can be re-flashed remotely (`~/nxp/board.sh isp|flash|hwreset <serial>`).
- OAI changes (`~/oai/openairinterface5g-develop`): `nfapi/oai_integration/nrlink_{proto.h,gnb.c,gnb.h}`,
  hook in `nfapi_pnf.c:handle_nr_slot_ind()`, PHY UL indications muted in `NR_IF_Module.c`,
  `executables/nrlink_ue.c` + switch in `nr-uesoftmodem.c`, clear-text UCI fields in the UE PUCCH PDU.
  Enabled only by the env vars `NRLINK_GNB` / `NRLINK_UE`.
- Host: `gnb_bridge.py`, `ue_bridge.py`, `config.py` (board roles), `nrlink.py` (board protocol + air framing).
  `gnb_bridge.py` options: `--ues 1,2` (UEs to poll; default every UE in `config.UES`), `--poll-timeout` (25 ms),
  `--skip-empty` (dynamic slotting: empty downlink-only slots are not sent over the air; see Notes).
  Its statistics line (`logs/gnb_bridge.log`, every 5 s) gives slots/s, kbit/s, polls and timeouts, and
  `slot ms DL x UL y rest z`: the bridge's time per downlink-only slot and per slot with uplink, and the rest of
  each slot's wall time (PNF, VNF, UDP).
- Bluetooth side (non-3GPP): `ble_ip_link.py` (IP over an LE L2CAP CoC; `--listen` on the core's adapter,
  `--connect <core> --local <board> --index k` on the UE PC), `ble_attach.sh`, `ble_watchdog.sh`.
- Demo apps (`apps/`): the factory floor (`factory2.py`, `factory_live2.py`) and the AGV testbed
  (`agv.py`, `agv_live.py`, `agv_live.html`), started by `nrlink.sh live start --scenario factory|agv`.
- Deployment: `~/oai/deploy/nrlink/` (VNF/PNF/UE configs, compose file), `run_nrlink.sh up|status|test|down`.

## Running it
`./nrlink.sh up` boots everything in order and waits for each part (`down`, `down --keep-core`, `restart`,
`status`, `boards`, `flash <serial>`; see the header of nrlink.sh). Two cores, one at a time (same network and IPs):
- default: **free5GC v4.3.0** (`~/oai/deploy/free5gc/`) with the **N3IWF**: the 3 nrUEs over the boards (3GPP
  access, slice 1/010203, DNN internet) plus a non-3GPP UE (`f5gc-n3iwue`, IMSI 208950000000040, IKEv2/EAP-5G,
  GRE in IPsec) on the same core, UPF and ext-dn. RAN configs for it are generated in `~/oai/deploy/nrlink/free5gc/`.
  Needs the gtp5g kernel module 0.9.3-0.10.2: `sudo sh ~/free5gc/install_modules.sh` (rebuild for a new kernel).
- `./nrlink.sh up --core oai`: the OAI CN5G core only (3GPP access, slice 1, DNN oai), as before.
Subscribers for free5GC are provisioned by `f5gc_subscribers.py` (webconsole http://192.168.70.147:5000, admin/free5gc).

## LEDs
- gNB board: RGB = class of the UE served in the current slot (eMBB blue, URLLC red, mMTC green);
  LED1 (PTC1) on while the allocation is uplink, off for downlink.
- UE boards: blink their class pattern whenever they are served (DL TB for them or UL TB from them).

## Scheduler settings (`deploy/nrlink/vnf.conf`, tuned 11 Nov 2026)
TDD 7 DL + special + 2 UL slots in a 5 ms period (30 kHz), `min_rxtxtime = 4` (was 6), and
`ulsch_max_frame_inactivity = 0` in `MACRLCs` (a UE gets an uplink grant every TDD period without asking).
With `gnb_bridge.py --ues 1,2 --skip-empty` (what `nrlink.sh` starts on the core PC) these took the AGV testbed's NR
one-way delay at load L2 from 643 to 144 ms (LiDAR) and from 441 to 70 ms (detections), and the slot rate from 49 to
about 240 a second. `min_rxtxtime = 2` and TDD 3 DL / 6 UL stop the nrUEs from
attaching; 5 DL / 4 UL attaches but is slower (each uplink slot costs a poll round). Data: `results/nr_tuning/`,
page `docs/pages/nr-tuning.html`.

After changing `vnf.conf`, restart in this order: stop oai-pnf and oai-vnf, start oai-vnf, wait for
`Received NGSetupResponse` in its log, start oai-pnf, then on the UE PC restart oai-nrue1, wait until it passes traffic,
then oai-nrue2. Starting the PNF straight after the VNF crashes the VNF (`Cell 1 not configured`); restarting both nrUEs
at once can leave one stuck (the gNB logs thousands of `CCE fail` for it).

## Results (2026-10-30, iperf3 between each nrUE's oaitun_ue1 and oai-ext-dn 192.168.70.132)
| UE / class | test | result |
|---|---|---|
| UE1 eMBB  | TCP bulk UL, 30 s, `-w 16K` | 147 kbit/s |
| UE1 eMBB  | TCP bulk DL (`-R`), 30 s, `-w 16K` | 140 kbit/s |
| UE2 URLLC | UDP 32 B / 20 ms UL and DL | 0/1000 and 0/1010 lost, jitter 21 / 10 ms |
| UE2 URLLC | ping 32 B | 0 % loss, RTT avg ~135 ms (min 43 ms) |
| UE3 mMTC  | UDP 20 B / 1 s UL | 0/20 lost |
| all three at once, 30 s | eMBB UL + URLLC + mMTC | eMBB 93 kbit/s, URLLC 0/1500 lost, mMTC 0/30 lost |

The slot clock runs at ~400 slots/s (real NR mu=1: 2000), set by the over-the-air exchange.
TCP needs a window sized to the link (`-w 16K`): with the default one, ~1 MB queues in RLC and
iperf3's end-of-test exchange times out behind it.

## Notes
- `pnf.conf` time server must bind `server_ip = "0.0.0.0"` (default 127.0.0.1) for the VNF to follow the slot clock.
- The gNB bridge polls every UE in every UL slot: a skipped UE is PUSCH DTX to the scheduler, which
  stops scheduling it after 10 (UL failure). A UE that is not on the air at all costs two 25 ms poll timeouts in
  every UL slot, so list only the live ones with `--ues`.
- Dynamic slotting (`--skip-empty`): the nrUE already runs any slots it did not hear when the next DL_SLOT
  arrives (`nrlink_ue.c`, up to 40 slots, as for slots lost over the air), so the gNB bridge can leave empty
  downlink-only slots off the air. The PNF still gets its UL_RESULT for every slot. The nrUE reports every UL slot (even empty) so its bridge can
  wait for a late one instead of answering empty.
- Subscribers 037/038 were switched to SST 1 / DNN oai in the running core's DB (`run_nrlink.sh up` reapplies it).
