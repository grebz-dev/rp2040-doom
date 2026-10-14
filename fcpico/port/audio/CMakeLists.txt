# SPDX-License-Identifier: BSD-3-Clause
cmake_policy(SET CMP0118 NEW)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(AUDIO_TOOLS ${CMAKE_CURRENT_LIST_DIR}/../../tools/audio)
set(AUDIO_OUTPUT ${CMAKE_CURRENT_BINARY_DIR})
set(AUDIO_C ${AUDIO_OUTPUT}/audio_bank.c)
set(AUDIO_BIN ${AUDIO_OUTPUT}/audio.bin)
set(AUDIO_MANIFEST ${AUDIO_OUTPUT}/audio.json)
add_custom_command(
    OUTPUT ${AUDIO_C} ${AUDIO_BIN} ${AUDIO_MANIFEST}
    COMMAND ${Python3_EXECUTABLE} ${AUDIO_TOOLS}/build_bank.py
        ${FCPICO_AUDIO_WAD}
        --sounds ${CMAKE_CURRENT_LIST_DIR}/../../../src/doom/sounds.c
        --layout ${CMAKE_CURRENT_LIST_DIR}/../flash_layout.h -o ${AUDIO_OUTPUT}
    DEPENDS ${FCPICO_AUDIO_WAD} ${AUDIO_TOOLS}/build_bank.py
        ${AUDIO_TOOLS}/mus2apus.py ${AUDIO_TOOLS}/sfx2apu.py
        ${AUDIO_TOOLS}/apus.py ${AUDIO_TOOLS}/wadextract.py
        ${CMAKE_CURRENT_LIST_DIR}/../../../src/doom/sounds.c
        ${CMAKE_CURRENT_LIST_DIR}/../flash_layout.h
    VERBATIM)
add_custom_target(fcpico_audio_bank DEPENDS ${AUDIO_C} ${AUDIO_BIN} ${AUDIO_MANIFEST})
add_dependencies(doom_tiny_fcpico fcpico_audio_bank)
set_source_files_properties(${AUDIO_C}
    DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/../../../src
    PROPERTIES GENERATED TRUE)
target_sources(doom_tiny_fcpico PRIVATE ${AUDIO_C})
target_include_directories(doom_tiny_fcpico PRIVATE ${CMAKE_CURRENT_LIST_DIR}/..)
target_compile_definitions(doom_tiny_fcpico PRIVATE FCPICO_AUDIO_BANK=1)
if(PICO_ON_DEVICE)
    # A complete image must carry the archive matching the compiled offsets.
    add_custom_target(fcpico_doom_audio_uf2
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_LIST_DIR}/../../tools/whx2uf2.py
            ${CMAKE_CURRENT_LIST_DIR}/../../../doom1.whx
            ${AUDIO_OUTPUT}/fcpico_doom_audio_whx.uf2
            --firmware $<TARGET_FILE_DIR:doom_tiny_fcpico>/fcpico_doom.uf2
            --audio ${AUDIO_BIN}
        DEPENDS doom_tiny_fcpico ${AUDIO_BIN}
        VERBATIM)
else()
    add_test(NAME engine_audio_bank
        COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_LIST_DIR}/../../tests/audio_engine/check_engine_bank.py
            $<TARGET_FILE:doom_tiny_fcpico>
            ${CMAKE_CURRENT_LIST_DIR}/../../../doom1.whx)
endif()
