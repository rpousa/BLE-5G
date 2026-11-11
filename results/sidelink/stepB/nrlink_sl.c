/*
 * nrlink sidelink: L1 emulation for NR sidelink mode 2 over the nrlink boards (FRDM-MCXW72, GenFSK).
 *
 * Enabled with NRLINK_SL=<bridge ip>:<port> (nr-uesoftmodem --sl-mode 2). It replaces UE_thread_sl and
 * UE_RU_thread_sl. The slot loop is driven by the local sl_bridge.py: a RUN message names the absolute slot to
 * reach and carries the items the peers sent for future slots. For every absolute slot k the UE runs, as the
 * real loop does, the MAC's TX indication for slot k + DURATION_RX_TO_TX and its RX indication for slot k:
 *   TX: the MAC's sidelink TX request (PSBCH payload, or SCI 1-A + SCI 2 + SL-SCH transport block) is captured
 *       instead of being modulated, and returned to the bridge as an item for slot k + DURATION_RX_TO_TX.
 *   RX: items received for slot k are fed to the MAC exactly as the PHY reports decodes: PSBCH -> SSB rx
 *       indication; PSCCH -> SCI 1-A indication, then (the MAC asks for SCI 2) SCI 2 indication, then (the MAC
 *       asks for the SL-SCH) the transport block as an rx indication with CRC ok.
 * The nearby UE starts unsynchronised and synchronises on the first PSBCH item it receives.
 * Transport blocks travel without their trailing zero padding (run without --rfsim: the MAC then pads with 0).
 *
 * Messages (UDP, little endian): header <BBHIB> magic 'S', type, n_items, abs_slot, flags; items <BIH> type,
 * target abs slot, length, then the data. Types: 1 HELLO (UE->bridge, flags bit0 = sync reference),
 * 2 RUN (bridge->UE), 3 DONE (UE->bridge: abs_slot reached; flags bit0 = the TX slot of abs_slot is a
 * sidelink slot, bit1 = synchronised; items = TX items).
 */

#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "PHY/defs_nr_UE.h"
#include "PHY/impl_defs_top.h"
#include "executables/nr-uesoftmodem.h"
#include "SCHED_NR_UE/defs.h"
#include "executables/softmodem-common.h"
#include "openair2/RRC/NR_UE/rrc_proto.h"
#include "openair2/LAYER2/nr_pdcp/nr_pdcp_oai_api.h"
#include "common/utils/LOG/log.h"

void nr_rlc_tick(int frame, int subframe);

#define SLM_MAGIC 0x53
#define SLM_HELLO 1
#define SLM_RUN 2
#define SLM_DONE 3
#define SLI_PSBCH 1
#define SLI_PSCCH_PSSCH 2
#define SL_MAX_MSG 60000
#define SL_MAX_PEND 512
#define SL_MAX_AGE 800 /* slots an item may be late (air and queueing) and wait for a fitting slot */
#define SL_MAX_CATCHUP 400

typedef struct __attribute__((packed)) {
  uint8_t magic, type;
  uint16_t n_items;
  uint32_t abs_slot;
  uint8_t flags;
} slm_hdr_t;

typedef struct __attribute__((packed)) {
  uint8_t type;
  uint32_t slot;
  uint16_t len;
} sli_hdr_t;

typedef struct __attribute__((packed)) {
  uint32_t payload;
  uint16_t slss_id;
} sli_psbch_t;

typedef struct __attribute__((packed)) {
  uint8_t sci1_bits;
  uint8_t sci1[8];
  uint8_t sci2_bits;
  uint8_t sci2[8];
  uint8_t harq_pid;
  uint32_t tb_size;
  /* data (tb without trailing zeros) follows */
} sli_pssch_t;

/* items received for future slots */
typedef struct {
  uint32_t slot;
  uint8_t type;
  uint16_t len;
  uint8_t *data;
} pend_t;

static int s_sock = -1;
static struct sockaddr_in s_bridge;
static uint8_t s_rx[SL_MAX_MSG], s_tx[SL_MAX_MSG];
static size_t s_tx_len;
static uint16_t s_tx_items;
static pend_t s_pend[SL_MAX_PEND];
static uint8_t s_tb[16384];
static struct {
  uint32_t tx_psbch, tx_pssch, rx_psbch, rx_sci1, rx_sci2, rx_slsch, dropped;
  uint32_t act[SL_NR_CONFIG_TYPE_RX_MAXIMUM + 1], rx_slots, late_max;
} st;

