#pragma once

// ============================================================================
// DEFAULT CONFIGURATION VALUES
// Fallbacks used when NVS holds no stored configuration.
// Runtime values live in NVS and are read via nvs_config_get().
// ============================================================================

// Optionale lokale Zugangsdaten. src/config_local.h steht in .gitignore und
// wird nie eingecheckt. Existiert die Datei, gewinnen ihre Werte; sonst
// bleiben alle Vorgaben leer und werden zur Laufzeit gesetzt:
//   wifi <ssid> <passwort>
//   mgr  <url> <instanz> [benutzer] [passwort]
#if defined(__has_include)
#  if __has_include("config_local.h")
#    include "config_local.h"
#  endif
#endif

// WiFi defaults (used only if NVS is not configured)
#ifndef DEFAULT_WIFI_SSID
#define DEFAULT_WIFI_SSID      ""
#endif
#ifndef DEFAULT_WIFI_PASS
#define DEFAULT_WIFI_PASS      ""
#endif

// kAIm56 manager defaults
#ifndef DEFAULT_BASE_URL
#define DEFAULT_BASE_URL   ""
#endif
#ifndef DEFAULT_MGR_USER
#define DEFAULT_MGR_USER   ""
#endif
#ifndef DEFAULT_MGR_PASS
#define DEFAULT_MGR_PASS   ""
#endif
#ifndef DEFAULT_INSTANCE
#define DEFAULT_INSTANCE   ""
#endif
#define DEFAULT_PROMPT     "Du wirst über einen Sprachclient bedient: antworte kurz und in vorlesbarer Prosa, ohne Listen, Links oder Code."

// Recording guards (the VAD remnants — see spec D1)
#define DEFAULT_MIN_MS     400    // shorter recordings are discarded
#define DEFAULT_MAX_S      30     // recording cap

// Playback volume in percent. Piper delivers audio at 100 % full scale, which
// clips audibly through the MAX98357A, so attenuate by default.
#define DEFAULT_SPK_VOLUME 25

// Capture gain in percent (100 = unity). The INMP441 on this board peaks at
// only ~500-2000 of 32767 on loud speech, which STT returns as empty, so
// amplify before upload.
#define DEFAULT_MIC_GAIN   800

// ---------------------------------------------------------------------------
// ALTERNATIVE BELEGUNG (ESP32-S3 SuperMini, siehe Readme.md)
// Noch nicht ausgeschlossen. Das Mikrofon liefert auf der aktiven Belegung
// kein Audio; die Mic-Pins 4/5/6 von unten wurden getestet (kein Signal),
// die DAC-Pins 7/15/16 nicht (auf dem XIAO nicht herausgeführt).
// Zum Umschalten die Blöcke tauschen.
//
//   #define DEFAULT_MIC_SCK   4
//   #define DEFAULT_MIC_WS    5
//   #define DEFAULT_MIC_SD    6
//   #define DEFAULT_DAC_BCLK  7
//   #define DEFAULT_DAC_LRC   15
//   #define DEFAULT_DAC_DIN   16
//   #define DEFAULT_DAC_SD    17
//   #define DEFAULT_BUTTON_GPIO 9
// ---------------------------------------------------------------------------

// Mic I2S (INMP441) - SOLL-Verdrahtung, so anschliessen:
//   SD  (Daten)  = D7  = GPIO 44
//   SCK (Bittakt)= D8  = GPIO 7
//   WS  (Wortsel)= D9  = GPIO 8
//   L/R          -> direkt auf GND, NICHT auf einen GPIO
//   VDD          -> 3V3      GND -> GND
// L/R auf GND ist die Standardbeschaltung fuer den linken Kanal und spart den
// Strap-Ausgang, der vorher mehrfach mit der Pinsuche kollidiert ist.
#define DEFAULT_MIC_SCK        7
#define DEFAULT_MIC_WS         8
#define DEFAULT_MIC_SD         44
#define DEFAULT_MIC_LR         (-1)   // -1 = fest auf GND verdrahtet

// DAC I2S (MAX98357A) - GEMESSEN 2026-09-07, Ton bestaetigt
//   SD   = D2 = GPIO 3     DIN  = D3 = GPIO 4
//   BCLK = D4 = GPIO 5     LRC  = D5 = GPIO 6
// SD muss HIGH sein: der MAX98357A hat dort einen internen Pulldown und
// bleibt sonst stumm, egal was I2S sendet.
#define DEFAULT_DAC_BCLK       5
#define DEFAULT_DAC_LRC        6
#define DEFAULT_DAC_DIN        4
// Gemessen 2026-09-07 mit btnscan: der Taster haengt auf GPIO 43 (D6).
// board_init trieb diesen Pin vorher als DAC-Shutdown aktiv auf HIGH und
// kaempfte damit gegen den Taster. DAC-SD auf 44 verschoben; beim MAX98357A
// darf SD auch fest auf 3V3 liegen, dann ist der Pin schlicht unbenutzt.
#define DEFAULT_DAC_SD         3

// Button - gemessen: D6 on XIAO ESP32-S3 = GPIO 43
#define DEFAULT_BUTTON_GPIO    43

// XIAO ESP32-S3 user LED — VERIFY against your board before trusting this.
#define DEFAULT_LED_GPIO       21
#define LED_ACTIVE_LOW         1

// GPIO aliases used by audio.c
#define MIC_SCK        DEFAULT_MIC_SCK
#define MIC_WS         DEFAULT_MIC_WS
#define MIC_SD         DEFAULT_MIC_SD
#define DAC_BCLK       DEFAULT_DAC_BCLK
#define DAC_LRC        DEFAULT_DAC_LRC
#define DAC_DIN        DEFAULT_DAC_DIN
#define DAC_SD         DEFAULT_DAC_SD
#define MIC_LR         DEFAULT_MIC_LR
#define BUTTON_GPIO    DEFAULT_BUTTON_GPIO
