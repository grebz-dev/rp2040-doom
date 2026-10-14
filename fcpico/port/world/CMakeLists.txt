# SPDX-License-Identifier: BSD-3-Clause
# Optional native world sprites: generate the atlas from a local IWAD.
# Device firmware reads it at FLASH_WORLD_ADDR; host builds embed the bytes.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(WORLD_TOOLS ${CMAKE_CURRENT_LIST_DIR}/../../tools)
set(WORLD_OUTPUT ${CMAKE_CURRENT_BINARY_DIR})
set(WORLD_BIN ${WORLD_OUTPUT}/world_atlas.bin)
set(WORLD_IDENTITY ${WORLD_OUTPUT}/world_atlas_identity.h)
add_custom_command(
    OUTPUT ${WORLD_BIN} ${WORLD_IDENTITY} ${WORLD_OUTPUT}/world_atlas.json
    COMMAND ${Python3_EXECUTABLE} ${WORLD_TOOLS}/build_world_atlas.py ${FCPICO_WORLD_WAD}
        --scope ${CMAKE_CURRENT_LIST_DIR}/../../assets/world-sprite-scope.json
        --info ${CMAKE_CURRENT_LIST_DIR}/../../../src/doom/info.h
        --layout ${CMAKE_CURRENT_LIST_DIR}/../flash_layout.h -o ${WORLD_OUTPUT}
    WORKING_DIRECTORY ${WORLD_TOOLS}
    DEPENDS ${FCPICO_WORLD_WAD} ${WORLD_TOOLS}/build_world_atlas.py
        ${WORLD_TOOLS}/build_world_sprite_art.py ${WORLD_TOOLS}/audio/wadextract.py
        ${CMAKE_CURRENT_LIST_DIR}/../../assets/world-sprite-scope.json
        ${CMAKE_CURRENT_LIST_DIR}/../../../src/doom/info.h
        ${CMAKE_CURRENT_LIST_DIR}/../flash_layout.h
    VERBATIM)
add_custom_target(fcpico_world_atlas DEPENDS ${WORLD_BIN} ${WORLD_IDENTITY})
add_dependencies(doom_tiny_fcpico fcpico_world_atlas)
set(ENGINE_SRC ${CMAKE_CURRENT_LIST_DIR}/../../../src)
target_sources(doom_tiny_fcpico PRIVATE ${ENGINE_SRC}/fcpico/world_sprites.c)
target_include_directories(doom_tiny_fcpico PRIVATE ${WORLD_OUTPUT} ${CMAKE_CURRENT_LIST_DIR}/..)
target_compile_definitions(doom_tiny_fcpico PRIVATE FCPICO_WORLD_SPRITES=1)
# Plan 14c option 5. The probe only reads PA12/CS1 on PIO1 (never drives a pin).
# Loose gating publishes pictures without waiting for their sprites.
option(FCPICO_SPRITE_FETCH_PROBE "Count PPU sprite-table fetches seen by the RP" ON)
option(FCPICO_WORLD_LOOSE "Publish pictures without waiting for world sprites" OFF)
if(FCPICO_SPRITE_FETCH_PROBE AND PICO_ON_DEVICE)
    target_compile_definitions(doom_tiny_fcpico PRIVATE FCPICO_SPRITE_FETCH_PROBE=1)
endif()
if(FCPICO_WORLD_LOOSE)
    target_compile_definitions(doom_tiny_fcpico PRIVATE FCPICO_WORLD_LOOSE=1)
endif()
if(PICO_ON_DEVICE)
    add_custom_target(fcpico_doom_world_uf2
        COMMAND ${Python3_EXECUTABLE} ${WORLD_TOOLS}/whx2uf2.py
            ${ENGINE_SRC}/../doom1.whx ${WORLD_OUTPUT}/fcpico_doom_world_whx.uf2
            --firmware $<TARGET_FILE_DIR:doom_tiny_fcpico>/fcpico_doom.uf2
            --world ${WORLD_BIN}
            $<$<BOOL:${FCPICO_AUDIO_WAD}>:--audio> $<$<BOOL:${FCPICO_AUDIO_WAD}>:${CMAKE_BINARY_DIR}/port/audio/audio.bin>
        DEPENDS doom_tiny_fcpico ${WORLD_BIN}
        COMMAND_EXPAND_LISTS VERBATIM)
else()
    set(WORLD_C ${WORLD_OUTPUT}/world_atlas_data.c)
    add_custom_command(OUTPUT ${WORLD_C}
        COMMAND ${Python3_EXECUTABLE} ${WORLD_TOOLS}/nes/bin2c.py ${WORLD_BIN} ${WORLD_C}
            fcpico_world_atlas_bytes
        DEPENDS ${WORLD_BIN} ${WORLD_TOOLS}/nes/bin2c.py VERBATIM)
    set_source_files_properties(${WORLD_C} DIRECTORY ${ENGINE_SRC} PROPERTIES GENERATED TRUE)
    add_custom_target(fcpico_world_atlas_c DEPENDS ${WORLD_C})
    add_dependencies(doom_tiny_fcpico fcpico_world_atlas_c)
    target_sources(doom_tiny_fcpico PRIVATE ${WORLD_C})
endif()
