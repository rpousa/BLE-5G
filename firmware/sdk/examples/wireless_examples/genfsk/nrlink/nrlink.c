/*
 * nrlink: host-driven Generic FSK radio modem for FRDM-MCXW72, used as the "PHY" of an OAI 5G NR
 * gNB / UE whose L1 is emulated on the host. The board only moves frames between the host serial
 * link and the 2.4 GHz radio and drives the LEDs; all NR logic lives on the host.
 *
 * Host link: MCU-Link VCOM (LPUART1) at BOARD_APP_UART_BAUDRATE, SLIP framed (0xC0 END, 0xDB ESC).
 *   host -> board                                 board -> host
 *   0x01 TX   <radio payload, <= 255 B>           0x81 TXDONE <status>
 *   0x02 LED  <mode><color><arg><dur_lo><dur_hi>  0x82 RX     <rssi:int8><ts:u32 le><radio payload>
 *   0x03 CFG  <channel><tx power level>           0x83 CFGOK
 *   0x04 PING                                     0x84 PONG   <version string>
 *   0x05 STATS   0x06 ISP (enter ROM bootloader)
 *                                                 0x85 STATS  <rx_ok:u32><rx_crc_err:u32><tx:u32>
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

#define NRLINK_VERSION "nrlink v4"

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
#define MSG_TXDONE 0x81U
#define MSG_RX     0x82U
#define MSG_CFGOK  0x83U
#define MSG_PONG   0x84U
#define MSG_STATSR 0x85U

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
#define EVT_ALL     (EVT_UART | EVT_RX_DONE | EVT_RX_FAIL | EVT_TX_DONE | EVT_TICK)

/* Host UART RX ring, filled by uart_irq() (the serial manager's per-byte path dropped bytes at speed) */
#define UART_RING_SIZE 2048U
static uint8_t s_uartRing[UART_RING_SIZE];
static volatile uint32_t s_uartHead;
static uint32_t s_uartTail;
static volatile uint32_t s_uartOverrun;
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

/* Host frame parser */
static uint8_t s_frame[RADIO_MAX_PAYLOAD + 8U];
static uint32_t s_frameLen;
static bool s_inFrame;
static bool s_esc;
static char s_line[16];
static uint32_t s_lineLen;

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
    (void)LPUART_WriteBlocking(LPUART1, p, len);
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
    out[0] = (uint8_t)s_rxRssi;
    out[1] = (uint8_t)s_rxTs;
    out[2] = (uint8_t)(s_rxTs >> 8);
    out[3] = (uint8_t)(s_rxTs >> 16);
    out[4] = (uint8_t)(s_rxTs >> 24);
    memcpy(&out[5], s_rxPayload, len);
    radio_rx_start(); /* re-arm before the slow host transfer: back-to-back fragments follow */
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
    while ((LPUART_GetStatusFlags(LPUART1) & kLPUART_TransmissionCompleteFlag) == 0U)
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
    LPUART1->CTRL  = 0U;
    LPUART1->FIFO  = LPUART_FIFO_TXFLUSH_MASK | LPUART_FIFO_RXFLUSH_MASK;
    LPUART1->WATER = 0U;
    LPUART1->STAT  = LPUART1->STAT; /* clear w1c flags */
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
    uint8_t reply[24];

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
        case MSG_STATS:
            memcpy(&reply[0], &s_statRxOk, 4U);
            memcpy(&reply[4], &s_statRxCrcErr, 4U);
            memcpy(&reply[8], &s_statTx, 4U);
            memcpy(&reply[12], (const void *)&s_cbEvt, 4U);
            memcpy(&reply[16], (const void *)&s_cbRx, 4U);
            memcpy(&reply[20], (const void *)&s_lastEvt, 4U);
            host_send(MSG_STATSR, reply, 24U);
            break;
        default:
            break;
    }
}

