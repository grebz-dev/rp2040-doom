/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Translate latched NES pad frames into Doom keyboard events. */

#include "config.h"
#include "doomtype.h"
#include "doomkeys.h"
#include "d_event.h"
#include "i_input.h"
#include "m_controls.h"
#include "doom/doomstat.h"
#include "fcinput.h"
#include "fcpico_video_sink.h"

extern int messageToPrint;
extern boolean messageNeedsInput;

static fcinput_t mapper;
static fcinput_event_t event_storage[32];
static fcinput_event_ring_t event_ring;
static int fire_key = KEY_RCTRL;

void I_InputInit(void)
{
    const fcinput_config_t config = {
        .code = {
            [FCINPUT_KEY_UP] = KEY_UPARROW,
            [FCINPUT_KEY_DOWN] = KEY_DOWNARROW,
            [FCINPUT_KEY_LEFT] = KEY_LEFTARROW,
            [FCINPUT_KEY_RIGHT] = KEY_RIGHTARROW,
            [FCINPUT_KEY_STRAFE_LEFT] = ',',
            [FCINPUT_KEY_STRAFE_RIGHT] = '.',
            [FCINPUT_KEY_FIRE] = KEY_RCTRL,
            [FCINPUT_KEY_USE] = ' ',
            [FCINPUT_KEY_NEXT_WEAPON] = ']',
            [FCINPUT_KEY_AUTOMAP] = KEY_TAB,
            [FCINPUT_KEY_MENU] = KEY_ESCAPE,
            [FCINPUT_KEY_PAUSE] = KEY_PAUSE,
            [FCINPUT_KEY_SPEED] = KEY_RSHIFT,
        },
        .always_run = true,
    };
    fcinput_init(&mapper, &config);
    fcinput_ring_init(&event_ring, event_storage,
                      sizeof event_storage / sizeof event_storage[0]);
}

void I_StartTic(void)
{
    uint8_t pad1;
    while (fcpico_read_pad_frame(&pad1)) {
        fcinput_latch_frame(&mapper, pad1, 0);
    }

    /* The tiny build leaves this optional key unbound by default. */
    if (key_nextweapon == 0) key_nextweapon = ']';
    fcinput_poll(&mapper, &event_ring);

    fcinput_event_t mapped;
    while (fcinput_ring_pop(&event_ring, &mapped)) {
        int key = mapped.key;
        if (key == KEY_RCTRL) {
            if (mapped.down) {
                fire_key = messageToPrint && messageNeedsInput
                         ? key_menu_confirm : menuactive ? KEY_ENTER : KEY_RCTRL;
            }
            key = fire_key;
        } else if (key == ' ' && menuactive) {
            key = messageToPrint && messageNeedsInput
                ? key_menu_abort : KEY_BACKSPACE;
        }
        event_t event = {
            .type = mapped.down ? ev_keydown : ev_keyup,
            .data1 = key,
            .data2 = key < 128 ? key : 0,
        };
        D_PostEvent(&event);
    }
}

void I_BindInputVariables(void) {}
void I_ReadMouse(void) {}
void I_StartTextInput(int x1, int y1, int x2, int y2)
{
    (void)x1; (void)y1; (void)x2; (void)y2;
}
void I_StopTextInput(void) {}
void I_GetEvent(void) {}
void I_GetEventTimeout(int key_timeout) { (void)key_timeout; }
int GetTypedChar(int scancode, boolean shiftdown)
{
    (void)scancode; (void)shiftdown;
    return 0;
}
