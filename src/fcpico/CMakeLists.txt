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
    target_sources(common_fcpico INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/../pico/picoflash.c
        ${CMAKE_CURRENT_LIST_DIR}/../pico/blit.S)
endif()
target_include_directories(common_fcpico INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/..
    ${CMAKE_CURRENT_LIST_DIR}/../../textscreen)
target_compile_definitions(common_fcpico INTERFACE
    NO_USE_MOUSE=1 PICO_USE_SW_SPIN_LOCKS=0 PICO_USE_STACK_GUARDS=0)
target_link_libraries(common_fcpico INTERFACE
    pico_stdlib pico_multicore hardware_interp hardware_dma hardware_flash)