static void tx_item(uint8_t type, uint32_t slot, const void *a, size_t alen, const void *b, size_t blen)
{
  if (s_tx_len + sizeof(sli_hdr_t) + alen + blen > SL_MAX_MSG) {
    LOG_E(NR_PHY, "nrlink_sl: TX message full, item dropped\n");
    return;
  }
  sli_hdr_t h = {.type = type, .slot = slot, .len = alen + blen};
  memcpy(s_tx + s_tx_len, &h, sizeof(h));
  s_tx_len += sizeof(h);
  memcpy(s_tx + s_tx_len, a, alen);
  s_tx_len += alen;
  if (blen) {
    memcpy(s_tx + s_tx_len, b, blen);
    s_tx_len += blen;
  }
  s_tx_items++;
}

static void send_msg(uint8_t type, uint32_t abs_slot, uint8_t flags)
{
  slm_hdr_t h = {.magic = SLM_MAGIC, .type = type, .n_items = s_tx_items, .abs_slot = abs_slot, .flags = flags};
  memcpy(s_tx, &h, sizeof(h));
  if (sendto(s_sock, s_tx, s_tx_len, 0, (struct sockaddr *)&s_bridge, sizeof(s_bridge)) < 0)
    LOG_E(NR_PHY, "nrlink_sl: sendto: %s\n", strerror(errno));
  s_tx_len = sizeof(slm_hdr_t);
  s_tx_items = 0;
}

static void pend_add(uint8_t type, uint32_t slot, const uint8_t *data, uint16_t len)
{
  LOG_D(NR_PHY, "nrlink_sl: item type %d for slot %u, %u B\n", type, slot, len);
  for (int i = 0; i < SL_MAX_PEND; i++)
    if (!s_pend[i].data) {
      s_pend[i] = (pend_t){.slot = slot, .type = type, .len = len, .data = malloc(len ? len : 1)};
      memcpy(s_pend[i].data, data, len);
      return;
    }
  st.dropped++;
  LOG_W(NR_PHY, "nrlink_sl: pending items full, item for slot %u dropped\n", slot);
}

/* ---- TX: capture the MAC's sidelink TX request for the TX slot of this iteration ---- */
static void run_tx(PHY_VARS_NR_UE *UE, UE_nr_rxtx_proc_t *proc, uint32_t abs_tx)
{
  nr_phy_data_tx_t phy_data = {0};
  nr_sidelink_indication_t ind = {0};
  ind.module_id = UE->Mod_id;
  ind.gNB_index = proc->gNB_id;
  ind.cc_id = UE->CC_id;
  ind.frame_rx = proc->frame_rx;
  ind.slot_rx = proc->nr_slot_rx;
  ind.frame_tx = proc->frame_tx;
  ind.slot_tx = proc->nr_slot_tx;
  ind.phy_data = &phy_data;
  ind.slot_type = SIDELINK_SLOT_TYPE_TX;
  UE->if_inst->sl_indication(&ind);

  sl_nr_ue_phy_params_t *sp = &UE->SL_UE_PHY_PARAMS;
  if (phy_data.sl_tx_action == SL_NR_CONFIG_TYPE_TX_PSBCH) {
    sli_psbch_t p = {.payload = *(uint32_t *)phy_data.psbch_vars.psbch_payload, .slss_id = phy_data.psbch_vars.tx_slss_id};
    tx_item(SLI_PSBCH, abs_tx, &p, sizeof(p), NULL, 0);
    sp->psbch.num_psbch_tx++;
    st.tx_psbch++;
  } else if (phy_data.sl_tx_action == SL_NR_CONFIG_TYPE_TX_PSCCH_PSSCH
             || phy_data.sl_tx_action == SL_NR_CONFIG_TYPE_TX_PSCCH_PSSCH_CSI_RS
             || phy_data.sl_tx_action == SL_NR_CONFIG_TYPE_TX_PSCCH_PSSCH_PSFCH) {
    sl_nr_tx_config_pscch_pssch_pdu_t *pdu = &phy_data.nr_sl_pssch_pscch_pdu;
    sli_pssch_t p = {.sci1_bits = pdu->pscch_sci_payload_len,
                     .sci2_bits = pdu->sci2_payload_len,
                     .harq_pid = pdu->harq_pid,
                     .tb_size = pdu->tb_size};
    memcpy(p.sci1, pdu->pscch_sci_payload, 8);
    memcpy(p.sci2, pdu->sci2_payload, 8);
    uint32_t n = pdu->tb_size;
    while (n > 0 && pdu->slsch_payload[n - 1] == 0)
      n--;
    tx_item(SLI_PSCCH_PSSCH, abs_tx, &p, sizeof(p), pdu->slsch_payload, n);
    if (pdu->harq_pid < NR_MAX_SLSCH_HARQ_PROCESSES)
      UE->sl_harq_processes[pdu->harq_pid].status = SCH_IDLE;
    if (phy_data.sl_tx_action == SL_NR_CONFIG_TYPE_TX_PSCCH_PSSCH_PSFCH) { /* as the PHY: only then is the list set */
      free(pdu->psfch_pdu_list);
      pdu->psfch_pdu_list = NULL;
    }
    sp->pscch.num_pscch_tx++;
    sp->pssch.num_pssch_sci2_tx++;
    sp->pssch.num_pssch_tx++;
    st.tx_pssch++;
    LOG_D(NR_PHY, "nrlink_sl: TX PSCCH/PSSCH for %d.%d, tb %u (%u sent)\n", proc->frame_tx, proc->nr_slot_tx, pdu->tb_size, n);
  }
}

