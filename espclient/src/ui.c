#include "ui.h"
#include "config.h"
#include "nvs_config.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include <stdbool.h>

#define LEDC_TIMER      LEDC_TIMER_0
#define LEDC_CHANNEL    LEDC_CHANNEL_0
#define LEDC_RES        LEDC_TIMER_10_BIT
#define LEDC_MAX        1023

static uint8_t    g_btn;
static gesture_t  g_gesture;
static ui_state_t g_state = UI_IDLE;
static int64_t    g_state_since_ms;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static void led_set(uint16_t duty)
{
#if LED_ACTIVE_LOW
    duty = LEDC_MAX - duty;
#endif
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL);
}

// Triangle wave between off and full, one cycle per period_ms.
static uint16_t pulse(int64_t elapsed_ms, int period_ms)
{
    int phase = (int)(elapsed_ms % period_ms);
    int half = period_ms / 2;
    int up = phase < half ? phase : period_ms - phase;
    return (uint16_t)((long)up * LEDC_MAX / half);
}

static void led_update(void)
{
    int64_t elapsed = now_ms() - g_state_since_ms;

    switch (g_state) {
        case UI_IDLE:       led_set(0); break;
        case UI_RECORDING:  led_set(LEDC_MAX); break;
        case UI_THINKING:   led_set(pulse(elapsed, 1000)); break;   // ~1 Hz
        case UI_SPEAKING:   led_set(pulse(elapsed, 250)); break;    // ~4 Hz
        case UI_NO_NET: {
            int64_t p = elapsed % 2000;                             // double blink
            led_set((p < 100 || (p > 200 && p < 300)) ? LEDC_MAX : 0);
            break;
        }
        case UI_DISCARDED:
            if (elapsed > 600) { ui_set_state(UI_IDLE); break; }    // three flashes
            led_set(((elapsed / 100) % 2 == 0) ? LEDC_MAX : 0);
            break;
    }
}

void ui_init(void)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    g_btn = cfg->gpio.button;
    gesture_init(&g_gesture);

    // GPIO 43/44 are U0TXD/U0RXD. gpio_config alone does not detach the UART
    // from the pad, so the button reads nothing; reset the pin first.
    gpio_reset_pin(g_btn);

    gpio_config_t btn = {
        .pin_bit_mask = 1ULL << g_btn,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&btn);

    // ui_init is re-run after diagnostics reconfigure GPIO; LEDC only needs
    // setting up once and complains if the pin is claimed twice.
    static bool ledc_ready = false;
    if (ledc_ready) {
        g_state_since_ms = now_ms();
        return;
    }

    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER,
        .duty_resolution = LEDC_RES,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t ch = {
        .gpio_num = DEFAULT_LED_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL,
        .timer_sel = LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ledc_channel_config(&ch);

    ledc_ready = true;
    g_state_since_ms = now_ms();
    led_set(0);
}

void ui_set_state(ui_state_t s)
{
    if (g_state == s) return;
    g_state = s;
    g_state_since_ms = now_ms();
}

gesture_event_t ui_poll(void)
{
    led_update();
    bool pressed = (gpio_get_level(g_btn) == 0);          // active low
    return gesture_update(&g_gesture, pressed, now_ms());
}
