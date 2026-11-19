# SPDX-License-Identifier: BSD-3-Clause
# Per-map world background palettes, chosen from a local IWAD at build time.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(PALETTE_TOOLS ${CMAKE_CURRENT_LIST_DIR}/../../tools)
set(PALETTE_HEADER ${CMAKE_CURRENT_BINARY_DIR}/fcpico_map_palettes.h)
add_custom_command(
    OUTPUT ${PALETTE_HEADER} ${CMAKE_CURRENT_BINARY_DIR}/map_palettes.json
    COMMAND ${Python3_EXECUTABLE} ${PALETTE_TOOLS}/map_palettes.py ${FCPICO_MAP_PALETTE_WAD}
        -o ${PALETTE_HEADER} --json ${CMAKE_CURRENT_BINARY_DIR}/map_palettes.json
    WORKING_DIRECTORY ${PALETTE_TOOLS}
    DEPENDS ${FCPICO_MAP_PALETTE_WAD} ${PALETTE_TOOLS}/map_palettes.py
        ${PALETTE_TOOLS}/fcvideo_ref.py ${PALETTE_TOOLS}/build_world_sprite_art.py
    VERBATIM)
add_custom_target(fcpico_map_palettes DEPENDS ${PALETTE_HEADER})
add_dependencies(doom_tiny_fcpico fcpico_map_palettes)
target_include_directories(doom_tiny_fcpico PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_compile_definitions(doom_tiny_fcpico PRIVATE FCPICO_MAP_PALETTES=1)
