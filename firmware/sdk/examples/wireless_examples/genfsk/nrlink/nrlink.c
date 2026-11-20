/*
 * nrlink: host-driven Generic FSK radio modem for FRDM-MCXW72, used as the "PHY" of an OAI 5G NR
 * gNB / UE whose L1 is emulated on the host. The board only moves frames between the host serial
 * link and the 2.4 GHz radio and drives the LEDs; all NR logic lives on the host.
 *
 * Host link: MCU-Link VCOM (LPUART1) at BOARD_APP_UART_BAUDRATE, SLIP framed (0xC0 END, 0xDB ESC).
 * v6: also LPUART0 (PTA16 RX = J1-1, PTA17 TX = J1-2, no flow control) at the same rate, for a Raspberry Pi Pico USB
 * bridge. Each port has its own ring and parser; replies go to the port that last sent a whole frame or text line.
 *   host -> board                                 board -> host
 *   0x01 TX   <radio payload, <= 255 B>           0x81 TXDONE <status>
 *   0x02 LED  <mode><color><arg><dur_lo><dur_hi>  0x82 RX     <rssi:int8><ts:u32 le><radio payload>
 *   0x03 CFG  <channel><tx power level>           0x83 CFGOK
 *   0x04 PING                                     0x84 PONG   <version string>
 *   0x05 STATS   0x06 ISP (enter ROM bootloader)
 *                                                 0x85 STATS  <rx_ok:u32><rx_crc_err:u32><tx:u32>... (36 B, v5)
 *   0x07 REPEAT <flags><n>{<kind><from><to>}*n    0x87 REPEATOK <n>
 *   0x08 SLCFG <id><mode><gap/50us>               0x88 SLCFGOK <id><mode><queue size>
 *   0x09 SLQ   <radio payload>                    (no reply; 0x8A SLDONE with dropped+1 when the queue is full)
 *   0x0A SLFLUSH                                  0x8A SLDONE <sent><left><dropped><mode><granted:u16><used:u16> (100 us)
 *
 * Sidelink mode 1 (v7, the gNB schedules the sidelink): the host stages its sidelink packets on the board (SLQ) at
 * any time; they stay there until the gNB grants this board air time. The gNB's sidelink window announcement
 * (AIR_SLWIN, kind 0x08 from link 0: header, <width ms:u16>, then <n>{<id><offset:u16><length:u16>}*n in 100 us units,
 * like DCI 3_0 sidelink grants) is read by the board itself: with mode 1 it takes the grant whose id is its own
 * (SLCFG), with mode 2 the grant with id 0xFF (the pool for UEs out of coverage; a random start in its first half,
 * as a mode 2 UE picks its resource). At the grant's start the board sends its staged packets back to back, as many as
 * fit before the grant ends, and reports SLDONE. Mode 0 = off (v6 behaviour). The radio keeps receiving until the
 * grant starts, so a board hears the grants before its own.
 *
 * Repeater (v5, for daisy chains): with n rules set, a received packet whose air header (nrlink.py AIR_HDR:
 * kind, link, seq:u16, slot, frag, nfrag) matches a rule's kind and link is stored with its link byte set to the
 * rule's <to>; when the message's last fragment arrives (or 30 ms after the last packet) the stored packets are sent
 * back to back from RAM, then the radio listens again. A message is forwarded whole, so one radio can repeat at
 * about half the air rate. Repeated packets go to the host only with flags bit 0; flags bits 4-7 set the gap between
 * repeated packets, and before the first one, in 200 us steps (0: 800 us; a receiver needs the time to re-arm,
 * which a host's UART always gave it); n = 0 turns the repeater off.
 * Outside of a SLIP frame, ASCII lines "isp" (jump to the ROM ISP bootloader) and "ver" are accepted,
 * so the board can always be re-flashed remotely (see ~/nxp/board.sh).
 *
 * LED modes: 0 = solid <color> for <dur> ms, LED1 on if <arg> != 0 (gNB: served UE, UL on / DL off)
 *            1 = blink <color> <arg> times (UE: served indication), ignored while a blink is running
 * Colors: 0 off, 1 red, 2 green, 3 blue.
 */

#include <string.h>

#include "board.h"
#include "app.h"
#include "fsl_component_mem_manager.h"
#include "fsl_component_timer_manager.h"
#include "fsl_os_abstraction.h"
#include "fsl_gpio.h"
#include "fsl_port.h"
#include "RNG_Interface.h"
#include "SecLib.h"
#include "fwk_platform.h"
#include "fwk_platform_genfsk.h"
#include "genfsk_interface.h"
#include "fsl_lpuart.h"
#include "pin_mux.h" /* v6: BOARD_InitPinLPUART0_RX/TX */

#define NRLINK_VERSION "nrlink v7"

/* Radio: 1 Mbps GFSK, formatted packets with an 8-bit length field (payload <= 255 B), 24-bit CRC. */
#define RADIO_CHANNEL_DEFAULT   0x2AU /* 2360 MHz + 42 MHz = 2402 MHz */
#define RADIO_TX_POWER_DEFAULT  0x1FU
#define RADIO_SYNC_ADDRESS      0x4E525F35U /* "NR_5" */
#define RADIO_SYNC_ADDR_SIZE    3U          /* bytes on air = size + 1 */
#define RADIO_HEADER_BYTES      2U          /* H0 (8 bit) + length (8 bit) */
#define RADIO_CRC_BYTES         3U
#define RADIO_MAX_PAYLOAD       255U
#define RADIO_BUF_SIZE          (RADIO_SYNC_ADDR_SIZE + 1U + RADIO_HEADER_BYTES + RADIO_MAX_PAYLOAD + RADIO_CRC_BYTES)

/* Host link */
#define SLIP_END     0xC0U
#define SLIP_ESC     0xDBU
#define SLIP_ESC_END 0xDCU
#define SLIP_ESC_ESC 0xDDU

