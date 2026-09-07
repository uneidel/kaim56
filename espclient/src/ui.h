#pragma once
#include "gesture.h"

typedef enum {
    UI_IDLE = 0,
    UI_RECORDING,
    UI_THINKING,
    UI_SPEAKING,
    UI_DISCARDED,
    UI_NO_NET,
} ui_state_t;

void            ui_init(void);
gesture_event_t ui_poll(void);        // call every ~10 ms
void            ui_set_state(ui_state_t s);