/* ---- RX: feed the received items for this slot to the MAC as the PHY would ---- */
static void deliver_ssb(PHY_VARS_NR_UE *UE, UE_nr_rxtx_proc_t *proc, const sli_psbch_t *p)
{
  nr_sidelink_indication_t ind;
  sl_nr_rx_indication_t rx_ind = {0};
  uint8_t payload[4];
  memcpy(payload, &p->payload, 4);
  nr_fill_sl_indication(&ind, &rx_ind, NULL, proc, UE, NULL);
  nr_fill_sl_rx_indication(&rx_ind, SL_NR_RX_PDU_TYPE_SSB, UE, 1, proc, payload, p->slss_id);
  rx_ind.sfn = proc->frame_rx;
  rx_ind.slot = proc->nr_slot_rx;
  UE->SL_UE_PHY_PARAMS.psbch.rx_ok++;
  UE->if_inst->sl_indication(&ind);
  st.rx_psbch++;
}

static void deliver_pssch(PHY_VARS_NR_UE *UE, UE_nr_rxtx_proc_t *proc, nr_phy_data_t *phy, const uint8_t *data, uint16_t len)
{
  if (len < sizeof(sli_pssch_t))
    return;
  const sli_pssch_t *p = (const sli_pssch_t *)data;
  nr_sidelink_indication_t ind;

  /* SCI 1-A on PSCCH */
  sl_nr_sci_indication_t sci = {0};
  sci.sfn = proc->frame_rx;
  sci.slot = proc->nr_slot_rx;
  sci.sci_pdu[0].sci_format_type = SL_SCI_FORMAT_1A_ON_PSCCH;
  sci.sci_pdu[0].sci_payloadlen = p->sci1_bits;
  sci.sci_pdu[0].pscch_rsrp = -60;
  memcpy(sci.sci_pdu[0].sci_payloadBits, p->sci1, 8);
  sci.number_of_SCIs = 1;
  nr_fill_sl_indication(&ind, NULL, &sci, proc, UE, phy);
  UE->SL_UE_PHY_PARAMS.pscch.rx_ok++;
  UE->if_inst->sl_indication(&ind);
  st.rx_sci1++;
  if (phy->sl_rx_action != SL_NR_CONFIG_TYPE_RX_PSSCH_SCI) {
    LOG_D(NR_PHY, "nrlink_sl: %d.%d SCI 1-A not followed by an SCI 2 request (action %d)\n", proc->frame_rx, proc->nr_slot_rx, phy->sl_rx_action);
    return;
  }

  /* SCI 2 on PSSCH */
  memset(&sci, 0, sizeof(sci));
  sci.sfn = proc->frame_rx;
  sci.slot = proc->nr_slot_rx;
  sci.sci_pdu[0].sci_format_type = SL_SCI_FORMAT_2_ON_PSSCH;
  sci.sci_pdu[0].sci_payloadlen = p->sci2_bits;
  memcpy(sci.sci_pdu[0].sci_payloadBits, p->sci2, 8);
  sci.number_of_SCIs = 1;
  nr_fill_sl_indication(&ind, NULL, &sci, proc, UE, phy);
  UE->SL_UE_PHY_PARAMS.pssch.rx_sci2_ok++;
  UE->if_inst->sl_indication(&ind);
  st.rx_sci2++;
  if (phy->sl_rx_action != SL_NR_CONFIG_TYPE_RX_PSSCH_SLSCH && phy->sl_rx_action != SL_NR_CONFIG_TYPE_RX_PSSCH_SLSCH_PSFCH
      && phy->sl_rx_action != SL_NR_CONFIG_TYPE_RX_PSSCH_SLSCH_CSI_RS) {
    LOG_D(NR_PHY, "nrlink_sl: %d.%d SCI 2 not followed by an SL-SCH request (action %d)\n", proc->frame_rx, proc->nr_slot_rx, phy->sl_rx_action);
    return;
  }

  /* SL-SCH transport block, CRC ok */
  uint32_t tbs = phy->nr_sl_pssch_pdu.tb_size ? phy->nr_sl_pssch_pdu.tb_size : p->tb_size;
  if (tbs > sizeof(s_tb))
    tbs = sizeof(s_tb);
  uint16_t dlen = len - sizeof(*p);
  memset(s_tb, 0, tbs);
  memcpy(s_tb, data + sizeof(*p), dlen < tbs ? dlen : tbs);
  sl_nr_rx_indication_t rx_ind = {0};
  rx_ind.sfn = proc->frame_rx;
  rx_ind.slot = proc->nr_slot_rx;
  rx_ind.number_pdus = 1;
  uint8_t pdu_type = phy->sl_rx_action == SL_NR_CONFIG_TYPE_RX_PSSCH_SLSCH_PSFCH ? SL_NR_RX_PDU_TYPE_SLSCH_PSFCH : SL_NR_RX_PDU_TYPE_SLSCH;
  rx_ind.rx_indication_body[0].pdu_type = pdu_type;
  sl_nr_slsch_pdu_t *s = &rx_ind.rx_indication_body[0].rx_slsch_pdu;
  s->pdu = s_tb;
  s->pdu_length = tbs;
  s->harq_pid = phy->nr_sl_pssch_pdu.harq_pid;
  s->ack_nack = 1;
  s->ack_nack_rcvd = calloc(1, sizeof(uint8_t));
  s->num_acks_rcvd = 0;
  if (phy->psfch_pdu_list) {
    free(phy->psfch_pdu_list);
    phy->psfch_pdu_list = NULL;
  }
  nr_fill_sl_indication(&ind, &rx_ind, NULL, proc, UE, phy);
  UE->SL_UE_PHY_PARAMS.pssch.rx_ok++;
  UE->if_inst->sl_indication(&ind);
  st.rx_slsch++;
}

