# SPDX-License-Identifier: BSD-3-Clause

find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(BOOTROM_C ${CMAKE_CURRENT_BINARY_DIR}/tutorial_bootrom.c)
add_custom_command(
    OUTPUT ${BOOTROM_C}
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_LIST_DIR}/../tools/nes/bin2c.py
            ${CMAKE_CURRENT_LIST_DIR}/../external/fc-pico/tutorial_project/BOOTROM/rom.NES
            ${BOOTROM_C} fcpico_bootrom
    DEPENDS ${CMAKE_CURRENT_LIST_DIR}/../external/fc-pico/tutorial_project/BOOTROM/rom.NES
            ${CMAKE_CURRENT_LIST_DIR}/../tools/nes/bin2c.py
    VERBATIM)

add_executable(fcpico_testpattern
    main_testpattern.c
    cli.c
    trace.c
    ${BOOTROM_C})
target_include_directories(fcpico_testpattern PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(fcpico_testpattern PRIVATE
    fcbus_device fcpico_testpattern_app hardware_clocks hardware_dma hardware_flash hardware_pio
    hardware_watchdog pico_bootrom pico_stdlib pico_unique_id)
target_compile_options(fcpico_testpattern PRIVATE -Wall -Wextra -Werror)
pico_enable_stdio_usb(fcpico_testpattern 1)
pico_enable_stdio_uart(fcpico_testpattern 0)
pico_set_binary_type(fcpico_testpattern copy_to_ram)
# pico_add_extra_outputs() also appends its own -Wl,-Map, after anything set
# here, and GNU ld honours the last one.  So there is exactly one link map and
# the SDK names and places it: <build root>/fcpico_testpattern.elf.map.  Setting
# a second one here produced no file and a CI artifact path that never existed.
pico_add_extra_outputs(fcpico_testpattern)