#define MSG_TX     0x01U
#define MSG_LED    0x02U
#define MSG_CFG    0x03U
#define MSG_PING   0x04U
#define MSG_STATS  0x05U
#define MSG_ISP    0x06U
#define MSG_REPEAT 0x07U
#define MSG_REPEATOK 0x87U
#define MSG_TXDONE 0x81U
#define MSG_RX     0x82U
#define MSG_CFGOK  0x83U
#define MSG_PONG   0x84U
#define MSG_STATSR 0x85U
#define MSG_SLCFG   0x08U
#define MSG_SLQ     0x09U
#define MSG_SLFLUSH 0x0AU
#define MSG_SLCFGOK 0x88U
#define MSG_SLDONE  0x8AU
#define AIR_SLWIN   0x08U

/* LEDs (active low): RGB on PTA19 (green) / PTA20 (blue) / PTA21 (red), LED1 on PTC1. */
#define LED_RED_PIN   21U
#define LED_GREEN_PIN 19U
#define LED_BLUE_PIN  20U
#define LED1_PIN      1U
#define LED_BLINK_MS  120U
#define TICK_MS       5U

/* ROM bootloader entry (see isp_agent) */
#define ROM_API_TREE_ADDR 0x14816800U
#define ROM_ISP_BOOT_ARG  0xEB100000U

/* App events */
#define EVT_UART    (1U << 0)
#define EVT_RX_DONE (1U << 1)
#define EVT_RX_FAIL (1U << 2)
#define EVT_TX_DONE (1U << 3)
#define EVT_TICK    (1U << 4)
#define EVT_SLGO    (1U << 5)
#define EVT_ALL     (EVT_UART | EVT_RX_DONE | EVT_RX_FAIL | EVT_TX_DONE | EVT_TICK | EVT_SLGO)

/* Host UART RX rings, filled by the UART ISRs (the serial manager's per-byte path dropped bytes at speed).
 * v6: port 0 = LPUART1 (MCU-Link VCOM), port 1 = LPUART0 (Pico). Each has its own SLIP / text-line parser. */
#define UART_RING_SIZE 2048U
#define HOST_PORTS     2U
typedef struct
{
    LPUART_Type *uart;
    uint8_t ring[UART_RING_SIZE];
    volatile uint32_t head;
    uint32_t tail;
    volatile uint32_t overrun;
    uint8_t frame[RADIO_MAX_PAYLOAD + 8U];
    uint32_t frameLen;
    bool inFrame;
    bool esc;
    char line[16];
    uint32_t lineLen;
    uint32_t window;
} host_port_t;
static host_port_t s_port[HOST_PORTS];
static host_port_t *s_cur = &s_port[0]; /* port whose bytes are being parsed */
static LPUART_Type *s_hostUart;         /* replies go here */
static TIMER_MANAGER_HANDLE_DEFINE(s_tick);
static OSA_EVENT_HANDLE_DEFINE(s_evt);
static OSA_TASK_HANDLE_DEFINE(s_taskHandle);

static uint8_t s_genfskId;
static uint8_t s_rxBuf[RADIO_BUF_SIZE];
static uint8_t s_txBuf[RADIO_BUF_SIZE];
static uint8_t s_rxPayload[RADIO_MAX_PAYLOAD];
static volatile uint16_t s_rxLen;
static volatile int8_t s_rxRssi;
static volatile uint32_t s_rxTs;
static volatile uint8_t s_rxCrcOk;
static volatile genfskEventStatus_t s_txStatus;
static bool s_txBusy;

static uint32_t s_statRxOk;
static uint32_t s_statRxCrcErr;
static uint32_t s_statTx;
static volatile uint32_t s_cbEvt;
static volatile uint32_t s_cbRx;
static volatile uint32_t s_lastEvt;

/* Repeater */
#define REP_RULES_MAX  8U
#define REP_QUEUE      32U
#define REP_FLUSH_MS   30U /* > a host's gap between fragments (TX, wait for TXDONE, next: up to ~6 ms) plus the 5 ms tick */
#define REP_GAP_US     800U
#define AIR_KIND 0U
#define AIR_LINK 1U
#define AIR_FRAG 5U
#define AIR_NFRAG 6U
static uint8_t s_repRules[REP_RULES_MAX][3];
static uint8_t s_repN;
static uint8_t s_repFlags;
static uint8_t s_repPkt[REP_QUEUE][RADIO_MAX_PAYLOAD];
static uint8_t s_repLen[REP_QUEUE];
static uint8_t s_repCount;     /* packets stored */
static uint8_t s_repNext;      /* next stored packet to send, while forwarding */
static bool s_repFwd;          /* forwarding: the radio sends the stored packets */
static bool s_txFromRep;       /* the packet in the air is a repeated one (no TXDONE to the host) */
static uint32_t s_repLastMs;
static uint32_t s_statRep, s_statRepDrop, s_statRepMsgs;

/* Sidelink mode 1 (v7) */
#define SL_QUEUE 24U
static uint8_t s_slPkt[SL_QUEUE][RADIO_MAX_PAYLOAD];
static uint8_t s_slLen[SL_QUEUE];
static uint8_t s_slHead, s_slCount;     /* ring of staged packets */
static uint8_t s_slId, s_slMode;         /* grant id this board answers to; 0 off, 1 own grants, 2 the pool */
static uint32_t s_slGapUs = 300U;
static bool s_slArmed;                   /* a grant is pending (timer running) */
static bool s_slSending;                 /* inside a grant: the radio sends staged packets */
static bool s_txFromSl;
static uint32_t s_slEnd;                 /* GENFSK timestamp (24 bit) at which the grant ends */
static uint32_t s_slGrantUs, s_slT0;
static uint8_t s_slSent, s_slDropped;
static uint32_t s_slRand = 0x2545F491U;
static TIMER_MANAGER_HANDLE_DEFINE(s_slTimer);


