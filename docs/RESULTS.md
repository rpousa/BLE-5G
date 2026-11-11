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
- Since the 11 Nov tuning (section 10): about 240 slots/s, no poll timeouts, and a 56 B round trip of 96–114 ms (was 530–580). Throughput was not re-measured.

## 2. Non-3GPP access over Bluetooth LE

- Idle, the core ↔ board link has an RTT of 86 ms (BLE1) and 75 ms (BLE2), with minimums of 53–54 ms. A 1000 B ping takes 335–443 ms.
- Connection interval 45 ms until 10 Nov 2026, 15 ms since (section 7b); supervision timeout 420 ms. LE 1M and at most 27 B per link-layer packet:
  the core PC's Intel 8265 (Bluetooth 4.2) has neither 2M nor data length extension, though the boards have both.
- `ble_ip_link.py` paced each link to 40 kbit/s; since 10 Nov 2026 to 150 kbit/s down and 50 up. Small packets (pings, ACKs, IKE) go first, and the bulk queue is capped at 1 s.
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

## 7b. Faster Bluetooth links: the connection interval, 10 Nov 2026 (`results/compare/compare_20261110_*`)

2M PHY and 251 B link-layer packets were tried first, on BLE1. The board accepted LE Set Data Length but nothing changed,
and refused LE Set PHY (status 0x11): the peer, the core PC's Intel 8265, reports neither feature (LE remote features 0x1f).
The connection interval is the knob that works. The UE asks for it with HCI LE Connection Update, and the Intel accepts.

Same tests as section 7 (`compare_links.py`, one UE at a time, live demo stopped). UDP clean / most, kbit/s:

| Setting | UE | Idle RTT | UDP up | UDP down | TCP up · down | RTT under TCP up · down |
|---|---|---|---|---|---|---|
| 45 ms, unpaced (baseline) | n3iwue1 | 111 ms | 30 / 35 | 40 / 43.5 | 25.4 · 21.4 | |
| BLE1 at 15 ms, BLE2 at 45, unpaced | n3iwue1 | 67–75 ms | | 100 / 110 | fails · 102–105 | · 0.22 s |
| BLE1 at 7.5 ms, BLE2 at 45, unpaced | n3iwue1 | 70 ms | 40 / 46 | 100 / 143 | · 105 | · 0.18 s |
| both at 7.5 ms, paced 150/50 | n3iwue1 | 78 ms | 40 / 40 | (dips) / 103 | 39.1 · 104.9 | 0.45 · 0.19 s |
| both at 7.5 ms, paced 150/50 | n3iwue2 | 198 ms | 40 / 41 | 60 / 74 | 14.0 · 4.8 | 0.87 · 0.94 s |
| both at 15 ms, paced 150/50 | n3iwue1 | 67 ms | 40 / 40.5 | 120 / 122 | 37.0 · 104.3 | 0.45 · 0.16 s |
| both at 15 ms, paced 150/50 | n3iwue2 | 87 ms | 40 / 40 | 20 (dips), 120 / 119 | 37.9 · 61.7 | 0.46 · 0.20 s |
| **BLE1 15 ms, BLE2 18.75 ms, paced 150/50 (the default now)** | n3iwue1 | 76 ms | 30 / 38 | 60 / 79 | 32.1 · 51.7 | 0.45 · 0.29 s |
| **BLE1 15 ms, BLE2 18.75 ms, paced 150/50 (the default now)** | n3iwue2 | 94 ms | 40 / 40 | 80 / 80 | 31.6 · 36.3 | 0.45 · 0.35 s |

- One link alone is fastest at 7.5 ms: about 3× the downlink of 45 ms (143 against 43.5 at most) and faster than NR's downlink (80 / 95).
- **The two links must not share an interval.** Each board is the central of its own link and times it by its own clock; the Intel
  adapter is the peripheral on both and serves one connection event at a time. At equal intervals the two links' events drift into
  each other and stay overlapped for minutes. Live, with both at 15 ms: one link's pings went to 0.3–1.3 s (1000 B: 2–8 s, 15–25 % lost)
  while the other's stayed at about 55 ms, and after about 2 min they swapped (`ping -D` on both, 10 s bins). The camera stream on
  BLE2 dropped 1936 of 4457 packets for queueing over 1 s, and BLE2 had a supervision timeout. This is also what the "dips" in the
  equal-interval rows are. With BLE1 at 15 and BLE2 at 18.75 ms the same 3-minute test stayed at 50–160 ms on both (median about 70),
  and 1000 B pings at about 320 ms with no loss. So `ble_ip_link.py` asks for 15 + 3.75·(k−1) ms on UE k (`conn_interval_ms`, or `--interval`).
