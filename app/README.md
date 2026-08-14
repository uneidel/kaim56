# KatAgent (Android)

Kleine Android-App mit **zwei Modi**:

1. **Server-Agent** — chattet mit einem laufenden Firecracker-Agenten über den Manager:
   `POST {Server-URL}/i/{Instanz}/api/chat` (Body `{"message": …}` → `{"reply": …}`), Basic-Auth.
2. **Gerät (Gemma)** — führt ein **Gemma-Modell lokal** auf dem Telefon aus (MediaPipe LLM Inference, offline, funktioniert auch ohne Netz/VPN, z. B. unterwegs).

## Bauen (ohne lokales Android-SDK, via Docker)
```bash
cd /home/ulrich/katagent
docker build -f Dockerfile.build -t katagent-build .          # einmalig (Android-SDK+Gradle)
docker run --rm -v /home/ulrich/katagent:/project \
  -v katagent-gradle:/root/.gradle katagent-build \
  gradle assembleDebug --no-daemon --console=plain
# Ergebnis:
#   app/build/outputs/apk/debug/app-debug.apk
```

## Chat-Sync
Chats liegen im gemeinsamen Store des Managers (`/api/chats`) und werden **live**
abgeglichen: die App haengt an einem Long-Poll (`?since=<rev>&wait=25`), der
Manager antwortet, sobald App oder Web-UI schreibt. Neue Nachrichten der jeweils
anderen Seite stehen damit binnen Sekundenbruchteilen da — ohne Neustart.
Der Manager merged serverseitig pro Chat-`id` (neueres `updatedAt` gewinnt);
Loeschungen synchronisieren nicht (es gibt keine Tombstones).

## Installieren
Debug-APK aufs Handy kopieren und öffnen → „Aus unbekannten Quellen erlauben" → installieren.
(Oder per `adb install app-debug.apk`.)

## Konfiguration (Zahnrad oben rechts)
- **Server-URL**: `https://agents.kat56.de` (nur im Heimnetz/VPN erreichbar).
- **Instanz**: Name einer **laufenden** Instanz im Manager (z. B. eine openrouter-/pi-Web-Instanz). Erst im Manager erstellen & starten.
- **Benutzer/Passwort**: Manager-Basic-Auth (`admin` / …).
- **Gemma-.task-Modell wählen**: eine `.task`-Datei (siehe unten). Wird in den App-Speicher kopiert und geladen.

## On-Device-Modell (Gemma, `.task`)
MediaPipe braucht ein **`.task`-Bundle**. Passende Modelle (auf dem Handy in `Downloads` speichern, dann in den Einstellungen wählen):
- Google AI Edge / LiteRT-Community (HuggingFace `litert-community`) oder Kaggle „Gemma" — z. B.
  `gemma-3n-E2B-it` / `gemma-3n-E4B-it` oder `gemma2-2b-it` als `.task` (CPU/GPU, int4/int8).
- Größe je nach Variante ~1–4 GB. Für das Xiaomi 15 (arm64) passt eine int4-Variante gut.

Hinweis: reine `.gguf`-Modelle funktionieren **nicht** direkt — MediaPipe erwartet das `.task`-Format.

## Architektur
- `MainActivity.kt` — Compose-UI (Chat, Modus-Umschalter, Einstellungen, Modell-Picker via SAF).
- `ServerAgent.kt` — HTTP-Client für den Manager-Agenten.
- `LocalGemma.kt` — MediaPipe-`LlmInference`-Wrapper (laden/generieren).
- `Prefs.kt` — Einstellungen (SharedPreferences).
- `Dockerfile.build` — reproduzierbare Android-Build-Umgebung.

## Bekannte MVP-Grenzen (Ausbaustufen)
- Antworten kommen als Ganzes (kein Token-Streaming) — Streaming ließe sich ergänzen.
- Kein Chat-Verlauf-Persistenz, kein Modell-Download in der App (nur Datei-Auswahl).
- Server-Modus braucht Heimnetz/VPN; On-Device läuft überall offline.
