# kAIm56 voice client (Linux desktop, Go)

Talk hands-free to a platform instance — `myassistant` by default, switchable
in the menu. An icon in the top bar shows the state (green listening / amber
thinking / blue speaking / grey off); an energy VAD detects utterances on its
own, there is no push-to-talk.

The client is deliberately dumb: recording → `/api/stt` → `/api/chat/<instance>` →
`/api/tts` → playback. STT, LLM and Piper TTS all run on the server; the
history lives in the agent itself, "New conversation" simply sends `/reset`.

One static binary (pure Go, no cgo, no Python environment). Audio runs through
PipeWire tools as subprocesses (parec/pw-record/arecord and paplay/pw-play/aplay
— the first one available; with PipeWire they are already on board).

## Building

```bash
./build.sh          # release: ONE binary, kaim56-tunnel embedded
                    # (builds the tunnel first if needed, Docker)
go build .          # dev build without the embedded tunnel — looks for it
                    # next to the binary or in the PATH
```

## Top-bar icon (StatusNotifier)

KDE does it natively. GNOME needs the AppIndicator extension:

```bash
sudo dnf install gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com   # then log out and in
```

## Setup (iroh — the normal route)

Like the app, the client reaches the manager over iroh (P2P, end-to-end
encrypted, no open HTTPS port, no VPN). The tunnel is inside the binary: at
start it is unpacked to `~/.cache/kaim56-voice/` and run as a child process —
copying one file is enough.

```bash
./kaim56-voice                      # the first run writes ~/.config/kaim56-voice.json
$EDITOR ~/.config/kaim56-voice.json # set "iroh": "<manager NodeId>"
                                    # (shown in the web UI, iroh tab)
./kaim56-voice --probe "What time is it?"
```

The first start prints its own iroh identity — add it once to the allowlist in
the web UI (iroh tab), like for the phone. The key stays put in
`~/.config/kaim56-tunnel.key`. After that:

```bash
./kaim56-voice                      # icon in the top bar; the tunnel runs as a
                                    # child process and dies with the client
```

With `"iroh"` set, `base_url`, `user` and `pass` are unnecessary
(`iroh_listen` changes the local port if needed, default 127.0.0.1:8701).

## Alternative: direct HTTP(S)

Without `"iroh"`, `base_url` + `user`/`pass` apply — e.g. `http://<manager>:8700`
in the LAN or the public HTTPS address with the web UI credentials. The
template placeholder `manager.example` is rejected at start, not at the first
sentence.

Autostart: copy `kaim56-voice.desktop` to `~/.config/autostart/` and adjust
the path to the binary in it.

## Usage

Everything hangs off the top-bar menu: **Listening on/off**, **Stop speaking**,
**New conversation** (`/reset` to the agent), **Instance** (the list comes live
from the manager). While the client thinks or speaks, the microphone is muted —
otherwise it listens to itself.

Without a top bar (SSH, testing): `--headless` logs states and transcripts to
stdout (dropped utterances too, as `(ignored: …)`), `--once` processes exactly
one utterance and exits, `--instance <name>` overrides the config.

## Custom prompt

`"prompt": "…"` in the config is prepended to every spoken message (marked as
`[Voice client] …`, the agent sees instruction and sentence separately). The
template default: answer briefly and readably, without lists/links.
`--prompt "…"` overrides the config for one run, `--prompt -` switches it off.
Empty = the message goes out unchanged.

## Hotword — two stages

**Stage 1, text gate** (default): `"wake_word": "Kati, Katharina"` — a comma
list of variants; empty = every utterance passes. Only transcripts that start
with a variant reach the agent. Per variant of 4+ letters one typo is allowed
(Levenshtein 1; not at 3 — otherwise "hat" wakes the word "Kat"), the word
boundary stays mandatory. Measure the choice of word against STT, not by
feel: Parakeet garbles short words ("Kat" → "Tat", "Kaim" → "Kein"); tested
robust are **Kati, Katharina, Computer**. The audio goes to your own server
for transcription; what is dropped is forwarded nowhere.

**Stage 2, local model** (`"wake_mode": "local"`): audio leaves the desktop
only AFTER the word — the right stage for conference calls. No pre-trained
network, but your own voice as the reference (MFCC templates + subsequence
DTW, pure Go):

```bash
./kaim56-voice --enroll      # say the wake word three times -> model
./kaim56-voice --wake-test   # watch the scores live, nothing is sent
# then in the config: "wake_mode": "local"
```

On a hit the word is cut out of the audio (the DTW knows where the alignment
ends) and only the message after it goes to STT — so the word can be
anything, "Kaim" included; STT never sees it. Speaker-dependent: other voices
in the call match poorly, exactly right. The threshold comes from the
enrollment; `"wake_threshold"` overrides it (smaller = stricter), `--wake-test`
shows where your own sentences land. Similar-sounding words ("Kein …" vs.
"Kaim") remain the limit of the method — when in doubt, enroll a more
distinctive word. The word alone is acknowledged with a spoken "Yes?"; the
model lives in `~/.config/kaim56-voice-wake.json`.

Dropped utterances are visible: the top-bar status shows ✕ (with the score or
the transcript), `--headless` prints them as `(ignored/dropped locally: …)`.

## The tunnel by hand (curl, browser, other clients)

`kaim56-tunnel` is not tied to the voice client — started by hand it puts the
manager on a local port for ANY HTTP client:

```bash
./kaim56-tunnel --id                 # show the NodeId -> allowlist
./kaim56-tunnel <manager-node-id> &  # listens on 127.0.0.1:8701
curl http://127.0.0.1:8701/api/instances
```

## Tuning the VAD

In the config under `vad`:

| Key                | Default | Meaning                                       |
|--------------------|---------|-----------------------------------------------|
| `start_frames`     | 5       | voiced 30 ms frames (out of 8) until start    |
| `end_ms`           | 800     | silence that ends an utterance                |
| `min_ms`           | 400     | shorter segments are dropped                  |
| `max_s`            | 30      | forced cut during continuous speech           |
| `threshold_factor` | 3.0     | threshold = noise floor × factor              |
| `threshold_min`    | 350     | lower bound of the threshold (RMS, s16)       |

Too sensitive (reacts to the keyboard): raise `threshold_factor` or
`threshold_min`. Cuts off sentence ends: raise `end_ms`.

## Tests

```bash
go vet ./... && go test ./...   # VAD on synthetic PCM, WAV header,
                                # read-aloud filter, config — no microphone/manager
```