- Two links at 7.5 ms starve the second outright (n3iwue2 TCP down 4.8 kbit/s); 7.5 ms is only for a single link.
- Uplink tops out at about 40 kbit/s whatever the interval (46 unpaced, 7.5 ms). The pacing (50 kbit/s of IP up) sits just above it.
- The live demo at 15 / 18.75 ms (`results/factory/by_level_20261110_ci15_18.json`): camera video, both links streaming at once,
  Trial 1 100 % of frames at a median 196 / 280 ms (camera 1 / 2), Trial 2 100 % at 185 / 254 ms (33–40 % at about 6 s at 45 ms),
  Trial 3 68 % at 5.4 s (0–2 % at 45 ms). Both links together leave each about 30 kbit/s of video. The NR side is unchanged.
- Idle pings at 15 / 18.75 ms: core ↔ BLE1 and BLE2 62 ms (86 / 75 at 45 ms), 1000 B 417 / 397 ms; with the demo at Trial 1, 65 ms and 464–500 ms.
- Open: TCP uplink at 15 ms with BLE2 at 45 once lost n3iwue1's session (the watchdog restarted it); the uplink limit (about 40 kbit/s
  at any interval) is not located.

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

## 9. AGV testbed: the same data over NR and Bluetooth at once, 11 Nov 2026 (`results/agv/`, page `docs/pages/agv-testbed.html`)

One simulated AGV drives a 124 m loop through a 48 × 30 m hall with six checkpoints. Every message is built once and the identical
bytes go out on two paths at the same moment: the LiDAR scan (752 B) on nrUE1 and n3iwue1, the camera detections plus the AGV's pose
(36–80 B) on nrUE2 and n3iwue2. The edge in ext-dn times both copies on the core clock. At a checkpoint the AGV asks for a position fix
on both paths and takes the first answer. The radio channel is simulated from the AGV's position (3GPP TR 38.901 InF-SL path loss,
shadowing, Rician/Rayleigh fading; NR on one fixed channel, Bluetooth hopping every 15 ms event). Loads: L1 LiDAR 1 Hz + detections 5 Hz,
L2 2 + 10, L3 4 + 20. Each setting was held 210 s and summarised from 60 s on.

Run with the tuned NR (section 10, all steps kept) and both Bluetooth links at 15 / 18.75 ms (`by_cfg_20261111_tuned.json`). One-way ms, p50 / p90:

| Setting | NR LiDAR | BLE LiDAR | LiDAR first on NR | NR detections | BLE detections | Detections first on BLE | Position fix NR / BLE p50 |
|---|---|---|---|---|---|---|---|
| both · L1 | 143 / 174 | 172 / 196 | 82 % | 59 / 83 | 50 / 71 | 64 % | 233 / 229 |
| both · L2 | 142 / 180 | 173 / 200 | 84 % | 69 / 108 | 51 / 69 | 74 % | 138 / 105 |
| both · L3 | 142 / 180 | 179 / 227 | 91 % | 74 / 115 | 50 / 70 | 72 % | 122 / 76 |
| NR only · L2 | 136 / 182 | – | – | 68 / 107 | – | – | 100 / – |
| Bluetooth only · L2 | – | 172 / 203 | – | – | 51 / 71 | – | – / 111 |
| both · L1 · −20 dBm | 141 / 170 | 168 / 195 | 86 % | 60 / 83 | 50 / 71 | 65 % | 142 / 117 |
| both · L1 · −30 dBm, fades drop | 144 / 172 | 168 / 197 | 84 % | 59 / 82 | 52 / 72 | 60 % | 182 / 168 |

- **Nothing lost.** Every copy reached the edge on all four paths at every load, L3 included. With fades dropping messages (−30 dBm),
  NR did not send 14 of 162 LiDAR scans and 22 of 813 detection messages, Bluetooth 2 of 162 and 8 of 814: NR's fixed channel fades for
  up to 0.7 s, a Bluetooth fade lasts one 15 ms event.