static void run_rx(PHY_VARS_NR_UE *UE, UE_nr_rxtx_proc_t *proc, uint32_t abs_rx)
{
  NR_DL_FRAME_PARMS *fp = &UE->SL_UE_PHY_PARAMS.sl_frame_params;
  if (proc->nr_slot_rx % fp->slots_per_subframe == 0) {
    nr_rlc_tick(proc->frame_rx, proc->nr_slot_rx / fp->slots_per_subframe);
    nr_pdcp_tick(proc->frame_rx, proc->nr_slot_rx / fp->slots_per_subframe);
  }
  nr_phy_data_t phy = {0};
  if (proc->rx_slot_type == NR_SIDELINK_SLOT) {
    nr_sidelink_indication_t ind;
    nr_fill_sl_indication(&ind, NULL, NULL, proc, UE, &phy);
    UE->if_inst->sl_indication(&ind);
    st.rx_slots++;
    if (phy.sl_rx_action <= SL_NR_CONFIG_TYPE_RX_MAXIMUM)
      st.act[phy.sl_rx_action]++;
  }
  /* Items keep their sender's slot number; they are delivered, oldest first, at the first slot from that number on
   * whose RX action fits them (the air takes milliseconds: the MAC takes PSBCH and SCI timing from their contents). */
  int best = -1;
  for (int i = 0; i < SL_MAX_PEND; i++) {
    pend_t *e = &s_pend[i];
    if (!e->data)
      continue;
    if ((int32_t)(abs_rx - e->slot) > SL_MAX_AGE) {
      LOG_W(NR_PHY, "nrlink_sl: item type %d for slot %u expired at slot %u\n", e->type, e->slot, abs_rx);
      free(e->data);
      e->data = NULL;
      st.dropped++;
      continue;
    }
    if ((int32_t)(e->slot - abs_rx) > 0 || proc->rx_slot_type != NR_SIDELINK_SLOT)
      continue;
    bool fits = (e->type == SLI_PSBCH && phy.sl_rx_action == SL_NR_CONFIG_TYPE_RX_PSBCH)
                || (e->type == SLI_PSCCH_PSSCH && phy.sl_rx_action == SL_NR_CONFIG_TYPE_RX_PSCCH);
    if (fits && (best < 0 || (int32_t)(e->slot - s_pend[best].slot) < 0))
      best = i;
  }
  if (best >= 0) {
    pend_t *e = &s_pend[best];
    if (abs_rx - e->slot > st.late_max)
      st.late_max = abs_rx - e->slot;
    if (e->type == SLI_PSBCH)
      deliver_ssb(UE, proc, (sli_psbch_t *)e->data);
    else
      deliver_pssch(UE, proc, &phy, e->data, e->len);
    free(e->data);
    e->data = NULL;
  }
  if (phy.psfch_pdu_list)
    free(phy.psfch_pdu_list);
}

