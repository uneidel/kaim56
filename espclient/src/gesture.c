#include "gesture.h"
#include <string.h>

void gesture_init(gesture_t *g)
{
    memset(g, 0, sizeof(*g));
}

gesture_event_t gesture_update(gesture_t *g, bool pressed, int64_t now_ms)
{
    // Refuse to interpret anything until the button has been released once.
    if (!g->armed) {
        if (pressed) return GESTURE_NONE;
        g->armed = true;
        g->down = false;
        g->edge_ms = now_ms;
    }

    // --- debounced edges ---------------------------------------------------
    if (pressed != g->down && (now_ms - g->edge_ms) >= GESTURE_DEBOUNCE_MS) {
        g->down = pressed;
        g->edge_ms = now_ms;

        if (g->down) {
            g->factory_fired = false;
            if (g->pending_tap_ms > 0) g->second_press = true;
            return GESTURE_NONE;
        }

        // release
        if (g->hold_active) {
            g->hold_active = false;
            return GESTURE_HOLD_END;
        }
        if (g->second_press) {
            g->second_press = false;
            g->pending_tap_ms = 0;
            return GESTURE_DOUBLE_TAP;
        }
        g->pending_tap_ms = now_ms;      // wait and see if a second tap comes
        return GESTURE_NONE;
    }

    // --- held down ---------------------------------------------------------
    if (g->down) {
        int64_t held = now_ms - g->edge_ms;

        if (held >= GESTURE_FACTORY_HOLD_MS && !g->factory_fired) {
            g->factory_fired = true;
            g->hold_active = false;
            g->second_press = false;
            g->pending_tap_ms = 0;
            return GESTURE_FACTORY_RESET;
        }
        if (held >= GESTURE_HOLD_MIN_MS && !g->hold_active) {
            // A hold outranks a pending tap: push-to-talk must never be
            // blocked by an earlier stray press.
            g->hold_active = true;
            g->second_press = false;
            g->pending_tap_ms = 0;
            return GESTURE_HOLD_START;
        }
        return GESTURE_NONE;
    }

    // --- up, waiting out the double-tap window -----------------------------
    if (g->pending_tap_ms > 0 && (now_ms - g->pending_tap_ms) >= GESTURE_DOUBLE_GAP_MS) {
        g->pending_tap_ms = 0;
        return GESTURE_TAP;
    }

    return GESTURE_NONE;
}
