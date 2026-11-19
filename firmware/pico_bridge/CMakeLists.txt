cmake_minimum_required(VERSION 3.13)
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)
project(nrlink_bridge C CXX ASM)
pico_sdk_init()
add_executable(nrlink_bridge main.c)
target_link_libraries(nrlink_bridge pico_stdlib hardware_uart hardware_irq)
target_compile_definitions(nrlink_bridge PRIVATE
    PICO_STDIO_USB_ENABLE_RESET_VIA_BAUD_RATE=1
    PICO_STDIO_DEFAULT_CRLF=0
    PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS=0
    PICO_STDIO_USB_STDOUT_TIMEOUT_US=20000)
pico_enable_stdio_usb(nrlink_bridge 1)
pico_enable_stdio_uart(nrlink_bridge 0)
pico_add_extra_outputs(nrlink_bridge)