- **NR wins the LiDAR race, Bluetooth the detection race.** NR carries a 752 B scan in one uplink slot with a pre-grant, and is first by a
  median 31–40 ms. Bluetooth's 15 ms events suit the small detection messages, first by 15–26 ms.
- **No coexistence penalty**: NR alone and Bluetooth alone at L2 match the "both" row within a few ms.
- **Before the NR tuning** (`by_cfg_20261111.json`, `trace_20261111-051631.jsonl.gz`): NR took 630–840 ms for LiDAR and 430–505 ms for
  detections (p90 near 1 s), and Bluetooth won 93–100 % of both races. Bluetooth's numbers were the same as now.

## 10. NR tuning, 11 Nov 2026 (`results/nr_tuning/`, page `docs/pages/nr-tuning.html`)

Each step was measured on the AGV testbed (section 9) at load L2 (LiDAR 2 Hz on nrUE1, detections 10 Hz on nrUE2), with Bluetooth
carrying the same data alongside, 60 s to settle and 150 s measured. Steps are cumulative. One-way ms, p50 / p90 / p99:

| Step | gNB slots/s | Polls timed out | NR LiDAR | NR detections | NR position fix p50 / p90 |
|---|---|---|---|---|---|
| 0. Baseline: TDD 7 DL + S + 2 UL, `min_rxtxtime` 6, bridge polls UE1–UE3 | 49 | 50 % | 643 / 1028 / 1175 | 441 / 805 / 1068 | 408 / 555 |
| 1. `gnb_bridge.py --ues 1,2` | 220 | 0 % | 212 / 256 / 285 | 120 / 225 / 300 | 133 / 361 |
| 2. `ulsch_max_frame_inactivity = 0` | 217 | 0 % | 188 / 226 / 256 | 102 / 158 / 201 | 152 / 315 |
| 3. `min_rxtxtime = 4` (kept) | 201 | 0 % | 155 / 192 / 215 | 82 / 123 / 162 | 179 / 206 |
| 4. TDD 5 DL + S + 4 UL (reverted) | 156 | 0 % | 170 / 209 / 244 | 85 / 158 / 198 | 164 / 218 |
| 3, re-measured before step 5 | 209 | 0 % | 153 / 190 / 210 | 80 / 122 / 162 | 120 / 199 |
| 5. Dynamic slotting, `gnb_bridge.py --skip-empty` (kept) | 248 | 0 % | 144 / 181 / 189 | 70 / 109 / 149 | 118 / 215 |

- **Why smaller slots would not help.** The air interface is emulated: each slot lasts as long as its exchange over the boards,
  not the 0.5 ms of 30 kHz numerology. What counts is the wait in each exchange and how many slots a packet needs end to end.
- **Step 1 was most of the gain.** UE3's board became BLE1 at the two-PC split, but the bridge still polled UE3 in every uplink slot and
  waited out two 25 ms timeouts each time, about two thirds of all wall time.
- **Step 2:** a UE gets an uplink grant every TDD period without a scheduling request. **Step 3:** shorter grant-to-PUSCH and data-to-ACK gaps.
  `min_rxtxtime = 2` was tried twice and the nrUEs never attach (no uplink at all).
- **Step 4:** TDD 3 DL + 6 UL fails at random access (Msg3 never received). 5 + 4 attaches but is slower, because each uplink slot
  costs a poll round: the slot rate fell to about 150/s. Bluetooth LiDAR was also slower in that run (266 ms), but that was BLE1 being
  left at 45 ms after a reconnect (`docs/REPRODUCE.md`, known issues), not the extra polling.
- **Where the time goes** (bridge statistics, `slot ms DL x UL y rest z`), before step 5: 2.0 ms per downlink-only slot (the broadcast),
  9.6–10.4 ms per slot with uplink (broadcast plus two polls), 0.7 ms per slot in the PNF, VNF and UDP. About 50 ms per 5 ms TDD period.
- **Step 5, dynamic slotting.** A downlink-only slot with no SSB, DCI or transport block is not broadcast; the nrUE runs the slots it
  did not hear when the next one arrives (`nrlink_ue.c` already did this for slots lost over the air, up to 40). About 115 slots a second,
  half of all slots, stay off the air, and the average downlink-only slot drops from 2.0 to 1.2 ms. Nothing was lost and both nrUEs stayed attached.
  What is left is mostly the uplink slots; polling both UEs in one exchange would be the next step.

