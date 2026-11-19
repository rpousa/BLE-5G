# AGV relay chain, option 3: OAI's NR sidelink between the AGVs: what carries over (12 Nov 2026)

Option 2, built and run: a UE per AGV (oai-nrue1..3 on the UE1, UE2 and BLE1 boards). Only the pivot's UE is in
coverage and attached; a new pivot's UE attaches for real. The hops between the AGVs are the controller's own packets
(AIR_CH) in gNB-granted windows. Option 3 replaces those hops with OAI's NR sidelink (PC5: SCI, sidelink MAC, RLC,
PDCP, SRAP) and the relaying with OAI's UE-to-network (U2N) relay, as in steps B and C.

## What carries over

| piece | from | reuse in option 3 |
|---|---|---|
| Sidelink over the boards: `nrlink_sl.c` (OAI side, 478 lines), `sl_bridge.py` (poll/reply air protocol, age limit) | step B | as is: carries SCI + TB items between OAI sidelink UEs |
| U2N relay on the release tree: `sl-tree-stepC.patch` (relay DRB by address, free5GC NAS fixes, SIB1 timer fix, mode-1 timer fix), relay subscriber, step-C scripts | step C | the relay UE and remote UE; ping 77-86 ms and 316 kbit/s up through the relay over the boards |
| Link ids in the air header, `sl_bridge --role relay`, v5 repeater firmware, receiver re-arm gap | daisy chain | several PC5 pairs on one channel; two hops measured (A: 94 ms ping, 100 kbit/s; B: 68 ms, 121 kbit/s) |
| gNB-granted sidelink windows: `gnb_bridge --sl-window`, AIR_SLWIN; window-gated, paced air scheduler (`agv_chain.py air_loop`, `--guard-ms`) | option 2 | Uu and PC5 share one channel per board; sl_bridge's exchanges must move into the windows |
| Coverage switching: stop the old pivot's UE, start the new one, wait for its session; gNB releases a vanished UE (UL failure after 10-12 PUSCH DTX, RRC Release ~1.3 s later); attach in 2.5-5.5 s | option 2 | the orchestration for role changes, which in option 3 also restart the sidelink UEs with new roles |
| Positions, election with hysteresis, beacons, LEDs by role, JSONL logs, clock offsets, report and page replay | option 2 | unchanged |
| oai-nrue1..3 containers, IMSIs 036-038 in free5GC, UE1/UE2/BLE1 boards as A/B/C | option 2 | the Uu side of each AGV |

## What does not exist yet

1. **One UE with Uu and PC5 over the boards.** The boards' Uu shim (`nrlink_ue.c`, `nrlink_gnb.c`) is in the develop
   tree; the sidelink and the U2N relay are in the separate sidelink release tree (`~/oai/sl-5g-nr`). In step C the
   relay's Uu ran on rfsim, all on the UE PC. Port `nrlink_ue.c` (462 lines) into the release tree's UE, or the
   sidelink + relay patches into develop. The gNB can stay the live develop one if the remote's traffic rides the
   relay's own DRB (as in step C, where the remote uses the relay's PDU address).
2. **One slot clock per UE.** In mode 1 the relay's sidelink timing comes from the gNB, and over the boards the gNB
   runs about 8x slower than real time (nFAPI slots wait for the air: 240 slots/s, 13-15 windows/s with the sidelink
   window). `nrlink_sl.c` runs the sidelink in real time (RUN every 1 ms). Either the sidelink follows the slowed Uu
   clock (mode-1 grants then arrive on the slowed timeline; to be checked) or the sidelink stays in mode 2 on its own
   clock. This is the main technical risk.
3. **Two hops.** OAI's U2N relay is one hop: remote <-> relay <-> gNB. A UE cannot be remote and relay at once. A chain
   (remote -> relay -> pivot) needs, at the middle AGV, two sidelink UE processes (a remote toward the pivot and a
   relay toward the far AGV) joined by IP routing, each on its own link id over the same board. About a CPU core per
   OAI UE process; the UE PC has 22.
4. **Roles fixed at start.** Sync reference, mode, `--is-relay-ue`, `--relay-type` are command-line choices. Every role
   change means restarting the sidelink UEs with new roles, plus sidelink sync (PSBCH every 60 ms) and the relay's
   attach. Expect seconds per change, like option 2's attach, but for more processes at once.
5. **The air budget.** Step B's exchanges run free (about 268 a second idle). Inside 25 ms windows every 67-77 ms the
   sidelink gets about a third of the air, shared by two PC5 pairs and the beacons.

## Order of work, if taken on

1. Port `nrlink_ue.c` into the release tree; attach a release-tree UE through the live gNB over a board (Uu only).
2. Same UE with mode-1 sidelink to one remote over `nrlink_sl.c`, sl_bridge gated into the gNB windows: step C over
   the boards end to end, one hop. Settles the clock question (2).
3. Two PC5 pairs with IP routing at the middle AGV (3), on link ids 0 and 1.
4. Role changes driven by agv_chain's election: restart the affected UE processes with new roles; log each phase as
   option 2 does; replay on the page.

## Built (12 Nov 2026): `agv_chain.py ue --uu per-agv --in-coverage 2 --pc5 oai`

Hops over OAI's NR sidelink, mode 2: six release-tree sidelink nrUEs (agvc-sl{0,1,2}{m,p}, pairs A-B, B-C, C-A, sync
reference + nearby UE, all up for the whole run), nrlink_sl.c PHY paced by the controller (sl_bridge.UE), one AIR_SLX
air message per board and window; a hop is UDP over the link's oaitun (slfwd sidecar), routed on by the AGV's computer
(layer-3 relay). Not done: mode 1 (gap 1 and 2 above), OAI's L2 relay (gap 3), beacons over the sidelink.
Runs (10 min, from minute 1): L1 every flow <= 1.6 % lost, hops <= 0.9 %; sidelink part 79 ms one hop, 195 ms two
(controller hops: 54, 122). L2 overloads the windows (7.6 kB/s of sidelink items): relay/remote 38-59 % lost.
Fixes: --egress-settle 8 (fresh UE as egress queued 3-7 s), beacons before sidelink items in each window.
Results: run_o3_L1, run_o3b_L2 (+ edge_, ctl_, trace_). Page card "Option 3" generated by scratchpad o3_fig.py.
