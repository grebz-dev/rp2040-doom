/* SPDX-License-Identifier: BSD-3-Clause */
#include "i_sound.h"
#include "i_audio_fcpico.h"
#include "i_picosound.h"
#include "ctest_lite.h"

extern sound_module_t sound_fcpico_module;
extern const music_module_t music_fcpico_module;

static uint32_t heartbeat;
static uint8_t registers[32], values[32];
static unsigned count;

#if FCPICO_AUDIO_IRQ_GUARD
static uint32_t interrupt_mask;
uint32_t save_and_disable_interrupts(void)
{
    uint32_t saved = interrupt_mask;
    interrupt_mask = 1;
    return saved;
}
void restore_interrupts(uint32_t saved) { interrupt_mask = saved; }
#endif

bool fcpico_audio_write(void *user, uint8_t reg, uint8_t value)
{
    (void)user;
#if FCPICO_AUDIO_IRQ_GUARD
    CHECK_EQ(interrupt_mask, 1);
#endif
    CHECK(count < 15);
    if (count < 32) {
        registers[count] = reg;
        values[count++] = value;
    }
    return true;
}

void fcpico_audio_update(void) { I_FCPicoAudioPump(heartbeat); }

/* WAD lookup only: the adapter must never request/copy MUSX lump bytes. */
lumpindex_t W_CheckNumForName(const char *name)
{
    return strcmp(name, "D_E1M1") == 0 ? 123 : -1;
}

static void tick(void)
{
    count = 0;
    heartbeat++;
    sound_fcpico_module.Update();
#if FCPICO_AUDIO_IRQ_GUARD
    CHECK_EQ(interrupt_mask, 0);
#endif
    CHECK(count <= 15);
    CHECK_EQ(fcapu_stats()->dropped, 0);
}

static bool has(uint8_t reg, uint8_t value)
{
    for (unsigned i = 0; i < count; i++)
        if (registers[i] == reg && values[i] == value) return true;
    return false;
}

static void stream(uint8_t *data, uint32_t frames, const uint8_t *body, uint32_t size)
{
    memset(data, 0, 16);
    memcpy(data, "APUS", 4);
    data[4] = 1;
    for (unsigned i = 0; i < 4; i++) {
        data[8 + i] = (uint8_t)(frames >> (8 * i));
        data[12 + i] = (uint8_t)(size >> (8 * i));
    }
    memcpy(data + 16, body, size);
}

static const sfxinfo_t pistol = {.name = "pistol", .priority = 64};
static const sfxinfo_t door = {.name = "doropn", .priority = 16};
static const sfxinfo_t noise = {.name = "rlaunc", .priority = 32};
static const sfxinfo_t unknown = {.name = "missing"};
static const sfxinfo_t alias = {.name = "chgun", .link = &pistol};