/* LED state */
static uint32_t s_ms;
static uint32_t s_solidUntil;
static uint8_t s_blinkColor;
static uint8_t s_blinkPhases; /* remaining on/off phases */
static uint32_t s_blinkNext;

static GENFSK_packet_config_t s_pktConfig = {
    .preambleSizeBytes   = 0,
    .packetType          = gGenfskFormattedPacket,
    .lengthSizeBits      = 8,
    .lengthBitOrder      = gGenfskLengthBitLsbFirst,
    .syncAddrSizeBytes   = RADIO_SYNC_ADDR_SIZE,
    .lengthAdjBytes      = RADIO_CRC_BYTES,
    .h0SizeBits          = 8,
    .h1SizeBits          = 0,
    .h0Match             = 0,
    .h0Mask              = 0,
    .h1Match             = 0,
    .h1Mask              = 0,
    .preambleBytePattern = 0,
};

static GENFSK_crc_config_t s_crcConfig = {
    .crcEnable    = gGenfskCrcEnable,
    .crcSize      = RADIO_CRC_BYTES,
    .crcStartByte = RADIO_SYNC_ADDR_SIZE + 1U,
    .crcRefIn     = gGenfskCrcInputNoRef,
    .crcRefOut    = gGenfskCrcOutputNoRef,
    .crcByteOrder = gGenfskCrcLSByteFirst,
    .crcSeed      = 0x00555555U,
    .crcPoly      = 0x0000065BU,
    .crcXorOut    = 0,
};

static GENFSK_whitener_config_t s_whitenConfig = {
    .whitenEnable        = gGenfskWhitenEnable,
    .whitenStart         = gWhitenStartWhiteningAtH0,
    .whitenEnd           = gWhitenEndAtEndOfCrc,
    .whitenB4Crc         = gCrcB4Whiten,
    .whitenPolyType      = gGaloisPolyType,
    .whitenRefIn         = gGenfskWhitenInputNoRef,
    .whitenPayloadReinit = gGenfskWhitenNoPayloadReinit,
    .whitenSize          = 7,
    .whitenInit          = 0x53,
    .whitenPoly          = 0x11,
    .whitenSizeThr       = 0,
    .manchesterEn        = gGenfskManchesterDisable,
    .manchesterStart     = gGenfskManchesterStartAtPayload,
    .manchesterInv       = gGenfskManchesterNoInv,
};

static GENFSK_radio_config_t s_radioConfig = {
    .radioMode = gGenfskGfskBt0p5h0p5,
    .dataRate  = gGenfskDR1Mbps,
};

static GENFSK_nwk_addr_match_t s_nwkAddr = {
    .nwkAddrSizeBytes = RADIO_SYNC_ADDR_SIZE,
    .nwkAddrThrBits   = 0,
    .nwkAddr          = RADIO_SYNC_ADDRESS,
};

/*******************************************************************************
 * LEDs
 ******************************************************************************/

static void led_init(void)
{
    const gpio_pin_config_t off = {kGPIO_DigitalOutput, 1U};

    CLOCK_EnableClock(kCLOCK_PortA);
    CLOCK_EnableClock(kCLOCK_GpioA);
    CLOCK_EnableClock(kCLOCK_PortC);
    CLOCK_EnableClock(kCLOCK_GpioC);
    PORT_SetPinMux(PORTA, LED_RED_PIN, kPORT_MuxAsGpio);
    PORT_SetPinMux(PORTA, LED_GREEN_PIN, kPORT_MuxAsGpio);
    PORT_SetPinMux(PORTA, LED_BLUE_PIN, kPORT_MuxAsGpio);
    PORT_SetPinMux(PORTC, LED1_PIN, kPORT_MuxAsGpio);
    GPIO_PinInit(GPIOA, LED_RED_PIN, &off);
    GPIO_PinInit(GPIOA, LED_GREEN_PIN, &off);
    GPIO_PinInit(GPIOA, LED_BLUE_PIN, &off);
    GPIO_PinInit(GPIOC, LED1_PIN, &off);
}

static void led_rgb(uint8_t color)
{
    const uint32_t all = (1U << LED_RED_PIN) | (1U << LED_GREEN_PIN) | (1U << LED_BLUE_PIN);
    uint32_t on        = 0U;

    if (color == 1U)
    {
        on = 1U << LED_RED_PIN;
    }
    else if (color == 2U)
    {
        on = 1U << LED_GREEN_PIN;
    }
    else if (color == 3U)
    {
        on = 1U << LED_BLUE_PIN;
    }
    GPIO_PortSet(GPIOA, all & ~on);
    GPIO_PortClear(GPIOA, on);
}

static void led1(bool on)
{
    GPIO_PinWrite(GPIOC, LED1_PIN, on ? 0U : 1U);
}

static void led_command(const uint8_t *p, uint32_t len)
{
    if (len < 5U)
    {
        return;
    }
    uint8_t mode   = p[0];
    uint8_t color  = p[1];
    uint8_t arg    = p[2];
    uint16_t durMs = (uint16_t)(p[3] | ((uint16_t)p[4] << 8));

    if (mode == 0U)
    {
        s_blinkPhases = 0U;
        led_rgb(color);
        led1(arg != 0U);
        s_solidUntil = (durMs != 0U) ? (s_ms + durMs) : 0U;
    }
    else if ((mode == 1U) && (s_blinkPhases == 0U) && (arg != 0U))
    {
        s_blinkColor  = color;
        s_blinkPhases = (uint8_t)(arg * 2U);
        s_blinkNext   = s_ms;
    }
}