static void handle_text_byte(uint8_t c)
{
    if ((c == '\r') || (c == '\n'))
    {
        s_line[s_lineLen] = '\0';
        if (strcmp(s_line, "isp") == 0)
        {
            enter_isp();
        }
        else if (strcmp(s_line, "ver") == 0)
        {
            host_write_raw((const uint8_t *)NRLINK_VERSION "\r\n", sizeof(NRLINK_VERSION) + 1U);
        }
        s_lineLen = 0U;
    }
    else if (s_lineLen < sizeof(s_line) - 1U)
    {
        s_line[s_lineLen++] = (char)c;
    }
    else
    {
        s_lineLen = 0U;
    }
}

static void host_rx_byte(uint8_t c)
{
    static uint32_t window;

    window = (window << 8) | c;
    if (window == (('i' << 24) | ('s' << 16) | ('p' << 8) | '\r'))
    {
        enter_isp();
    }
    if (c == SLIP_END)
    {
        if (s_inFrame && (s_frameLen > 0U))
        {
            handle_frame(s_frame, s_frameLen);
        }
        s_inFrame  = true; /* END both terminates and starts a frame */
        s_frameLen = 0U;
        s_esc      = false;
        s_lineLen  = 0U;
        return;
    }
    if (!s_inFrame)
    {
        handle_text_byte(c);
        return;
    }
    if (s_esc)
    {
        c     = (c == SLIP_ESC_END) ? SLIP_END : ((c == SLIP_ESC_ESC) ? SLIP_ESC : c);
        s_esc = false;
    }
    else if (c == SLIP_ESC)
    {
        s_esc = true;
        return;
    }
    if (s_frameLen < sizeof(s_frame))
    {
        s_frame[s_frameLen++] = c;
    }
    else
    {
        s_inFrame = false; /* oversize: drop and resync on next END */
    }
}

/*******************************************************************************
 * Task
 ******************************************************************************/

static void uart_irq(void)
{
    uint32_t stat = LPUART1->STAT;

    if ((stat & LPUART_STAT_OR_MASK) != 0U)
    {
        LPUART1->STAT = LPUART_STAT_OR_MASK;
        s_uartOverrun++;
    }
    while ((LPUART1->WATER & LPUART_WATER_RXCOUNT_MASK) != 0U)
    {
        uint32_t head = s_uartHead;
        s_uartRing[head]  = (uint8_t)LPUART1->DATA;
        s_uartHead        = (head + 1U) & (UART_RING_SIZE - 1U);
    }
    (void)OSA_EventSet((osa_event_handle_t)s_evt, EVT_UART);
    SDK_ISR_EXIT_BARRIER;
}

static void uart_init(void)
{
    lpuart_config_t cfg;

    BOARD_InitAppConsole(); /* clock source + pins */
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

    (void)GENFSK_RegisterCallbacks(s_genfskId, genfsk_rx_cb, genfsk_evt_cb);
    radio_configure(RADIO_CHANNEL_DEFAULT, RADIO_TX_POWER_DEFAULT);
    radio_rx_start();

    host_write_raw((const uint8_t *)"\r\n" NRLINK_VERSION " ready\r\n", sizeof(NRLINK_VERSION) + 9U);

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
            while (s_uartTail != s_uartHead)
            {
                uint8_t c   = s_uartRing[s_uartTail];
                s_uartTail = (s_uartTail + 1U) & (UART_RING_SIZE - 1U);
                host_rx_byte(c);
            }
        }
        if ((flags & EVT_RX_DONE) != 0U)
        {
            radio_deliver_rx();
            radio_rx_start(); /* no-op if radio_deliver_rx() already re-armed */
        }
        if ((flags & EVT_RX_FAIL) != 0U)
        {
            radio_rx_start();
        }
        if ((flags & EVT_TX_DONE) != 0U)
        {
            uint8_t status = (s_txStatus == gGenfskSuccess) ? 0U : 2U;
            s_txBusy       = false;
            host_send(MSG_TXDONE, &status, 1U);
            radio_rx_start();
        }
        if ((flags & EVT_TICK) != 0U)
        {
            s_ms += TICK_MS;
            led_tick();
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
