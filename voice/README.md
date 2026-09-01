# Voice service

Recognition (**Parakeet TDT v3**, ONNX int8) and output (**Piper**, voice
Thorsten) as a single host service. Measured on the i5-10500T: both around
**a factor of 0.07–0.1 of real time** — 6.5 s of audio take about 0.6 s.

## Why on the host and not in the microVMs

Together the models take a good 700 MB. In the guests that would sit in memory
per instance (they have 1–2 GB), and every change would be a rootfs rebuild per
template. On the host a single `docker restart` is enough.

## Why loopback only

The service has no permission management — it should not be reachable from
outside at all. The only door is the manager, which already knows the caller
(basic auth or the VM's source IP) and passes raw audio through unchanged.

**Important:** In the container it binds to `0.0.0.0`; the restriction sits on
the host side of the port mapping (`-p 127.0.0.1:8770:8770`). Docker's
forwarding does *not* reach the container loopback — if you bind to
127.0.0.1 there, the service is dead from outside the container.

## Starting

```bash
docker build -t kaim56-voice .
docker run -d --name kaim56-voice --restart unless-stopped \
  -p 127.0.0.1:8770:8770 \
  -v /home/ulrich/voice-bench/hf-cache:/root/.cache/huggingface \
  kaim56-voice
```

The volume keeps the Parakeet model (640 MB) outside the image; without it the
first start downloads it again.

## Interface

| | | |
|---|---|---|
| `POST /stt` | audio, any format | `{"text","seconds","took"}` |
| `POST /tts` | `{"text": …}` | `audio/wav` |
| `GET /health` | | `{"ready","voice","asr"}` |

Through the manager as `/api/stt` and `/api/tts` — for the web frontend, the app
and (enabled via allowlist) for the agents themselves.

`ffmpeg` in the container converts every input format to 16 kHz mono PCM. Without
this step, recognition fails on anything that isn't WAV — Signal
delivers Opus, Android AAC.
