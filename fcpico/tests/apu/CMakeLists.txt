# SPDX-License-Identifier: BSD-3-Clause
add_subdirectory(../../port/apu ${CMAKE_CURRENT_BINARY_DIR}/fcapu)
add_executable(test_sequencer test_sequencer.c)
target_link_libraries(test_sequencer PRIVATE fcapu)
target_include_directories(test_sequencer PRIVATE .. ../../fcbus)
add_test(NAME test_sequencer COMMAND test_sequencer)