static void led_tick(void)
{
    if ((s_solidUntil != 0U) && ((int32_t)(s_ms - s_solidUntil) >= 0))
    {
        s_solidUntil = 0U;
        led_rgb(0U);
    }
    if ((s_blinkPhases != 0U) && ((int32_t)(s_ms - s_blinkNext) >= 0))
    {
        /* even phase count remaining -> LED on, odd -> off */
        led_rgb(((s_blinkPhases & 1U) == 0U) ? s_blinkColor : 0U);
        s_blinkPhases--;
        s_blinkNext = s_ms + LED_BLINK_MS;
    }
}

/*******************************************************************************
 * Host link
 ******************************************************************************/

static void host_write_raw(const uint8_t *p, uint32_t len)
{
    (void)LPUART_WriteBlocking(s_hostUart, p, len);
}

static void host_send(uint8_t type, const uint8_t *p, uint32_t len)
{
    static uint8_t out[2U * (RADIO_MAX_PAYLOAD + 8U) + 4U];
    uint32_t n = 0U;

    out[n++] = SLIP_END;
    const uint8_t *src = &type;
    for (uint32_t i = 0U; i < len + 1U; i++)
    {
        uint8_t b = (i == 0U) ? *src : p[i - 1U];
        if (b == SLIP_END)
        {
            out[n++] = SLIP_ESC;
            out[n++] = SLIP_ESC_END;
        }
        else if (b == SLIP_ESC)
        {
            out[n++] = SLIP_ESC;
            out[n++] = SLIP_ESC_ESC;
        }
        else
        {
            out[n++] = b;
        }
    }
    out[n++] = SLIP_END;
    host_write_raw(out, n);
}

/*******************************************************************************
 * Radio
 ******************************************************************************/

static void radio_rx_start(void)
{
    if (!s_txBusy)
    {
        (void)GENFSK_StartRx(s_genfskId, s_rxBuf, (uint16_t)sizeof(s_rxBuf), 0, 0);
    }
}

static void radio_configure(uint8_t channel, uint8_t power)
{
    (void)GENFSK_AbortAll();
    (void)GENFSK_RadioConfig(s_genfskId, &s_radioConfig);
    (void)GENFSK_SetPacketConfig(s_genfskId, &s_pktConfig);
    (void)GENFSK_SetWhitenerConfig(s_genfskId, &s_whitenConfig);
    (void)GENFSK_SetCrcConfig(s_genfskId, &s_crcConfig);
    (void)GENFSK_SetNetworkAddress(s_genfskId, 0, &s_nwkAddr);
    (void)GENFSK_EnableNetworkAddress(s_genfskId, 0);
    (void)GENFSK_SetTxPowerLevel(s_genfskId, power);
    (void)GENFSK_SetChannelNumber(s_genfskId, channel);
}

static void radio_tx(const uint8_t *payload, uint32_t len)
{
    GENFSK_packet_t pkt;
    uint8_t status;

    if (len > RADIO_MAX_PAYLOAD)
    {
        len = RADIO_MAX_PAYLOAD;
    }
    pkt.addr               = RADIO_SYNC_ADDRESS;
    pkt.header.h0Field     = 0;
    pkt.header.lengthField = (uint16_t)len;
    pkt.header.h1Field     = 0;
    pkt.payload            = (uint8_t *)payload;

    (void)GENFSK_AbortAll();
    (void)GENFSK_PacketToByteArray(s_genfskId, &pkt, s_txBuf);
    s_txBusy = true;
    if (GENFSK_StartTx(s_genfskId, s_txBuf, (uint16_t)(len + RADIO_HEADER_BYTES + RADIO_SYNC_ADDR_SIZE + 1U), 0) !=
        gGenfskSuccess_c)
    {
        s_txBusy = false;
        status   = 1U;
        host_send(MSG_TXDONE, &status, 1U);
        radio_rx_start();
        return;
    }
    s_statTx++;
}

static uint32_t ts24(void)
{
    return (uint32_t)GENFSK_GetTimestamp() & 0xFFFFFFU;
}

static uint32_t sl_air_us(uint32_t len)
{
    /* sync 4 + header 2 + payload + CRC 3 at 1 Mbit/s, plus the radio's ramp and turnaround */
    return (len + 9U) * 8U + 180U;
}

static uint32_t sl_gap_us(uint32_t len)
{
    /* every receiver passes the packet to its host (~10 us a byte at 1 Mbaud) before it can take the next one */
    uint32_t g = s_slGapUs + 2U * len;
    return g;
}

static void sl_report(void)
{
    uint8_t r[8];
    uint16_t g = (uint16_t)(s_slGrantUs / 100U), u = (uint16_t)(((ts24() - s_slT0) & 0xFFFFFFU) / 100U);
    r[0] = s_slSent;
    r[1] = s_slCount;
    r[2] = s_slDropped;
    r[3] = s_slMode;
    r[4] = (uint8_t)g;
    r[5] = (uint8_t)(g >> 8);
    r[6] = (uint8_t)u;
    r[7] = (uint8_t)(u >> 8);
    host_send(MSG_SLDONE, r, 8U);
    s_slSent = 0U;
    s_slDropped = 0U;
}

static void radio_tx(const uint8_t *payload, uint32_t len);

/* the next staged packet if it fits before the grant ends, else the end of the grant */
static void sl_send_next(void)
{
    if (s_slCount != 0U)
    {
        uint32_t left = (s_slEnd - ts24()) & 0xFFFFFFU;
        uint8_t i     = s_slHead;
        if ((left < 0x800000U) && (sl_air_us(s_slLen[i]) + 100U <= left))
        {
            s_slHead = (uint8_t)((s_slHead + 1U) % SL_QUEUE);
            s_slCount--;
            s_txFromSl = true;
            radio_tx(s_slPkt[i], s_slLen[i]);
            if (s_txBusy)
            {
                s_slSent++;
                return;
            }
            s_txFromSl = false;
        }
    }
    s_slSending = false;
    s_txFromSl  = false;
    sl_report();
}

