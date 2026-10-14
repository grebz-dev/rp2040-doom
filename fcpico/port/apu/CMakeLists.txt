# SPDX-License-Identifier: BSD-3-Clause
add_library(fcapu STATIC fcapu.c)
target_include_directories(fcapu PUBLIC ${CMAKE_CURRENT_LIST_DIR})
target_include_directories(fcapu PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../../fcbus)
target_compile_options(fcapu PRIVATE -Wall -Wextra -Werror)
