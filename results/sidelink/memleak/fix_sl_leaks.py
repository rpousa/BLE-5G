#!/usr/bin/env python3
"""Fix the memory leaks of the OAI sidelink mode-2 UE (sl-5g-nr tree), nrlink 2026-11-23.
Each edit must match exactly once; nothing is written unless all edits of a file match."""
import shutil, sys, os

T = os.path.expanduser("~/oai/sl-5g-nr")
EDITS = {
"openair2/LAYER2/NR_MAC_UE/nr_ue_scheduler.c": [
    # the selected resource points into mac->sl_candidate_resources, which is replaced (and now freed) further down
    ("  sl_resource_info_t *resource = NULL;\n  bool resource_available = false;\n",
     "  sl_resource_info_t *resource = NULL;\n  sl_resource_info_t resource_copy; /* nrlink: the list it comes from may be freed below */\n"
     "  bool resource_available = false;\n"),
    ("      resource = get_resource_element(mac->sl_candidate_resources, frame_slot);\n      if (resource) {\n",
     "      resource = get_resource_element(mac->sl_candidate_resources, frame_slot);\n      if (resource) {\n"
     "        resource_copy = *resource;\n        resource = &resource_copy;\n"),
    # the previous candidate list was dropped on every reselection (dominant leak, ~650 MB/h per UE)
    ("          mac->sl_candidate_resources = get_candidate_resources(&frame_slot, mac, &mac->sl_sensing_data, &mac->sl_transmit_history);\n",
     "          List_t *old_rsrc = mac->sl_candidate_resources; /* nrlink: was leaked on every reselection */\n"
     "          mac->sl_candidate_resources = get_candidate_resources(&frame_slot, mac, &mac->sl_sensing_data, &mac->sl_transmit_history);\n"
     "          if (old_rsrc && old_rsrc != mac->sl_candidate_resources) {\n"
     "            free_list_mem(old_rsrc);\n            free(old_rsrc);\n          }\n"),
    # get_candidate_resources_from_slots: scratch element
    ("        push_back(nr_resource_list, rsrc_info);\n    }\n  }\n  return nr_resource_list;\n}\n",
     "        push_back(nr_resource_list, rsrc_info);\n    }\n  }\n  free(rsrc_info); /* nrlink */\n  return nr_resource_list;\n}\n"),
    # get_candidate_resources: candidate slots, per-candidate projections, sensing projections
    ("  if (candidate_slots.size == 0 ) {\n    return NULL;\n  }\n",
     "  if (candidate_slots.size == 0 ) {\n    free_list_mem(&candidate_slots); /* nrlink */\n    return NULL;\n  }\n"),
    ("  print_candidate_list(candidate_resources, __LINE__);\n\n  uint64_t m_total",
     "  print_candidate_list(candidate_resources, __LINE__);\n  free_list_mem(&candidate_slots); /* nrlink */\n\n  uint64_t m_total"),
    ("      if (!erased) {\n        // Only need to increment if not erased above",
     "      free_list_mem(resource_info_list); /* nrlink */\n      free(resource_info_list);\n"
     "      if (!erased) {\n        // Only need to increment if not erased above"),
    ("  LOG_D(NR_MAC, \"%ld resources selected after sensing resource selection from %ld slots\\n\", remaining_candidates->size, m_total);\n  return remaining_candidates;\n",
     "  LOG_D(NR_MAC, \"%ld resources selected after sensing resource selection from %ld slots\\n\", remaining_candidates->size, m_total);\n"
     "  free_vector(&sensing_data_projections); /* nrlink */\n  return remaining_candidates;\n"),
],
"openair2/LAYER2/NR_MAC_UE/nr_ue_procedures.c": [
    # 8 bytes allocated for a 32-byte List_t (heap overflow)
    ("  mac->sl_candidate_resources = (List_t*)malloc16_clear(sizeof(List_t*));\n",
     "  mac->sl_candidate_resources = (List_t*)malloc16_clear(sizeof(List_t)); /* nrlink: was sizeof(List_t*) */\n"),
],
"executables/nrlink_sl.c": [
    ("  UE->if_inst->sl_indication(&ind);\n  st.rx_slsch++;\n",
     "  UE->if_inst->sl_indication(&ind);\n  free(s->ack_nack_rcvd); /* processed synchronously above */\n  st.rx_slsch++;\n"),
],
}

ok = True
new = {}
for f, eds in EDITS.items():
    src = open(os.path.join(T, f)).read()
    for old, rep in eds:
        n = src.count(old)
        if n != 1:
            print(f"{f}: edit matches {n} times: {old[:70]!r}"); ok = False; continue
        src = src.replace(old, rep)
    new[f] = src
if not ok:
    sys.exit("nothing written")
for f, src in new.items():
    p = os.path.join(T, f)
    if not os.path.exists(p + ".pre-leak"):
        shutil.copy2(p, p + ".pre-leak")
    open(p, "w").write(src)
    print("patched", f)
