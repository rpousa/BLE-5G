# Results

Raw data is in `results/`. Unless noted, the measurements are from ext-dn (192.168.70.132) to each UE, through its PDU session.

## 1. NR over the GenFSK boards

**Single PC, 30 Oct 2026** (OAI core; 3 nrUEs: eMBB, URLLC, mMTC; about 400 slots/s):

| UE | Test | Result |
|---|---|---|
| UE1 eMBB | TCP up / down, 30 s, `-w 16K` | 147 / 140 kbit/s |
| UE2 URLLC | UDP 32 B every 20 ms, up and down | 0/1000 and 0/1010 lost, jitter 21 / 10 ms |
| UE2 URLLC | ping 32 B | 0 % loss, average 135 ms (min 43 ms) |
| UE3 mMTC | UDP 20 B every 1 s, up | 0/20 lost |
| all three at once | eMBB up + URLLC + mMTC | eMBB 93 kbit/s, URLLC 0/1500 lost, mMTC 0/30 lost |

**Two PCs, 5–6 Nov 2026** (free5GC + N3IWF; gNB on the core PC, nrUE1/2 on the UE PC):
- The slot clock runs at about 94 slots/s. One slot exchange takes 10.6 ms on average: about 3.5 ms for a DL slot and 28 ms for a UL slot, because of the polls.
  Half of gnb_bridge's polls wait out their 25 ms timeout.
- Clean throughput is about 44 kbit/s up and 90 kbit/s down. Under TCP load the RTT is 3–10 s, because RLC queues grow.

## 2. Non-3GPP access over Bluetooth LE

- Idle, the core ↔ board link has an RTT of 86 ms (BLE1) and 75 ms (BLE2), with minimums of 53–54 ms. A 1000 B ping takes 335–443 ms.
- Connection interval 45 ms, supervision timeout 420 ms. At most 27 B per link-layer packet (no data length extension), and about 9 exchanges per event at 40 kbit/s pacing.
- `ble_ip_link.py` paces each link to 40 kbit/s. Small packets (pings, ACKs, IKE) go first, and the bulk queue is capped at 1 s.
  This cut the RTT under load from 2–5 s to 0.4–1.1 s.
- n3iwue1/2 carry 20–30 kbit/s clean and saturate at about 32 kbit/s, measured on hci_bb v12 and v15.

## 3. Stress test, 5 Nov 2026 (`results/stress/`)

All four UEs together. NR: about 44 kbit/s up and 90 kbit/s down clean, with 3–10 s RTT under TCP.
BLE: 20–30 kbit/s clean, with 0.4–1.1 s RTT under load. In the final 10-minute run two BLE board hangs happened, and `ble_watchdog.sh` recovered both.
(The hangs were the hci_bb bug of section 8.)

## 4. Factory floor, downlink only, 6 Nov 2026 (`results/factory/run1`)

- NR downlink is clean up to about 45 kbit/s combined, with 110–280 ms one-way delay. It queues for seconds at 95 kbit/s.
- Bluetooth video is clean at 8 kbit/s per camera, about 0.4 s per frame. At 16.5 kbit/s a third of the frames are lost.
- A downlink-only load never hung a board.

## 5. Factory floor, closed loop (live demo), 6 Nov 2026 (`results/factory/live2_core/by_level.json`)

| Trial (level) | LiDAR up | AGV reports up | Commands down | Video to the handhelds |
|---|---|---|---|---|
| Trial 1 | 1.1 s | 0.46 s | 0.11 s | 0.8 s |
| Trial 2 | queues for about 60 s | | | 33–40 % of frames arrive |
| Trial 3 | more than 60 s behind | | | no video arrives |

Two findings:
- The AGV reaction time (median 0.56 s, p90 1.0 s) often trips the 1 s fail-safe.
- The gNB splits UL slots evenly, about 21 kbit/s per UE, so a Trial 2/3 backlog never drains. `docker restart oai-nrue1 oai-nrue2` flushes it.

During Trial 3 the free5GC N3IWF panicked once (`ngap handler.go:1272`). The live server now restarts it.

## 6. Factory floor at the maximum, Trial 3, 9 Nov 2026 (`results/factory/trial3_20261109.jsonl`)

5 minutes of gnb_bridge and Bluetooth telemetry once the level had settled.

| Flow | Offered | Carried | Result |
|---|---|---|---|
| NR uplink: LiDAR (nrUE1) + AGV reports (nrUE2) | ≈124 kbit/s | 44.8 kbit/s, 22.4 per UE | queue grows; nothing arrives within 60 s |
| NR downlink: stop/go commands | ≈26 kbit/s at most | 13 kbit/s | 100 % delivered, median 109 ms, p90 195 ms |
| Bluetooth downlink: video per link | 40 kbit/s + IPsec/GRE | ≈42.5 kbit/s on each link | 0–2 % of frames arrive whole; RTT ≈3 s |

The slot rate fell to 37.5 slots/s (11.2 UL slots/s) with both uplinks saturated.

## 7. NR against Bluetooth LE, same source and sink, 9 Nov 2026 (`results/compare/`)

`compare_links.py`, one UE at a time, everything else idle. UDP datagrams are 500 B. "Clean" is the highest offered rate with under 1 % loss and at least 95 % delivered.