/* drop items for slots already processed (missed) */
static void pend_expire(uint32_t abs_done)
{
  for (int i = 0; i < SL_MAX_PEND; i++)
    if (s_pend[i].data && (int32_t)(s_pend[i].slot - abs_done) <= 0) {
      free(s_pend[i].data);
      s_pend[i].data = NULL;
      st.dropped++;
    }
}

static int parse_items(const uint8_t *buf, size_t len, int n_items, bool *psbch_seen, uint32_t *psbch_slot, sli_psbch_t *psbch)
{
  size_t off = sizeof(slm_hdr_t);
  for (int i = 0; i < n_items; i++) {
    if (off + sizeof(sli_hdr_t) > len)
      return -1;
    sli_hdr_t h;
    memcpy(&h, buf + off, sizeof(h));
    off += sizeof(h);
    if (off + h.len > len)
      return -1;
    if (h.type == SLI_PSBCH && psbch_seen && h.len >= sizeof(sli_psbch_t)) {
      *psbch_seen = true;
      *psbch_slot = h.slot;
      memcpy(psbch, buf + off, sizeof(*psbch));
    }
    pend_add(h.type, h.slot, buf + off, h.len);
    off += h.len;
  }
  return 0;
}

void *nrlink_sl_thread(void *arg)
{
  PHY_VARS_NR_UE *UE = (PHY_VARS_NR_UE *)arg;
  NR_DL_FRAME_PARMS *fp = &UE->SL_UE_PHY_PARAMS.sl_frame_params;
  const int nb_slot_frame = fp->slots_per_frame;
  const bool sync_ref = get_nrUE_params()->sync_ref;

  char *env = strdup(getenv("NRLINK_SL"));
  char *colon = strchr(env, ':');
  AssertFatal(colon, "NRLINK_SL must be <ip>:<port>\n");
  *colon = 0;
  memset(&s_bridge, 0, sizeof(s_bridge));
  s_bridge.sin_family = AF_INET;
  s_bridge.sin_port = htons(atoi(colon + 1));
  AssertFatal(inet_aton(env, &s_bridge.sin_addr), "NRLINK_SL: bad address %s\n", env);
  s_sock = socket(AF_INET, SOCK_DGRAM, 0);
  AssertFatal(s_sock >= 0, "socket: %s\n", strerror(errno));
  int rcvbuf = 4 << 20;
  setsockopt(s_sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
  s_tx_len = sizeof(slm_hdr_t);

  /* wait until the MAC has configured the sidelink PHY */
  while (!oai_exit && !UE->phy_config_request_sent)
    usleep(10000);
  UE->is_synchronized_sl = sync_ref ? 1 : 0;
  LOG_I(NR_PHY, "nrlink_sl: %s, bridge %s:%d, %d slots per frame, TX at RX + %d\n", sync_ref ? "sync reference" : "nearby UE", env,
        ntohs(s_bridge.sin_port), nb_slot_frame, DURATION_RX_TO_TX);

  int64_t abs_slot = -1; /* last slot processed */
  struct timespec t_hello = {0}, t_stat = {0};
  uint32_t n_run = 0;
  while (!oai_exit) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    if (now.tv_sec - t_hello.tv_sec >= 1 && abs_slot < 0) {
      send_msg(SLM_HELLO, 0, sync_ref ? 1 : 0);
      t_hello = now;
    }
    if (now.tv_sec - t_stat.tv_sec >= 10) {
      if (t_stat.tv_sec)
        LOG_I(NR_PHY, "nrlink_sl: slot %ld, %u RUNs; TX psbch %u pssch %u; RX psbch %u sci1 %u sci2 %u slsch %u; dropped %u\n", (long)abs_slot,
              n_run, st.tx_psbch, st.tx_pssch, st.rx_psbch, st.rx_sci1, st.rx_sci2, st.rx_slsch, st.dropped);
      LOG_I(NR_PHY, "nrlink_sl: %u SL RX slots, actions none %u psbch %u pscch %u sci2 %u slsch %u; latest delivery %u slots\n",
            st.rx_slots, st.act[0], st.act[SL_NR_CONFIG_TYPE_RX_PSBCH], st.act[SL_NR_CONFIG_TYPE_RX_PSCCH],
            st.act[SL_NR_CONFIG_TYPE_RX_PSSCH_SCI], st.act[SL_NR_CONFIG_TYPE_RX_PSSCH_SLSCH], st.late_max);
      t_stat = now;
    }
    struct pollfd pfd = {.fd = s_sock, .events = POLLIN};
    if (poll(&pfd, 1, 200) <= 0)
      continue;
    ssize_t len = recv(s_sock, s_rx, sizeof(s_rx), 0);
    if (len < (ssize_t)sizeof(slm_hdr_t))
      continue;
    slm_hdr_t h;
    memcpy(&h, s_rx, sizeof(h));
    if (h.magic != SLM_MAGIC || h.type != SLM_RUN)
      continue;
    n_run++;

    bool psbch_seen = false;
    uint32_t psbch_slot = 0;
    sli_psbch_t psbch = {0};
    if (parse_items(s_rx, len, h.n_items, &psbch_seen, &psbch_slot, &psbch) < 0) {
      LOG_E(NR_PHY, "nrlink_sl: malformed RUN\n");
      continue;
    }

    if (!UE->is_synchronized_sl) {
      if (!psbch_seen) {
        pend_expire(h.abs_slot + 1000000); /* drop everything until synchronised */
        send_msg(SLM_DONE, h.abs_slot, 0);
        continue;
      }
      /* initial sync on this PSBCH: report it as the SLSS search would, then run from its slot on */
      /* (the MAC learns the sidelink timing from this report: build the slot numbers by hand, as the SLSS
       * search does, before update_curMsg can use the MAC's pools) */
      UE_nr_rxtx_proc_t sp = {0};
      sp.nr_slot_rx = psbch_slot % nb_slot_frame;
      sp.frame_rx = (psbch_slot / nb_slot_frame) % MAX_FRAME_NUMBER;
      sp.nr_slot_tx = (psbch_slot + DURATION_RX_TO_TX) % nb_slot_frame;
      sp.frame_tx = ((psbch_slot + DURATION_RX_TO_TX) / nb_slot_frame) % MAX_FRAME_NUMBER;
      sp.rx_slot_type = sp.tx_slot_type = NR_SIDELINK_SLOT;
      deliver_ssb(UE, &sp, &psbch);
      UE->is_synchronized_sl = 1;
      abs_slot = psbch_slot;
      LOG_I(NR_PHY, "nrlink_sl: synchronised on PSBCH (slss id %u) at %d.%d\n", psbch.slss_id, sp.frame_rx, sp.nr_slot_rx);
      pend_expire(abs_slot);
    }
    if (abs_slot < 0)
      abs_slot = (int64_t)h.abs_slot - 1;

    uint8_t flags = 2;
    int n = 0;
    while (abs_slot < (int64_t)h.abs_slot && n++ < SL_MAX_CATCHUP) {
      abs_slot++;
      nr_rxtx_thread_data_t m = {0};
      update_curMsg(UE, &m, abs_slot, nb_slot_frame, PC5);
      UE_nr_rxtx_proc_t *proc = &m.proc;
      nr_ue_rrc_timer_trigger(UE->Mod_id, proc->frame_tx, proc->nr_slot_tx, proc->gNB_id);
      if (proc->tx_slot_type == NR_SIDELINK_SLOT)
        run_tx(UE, proc, abs_slot + DURATION_RX_TO_TX);
      run_rx(UE, proc, abs_slot);
      flags = 2 | (proc->tx_slot_type == NR_SIDELINK_SLOT ? 1 : 0);
    }
    if (abs_slot < (int64_t)h.abs_slot) {
      LOG_W(NR_PHY, "nrlink_sl: %ld slots behind, skipping ahead to %u\n", (long)(h.abs_slot - abs_slot), h.abs_slot);
      abs_slot = h.abs_slot;
    }
    send_msg(SLM_DONE, abs_slot, flags);
  }
  return NULL;
}
