/* SPDX-License-Identifier: GPL-2.0-or-later */
/* FC PICO input skeleton. Controller mapping follows in P1-T4. */

#include "config.h"
#include "doomtype.h"
#include "i_input.h"

void I_InputInit(void) {}
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
