/*
 * Copyright 2022 NXP
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef _APP_PREINCLUDE_H_
#define _APP_PREINCLUDE_H_


/*! *********************************************************************************
 *     Board Configuration
 ********************************************************************************** */
/* Number of Button required by the application */
#define gAppButtonCnt_c         0

/* Number of LED required by the application */
#define gAppLedCnt_c            0

#define gDebugConsoleEnable_d   0

/*! *********************************************************************************
 * 	App Configuration
 ********************************************************************************** */

/* hybrid genfsk enablement */
#define gAppEnableHybridGenfsk_d   1

/*! *********************************************************************************
 *     Framework Configuration
 ********************************************************************************** */
/* Do not modify. Not used for this application */
#define gAppUseNvm_d                    0

/* Enable Serial Manager interface */
#define gAppUseSerialManager_c          1

/* Defines Size for Timer Task*/
#define gTmrTaskStackSize_c             384

/* Enable capture of NBU failure */
#if !defined(__MCUXPRESSO)
#define HCIBB_DBG_NBU_ENABLE    1
#endif

/*
 * TimerManager Configuration
 */
#define FSL_OSA_BM_TIMER_CONFIG         FSL_OSA_BM_TIMER_SYSTICK

/* Enable debug signals for the debug target */
#if !defined(NDEBUG)
#define FWK_LCL_ENABLE_DTEST
#define FWK_LCL_ENABLE_GPIO_DEBUG
#endif /* !defined(NDEBUG) */

/*! *********************************************************************************
 *     RTOS Configuration
 ********************************************************************************** */


/*! *********************************************************************************
 *     BLE LL Configuration
 ***********************************************************************************/

/* Define as 1 in order to configure FEM to use external PA.
   FEM in NBU is activated by API function PLATFORM_InitFEM.
   Remark: FEM may conflict with Localisation/Coex features in term of RF_GPO use. */
//#define gAppConfigureFEM                       1

/* Define as 1 when use with an external WIFI chip offering Coexistence control to BLE.
   Coexistence in NBU is activated by API function PLATFORM_InitCOEX.
   Remark: Coexistence may conflict with Localisation/FEM feature in term of RF_GPO use. */
//#define gAppConfigureCOEX                      1

/*  ble_ll_config.h file lists the parameters with their default values. User can override
 *    the parameter here by defining the parameter to a user defined value. */
/* enable periodic advertiser list */
#define gAppExtAdvEnable_d                   1
#define gLlScanPeriodicAdvertiserListSize_c (8U)
/* disable autonomous feature exchange */
#define gL1AutonomousFeatureExchange_d 0

#define RL_BUFFER_COUNT (4U)

/*By default DTM through HCI interface is used (gAppUseDtm2Wire not defined).
    If DTM 2-wire interface is prefered, please define gAppUseDtm2Wire.*/
/*#define gAppUseDtm2Wire                      1*/

#define gEnableCoverage                        0
#if (defined(gEnableCoverage) && (gEnableCoverage == 1))
#undef gDebugConsoleEnable_d
#define gDebugConsoleEnable_d 1
#endif

/* define the max tx power setting, allowed value 0, 7 or 10 */
#define gAppMaxTxPowerDbm_c                    10U

/* nrlink: HCI UART at 115200 baud, host side: btattach -N -P h4 -S 115200. 1 Mbaud loses bytes in bursts longer than
 * ~11 bytes (interrupt-driven serial manager, no RTS/CTS on the MCU-Link VCOM), which breaks e.g. LE scan commands. */
#define BOARD_APP_UART_BAUDRATE                115200U

/* nrlink v3: receive ring buffer of the HCI UART. The SDK default is 128 bytes, less than one full ACL packet from the
 * host (256 + 5). The host may send 12 of them back to back (ACL credits), and while the main loop hands a packet
 * to the NBU the ring overflowed: bytes lost, H4 framing broken, board deaf ("command tx timeout"). */
#define SERIAL_MANAGER_RING_BUFFER_SIZE        4096U

/* Include common configuration file and board configuration file */
#include "app_preinclude_common.h"
#endif /* _APP_PREINCLUDE_H_ */