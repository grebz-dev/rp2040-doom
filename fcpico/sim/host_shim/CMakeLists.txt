# SPDX-License-Identifier: BSD-3-Clause
#
# Standalone project for the fc-pico host-build shim: fills in the pieces the pico-sdk host
# platform (PICO_PLATFORM=host) ships headers for but no implementation of, without needing
# SDL2: core 1 and the inter-core FIFO, alarms and repeating timers, and hard assertions.
#
# Usage:
#   cmake -S . -B /tmp/build-shim -G Ninja -DPICO_SDK_PATH=/path/to/pico-sdk -DPICO_PLATFORM=host
#   cmake --build /tmp/build-shim
#   ctest --test-dir /tmp/build-shim --output-on-failure

cmake_minimum_required(VERSION 3.20)

if(NOT FCPICO_SUPERBUILD)
    include(${CMAKE_CURRENT_SOURCE_DIR}/pico_sdk_import.cmake)
    project(host_shim C CXX ASM)
    pico_sdk_init()
endif()

if (NOT PICO_PLATFORM STREQUAL "host")
    message(FATAL_ERROR "fcpico/sim/host_shim only supports PICO_PLATFORM=host (got '${PICO_PLATFORM}')")
endif ()

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

add_library(host_shim STATIC
        multicore_host.c
        alarm_host.c
        runtime_host.c
)

target_include_directories(host_shim PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

target_link_libraries(host_shim
        PUBLIC
        pico_stdlib
        pico_multicore
        pico_sync
        pico_time
        PRIVATE
        Threads::Threads
)

enable_testing()

add_executable(host_shim_test test_host_shim.c)
target_link_libraries(host_shim_test PRIVATE host_shim Threads::Threads)

add_test(NAME host_shim_test COMMAND host_shim_test)

# -Wall -Wextra -Werror applies only to the shim's own translation units. pico_stdlib and the
# other pico-sdk libraries linked above are INTERFACE libraries that inject their .c files
# directly into whichever target links them (see e.g.
# src/host/pico_stdlib/CMakeLists.txt: target_sources(pico_stdlib INTERFACE ...)), so a
# target-wide target_compile_options(host_shim ...) would also compile pico-sdk's own sources
# (which are not warning-clean, e.g. src/host/pico_stdlib/stdlib.c's unused freq_khz parameter)
# under -Werror and break the build. set_source_files_properties() scopes the flags to exactly
# our files instead, in every target that compiles them.
set_source_files_properties(
        multicore_host.c
        alarm_host.c
        runtime_host.c
        test_host_shim.c
        PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror"
)
