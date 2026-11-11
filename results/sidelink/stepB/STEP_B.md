# Sidelink step B: OAI NR sidelink mode 2 over the nrlink boards (11 Nov 2026)

Step A ran two OAI sidelink UEs over rfsim. Here the same two UEs, unchanged above L1, talk over two FRDM-MCXW72
boards (the nrlink UE1 and UE2 boards, borrowed for the test, on GenFSK channel 0x5A = 2450 MHz, away from nrlink's 2402 MHz).

## How

| piece | where | what |
|---|---|---|
| `nrlink_sl.c` | sidelink tree `executables/` | L1 emulation, on with `NRLINK_SL=<bridge ip>:<port>`, replaces `UE_thread_sl` / `UE_RU_thread_sl` |
| `sl-tree-hooks.patch` | `executables/nr-ue.c`, `CMakeLists.txt` | starts `nrlink_sl_thread` when `NRLINK_SL` is set; adds the file to nr-uesoftmodem |
| `sl_bridge.py` | `nrlink/` (UE PC `~/oai/nrlink`) | one per UE: paces its UE in real time, carries its items over a board |

Per slot the UE's MAC runs exactly as with a radio. On TX, what the MAC hands the PHY is captured instead of modulated:
the PSBCH payload, or SCI 1-A + SCI 2 + the SL-SCH transport block (trailing zero padding stripped).
On RX, items from the other UE are fed to the MAC as the PHY reports decodes:
- PSBCH becomes an SSB rx indication;
- PSCCH becomes the SCI 1-A indication, then, when the MAC asks for them, SCI 2 and then the transport block with CRC ok.

An item waits at the UE for the first slot whose RX action fits it. The MAC takes PSBCH and SCI timing from their
contents, so the milliseconds of air time do not matter. The nearby UE synchronises on the first PSBCH it receives.

On the air, the sync-reference side's bridge (master) polls and the nearby side's bridge answers, so only one board talks at
a time. Each air message holds up to 20 radio packets of 248 B. The exchange is stop and wait: a lost poll or reply is
sent again, and a repeated poll gets the same reply, so nothing is lost or duplicated. Items that waited more than
200 slots (100 ms) for the air are dropped before they cost air time.

Run (UE PC): `sl_board_restart.sh` (both bridges plus both UE containers), `sl_measure.sh` (ping and iperf3).
`sl_udp_restart.sh` runs the same thing with UDP in place of the boards.

## Results (nearby UE 10.0.0.100 -> sync reference 10.0.0.1, over the boards)

| test | boards (step B) | rfsim (step A) |
|---|---|---|
| ping 56 B | 0 % loss, 18 / 28 / 35 ms | 5 / 8 / 12 ms |
| ping 500 B | 0 % loss, 40 / 49 / 54 ms | 6 / 9 / 12 ms |
| ping 1400 B | 0 % loss, 81 / 89 / 95 ms | 7 / 9 / 11 ms |
| UDP up 100 / 200 / 300 kbit/s | all delivered | |
| UDP up 400 / 600 kbit/s | 320 / 317 delivered (saturates) | ceiling 7.8 Mbit/s |
| UDP down 200 / 300 / 400 kbit/s | 201 / 300 / 318 | |
| TCP up / down, 30 s | 276 / 280 kbit/s | 6.9 / 6.6 Mbit/s |
| ping 56 B under TCP | 10 % loss, avg 156 ms (47-262) | |

The ceiling is the boards' air time: about 330 kbit/s of items in both directions together.
The UEs' sidelink could carry 7.8 Mbit/s, and nothing tells its MAC about the smaller pipe.
Past the ceiling, delivery holds at about 320 kbit/s instead of collapsing.

## Bugs found on the way (fixed)

- The UE PC clock is about 8.8 h ahead, so ninja skipped the copied `nr-ue.c`: touch files after copying them.
- A bridge latched onto a HELLO from the previous container; a new HELLO now gets the RUN at once.
- The peer took its slot clock from a PSBCH that had waited in its queue (240 ms behind, every item expired);
  it now re-aligns on every PSBCH.
- A 6-packet reply outlasted the 30 ms reply timeout: the master resent its poll over the reply (half duplex).
  The master now waits while packets keep arriving.
- Overload: the outbox grew past one air message (more than 255 packets), and RUNs past the UE's 60 KB buffer.
  Both are now bounded.
- Segfault on TCP start: `nrlink_sl.c` freed the TX PSFCH list for every PSCCH/PSSCH. The PHY frees it only for the
  PSFCH action; otherwise it holds garbage.

## Afterwards

Sidelink stopped; ue_bridge 1 / 2 restarted. nrUE1/2 had died while their bridges were away: an OAI assertion
(`pthread_join ... Cannot allocate memory`) when RRC dropped them to IDLE. They were restarted with `docker start`,
and agv-fwd-nr1/2 restarted to join their new network namespaces.

Logs: `master_board.log`, `peer_board.log`, `sl-syncref.log`, `sl-nearby.log`, `measure_boards.log`.