| UE | Idle RTT 56 / 500 / 1400 B | UDP up clean / most | UDP down clean / most | TCP up · down | RTT under TCP up · down |
|---|---|---|---|---|---|
| nrUE1 (NR) | 502 / 533 / 687 ms | 40 / 44 kbit/s | 80 / 93 kbit/s | 23.5 · 55.8 kbit/s | 6.8 · 3.3 s |
| nrUE2 (NR) | 478 / 603 / 695 ms | 40 / 45 | 80 / 95 | 23.5 · 55.4 | 6.8 · 3.4 s |
| n3iwue1 (BLE, paced 40) | 120 / 244 / 552 ms | 30 / 32 | 30 / 33 | 26.9 · 15.5 | 0.50 · 0.30 s |
| n3iwue2 (BLE, paced 40) | 110 / 256 / 561 ms | 30 / 32 | 30 / 32 | 26.2 · 7.0 | 0.49 · 0.20 s |
| n3iwue1 (BLE, unpaced) | 116 / 256 / 567 ms | 30 / 35 | 40 / 42 | 23.8 · 17.7 | 0.60 · 0.43 s |
| n3iwue2 (BLE, unpaced) | 112 / 329 / 1006 ms | 20 / 22 | – / 41 | 21.0 · 24.0 | 0.51 · 0.60 s |

- **Throughput.** NR wins downlink (80 clean, about 95 at most), and the two are close uplink (NR 40–44, Bluetooth 30–33).
  NR's limit is its slot scheme: 2 UL slots per period, each costing a 25 ms poll timeout.
  Bluetooth's limit is 27 B link-layer packets in 45 ms connection events, after about 80 B of IPsec/GRE/IP overhead per packet. The 40 kbit/s pacing sits just under it.
- **Latency.** Bluetooth is about 4× faster idle (110–120 ms against 480–500 ms; an NR uplink packet first needs a scheduling request and a grant).
  Under TCP the gap grows: 3–7 s on NR (deep RLC queues), 0.2–0.6 s on Bluetooth (bulk queue capped at 1 s).
- **Pacing.** Unpaced, Bluetooth gains about 1 kbit/s up and 8 down. But n3iwue2's IKE gave up three times under uplink overload, and the watchdog restarted it.
  The board kept running (hci_bb v15). The default pacing of 40 kbit/s was put back.

## 8. The hci_bb board hang (5–9 Nov 2026, `results/hci_bb_loadtests/`)

**Symptom:** under PC → board (uplink) traffic, a BLE board stopped completely. There were no HCI events, no answer to vendor commands, and RTS stayed high. The radio core kept the LE link up.

| Build | What changed | Result |
|---|---|---|
| v1 | 1 Mbaud H4, HCI 0xFDB1 enters the ROM bootloader | lost bytes in bursts over 11 B |
| v2 | 115200 baud, no flow control | works; hangs under uplink load |
| v3 | UART ring 128 B → 4 KB, H4 resync | still hangs, 3 of 3 |
| v4–v6 | diagnostic reports (vendor event 0xF7): asserts, faults, stalls, masked interrupts, report on demand 0xFDB2 | hung with no report: the 1 ms tick itself had stopped |
| v7 | 8 KB stack | hung; only 1.4 KB ever used |
| v8 | steady green LED | |
| v9–v10 | HCI on LPUART0 with RTS/CTS through a Pico; VCOM `isp` | every command over about 12 B lost: RTS never reached the pin |
| v11 | PTA19 put back to RTS after `PLATFORM_InitLcl()` | 1000/1000 commands, a 300-command burst lossless; hung at 20 kbit/s after 8 s |
| v12 | release build (-Os); every other interrupt below the tick | clean at 20–30 kbit/s, saturates at about 32; hung at 60 kbit/s after 37 s, still no report |
| v13 | breadcrumbs that survive a reset, read with HCI 0xFDB3; WDOG0 hardware watchdog (2 s) | the board recovered by itself; the tick and the main loop had stopped together, with interrupts **on** |
| v14 | the tick records what it interrupted; NMI and the BusFault, MemManage and UsageFault handlers record and reset | **caught it:** BusFault in `RF_IMU0_IRQHandler+0xa`, CFSR 0x8200 (precise, BFAR valid) |
| v15 | `PLATFORM_RemoteActiveReq()` once at start, never released | 17 min of 30–70 kbit/s uplink with no hang and no watchdog reset; worst HCI reply 39 ms |

**Root cause.** When the radio core (NBU) messages the host, `RF_IMU0_IRQHandler` reads an IMU register in the radio's power domain (0x489481E0).
The SDK's `mcmgr_imu_remote_active_req()` does nothing in interrupt context, so nothing guarantees that domain is awake.
If the NBU went to sleep between raising the interrupt and the handler running, the read was a bus fault. Host-side load widens that gap.
The SDK's default BusFault handler is a silent endless loop that outranks SysTick, which explains every symptom.
**Fix:** keep the radio domain awake for good. **Safety nets kept:** the 2 s hardware watchdog, the fault handlers that record and reset, and the breadcrumbs.

**Open:** under heavy uplink, n3iwue1's IPsec/GRE tunnel sometimes stops passing traffic while the board and the Bluetooth link keep working. Restarting n3iwue1 recovers it.