static void sl_timer_cb(void *param)
{
    (void)param;
    (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_SLGO);
}

/* AIR_SLWIN heard from the gNB: arm this board's grant, if it has one and something to send */
static void sl_grant(const uint8_t *p, uint32_t len, uint32_t rxTs)
{
    if ((s_slMode == 0U) || (len < 10U) || s_slArmed || s_slSending)
    {
        return;
    }
    uint32_t n = p[9];
    for (uint32_t k = 0U; (k < n) && (10U + 5U * k + 5U <= len); k++)
    {
        const uint8_t *g = &p[10U + 5U * k];
        if (!((s_slMode == 1U && g[0] == s_slId) || (s_slMode == 2U && g[0] == 0xFFU)))
        {
            continue;
        }
        uint32_t off = 100U * (uint32_t)(g[1] | (g[2] << 8)), ln = 100U * (uint32_t)(g[3] | (g[4] << 8));
        if (s_slMode == 2U && ln > 2000U)
        {
            /* the pool: a random start in its first half (mode 2 resource selection) */
            s_slRand ^= s_slRand << 13;
            s_slRand ^= s_slRand >> 17;
            s_slRand ^= s_slRand << 5;
            uint32_t r = (s_slRand % (ln / 200U)) * 100U;
            off += r;
            ln -= r;
        }
        uint32_t el = (ts24() - rxTs) & 0xFFFFFFU;
        if (el > 5000U)
        {
            el = 0U;              /* not on the radio timebase, or a stale packet: count from now */
        }
        uint32_t wait = (off > el) ? (off - el) : 0U;
        if (ln <= (el > off ? el - off : 0U))
        {
            return;               /* already over */
        }
        ln -= (el > off ? el - off : 0U);
        s_slGrantUs = ln;
        s_slEnd     = (ts24() + wait + ln) & 0xFFFFFFU;
        if (s_slCount == 0U)
        {
            sl_report();          /* nothing staged: the grant goes unused */
            return;
        }
        s_slArmed = true;
        if (wait < 500U)
        {
            (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_SLGO);
        }
        else
        {
            (void)TM_Start((timer_handle_t)s_slTimer, (uint8_t)(kTimerModeSingleShot | kTimerModeSetMicrosTimer), wait);
        }
        return;
    }
}

static void sl_go(void)
{
    if (!s_slArmed)
    {
        return;
    }
    s_slArmed   = false;
    s_slSending = true;
    s_slT0      = ts24();
    if (s_txBusy)
    {
        return;                   /* a host TX in the air: start when it is done */
    }
    sl_send_next();
}

static void rep_send_next(void)
{
    if (s_repNext < s_repCount)
    {
        s_txFromRep = true;
        radio_tx(s_repPkt[s_repNext], s_repLen[s_repNext]);
        s_repNext++;
        s_statRep++;
        if (!s_txBusy) /* start failed: give up on this message */
        {
            s_txFromRep = false;
            s_repFwd = false;
            s_repCount = 0U;
        }
        return;
    }
    s_repFwd    = false;
    s_repCount  = 0U;
    s_txFromRep = false;
}

static void rep_gap(void)
{
    uint32_t gap = (uint32_t)(s_repFlags >> 4) * 200U;
    SDK_DelayAtLeastUs((gap != 0U) ? gap : REP_GAP_US, SystemCoreClock);
}

static void rep_start(void)
{
    if (!s_repFwd && (s_repCount != 0U))
    {
        /* the other receivers heard the same last packet and must re-arm before the first repeated one */
        rep_gap();
        s_repFwd  = true;
        s_repNext = 0U;
        s_statRepMsgs++;
        rep_send_next();
    }
}

/* true when the packet was taken by the repeater */
static bool rep_take(const uint8_t *p, uint32_t len)
{
    if ((s_repN == 0U) || (len < 7U) || s_repFwd)
    {
        return false;
    }
    for (uint32_t i = 0U; i < s_repN; i++)
    {
        if ((p[AIR_KIND] == s_repRules[i][0]) && (p[AIR_LINK] == s_repRules[i][1]))
        {
            if (s_repCount >= REP_QUEUE)
            {
                s_statRepDrop++;
                return true;
            }
            memcpy(s_repPkt[s_repCount], p, len);
            s_repPkt[s_repCount][AIR_LINK] = s_repRules[i][2];
            s_repLen[s_repCount]           = (uint8_t)len;
            s_repCount++;
            s_repLastMs = s_ms;
            if ((uint32_t)p[AIR_FRAG] + 1U >= (uint32_t)p[AIR_NFRAG])
            {
                rep_start();
            }
            return true;
        }
    }
    return false;
}

static void genfsk_rx_cb(uint8_t *pBuffer, uint16_t bufferLength, uint64_t timestamp, int8_t rssi, uint8_t crcValid)
{
    (void)pBuffer;
    s_cbRx++;
    s_rxLen    = bufferLength;
    s_rxRssi   = rssi;
    s_rxTs     = (uint32_t)timestamp;
    s_rxCrcOk  = crcValid;
    (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_RX_DONE);
}

static void genfsk_evt_cb(genfskEvent_t event, genfskEventStatus_t status)
{
    s_cbEvt++;
    s_lastEvt = ((uint32_t)event << 16) | (uint32_t)status;
    if ((event & gGenfskTxEvent) != 0U)
    {
        s_txStatus = status;
        (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_TX_DONE);
    }
    if ((event & gGenfskRxEvent) != 0U)
    {
        (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_RX_FAIL);
    }
}

