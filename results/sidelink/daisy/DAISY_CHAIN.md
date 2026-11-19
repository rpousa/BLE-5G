# Daisy-chaining nrlink boards (11-12 Nov 2026)

Goal: more than one radio hop between the ends. Tested with the step-B sidelink (two OAI mode-2 UEs, sync reference
-> nearby) carried over a two-hop chain: master bridge (sync-ref UE, UE1 board) -> middle node -> peer bridge (nearby
UE, UE2 board), all on GenFSK channel 0x5A. The middle node was the gNB board (3TJ2B15JGASV3, core PC), borrowed
while UE1/UE2 were borrowed anyway (live NR down for the test, BLE untouched).

```
 sync-ref UE -- sl_bridge master --link 0-->  middle node  --link 1--> sl_bridge peer -- nearby UE
                (UE1 board)                   A: host relay (sl_bridge --role relay, nrlink v4)
                                              B: nrlink v5 repeater (firmware only, configured by repeater.py)
```

The ends are the same in A and B: the master polls on link 0, the peer answers on link 1. The link id is the
`ue_id` byte of the air header, so every bridge ignores the other hop's packets (all boards hear each other here).

## A: host relay (`sl_bridge.py --role relay`)

A relay node runs no UE: it answers polls from above like a peer and polls the hop below like a master, passing
items through. What it needed beyond the obvious:
- **Age limit at the relay.** Items that waited more than 100 ms are dropped before they take air time, as at the
  ends. Without it, overload collapsed delivery (400 kbit/s UDP up -> 20 kbit/s delivered): everything forwarded was
  already too old for the UE.
- **One exchange at a time.** The lower hop polls only right after the relay has answered a poll from above (or
  every 50 ms). Free-running idle polls on the lower hop filled the channel and starved the master (7 polls/s).
- **Carrier sense at the master** (`--sense 0.015`): its polls wait for 15 ms of quiet, longer than the peer's
  turnaround, so they do not land in the lower hop's exchange. Default 0, so single-hop runs are unchanged.
- **Short air messages** (`--air-packets 6`, default 20). With 20-packet messages one round of the chain takes about
  200 ms at full load, past the 100 ms age limit: delivery collapsed instead of saturating.

## B: firmware repeater (nrlink v5)

`firmware/sdk/.../genfsk/nrlink/nrlink.c`, image `firmware/images/nrlink_v5.bin`, diff `nrlink_v4_to_v5.patch`.
v5 is v4 plus one command, so the bridges and v4 boards work with it unchanged:
- `0x07 REPEAT <flags><n>{<kind><from link><to link>}*n` (reply `0x87 <n>`; `n = 0` turns it off). `repeater.py`
  sets the two rules `(poll 0x04, L -> L+1)` and `(reply 0x05, L+1 -> L)` and prints the counters (STATS grows to 36 B:
  repeated packets, queue full, messages).
- A matching packet is stored with its link byte rewritten. When a message's last fragment arrives, the stored
  packets are sent back to back from RAM (no UART), then the radio listens again. Nothing goes to the host.

Fixes found on the boards (the raw probe `rep_probe.py` sends one message and logs what the far board hears):
- **Flush timeout.** A message whose last fragment is lost is forwarded after a timeout. At 10 ms on the 5 ms tick it
  fired between a host's fragments (send, wait for TX done, next: up to about 6 ms) and forwarded half messages over
  the sender's next fragment. Now 30 ms.
- **Re-arm gap.** The receivers hear the same last packet as the repeater, and need time to re-arm before the first
  repeated one; the master missed fragment 0 of every multi-packet reply. The repeater now waits before and between
  repeated packets (flags bits 4-7, 200 us steps, default 800 us; the measurements used 1.2 ms).
- **Master reply timeout** 100 ms (`--reply-timeout 0.1 --frag-gap 0.02`): a reply now crosses store-and-forward,
  and the default 30 ms caused resends that cut the replies being collected.

## Results (nearby UE 10.0.0.100 -> sync reference 10.0.0.1)

Ping is avg (min) round trip; UDP is what was delivered at the offered rate.

