#pragma once
#include <stdint.h>
#include <stdbool.h>

// Button gesture grammar (spec D3):
//   hold        -> push-to-talk        (HOLD_START ... HOLD_END)
//   single tap  -> stop speaking       (TAP, reported once the double window closes)
//   double tap  -> new conversation    (DOUBLE_TAP)
//   10 s hold   -> factory reset       (FACTORY_RESET)
//
// A tap followed by a hold resolves as a hold: the earlier tap is abandoned,
// because push-to-talk must never be blocked by a stray press.
//
// Nothing is recognised until the button has been seen released once. A pin
// that reads "pressed" from the start - miswired, floating, or reconfigured as
// an output, which disables the input buffer - would otherwise look like a
// 10 s hold and trigger a factory reset on its own.

typedef enum {
    GESTURE_NONE = 0,
    GESTURE_HOLD_START,
    GESTURE_HOLD_END,
    GESTURE_TAP,
    GESTURE_DOUBLE_TAP,
    GESTURE_FACTORY_RESET,
} gesture_event_t;

#define GESTURE_DEBOUNCE_MS        30
#define GESTURE_HOLD_MIN_MS       250     // longer than this is a hold, not a tap
#define GESTURE_DOUBLE_GAP_MS     350     // window for the second tap
#define GESTURE_FACTORY_HOLD_MS 10000

typedef struct {
    bool    armed;            // saw the button released at least once
    bool    down;             // debounced level
    bool    hold_active;      // HOLD_START fired, HOLD_END has not
    bool    second_press;     // a press arrived while a tap was pending
    bool    factory_fired;
    int64_t edge_ms;          // last debounced transition
    int64_t pending_tap_ms;   // >0 while waiting out the double-tap window
} gesture_t;

void gesture_init(gesture_t *g);

// Feed the raw debounced-input level and the current time. Call frequently
// (every ~10 ms). Returns at most one event per call.
gesture_event_t gesture_update(gesture_t *g, bool pressed, int64_t now_ms);