int main(int argc, char **argv)
{
    (void)argv;
    const sound_module_t *sound = &sound_fcpico_module;
    const music_module_t *music = &music_fcpico_module;
    if (argc > 1) {
        CHECK(sound->Init(true));
        CHECK(music->Init());
        CHECK_EQ(sound->GetSfxLumpNum(&pistol), -1);
        CHECK_EQ(sound->StartSound(&pistol, 0, 127, 128, 127), -1);
        CHECK(I_FCPicoRegisterSongLump(123) == NULL);
        CHECK(music->RegisterSong("MUSX", 4) == NULL);
        CHECK(!music->MusicIsPlaying());
        CHECK(!I_FCPicoAudioHasBank());
        tick();
        CHECK_EQ(count, 0);
        return ctest_lite_result();
    }

    uint8_t song[26], pulse[29], percussion[27];
    const uint8_t song_body[] = {4, 0x15, 15, 0, 0xbf, 2, 0x10, 3, 8, 0x84};
    const uint8_t pulse_body[] = {4, 4, 0xbf, 5, 8, 6, 0x20, 7, 8, 0x84, 1, 4, 0x30};
    const uint8_t noise_body[] = {3, 12, 0x3f, 14, 3, 15, 8, 0x83, 1, 12, 0x30};
    stream(song, 5, song_body, sizeof(song_body));
    stream(pulse, 6, pulse_body, sizeof(pulse_body));
    stream(percussion, 5, noise_body, sizeof(noise_body));
    fcapu_sfx_t effects[] = {
        {.kind = FCAPU_SFX_PULSE2, .priority = 64, .script = {pulse, sizeof(pulse)}},
        {.kind = FCAPU_SFX_PULSE2, .priority = 16, .script = {pulse, sizeof(pulse)}},
        {.kind = FCAPU_SFX_NOISE, .priority = 32, .script = {percussion, sizeof(percussion)}},
    };
    fcapu_bank_t bank = {.music = {{song, sizeof(song)}}, .sfx = effects, .sfx_count = 3};
    const char *names[] = {"pistol", "doropn", "rlaunc"};
    fcpico_audio_bank_t config = {.streams = &bank, .music_names = {"D_E1M1"}, .sfx_names = names};
    fcpico_audio_bank_t invalid = {.streams = &bank};
    CHECK(!I_FCPicoAudioSetBank(&invalid));
    CHECK(I_FCPicoAudioSetBank(&config));
    CHECK(sound->Init(true));
    CHECK(I_PicoSoundIsInitialized());
    CHECK(I_FCPicoAudioHasBank());
    CHECK(music->Init());
    CHECK(!I_FCPicoAudioSetBank(&config));
    CHECK_EQ(sound->GetSfxLumpNum(&pistol), 0);
    CHECK_EQ(sound->GetSfxLumpNum(&alias), 0);
    CHECK_EQ(sound->GetSfxLumpNum(&unknown), -1);
    CHECK_EQ(sound->StartSound(&unknown, 0, 127, 128, 127), -1);
    CHECK_EQ(sound->StartSound(&pistol, 32, 127, 128, 127), -1);
    CHECK(music->RegisterSong("MUSX", 4) == NULL);
    CHECK(music->RegisterSong(song, sizeof(song) - 1) == NULL);
    void *handle = I_FCPicoRegisterSongLump(123);
    CHECK(handle != NULL);
    CHECK(handle == music->RegisterSong(song, sizeof(song)));
    CHECK(I_FCPicoRegisterSongLump(-1) == NULL);
    CHECK(I_FCPicoRegisterSongLump(999) == NULL);
    music->PlaySong(handle, true);
    CHECK(music->MusicIsPlaying());
    CHECK(sound->Init(true)); /* Initializing the other module preserves music. */
    tick();
    CHECK(has(0, 0xbf));
    unsigned before = count;
    uint32_t frames_before = fcapu_stats()->frames;
    sound->Update();
    music->Poll();
    CHECK_EQ(count, before);
    CHECK_EQ(fcapu_stats()->frames, frames_before);
    music->PauseMusic();
    tick();
    CHECK(has(0x15, 0));
    CHECK(music->MusicIsPlaying());
    music->ResumeMusic();
    tick();
    CHECK(has(0x15, 15));
    music->SetMusicVolume(0);
    tick();
    CHECK(has(0, 0xb0));
    music->SetMusicVolume(127);
    tick();
    CHECK(has(0, 0xbf));
    for (unsigned i = 0; i < 12; i++) tick();
    CHECK(music->MusicIsPlaying());
    music->UnRegisterSong(handle);
    CHECK(!music->MusicIsPlaying());
    tick();
    CHECK(has(0x15, 0));
    music->PlaySong(handle, false);
    for (unsigned i = 0; i < 6; i++) tick();
    CHECK(!music->MusicIsPlaying());

    int shot = sound->StartSound(&alias, 0, 127, 0, 255);
    CHECK(shot > 0 && sound->SoundIsPlaying(shot));
    tick();
    CHECK(has(4, 0xbf));
    /* The script has silent frames; a parameter change must still update it. */
    sound->UpdateSoundParams(shot, 63, 255);
    tick();
    CHECK(has(4, 0xb7));
    sound->UpdateSoundParams(shot, 127, 0);
    tick();
    CHECK(has(4, 0xbf));
    int opening = sound->StartSound(&door, 1, 127, 128, 127);
    CHECK(opening > 0 && sound->SoundIsPlaying(opening));
    CHECK(!sound->SoundIsPlaying(shot));
    sound->StopSound(shot);
    sound->UpdateSoundParams(shot, 0, 128);
    CHECK(sound->SoundIsPlaying(opening));
    int rocket = sound->StartSound(&noise, 2, 127, 128, 127);
    CHECK(rocket > 0 && sound->SoundIsPlaying(rocket));
    tick();
    CHECK(has(4, 0xbf) && has(12, 0x3f));
    sound->StopSound(opening);
    CHECK(!sound->SoundIsPlaying(opening));
    for (unsigned i = 0; i < 6; i++) tick();
    CHECK(!sound->SoundIsPlaying(rocket));
    /* Simulate IRQ heartbeats while the engine does no render/update polling. */
    music->PlaySong(handle, true);
    uint32_t paused_engine_frames = fcapu_stats()->frames;
    for (unsigned i = 0; i < 120; i++) {
        count = 0;
        I_FCPicoAudioPump(++heartbeat);
    }
    CHECK_EQ(fcapu_stats()->frames, paused_engine_frames + 120);
    CHECK(music->MusicIsPlaying());
    music->StopSong();
    CHECK(sound->StartSound(&pistol, 0, 127, 128, 127) > 0);
    music->PlaySong(handle, true);
    tick();
    sound->Shutdown();
    music->Shutdown();
    CHECK(!I_PicoSoundIsInitialized());
    CHECK(!music->MusicIsPlaying());
    tick();
    CHECK(has(0x15, 0));
    CHECK_EQ(fcapu_stats()->dropped, 0);
    return ctest_lite_result();
}