static void radio_deliver_rx(void)
{
    GENFSK_packet_t pkt;
    uint8_t out[1U + 4U + RADIO_MAX_PAYLOAD];

    if (s_rxCrcOk == 0U)
    {
        s_statRxCrcErr++;
        return;
    }
    pkt.payload = s_rxPayload;
    if (GENFSK_ByteArrayToPacket(s_genfskId, s_rxBuf, &pkt) != gGenfskSuccess_c)
    {
        s_statRxCrcErr++;
        return;
    }
    uint32_t len = pkt.header.lengthField;
    if (len > RADIO_MAX_PAYLOAD)
    {
        s_statRxCrcErr++;
        return;
    }
    s_statRxOk++;
    if ((len >= 10U) && (s_rxPayload[AIR_KIND] == AIR_SLWIN) && (s_rxPayload[AIR_LINK] == 0U))
    {
        sl_grant(s_rxPayload, len, s_rxTs & 0xFFFFFFU);
    }
    if (rep_take(s_rxPayload, len))
    {
        if (!s_repFwd)
        {
            radio_rx_start();
        }
        if ((s_repFlags & 1U) == 0U)
        {
            return;
        }
    }
    out[0] = (uint8_t)s_rxRssi;
    out[1] = (uint8_t)s_rxTs;
    out[2] = (uint8_t)(s_rxTs >> 8);
    out[3] = (uint8_t)(s_rxTs >> 16);
    out[4] = (uint8_t)(s_rxTs >> 24);
    memcpy(&out[5], s_rxPayload, len);
    if (!s_repFwd)
    {
        radio_rx_start(); /* re-arm before the slow host transfer: back-to-back fragments follow */
    }
    host_send(MSG_RX, out, 5U + len);
}

/*******************************************************************************
 * Commands
 ******************************************************************************/

static void enter_isp(void)
{
    static const uint32_t bootArg = ROM_ISP_BOOT_ARG;
    void (*runBootloader)(void *arg) = *(void (**)(void *))ROM_API_TREE_ADDR;

    host_write_raw((const uint8_t *)"ISP\r\n", 5U);
    while ((LPUART_GetStatusFlags(s_hostUart) & kLPUART_TransmissionCompleteFlag) == 0U)
    {
    }
    (void)GENFSK_AbortAll();
    led_rgb(0U);
    led1(false);

    /* Hand the ROM a quiet core: no app interrupt may fire once it unmasks, and LPUART1 back to reset state. */
    __disable_irq();
    SysTick->CTRL = 0U;
    for (uint32_t i = 0U; i < (sizeof(NVIC->ICER) / sizeof(NVIC->ICER[0])); i++)
    {
        NVIC->ICER[i] = 0xFFFFFFFFU;
        NVIC->ICPR[i] = 0xFFFFFFFFU;
    }
    for (uint32_t i = 0U; i < HOST_PORTS; i++)
    {
        LPUART_Type *u = s_port[i].uart;
        u->CTRL  = 0U;
        u->FIFO  = LPUART_FIFO_TXFLUSH_MASK | LPUART_FIFO_RXFLUSH_MASK;
        u->WATER = 0U;
        u->STAT  = u->STAT; /* clear w1c flags */
    }
    SCB->VTOR      = 0U;
    __DSB();
    __ISB();
    runBootloader((void *)&bootArg);
    for (;;)
    {
    }
}

static void handle_frame(const uint8_t *f, uint32_t len)
{
    uint8_t reply[36];

    if (len == 0U)
    {
        return;
    }
    switch (f[0])
    {
        case MSG_TX:
            radio_tx(&f[1], len - 1U);
            break;
        case MSG_LED:
            led_command(&f[1], len - 1U);
            break;
        case MSG_CFG:
            if (len >= 3U)
            {
                radio_configure(f[1], f[2]);
                radio_rx_start();
            }
            host_send(MSG_CFGOK, NULL, 0U);
            break;
        case MSG_PING:
            host_send(MSG_PONG, (const uint8_t *)NRLINK_VERSION, sizeof(NRLINK_VERSION) - 1U);
            break;
        case MSG_ISP:
            enter_isp();
            break;
        case MSG_REPEAT:
            s_repN = 0U;
            if (len >= 3U)
            {
                uint32_t n = f[2];
                if (n > REP_RULES_MAX)
                {
                    n = REP_RULES_MAX;
                }
                if (len < 3U + 3U * n)
                {
                    n = (len - 3U) / 3U;
                }
                for (uint32_t i = 0U; i < n; i++)
                {
                    memcpy(s_repRules[i], &f[3U + 3U * i], 3U);
                }
                s_repFlags = f[1];
                s_repCount = 0U;
                s_repN     = (uint8_t)n;
            }
            reply[0] = s_repN;
            host_send(MSG_REPEATOK, reply, 1U);
            break;
        case MSG_SLCFG:
            if (len >= 3U)
            {
                s_slId   = f[1];
                s_slMode = (f[2] <= 2U) ? f[2] : 0U;
                if (len >= 4U)
                {
                    s_slGapUs = 50U * (uint32_t)f[3];
                }
            }
            reply[0] = s_slId;
            reply[1] = s_slMode;
            reply[2] = (uint8_t)SL_QUEUE;
            host_send(MSG_SLCFGOK, reply, 3U);
            break;
        case MSG_SLQ:
            if ((len > 1U) && (len - 1U <= RADIO_MAX_PAYLOAD))
            {
                if (s_slCount >= SL_QUEUE)
                {
                    s_slDropped++;
                    sl_report();
                }
                else
                {
                    uint8_t i = (uint8_t)((s_slHead + s_slCount) % SL_QUEUE);
                    memcpy(s_slPkt[i], &f[1], len - 1U);
                    s_slLen[i] = (uint8_t)(len - 1U);
                    s_slCount++;
                }
            }
            break;
        case MSG_SLFLUSH:
            s_slCount = 0U;
            s_slHead  = 0U;
            sl_report();
            break;
        case MSG_STATS:
            memcpy(&reply[0], &s_statRxOk, 4U);
            memcpy(&reply[4], &s_statRxCrcErr, 4U);
            memcpy(&reply[8], &s_statTx, 4U);
            memcpy(&reply[12], (const void *)&s_cbEvt, 4U);
            memcpy(&reply[16], (const void *)&s_cbRx, 4U);
            memcpy(&reply[20], (const void *)&s_lastEvt, 4U);
            memcpy(&reply[24], &s_statRep, 4U);
            memcpy(&reply[28], &s_statRepDrop, 4U);
            memcpy(&reply[32], &s_statRepMsgs, 4U);
            host_send(MSG_STATSR, reply, 36U);
            break;
        default:
            break;
    }
}

