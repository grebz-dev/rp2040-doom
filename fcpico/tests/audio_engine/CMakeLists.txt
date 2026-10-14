# SPDX-License-Identifier: BSD-3-Clause
# Compile the real adapter against Doom's headers with the tiny const tables.
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/config.h
    "/* Standalone sound adapter test configuration. */\n#define HAVE_DECL_STRCASECMP 1\n#define HAVE_DECL_STRNCASECMP 1\n")
set(ENGINE_ROOT ${CMAKE_CURRENT_LIST_DIR}/../../..)
add_executable(test_engine_audio
    test_engine_audio.c
    ${ENGINE_ROOT}/src/fcpico/i_sound_fcpico.c)
target_compile_definitions(test_engine_audio PRIVATE
    FCPICO=1 DOOM_ONLY=1 DOOM_SMALL=1 DOOM_CONST=1 USE_CONST_SFX=1)
target_include_directories(test_engine_audio PRIVATE
    ${CMAKE_CURRENT_BINARY_DIR} ${ENGINE_ROOT}/src ${ENGINE_ROOT}/src/fcpico ..)
target_link_libraries(test_engine_audio PRIVATE fcapu)
add_test(NAME test_engine_audio COMMAND test_engine_audio)
add_test(NAME test_engine_audio_empty COMMAND test_engine_audio --empty)

# Compile the device critical-section path with an interrupt-mask model.
add_executable(test_engine_audio_irq
    test_engine_audio.c
    ${ENGINE_ROOT}/src/fcpico/i_sound_fcpico.c)
target_compile_definitions(test_engine_audio_irq PRIVATE
    FCPICO=1 FCPICO_AUDIO_IRQ_GUARD=1 DOOM_ONLY=1 DOOM_SMALL=1 DOOM_CONST=1 USE_CONST_SFX=1)
target_include_directories(test_engine_audio_irq PRIVATE
    irq_stub ${CMAKE_CURRENT_BINARY_DIR} ${ENGINE_ROOT}/src ${ENGINE_ROOT}/src/fcpico ..)
target_link_libraries(test_engine_audio_irq PRIVATE fcapu)
add_test(NAME test_engine_audio_irq COMMAND test_engine_audio_irq)
