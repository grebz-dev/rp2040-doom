/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Silent interface modules for the initial cartridge engine link. */

#include "config.h"
#include "i_sound.h"
#include <stddef.h>
#include "i_picosound.h"

uint8_t restart_song_state;

void I_SetOPLDriverVer(opl_driver_ver_t ver) { (void)ver; }

static snddevice_t devices[] = { SNDDEVICE_SB };

static boolean init_sound(boolean use_sfx_prefix)
{
    (void)use_sfx_prefix;
    return true;
}
static boolean init_music(void) { return true; }
static void shutdown_module(void) {}
static int get_sfx_lump(should_be_const sfxinfo_t *sfx)
{
    (void)sfx;
    return 0;
}
static void update_sound(void) {}
static void update_sound_params(int channel, int vol, int sep)
{
    (void)channel; (void)vol; (void)sep;
}
static int start_sound(should_be_const sfxinfo_t *sfx, int channel, int vol, int sep, int pitch)
{
    (void)sfx; (void)channel; (void)vol; (void)sep; (void)pitch;
    return -1;
}
static void stop_sound(int channel) { (void)channel; }
static boolean sound_is_playing(int channel) { (void)channel; return false; }
static void cache_sounds(should_be_const sfxinfo_t *sounds, int count)
{
    (void)sounds; (void)count;
}

sound_module_t sound_fcpico_module = {
    devices, 1, init_sound, shutdown_module, get_sfx_lump,
    update_sound, update_sound_params, start_sound, stop_sound,
    sound_is_playing, cache_sounds
};

static void set_music_volume(int volume) { (void)volume; }
static void pause_music(void) {}
static void resume_music(void) {}
static void *register_song(should_be_const void *data, int len)
{
    (void)data; (void)len;
    return NULL;
}
static void unregister_song(void *handle) { (void)handle; }
static void play_song(void *handle, boolean looping)
{
    (void)handle; (void)looping;
}
static void stop_song(void) {}
static boolean music_is_playing(void) { return false; }
static void poll_music(void) {}

const music_module_t music_fcpico_module = {
    devices, 1, init_music, shutdown_module, set_music_volume,
    pause_music, resume_music, register_song, unregister_song,
    play_song, stop_song, music_is_playing, poll_music
};

bool I_PicoSoundIsInitialized(void) { return false; }
void I_PicoSoundSetMusicGenerator(void (*generator)(audio_buffer_t *buffer))
{
    (void)generator;
}
void I_PicoSoundFade(bool in) { (void)in; }
bool I_PicoSoundFading(void) { return false; }
