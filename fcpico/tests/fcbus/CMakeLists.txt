# SPDX-License-Identifier: BSD-3-Clause
#
# One executable per test file, each linked against fcbus_host (which pulls in
# fcbus_core) plus ../ctest_lite.h, and registered with ctest.

set(FCBUS_TESTS
    test_mailbox
    test_rx
    test_sync
    test_stream
    test_host_roundtrip
)

foreach(test_name ${FCBUS_TESTS})
    add_executable(${test_name} ${test_name}.c)
    target_link_libraries(${test_name} PRIVATE fcbus_host)
    target_include_directories(${test_name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/..)
    add_test(NAME ${test_name} COMMAND ${test_name})
endforeach()
