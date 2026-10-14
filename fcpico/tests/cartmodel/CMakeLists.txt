# SPDX-License-Identifier: BSD-3-Clause

if(NOT TARGET fcpico_cartmodel)
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../sim/cartmodel"
                     "${CMAKE_CURRENT_BINARY_DIR}/cartmodel")
endif()

add_executable(test_cartmodel test_cartmodel.c)
target_link_libraries(test_cartmodel PRIVATE fcpico_cartmodel)
target_include_directories(test_cartmodel PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/..
    ${CMAKE_CURRENT_LIST_DIR}/../../sim/cartmodel
)
add_test(NAME test_cartmodel COMMAND test_cartmodel)
