# SPDX-License-Identifier: BSD-3-Clause
add_library(fcvideo STATIC fcvideo.c fcui.c fcworld.c)
target_include_directories(fcvideo PUBLIC ${CMAKE_CURRENT_LIST_DIR} ${CMAKE_CURRENT_LIST_DIR}/../../fcbus)
target_link_libraries(fcvideo PUBLIC m)
target_compile_options(fcvideo PRIVATE -Wall -Wextra -Werror)
if(PICO_ON_DEVICE)
    target_compile_definitions(fcvideo PRIVATE FCWORLD_RAM_FUNCTIONS=1)
endif()
