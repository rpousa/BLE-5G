#!/bin/bash
# Build the board firmware from this repository's SDK overlay.
#   firmware/build.sh nrlink            GenFSK SLIP modem for the gNB/UE boards  -> $W/build_nrlink/nrlink_bm.bin
#   firmware/build.sh hci_bb [debug]    Bluetooth LE controller for the BLE boards (release by default)
#                                       -> $W/build_hcibb_rel/hci_bb_bm.bin (or build_hcibb/ for debug)
#   firmware/build.sh isp_agent|led_green   small helper images
# W = the west workspace with MCUXpresso SDK v26.09.00 (default ~/nxp/mcuxsdk). The overlay (firmware/sdk/) is
# copied over $W/mcuxsdk first: it adds the nrlink/isp_agent/led_green examples and changes hci_bb, the bare-metal
# OSA and fsl_common_arm.h (see firmware/patches/). Needs west and the Arm GNU toolchain (~/nxp/toolchain/*/bin).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
W=${W:-$HOME/nxp/mcuxsdk}
export PATH="$(ls -d "$HOME"/nxp/toolchain/*/bin 2>/dev/null | head -1):$HOME/nxp/venv/bin:$PATH"
export ARMGCC_DIR=${ARMGCC_DIR:-$(dirname "$(dirname "$(command -v arm-none-eabi-gcc)")")}
[ -d "$W/mcuxsdk" ] || { echo "no SDK at $W (west init/update first, see docs/REPRODUCE.md)" >&2; exit 1; }
cp -r "$HERE/sdk/." "$W/mcuxsdk/"
cd "$W"
case "$1" in
  nrlink)    west build -p always -b frdmmcxw72 -d build_nrlink mcuxsdk/examples/wireless_examples/genfsk/nrlink/bm -Dcore_id=cm33_core0
             ls -la build_nrlink/*.bin ;;
  hci_bb)    if [ "$2" = debug ]; then d=build_hcibb; cfg=debug; else d=build_hcibb_rel; cfg=release; fi
             west build -p always -b frdmmcxw72 -d $d mcuxsdk/examples/wireless_examples/ble_controller/hci_bb/bm -Dcore_id=cm33_core0 --config $cfg
             arm-none-eabi-nm -n -S --defined-only $d/*.elf | grep -iE " [tw] " > $d/hci_bb_bm.syms   # for pico/crumbs.py
             ls -la $d/*.bin ;;
  isp_agent|led_green)
             west build -p always -b frdmmcxw72 -d build_$1 mcuxsdk/examples/demo_apps/$1 -Dcore_id=cm33_core0
             ls -la build_$1/*.bin ;;
  *) sed -n 2,9p "$0"; exit 1 ;;
esac
