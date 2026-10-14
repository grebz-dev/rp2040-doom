# SPDX-License-Identifier: BSD-3-Clause
add_library(fcinput_test_lib STATIC ../../port/input/fcinput.c)
target_compile_definitions(fcinput_test_lib PUBLIC FCINPUT_ENABLE_CHEATS=1)
target_include_directories(fcinput_test_lib PUBLIC ../../port/input)

add_executable(test_mapper test_mapper.c)
target_link_libraries(test_mapper PRIVATE fcinput_test_lib)
target_include_directories(test_mapper PRIVATE ..)
add_test(NAME test_mapper COMMAND test_mapper)
