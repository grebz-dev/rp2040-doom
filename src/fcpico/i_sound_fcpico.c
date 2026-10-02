/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Doom sound/music adapters for the cartridge APU register sequencer. */

#include "config.h"
#include "i_sound.h"
#include <stddef.h>
#include "i_picosound.h"
#include "i_audio_fcpico.h"
#include "w_wad.h"
#include <limits.h>
#include <string.h>

uint8_t restart_song_state;

void I_SetOPLDriverVer(opl_driver_ver_t ver) { (void)ver; }

static snddevice_t devices[] = { SNDDEVICE_SB };
static const fcpico_audio_bank_t *audio_bank;
static bool ready, initialized;
static int song_ids[FCAPU_MUSIC_COUNT];
static int channel_handles[32];
static void *current_song;

bool I_FCPicoAudioSetBank(const fcpico_audio_bank_t *bank)
{
    if (ready || (bank && (!bank->streams ||
        bank->streams->sfx_count > INT_MAX ||
        (bank->streams->sfx_count && !bank->sfx_names)))) return false;
    audio_bank = bank;
    return true;
}

static void initialize(void)
{
    if (!ready) {
#if FCPICO_AUDIO_BANK
        extern const fcpico_audio_bank_t *fcpico_audio_default_bank(void);
        if (!audio_bank) audio_bank = fcpico_audio_default_bank();
#endif
        fcapu_init(audio_bank ? audio_bank->streams : NULL, fcpico_audio_write, NULL);
        for (int i = 0; i < FCAPU_MUSIC_COUNT; i++) song_ids[i] = i;
        for (unsigned i = 0; i < sizeof(channel_handles) / sizeof(channel_handles[0]); i++)
            channel_handles[i] = -1;
        ready = true;
    }
    initialized = true;
}

void I_FCPicoAudioPump(uint32_t heartbeat)
{
    if (ready) fcapu_pump(heartbeat);
}

void *I_FCPicoRegisterSongLump(int lumpnum)
{
    if (!initialized || !audio_bank || lumpnum < 0) return NULL;
    for (int i = 0; i < FCAPU_MUSIC_COUNT; i++) {
        const char *name = audio_bank->music_names[i];
        if (name && audio_bank->streams->music[i].data &&
            W_CheckNumForName(name) == lumpnum) return &song_ids[i];
    }
    return NULL;
}

static int song_id(void *handle)
{
    if (!handle) return -1;
    for (int i = 0; i < FCAPU_MUSIC_COUNT; i++)
        if (handle == &song_ids[i]) return i;
    return -1;
}

static int effect_id(should_be_const sfxinfo_t *sfx)
{
    if (!audio_bank || !sfx) return -1;
    /* Resolve Doom aliases such as chaingun -> pistol without chasing cycles. */
    for (int depth = 0; sfx->link; depth++) {
        if (depth == 16) return -1;
        sfx = sfx->link;
    }
    for (size_t i = 0; i < audio_bank->streams->sfx_count; i++) {
        const char *name = audio_bank->sfx_names[i];
        if (name && strncmp(name, sfx->name, sizeof(sfx->name)) == 0) return (int)i;
    }
    return -1;
}

static boolean init_sound(boolean use_sfx_prefix)
{
    (void)use_sfx_prefix;
    initialize();
    return true;
}
static boolean init_music(void) { initialize(); return true; }
static void shutdown_module(void)
{
    if (!ready) return;
    fcapu_music_stop();
    current_song = NULL;
    for (unsigned i = 0; i < sizeof(channel_handles) / sizeof(channel_handles[0]); i++) {
        fcapu_sfx_stop(channel_handles[i]);
        channel_handles[i] = -1;
    }
    initialized = false;
    /* Stops are replayed at the next heartbeat, like other mailbox writes. */
    fcpico_audio_update();
}
static int get_sfx_lump(should_be_const sfxinfo_t *sfx)
{
    return effect_id(sfx);
}
static void update_sound(void) { fcpico_audio_update(); }
static void update_sound_params(int channel, int vol, int sep)
{
    (void)sep;
    if (initialized) fcapu_sfx_volume(channel, vol);
}
static int start_sound(should_be_const sfxinfo_t *sfx, int channel, int vol, int sep, int pitch)
{
    (void)sep; (void)pitch;
    int id = effect_id(sfx);
    if (!initialized || id < 0 || channel < 0 ||
        (unsigned)channel >= sizeof(channel_handles) / sizeof(channel_handles[0])) return -1;
    fcapu_sfx_stop(channel_handles[channel]);
    int handle = fcapu_sfx_start(id, vol);
    channel_handles[channel] = handle;
    return handle;
}
static void stop_sound(int channel) { if (ready) fcapu_sfx_stop(channel); }
static boolean sound_is_playing(int channel) { return initialized && fcapu_sfx_playing(channel); }
static void cache_sounds(should_be_const sfxinfo_t *sounds, int count)
{
    (void)sounds; (void)count;
}

sound_module_t sound_fcpico_module = {
    devices, 1, init_sound, shutdown_module, get_sfx_lump,
    update_sound, update_sound_params, start_sound, stop_sound,
    sound_is_playing, cache_sounds
};

static void set_music_volume(int volume)
{
    if (volume < 0) volume = 0;
    if (volume > 127) volume = 127;
    if (ready) fcapu_music_volume((volume * 15 + 63) / 127);
}
static void pause_music(void) { if (initialized) fcapu_music_pause(true); }
static void resume_music(void) { if (initialized) fcapu_music_pause(false); }
static void *register_song(should_be_const void *data, int len)
{
    if (!initialized || !audio_bank || !data || len < 0) return NULL;
    for (int i = 0; i < FCAPU_MUSIC_COUNT; i++) {
        fcapu_stream_t stream = audio_bank->streams->music[i];
        if (stream.data == data && stream.size == (size_t)len) return &song_ids[i];
    }
    return NULL;
}
static void unregister_song(void *handle)
{
    if (handle && handle == current_song) {
        fcapu_music_stop();
        current_song = NULL;
    }
}
static void play_song(void *handle, boolean looping)
{
    int id = song_id(handle);
    if (initialized && id >= 0 && fcapu_music_play(id, looping)) current_song = handle;
}
static void stop_song(void) { if (ready) fcapu_music_stop(); current_song = NULL; }
static boolean music_is_playing(void) { return initialized && fcapu_music_playing(); }
static void poll_music(void) { fcpico_audio_update(); }

const music_module_t music_fcpico_module = {
    devices, 1, init_music, shutdown_module, set_music_volume,
    pause_music, resume_music, register_song, unregister_song,
    play_song, stop_song, music_is_playing, poll_music
};

bool I_PicoSoundIsInitialized(void) { return initialized; }
void I_PicoSoundSetMusicGenerator(void (*generator)(audio_buffer_t *buffer))
{
    (void)generator;
}
void I_PicoSoundFade(bool in) { (void)in; }
bool I_PicoSoundFading(void) { return false; }

void I_OPL_DevMessages(char *result, size_t result_len)
{
    if (result_len != 0) result[0] = '\0';
}
