#include <unity.h>
#include "gesture.h"

static gesture_t g;
static int64_t now;

void setUp(void)
{
    gesture_init(&g);
    now = 1000;
    // At boot the button is released, so the recogniser arms on the first
    // poll. Tests that need the unarmed state re-init explicitly.
    gesture_update(&g, false, now);
}
void tearDown(void) {}

// Advance time in 10 ms polls, collecting the first non-NONE event seen.
static gesture_event_t run(bool pressed, int ms)
{
    gesture_event_t found = GESTURE_NONE;
    for (int i = 0; i < ms; i += 10) {
        now += 10;
        gesture_event_t e = gesture_update(&g, pressed, now);
        if (e != GESTURE_NONE && found == GESTURE_NONE) found = e;
    }
    return found;
}

static void test_hold_starts_and_ends(void)
{
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_END, run(false, 100));
}

static void test_short_tap_reports_tap_after_window(void)
{
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(true, 100));    // shorter than HOLD_MIN
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(false, 100));   // window still open
    TEST_ASSERT_EQUAL_INT(GESTURE_TAP, run(false, 400));
}

static void test_double_tap(void)
{
    run(true, 100);                                          // first tap down
    run(false, 100);                                         // first tap up
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(true, 100));     // second tap down
    TEST_ASSERT_EQUAL_INT(GESTURE_DOUBLE_TAP, run(false, 100));
}

static void test_double_tap_emits_no_single_tap(void)
{
    run(true, 100); run(false, 100);
    run(true, 100);
    TEST_ASSERT_EQUAL_INT(GESTURE_DOUBLE_TAP, run(false, 100));
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(false, 1000));  // no stray TAP later
}

// The deadlock this module exists to prevent: after a tap, a press-and-hold
// must still produce HOLD_START. The first draft blocked holds while a tap
// was pending, and the pending tap could only clear while the button was up,
// so push-to-talk died permanently after any single tap.
static void test_tap_then_hold_still_starts_hold(void)
{
    run(true, 100); run(false, 100);          // tap, window still open
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_END, run(false, 100));
}

static void test_hold_after_completed_tap(void)
{
    run(true, 100); run(false, 500);          // full tap, window closed
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
}

static void test_repeated_holds(void)
{
    for (int i = 0; i < 3; i++) {
        TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
        TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_END, run(false, 500));
    }
}

static void test_factory_reset_after_ten_seconds(void)
{
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
    TEST_ASSERT_EQUAL_INT(GESTURE_FACTORY_RESET, run(true, 10000));
}

static void test_factory_reset_fires_once(void)
{
    run(true, 600);
    TEST_ASSERT_EQUAL_INT(GESTURE_FACTORY_RESET, run(true, 10000));
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(true, 5000));
}

// Bounce shorter than the debounce window must not register.
static void test_bounce_is_rejected(void)
{
    now += 10;
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, gesture_update(&g, true, now));
    now += 10;
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, gesture_update(&g, false, now));
    now += 10;
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, gesture_update(&g, true, now));
}

// A pin that reads "pressed" from the very first poll must never produce a
// gesture - least of all the 10 s factory reset. This happened twice on real
// hardware when a diagnostic left the pad configured as an output.
static void test_stuck_pressed_never_fires(void)
{
    gesture_init(&g);                                         // unarmed
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(true, 20000));   // 20 s held from boot
}

static void test_arms_after_first_release(void)
{
    gesture_init(&g);                                         // unarmed
    run(true, 5000);                                          // stuck at boot
    TEST_ASSERT_EQUAL_INT(GESTURE_NONE, run(false, 500));     // released: now armed
    TEST_ASSERT_EQUAL_INT(GESTURE_HOLD_START, run(true, 600));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_stuck_pressed_never_fires);
    RUN_TEST(test_arms_after_first_release);
    RUN_TEST(test_hold_starts_and_ends);
    RUN_TEST(test_short_tap_reports_tap_after_window);
    RUN_TEST(test_double_tap);
    RUN_TEST(test_double_tap_emits_no_single_tap);
    RUN_TEST(test_tap_then_hold_still_starts_hold);
    RUN_TEST(test_hold_after_completed_tap);
    RUN_TEST(test_repeated_holds);
    RUN_TEST(test_factory_reset_after_ten_seconds);
    RUN_TEST(test_factory_reset_fires_once);
    RUN_TEST(test_bounce_is_rejected);
    return UNITY_END();
}
