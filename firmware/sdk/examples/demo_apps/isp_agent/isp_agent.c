/*
 * isp_agent: blinks the RGB LED and accepts serial commands on the MCU-Link VCOM (LPUART1, 115200 8N1)
 * so the board can be identified and re-flashed remotely, without pressing SW3:
 *   isp            jump into the ROM ISP bootloader (then use blhost on the same port)
 *   led r|g|b|off  change the blink colour
 *   ver            print version
 */

#include "board.h"
#include "app.h"
#include "fsl_debug_console.h"
#include "fsl_gpio.h"
#include "fsl_lpuart.h"
#include "fsl_port.h"

#include <string.h>

#define ISP_AGENT_VERSION "isp_agent v2 (sdk)"

#define LED_PORT  PORTA
#define LED_GPIO  GPIOA
#define LED_RED   21U
#define LED_GREEN 19U
#define LED_BLUE  20U

/* ROM bootloader API tree (fsl_romapi.c for MCXW727C); runBootloader is its first member. */
#define ROM_API_TREE_ADDR 0x14816800U
/* Boot argument: tag 0xEB, boot mode 1 = ISP, boot interface 0 = auto-detect. */
#define ROM_ISP_BOOT_ARG 0xEB100000U

#define BLINK_HALF_PERIOD_MS 250U

volatile uint32_t g_msTicks;

void SysTick_Handler(void)
{
    g_msTicks++;
}

static void led_init(void)
{
    const gpio_pin_config_t off = {kGPIO_DigitalOutput, 1U}; /* LEDs are active low */

    CLOCK_EnableClock(kCLOCK_PortA);
    CLOCK_EnableClock(kCLOCK_GpioA);
    PORT_SetPinMux(LED_PORT, LED_RED, kPORT_MuxAsGpio);
    PORT_SetPinMux(LED_PORT, LED_GREEN, kPORT_MuxAsGpio);
    PORT_SetPinMux(LED_PORT, LED_BLUE, kPORT_MuxAsGpio);
    GPIO_PinInit(LED_GPIO, LED_RED, &off);
    GPIO_PinInit(LED_GPIO, LED_GREEN, &off);
    GPIO_PinInit(LED_GPIO, LED_BLUE, &off);
}

static void led_show(uint32_t pinMask)
{
    const uint32_t all = (1U << LED_RED) | (1U << LED_GREEN) | (1U << LED_BLUE);

    GPIO_PortSet(LED_GPIO, all & ~pinMask);
    GPIO_PortClear(LED_GPIO, pinMask);
}

static void enter_isp(void)
{
    static const uint32_t bootArg = ROM_ISP_BOOT_ARG;
    void (*runBootloader)(void *arg) = *(void (**)(void *))ROM_API_TREE_ADDR;

    PRINTF("ISP\r\n");
    while ((LPUART_GetStatusFlags(LPUART1) & kLPUART_TransmissionCompleteFlag) == 0U)
    {
    }
    led_show(0U);
    SysTick->CTRL = 0U;
    __disable_irq();
    runBootloader((void *)&bootArg);
    for (;;)
    {
    }
}

static int read_char(void)
{
    uint32_t flags = LPUART_GetStatusFlags(LPUART1);

    if ((flags & kLPUART_RxOverrunFlag) != 0U)
    {
        (void)LPUART_ClearStatusFlags(LPUART1, kLPUART_RxOverrunFlag);
    }
    if ((flags & kLPUART_RxDataRegFullFlag) != 0U)
    {
        return (int)LPUART_ReadByte(LPUART1);
    }
    return -1;
}

static void handle_command(const char *cmd, uint32_t *blinkMask)
{
    if (strcmp(cmd, "isp") == 0)
    {
        enter_isp();
    }
    else if (strcmp(cmd, "ver") == 0)
    {
        PRINTF(ISP_AGENT_VERSION "\r\n");
    }
    else if (strcmp(cmd, "led r") == 0)
    {
        *blinkMask = 1U << LED_RED;
    }
    else if (strcmp(cmd, "led g") == 0)
    {
        *blinkMask = 1U << LED_GREEN;
    }
    else if (strcmp(cmd, "led b") == 0)
    {
        *blinkMask = 1U << LED_BLUE;
    }
    else if (strcmp(cmd, "led off") == 0)
    {
        *blinkMask = 0U;
    }
    else
    {
        PRINTF("? (isp, led r|g|b|off, ver)\r\n");
    }
}

int main(void)
{
    char line[32];
    uint32_t len       = 0U;
    uint32_t blinkMask = 1U << LED_GREEN;
    uint32_t nextBlink = 0U;
    bool on            = false;

    BOARD_InitHardware();
    led_init();
    SysTick_Config(SystemCoreClock / 1000U);

    PRINTF("\r\n" ISP_AGENT_VERSION " ready (isp, led r|g|b|off, ver)\r\n> ");

    for (;;)
    {
        int c = read_char();

        if ((c == '\r') || (c == '\n'))
        {
            if (len > 0U)
            {
                line[len] = '\0';
                PRINTF("\r\n");
                handle_command(line, &blinkMask);
                PRINTF("> ");
                len = 0U;
            }
        }
        else if ((c >= 0) && (len < sizeof(line) - 1U))
        {
            line[len++] = (char)c;
            PUTCHAR(c);
        }

        if ((int32_t)(g_msTicks - nextBlink) >= 0)
        {
            nextBlink = g_msTicks + BLINK_HALF_PERIOD_MS;
            on        = !on;
            led_show(on ? blinkMask : 0U);
        }
    }
}
