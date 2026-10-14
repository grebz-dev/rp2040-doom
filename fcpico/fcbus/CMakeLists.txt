# SPDX-License-Identifier: BSD-3-Clause
#
# fcbus -- cartridge bus library. Backend-independent core plus a host simulation
# backend today; mirrors tutorial_project/tuto1_hw/sys/rp_system.{cpp,h} (see fcpico/README.md).

add_library(fcbus_core STATIC
    fcbus_core.c
)
target_include_directories(fcbus_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_library(fcbus_host STATIC
    fcbus_host.c
)
target_link_libraries(fcbus_host PUBLIC fcbus_core)
target_include_directories(fcbus_host PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

if(PICO_ON_DEVICE)
    # Pico SDK libraries add source files to their consumer. Keeping this backend
    # as an interface target compiles those SDK sources exactly once per firmware.
    add_library(fcbus_device INTERFACE)
    target_sources(fcbus_device INTERFACE fcbus_device.c)
    target_include_directories(fcbus_device INTERFACE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(fcbus_device INTERFACE
        fcbus_core hardware_dma hardware_gpio hardware_irq hardware_pio pico_time)
    pico_generate_pio_header(fcbus_device ${CMAKE_CURRENT_SOURCE_DIR}/fcppu.pio)
endif()