| | one hop (step B) | A, hop 0 boards + hop 1 UDP | A, three boards | B, firmware repeater |
|---|---|---|---|---|
| ping 56 B | 28 (18) ms | 46 (38) ms | 94 (80) ms | 68 (58) ms |
| ping 500 B | 49 (40) ms | 64 (57) ms | 147 (126) ms | 109 (91) ms |
| ping 1400 B | 89 (81) ms | 107 (97) ms | 250 (217) ms | 188 (160) ms |
| UDP up, clean | 300 kbit/s | 200 kbit/s (300: 1 % loss) | 100 kbit/s | 100 kbit/s (1 % loss) |
| UDP up, saturated | ~320 kbit/s | ~296 kbit/s | ~101 kbit/s | ~121 kbit/s |
| UDP down, saturated | ~318 kbit/s | ~300 kbit/s | ~96 kbit/s | ~125 kbit/s |
| TCP up / down | 276 / 280 kbit/s | 209 / 210 kbit/s | 63 / 10 kbit/s | 35 / 5 kbit/s |

Two-hop chain over UDP only (no boards, `measure_chain_udp.log`): 16 ms ping, 3 Mbit/s UDP both ways clean, TCP
6.15 / 5.87 Mbit/s, the same as one hop: the relay logic costs nothing; the air does.

What it says:
- Over the air both chains saturate at a third of one hop or a little more, which is what one shared channel and a
  half-duplex middle node give (each item crosses the air twice and the middle node cannot receive while it sends).
  Both hold their ceiling under overload instead of collapsing.
- **B beats A** by 25-30 ms per round trip and about 20 % in throughput: the repeater forwards from RAM at air speed,
  while the host relay pays the 1 Mbaud UART twice per hop and its host's scheduling.
- **TCP is poor through either chain** (5-60 kbit/s). Each hop is stop-and-wait with a 100 ms age limit, so under a
  TCP burst items are dropped and TCP backs off hard. A chain would want per-hop windows (several messages in flight)
  or a larger age budget for relayed traffic. Not done.
- For more hops: B extends by adding repeaters with `--link 1`, `--link 2`, ... (each maps L <-> L+1), but on one
  channel neighbouring repeaters collide, so expect about 1/(N+1) of one hop. Separate channels per hop need a radio
  that can listen on two channels, which these boards cannot.

## Files

| file | what |
|---|---|
| `nrlink/sl_bridge.py` | `--role relay`, `--link`, `--sense`, `--air-packets`, `--relay-idle`, `--down-air udp|board`, `--debug-air` |
| `nrlink/repeater.py`, `nrlink/nrlink.py` | configure a v5 board as repeater; `Board.repeat()` |
| `firmware/.../nrlink.c`, `firmware/images/nrlink_v5.bin`, `nrlink_v4_to_v5.patch` | v5 firmware |
| `sl_chain_restart.sh` (UE PC `~/oai/sl-5g-nr`) | `AIR=udp` (all UDP), `AIR=board` (hop 0 boards, hop 1 UDP), `AIR=ends` (middle node elsewhere) |
| `measure_chain_*.log`, `relay_3board.log`, `repeater_counters.log` | the runs; `*_no_relay_age_limit`, `*_3board_20pkt`, `*_repeater_before_timeout_fix` are the failed ones |

Run B: on the core PC stop `gnb_bridge.py`, flash v5 to the middle board (`~/nxp/board.sh isp|backup|flash`), then
`repeater.py <serial> --channel 0x5A --gap-us 1200`; on the UE PC stop ue_bridge 1/2 and
`MASTER_EXTRA="--sense 0.015 --air-packets 6 --reply-timeout 0.1 --frag-gap 0.02" PEER_EXTRA="--air-packets 6" AIR=ends bash sl_chain_restart.sh`.
Run A three boards: the same ends, and on the middle board `sl_bridge.py --role relay --link 0 --board <serial> --air-packets 6`.

## Afterwards

The gNB board went back to nrlink v4; its flash read back identical to the backup taken before the test
(`~/nxp/backups/3TJ2B15JGASV3_20261111-175256.bin`). gnb_bridge, ue_bridge 1/2, nrUE1/2 (12.1.1.17 / 12.1.1.18) and
agv-fwd-nr1/2 were restarted.

## Options not built

- **C, two boards per relay node** (one per hop, on separate channels): about one hop's rate, at double the boards.
- **D, 3GPP relays per hop**: OAI's U2N relay is single hop (step C); multi-hop means IP routing between chained OAI
  sidelink pairs, about a CPU core per OAI UE.