static void handle_text_byte(uint8_t c)
{
    host_port_t *p = s_cur;

    if ((c == '\r') || (c == '\n'))
    {
        p->line[p->lineLen] = '\0';
        if (strcmp(p->line, "isp") == 0)
        {
            s_hostUart = p->uart;
            enter_isp();
        }
        else if (strcmp(p->line, "ver") == 0)
        {
            s_hostUart = p->uart;
            host_write_raw((const uint8_t *)NRLINK_VERSION "\r\n", sizeof(NRLINK_VERSION) + 1U);
        }
        p->lineLen = 0U;
    }
    else if (p->lineLen < sizeof(p->line) - 1U)
    {
        p->line[p->lineLen++] = (char)c;
    }
    else
    {
        p->lineLen = 0U;
    }
}

static void host_rx_byte(uint8_t c)
{
    host_port_t *p = s_cur;

    p->window = (p->window << 8) | c;
    if (p->window == (('i' << 24) | ('s' << 16) | ('p' << 8) | '\r'))
    {
        s_hostUart = p->uart;
        enter_isp();
    }
    if (c == SLIP_END)
    {
        if (p->inFrame && (p->frameLen > 0U))
        {
            if ((p->frame[0] >= MSG_TX) && (p->frame[0] <= MSG_SLFLUSH)) /* a noise frame must not steal the replies */
            {
                s_hostUart = p->uart;
            }
            handle_frame(p->frame, p->frameLen);
        }
        p->inFrame  = true; /* END both terminates and starts a frame */
        p->frameLen = 0U;
        p->esc      = false;
        p->lineLen  = 0U;
        return;
    }
    if (!p->inFrame)
    {
        handle_text_byte(c);
        return;
    }
    if (p->esc)
    {
        c      = (c == SLIP_ESC_END) ? SLIP_END : ((c == SLIP_ESC_ESC) ? SLIP_ESC : c);
        p->esc = false;
    }
    else if (c == SLIP_ESC)
    {
        p->esc = true;
        return;
    }
    if (p->frameLen < sizeof(p->frame))
    {
        p->frame[p->frameLen++] = c;
    }
    else
    {
        p->inFrame = false; /* oversize: drop and resync on next END */
    }
}

/*******************************************************************************
 * Task
 ******************************************************************************/

static void uart_rx_drain(host_port_t *p)
{
    LPUART_Type *u = p->uart;
    uint32_t stat  = u->STAT;

    if ((stat & LPUART_STAT_OR_MASK) != 0U)
    {
        u->STAT = LPUART_STAT_OR_MASK;
        p->overrun++;
    }
    while ((u->WATER & LPUART_WATER_RXCOUNT_MASK) != 0U)
    {
        uint32_t head = p->head;
        p->ring[head] = (uint8_t)u->DATA;
        p->head       = (head + 1U) & (UART_RING_SIZE - 1U);
    }
    (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_UART);
}

static void uart_irq(void) /* LPUART1, VCOM */
{
    uart_rx_drain(&s_port[0]);
    SDK_ISR_EXIT_BARRIER;
}

static void uart0_irq(void) /* LPUART0, Pico */
{
    uart_rx_drain(&s_port[1]);
    SDK_ISR_EXIT_BARRIER;
}

static void uart_init(void)
{
    lpuart_config_t cfg;

    s_port[0].uart = LPUART1;
    s_port[1].uart = LPUART0;
    s_hostUart     = LPUART1;

    BOARD_InitAppConsole(); /* LPUART1: clock source + pins */
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps    = BOARD_APP_UART_BAUDRATE;
    cfg.enableTx        = true;
    cfg.enableRx        = true;
    cfg.rxFifoWatermark = 0U;
    cfg.txFifoWatermark = 0U;
    (void)LPUART_Init(LPUART1, &cfg, BOARD_APP_UART_CLK_FREQ);
    (void)InstallIRQHandler(LPUART1_IRQn, (uint32_t)uart_irq);
    LPUART_EnableInterrupts(LPUART1, (uint32_t)kLPUART_RxDataRegFullInterruptEnable |
                                         (uint32_t)kLPUART_RxOverrunInterruptEnable);
    NVIC_SetPriority(LPUART1_IRQn, 1U);
    (void)EnableIRQ(LPUART1_IRQn);

    /* v6: LPUART0 for the Pico. PTA16/17 are not used by the LEDs (PTA19..21), so the RGB LED keeps working;
     * there is no RTS (PTA19 is the green LED): the Pico bridge runs without CTS. */
    CLOCK_SetIpSrc(kCLOCK_Lpuart0, (clock_ip_src_t)BOARD_UART_CLKSRC);
    CLOCK_EnableClockLPMode(kCLOCK_Lpuart0, kCLOCK_IpClkControl_fun3);
    BOARD_InitPinLPUART0_RX();
    BOARD_InitPinLPUART0_TX();
    (void)LPUART_Init(LPUART0, &cfg, CLOCK_GetIpFreq(kCLOCK_Lpuart0));
    (void)InstallIRQHandler(LPUART0_IRQn, (uint32_t)uart0_irq);
    LPUART_EnableInterrupts(LPUART0, (uint32_t)kLPUART_RxDataRegFullInterruptEnable |
                                         (uint32_t)kLPUART_RxOverrunInterruptEnable);
    NVIC_SetPriority(LPUART0_IRQn, 1U);
    (void)EnableIRQ(LPUART0_IRQn);
}

