# SPDX-License-Identifier: BSD-3-Clause

add_library(fcpico_testpattern_app STATIC testpattern.c)
target_include_directories(fcpico_testpattern_app PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(fcpico_testpattern_app PUBLIC fcbus_core)
target_compile_options(fcpico_testpattern_app PRIVATE -Wall -Wextra -Werror)
