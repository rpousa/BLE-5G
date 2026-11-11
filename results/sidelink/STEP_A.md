# Sidelink step A: OAI NR sidelink mode 2 over rfsim (11 Nov 2026)

Source: OAI tag `sl-release-2025w49` (EpiSci, branch `sl-5g-nr`, commit 1c43a54, 11 Dec 2025), cloned on the UE PC at
`~/oai/sl-5g-nr`. Built in container `sl-dev` (image `ran-base:local`, cores 12-21) with cmake + ninja in `build_sl/`:
nr-uesoftmodem, nr-softmodem, rfsimulator and libraries, no source changes.

Run: `sl_run.sh start` on the UE PC. Two containers on Docker network `sl-net` (172.30.99.0/24), no gNB and no core:

| UE | container | cores | sidelink address |
|---|---|---|---|
| sync reference (`--sync-ref`, `sl_sync_ref_nofb.conf`) | sl-syncref | 12-16 | oaitun_ue1 10.0.0.1 |
| nearby UE (`sl_ue1_nofb.conf`) | sl-nearby | 17-21 | oaitun_ue2 10.0.0.100 |

Both `--sa --sl-mode 2 --rfsim --thread-pool -1,-1,-1,-1`; 30 kHz, 106 PRB, one 100-PRB subchannel, 2594.98 MHz.
Each UE takes about one core.

## Results

The nearby UE synchronised to the sync-reference UE over the sidelink (PSBCH) and both opened their sidelink tun.

**With the release's configs** (PSFCH period default, CSI acquisition on): pings worked but 30-40 % were lost, RTT 8-38 ms.
The sync-reference UE logged 43 "SLSCH 0 in error" (decode failures of the nearby UE's data); the other direction none.
The release README lists such errors as a known issue while PSFCH is enabled.

**With PSFCH and CSI off** (`*_nofb.conf`: `sl_PSFCH_Period = 0` in both pools, `sl_CSI_Acquisition = 1`, README's
"CSI Disabled 0"), no decode errors on either side:

| test, nearby UE -> sync-reference UE | result |
|---|---|
| ping 56 B, 20 pings | 0 % loss, RTT 5.1 / 8.1 / 12.0 ms (min / avg / max) |
| ping 500 B | 0 % loss, 5.8 / 8.6 / 11.8 ms |
| ping 1400 B | 0 % loss, 6.5 / 8.9 / 11.0 ms |
| UDP 1 / 5 Mbit/s, 1200 B | all delivered, 0 % loss |
| UDP 10 / 20 / 40 Mbit/s | 7.24 / 7.77 / 7.80 Mbit/s delivered (saturates at about 7.8) |
| TCP nearby -> sync-ref, 15 s | 6.89 Mbit/s |
| TCP sync-ref -> nearby, 15 s | 6.64 Mbit/s |

The ceiling fits the release's limits: one subchannel, one PDU per slot, MCS up to 9.
No HARQ feedback means no retransmission: fine over rfsim's clean channel, not over a real one.

Logs: `sl-syncref_stepA.log`, `sl-nearby_stepA.log` (the run without feedback).

## Next (step B)

Carry this over the nrlink boards: port the nrlink patch onto this tree and give `nrlink_ue.c` sidelink items
(PSBCH payload, SCI 1 / SCI 2, SL-SCH transport block, PSFCH), with the sync-reference UE's bridge running the slot clock.