static void tick_cb(void *param)
{
    (void)param;
    (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_TICK);
}

static void nrlink_init(void);
static void nrlink_handle(osa_event_flags_t flags, uint8_t *buf, uint32_t bufSize);

static void nrlink_task(void *argument)
{
    static bool s_initialized;
    static uint8_t buf[64];
    osa_event_flags_t flags = 0U;

    (void)argument;
    if (!s_initialized)
    {
        s_initialized = true;
        nrlink_init();
    }

    /* Bare-metal OSA: handle one batch of events and return so the GenFSK/timer tasks can run. */
    for (;;)
    {
        (void)OSA_EventWait((osa_event_handle_t)s_evt, EVT_ALL, FALSE, osaWaitForever_c, &flags);
        nrlink_handle(flags, buf, sizeof(buf));
        if (gUseRtos_c == 0U)
        {
            break;
        }
    }
}

static void nrlink_init(void)
{
    (void)MEM_Init();
    SecLib_Init();
    RNG_Init();
    GENFSK_Init();
    (void)GENFSK_AllocInstance(&s_genfskId, NULL, NULL, NULL);
    PLATFORM_SetGenfskMaxTxPower(gAppMaxTxPowerDbm_c);
    (void)OSA_EventCreate((osa_event_handle_t)s_evt, TRUE);

    led_init();

    uart_init();

    (void)TM_Open((timer_handle_t)s_tick);
    (void)TM_InstallCallback((timer_handle_t)s_tick, tick_cb, NULL);
    (void)TM_Start((timer_handle_t)s_tick, (uint8_t)kTimerModeIntervalTimer, TICK_MS);

    (void)TM_Open((timer_handle_t)s_slTimer);
    (void)TM_InstallCallback((timer_handle_t)s_slTimer, sl_timer_cb, NULL);
    (void)GENFSK_RegisterCallbacks(s_genfskId, genfsk_rx_cb, genfsk_evt_cb);
    radio_configure(RADIO_CHANNEL_DEFAULT, RADIO_TX_POWER_DEFAULT);
    radio_rx_start();

    host_write_raw((const uint8_t *)"\r\n" NRLINK_VERSION " ready\r\n", sizeof(NRLINK_VERSION) + 9U);
    s_hostUart = LPUART0;
    host_write_raw((const uint8_t *)"\r\n" NRLINK_VERSION " ready\r\n", sizeof(NRLINK_VERSION) + 9U);
    s_hostUart = LPUART1;

    /* boot indication: one blue blink */
    led_command((const uint8_t[]){1U, 3U, 1U, 0U, 0U}, 5U);
}

static void nrlink_handle(osa_event_flags_t flags, uint8_t *buf, uint32_t bufSize)
{
    uint32_t n;

    {
        if ((flags & EVT_UART) != 0U)
        {
            (void)buf;
            (void)bufSize;
            (void)n;
            for (uint32_t i = 0U; i < HOST_PORTS; i++)
            {
                host_port_t *p = &s_port[i];
                s_cur          = p;
                while (p->tail != p->head)
                {
                    uint8_t c = p->ring[p->tail];
                    p->tail   = (p->tail + 1U) & (UART_RING_SIZE - 1U);
                    host_rx_byte(c);
                }
            }
        }
        if ((flags & EVT_RX_DONE) != 0U)
        {
            radio_deliver_rx();
            if (!s_repFwd)
            {
                radio_rx_start(); /* no-op if radio_deliver_rx() already re-armed */
            }
        }
        if ((flags & EVT_RX_FAIL) != 0U)
        {
            if (!s_repFwd)
            {
                radio_rx_start();
            }
        }
        if ((flags & EVT_TX_DONE) != 0U)
        {
            uint8_t status = (s_txStatus == gGenfskSuccess) ? 0U : 2U;
            bool rep       = s_txFromRep;
            bool sl        = s_txFromSl;
            s_txBusy       = false;
            if (sl)
            {
                s_txFromSl = false;
                if (s_slCount != 0U)
                {
                    SDK_DelayAtLeastUs(sl_gap_us(s_slLen[s_slHead]), SystemCoreClock);
                }
                sl_send_next();
            }
            else if (s_slSending && !s_txFromSl)
            {
                host_send(MSG_TXDONE, &status, 1U);
                sl_send_next();   /* the grant started during a host TX */
            }
            else if (rep)
            {
                if (s_repNext < s_repCount)
                {
                    rep_gap();
                }
                rep_send_next(); /* the next stored packet, or the end of the message */
            }
            else
            {
                host_send(MSG_TXDONE, &status, 1U);
            }
            if (!s_repFwd)
            {
                radio_rx_start();
            }
        }
        if ((flags & EVT_SLGO) != 0U)
        {
            sl_go();
        }
        if ((flags & EVT_TICK) != 0U)
        {
            s_ms += TICK_MS;
            led_tick();
            /* a message whose last fragment was lost: forward what arrived */
            if (!s_repFwd && (s_repCount != 0U) && ((s_ms - s_repLastMs) >= REP_FLUSH_MS))
            {
                rep_start();
            }
        }
    }
}

static OSA_TASK_DEFINE(nrlink_task, gMainThreadPriority_c, 1, gMainThreadStackSize_c, 0);

int main(void)
{
    OSA_Init();
    BOARD_InitHardware();
    PLATFORM_InitGenfsk();
    APP_InitServices();
    (void)OSA_TaskCreate((osa_task_handle_t)s_taskHandle, OSA_TASK(nrlink_task), NULL);
    OSA_Start();
    return 0;
}
