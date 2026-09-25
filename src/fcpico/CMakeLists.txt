# SPDX-License-Identifier: BSD-3-Clause

add_library(common_fcpico INTERFACE)
target_sources(common_fcpico INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/../pico/i_glob.c
    ${CMAKE_CURRENT_LIST_DIR}/../pico/i_system.c
    ${CMAKE_CURRENT_LIST_DIR}/../pico/i_timer.c
    ${CMAKE_CURRENT_LIST_DIR}/../pico/stubs.c
    ${CMAKE_CURRENT_LIST_DIR}/i_video_fcpico.c
    ${CMAKE_CURRENT_LIST_DIR}/i_input_fcpico.c
    ${CMAKE_CURRENT_LIST_DIR}/i_sound_fcpico.c)
if(PICO_ON_DEVICE)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    set(FCPICO_DOOM_BOOTROM_DIR ${CMAKE_CURRENT_BINARY_DIR}/doom_bootrom)
    set(FCPICO_DOOM_BOOTROM_NES ${FCPICO_DOOM_BOOTROM_DIR}/doom.nes)
    set(FCPICO_DOOM_BOOTROM_C ${CMAKE_CURRENT_BINARY_DIR}/doom_bootrom.c)
    add_custom_command(
        OUTPUT ${FCPICO_DOOM_BOOTROM_NES}
        COMMAND ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/bootrom/build.sh
                ${FCPICO_DOOM_BOOTROM_DIR}
        DEPENDS ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/bootrom/src/PG_main.asm
                ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/bootrom/version.inc
                ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/bootrom/gen/protocol.inc
                ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/tools/nes/normalize_ines.py
                ${CMAKE_CURRENT_LIST_DIR}/../../../../tutorial_project/BOOTROM/bootrom_fixr.bin
        VERBATIM)
    add_custom_command(
        OUTPUT ${FCPICO_DOOM_BOOTROM_C}
        COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/tools/nes/bin2c.py
                ${FCPICO_DOOM_BOOTROM_NES}
                ${FCPICO_DOOM_BOOTROM_C} fcpico_bootrom
        DEPENDS ${FCPICO_DOOM_BOOTROM_NES}
                ${CMAKE_CURRENT_LIST_DIR}/../../../../doom/tools/nes/bin2c.py
        VERBATIM)
    add_library(fcpico_bootrom STATIC ${FCPICO_DOOM_BOOTROM_C})
    target_link_libraries(common_fcpico INTERFACE fcpico_bootrom)
    target_sources(common_fcpico INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/../pico/picoflash.c
        ${CMAKE_CURRENT_LIST_DIR}/../pico/blit.S
        ${CMAKE_CURRENT_LIST_DIR}/device_video_fcpico.c)
endif()
target_include_directories(common_fcpico INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/..
    ${CMAKE_CURRENT_LIST_DIR}/../../textscreen)
target_compile_definitions(common_fcpico INTERFACE
    NO_USE_MOUSE=1 PICO_USE_SW_SPIN_LOCKS=0 PICO_USE_STACK_GUARDS=0)
target_link_libraries(common_fcpico INTERFACE
    pico_stdlib pico_multicore pico_runtime)
if(PICO_ON_DEVICE)
    target_link_libraries(common_fcpico INTERFACE
        hardware_interp hardware_dma hardware_flash)
endif()
