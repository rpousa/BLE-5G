# Sidelink UE memory leak (23 Nov 2026)

## What happened

The live `cover` run (sidelink mode 1, L1, 25 ms window) stopped on 20 Nov at 17:52 (UE PC time)
and stayed down for about 2.5 days. Causes, in order:

1. The six OAI sidelink UEs (`agvc-sl{0,1,2}{m,p}`, one pair per PC5 link) grew from about 370 MB
   to about 2.4 GB each. On the UE PC that filled all of the RAM (15.5 GB) and all of the swap (15.9 GB).
2. The AGV controller (`agv_chain.py ue`) died. A board call timed out:
   `sl_config` -> `_wait(MSG_SLCFGOK)` -> `queue.Empty` (`nrlink.py:147`). Nothing restarted it.
3. Without traffic the gNB PNF later lost its process. Docker still reported `oai-pnf` as running,
   but `docker top` was empty and the VNF logged `time client: connection ... failed` every second.

Recovery: `nrlink.sh live stop` on the UE PC, `nrlink.sh restart --role core` on the core PC,
`nrlink.sh live start` on the UE PC.

## Diagnosis

- `slmem.sh` (every 30 s: RSS, VSZ and threads of each sidelink `nr-uesoftmodem`) measured
  **about 650 MB/h per UE** as soon as sidelink traffic flowed.
- `/proc/<pid>/smaps` diff (through `docker exec`, the UEs run as root): `[heap]` stayed flat. One
  anonymous mapping, a glibc per-thread arena, went from 0 to 24 MB in 4 minutes, so the leak comes
  from a worker thread.
- A dump of that arena (`dd if=/proc/7/mem`), walked chunk by chunk: 17,362 chunks of 304 B, each an
  array of `sl_resource_info_t`, nearly one-to-one with 48-byte chunks (`List_t` headers). That points
  to the mode-2 resource selection.

## Leaks fixed (`sl-tree-memleak.patch`, applied by `fix_sl_leaks.py`)

| Where | Leak |
|---|---|
| `nr_ue_scheduler.c`, sidelink TX scheduling | `mac->sl_candidate_resources` replaced on every reselection without freeing the old list (the dominant leak). The selected `resource` pointed into that list, so it is now copied to a local struct first. |
| `get_candidate_resources_from_slots()` | the `rsrc_info` scratch element |
| `get_candidate_resources()` | the `candidate_slots` data (both return paths), one `resource_info_list` per candidate, the `sensing_data_projections` vector |
| `nr_ue_procedures.c:184` | `malloc16_clear(sizeof(List_t*))`: 8 bytes for a 32-byte `List_t` (heap overflow, not a leak) |
| `executables/nrlink_sl.c` (our hook) | `ack_nack_rcvd`, 1 byte per received PSSCH. `sl_indication` processes it synchronously, so it is freed right after the call. |

Not fixed, because the `*_nofb` configs (`sl_PSFCH_Period = 0`) never call them:
`compute_params()` (`prbs_sets` never freed), `configure_psfch_params_rx()` (`psfch_params` never
freed), and upstream PHY `nr_postDecode_slsch()` (`ack_nack_rcvd`, a path our hook replaces).

## Result

Rebuilt `build_sl/nr-uesoftmodem` (the old binary is kept as `nr-uesoftmodem.pre-leak` on the UE PC).
Same run, 10 minutes after the restart:

| UE (PID) | before | after, last 5 min |
|---|---|---|
| 24734 | about 650 MB/h | 0.1 MB/h |
| 25011 | about 650 MB/h | 0.8 MB/h |
| 25199 | about 650 MB/h | 0.5 MB/h |
| 25443 | about 650 MB/h | 19.5 MB/h |
| 25734 | about 650 MB/h | 0.6 MB/h |
| 25969 | about 650 MB/h | 0.2 MB/h |

Open: UE 25443 still creeps up (367 -> 370 MB over the next 10 minutes). That could be a smaller
leftover leak on whichever side holds a particular role, or the arena still settling. Even at
20 MB/h it would take days to matter. `slmem.txt` on the UE PC keeps recording.

## Files

- `sl-tree-memleak.patch`: diff against the step C tree (`../stepC/sl-tree-stepC.patch` + `../stepC/nrlink_sl.c`)
- `nrlink_sl.c`: the hook as it is now
- `fix_sl_leaks.py`: applies the patch to `~/oai/sl-5g-nr`. It writes nothing unless every edit
  matches exactly once, and it keeps `*.pre-leak` backups.
- `slmem.sh`: the memory sampler
