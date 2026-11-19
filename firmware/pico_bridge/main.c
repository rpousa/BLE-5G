/* nrlink Pico bridge (C): USB CDC <-> UART0 at 1 Mbaud for an FRDM-MCXW72 running nrlink v6 (LPUART0 host link).
 * Wiring: GP0 TX -> J1-1 (PTA16), GP1 RX <- J1-2 (PTA17), GND. GP2/GP3 stay inputs (PTA19 is the board's green LED).
 * No flow control. Board -> PC bytes go through an 16 KB ring filled by the UART RX interrupt, so a slow USB host
 * never overruns the 32-byte UART FIFO. To reflash: open the port at 1200 baud and close it (BOOTSEL mode). */
#include <string.h>
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "pico/stdio/driver.h"
#include "hardware/uart.h"
#include "hardware/irq.h"

#define BAUD 1000000
#define RING 16384u

static uint8_t ring[RING];
static volatile uint32_t head, tail, dropped;

static void on_uart_rx(void)
{
    while (uart_is_readable(uart0)) {
        uint8_t c = (uint8_t)uart_get_hw(uart0)->dr;
        uint32_t h = head, n = (h + 1u) & (RING - 1u);
        if (n == tail) { dropped++; continue; }
        ring[h] = c;
        head = n;
    }
}

int main(void)
{
    static uint8_t in[512], out[512];

    stdio_usb_init();
    stdio_set_translate_crlf(&stdio_usb, false);
    gpio_init(2); gpio_set_dir(2, GPIO_IN); gpio_disable_pulls(2);
    gpio_init(3); gpio_set_dir(3, GPIO_IN); gpio_disable_pulls(3);
    uart_init(uart0, BAUD);
    uart_set_hw_flow(uart0, false, false);
    uart_set_format(uart0, 8, 1, UART_PARITY_NONE);
    uart_set_fifo_enabled(uart0, true);
    gpio_set_function(0, GPIO_FUNC_UART);
    gpio_set_function(1, GPIO_FUNC_UART);
    irq_set_exclusive_handler(UART0_IRQ, on_uart_rx);
    irq_set_enabled(UART0_IRQ, true);
    uart_set_irq_enables(uart0, true, false);   /* RX (and RX timeout) interrupts */

    for (;;) {
        /* PC -> board */
        int n = stdio_usb.in_chars((char *)in, sizeof in);
        if (n > 0) {
            uart_write_blocking(uart0, in, (size_t)n);
        }
        /* board -> PC */
        uint32_t k = 0, t = tail;
        while (t != head && k < sizeof out) {
            out[k++] = ring[t];
            t = (t + 1u) & (RING - 1u);
        }
        if (k) {
            tail = t;
            if (stdio_usb_connected()) {
                stdio_usb.out_chars((const char *)out, (int)k);
            }
        }
        if (n <= 0 && k == 0) {
            tight_loop_contents();
        }
    }
}
