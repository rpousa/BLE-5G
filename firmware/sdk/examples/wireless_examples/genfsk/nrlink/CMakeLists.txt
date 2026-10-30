cmake_minimum_required(VERSION 3.22.0)

include(${SdkRootDirPath}/CMakeLists.txt)

mcux_add_source(
    SOURCES
          ./nrlink.c
)

mcux_add_configuration(
    CC "        -DHAL_RPMSG_SELECT_ROLE=0\
                -DSDK_COMPONENT_INTEGRATION=1\
                -DFSL_OSA_TASK_ENABLE=1\
                -DENABLE_RAM_VECTOR_TABLE\
                -DOSA_USED\
                -DMCUXPRESSO_SDK"
)

mcux_add_include(
    INCLUDES .
)

mcux_add_source(
    BASE_PATH ${SdkRootDirPath}
    SOURCES middleware/wireless/${bluetooth_root}/boards/${board}/app_preinclude_common.h
)

mcux_add_include(
    BASE_PATH ${SdkRootDirPath}
    INCLUDES middleware/wireless/${bluetooth_root}/boards/${board}
)

include(${SdkRootDirPath}/${board_root}/${board}/wireless_examples/genfsk/nrlink/reconfig.cmake OPTIONAL)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/nrlink_bm.bin)
