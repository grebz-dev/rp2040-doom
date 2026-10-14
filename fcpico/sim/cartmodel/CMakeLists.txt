# SPDX-License-Identifier: BSD-3-Clause
#
# Host cartridge model used by the Mesen mapper.  It can also be configured on
# its own for adapter-only tests: cmake -S fcpico/sim/cartmodel -B /tmp/cartmodel.

cmake_minimum_required(VERSION 3.20)

if(CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
    project(fcpico_cartmodel C)
    set(CMAKE_C_STANDARD 11)
    set(CMAKE_C_STANDARD_REQUIRED ON)
    set(CMAKE_C_EXTENSIONS OFF)
    set(FCPICO_HOST_ONLY ON)
endif()

if(NOT TARGET fcbus_host)
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../fcbus" "${CMAKE_CURRENT_BINARY_DIR}/fcbus")
endif()
if(NOT TARGET fcpico_testpattern_app)
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../testpattern"
                     "${CMAKE_CURRENT_BINARY_DIR}/testpattern")
endif()

# fcworld is compiled in directly: the Mesen makefile links a fixed library list.
add_library(fcpico_cartmodel STATIC fcpico_cart.c ${CMAKE_CURRENT_LIST_DIR}/../../port/video/fcworld.c)
target_include_directories(fcpico_cartmodel PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/../../port/video)
target_link_libraries(fcpico_cartmodel PUBLIC fcbus_host fcpico_testpattern_app)
target_compile_options(fcpico_cartmodel PRIVATE -Wall -Wextra -Werror)
