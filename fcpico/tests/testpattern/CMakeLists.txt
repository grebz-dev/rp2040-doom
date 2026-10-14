# SPDX-License-Identifier: BSD-3-Clause

add_executable(test_testpattern test_testpattern.c)
target_link_libraries(test_testpattern PRIVATE fcpico_testpattern_app fcbus_host)
target_include_directories(test_testpattern PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/..
    ${CMAKE_CURRENT_LIST_DIR}/../../testpattern
)
add_test(NAME test_testpattern COMMAND test_testpattern)
