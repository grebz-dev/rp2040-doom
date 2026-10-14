# SPDX-License-Identifier: BSD-3-Clause
add_library(fcinput STATIC fcinput.c)
target_include_directories(fcinput PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
