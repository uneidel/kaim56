# Sprachdienst

Erkennung (**Parakeet TDT v3**, ONNX int8) und Ausgabe (**Piper**, Stimme
Thorsten) als ein Host-Dienst. Auf dem i5-10500T gemessen: beides rund
**Faktor 0,07–0,1 der Echtzeit** — 6,5 s Audio brauchen etwa 0,6 s.

## Warum auf dem Host und nicht in den microVMs

Die Modelle belegen zusammen gut 700 MB. In den Gästen läge das pro Instanz im
Speicher (die haben 1–2 GB), und jede Änderung wäre ein Rootfs-Neubau je
Vorlage. Auf dem Host reicht ein `docker restart`.

## Warum nur Loopback

Der Dienst kennt keine Rechteverwaltung — er soll von außen gar nicht erreichbar
sein. Die einzige Tür ist der Manager, der den Anrufer schon kennt (Basic-Auth
bzw. Quell-IP der VM) und Roh-Audio unverändert durchreicht.

**Wichtig:** Im Container wird an `0.0.0.0` gebunden, die Beschränkung sitzt auf
der Host-Seite der Portzuordnung (`-p 127.0.0.1:8770:8770`). Dockers
Weiterleitung erreicht das Container-Loopback *nicht* — bindet man dort auf
127.0.0.1, ist der Dienst von außerhalb des Containers tot.

## Starten

```bash
docker build -t kaim56-voice .
docker run -d --name kaim56-voice --restart unless-stopped \
  -p 127.0.0.1:8770:8770 \
  -v /home/ulrich/voice-bench/hf-cache:/root/.cache/huggingface \
  kaim56-voice
```

Das Volume hält das Parakeet-Modell (640 MB) außerhalb des Images; ohne es lädt
der erste Start es neu herunter.

## Schnittstelle

| | | |
|---|---|---|
| `POST /stt` | Audio, beliebiges Format | `{"text","seconds","took"}` |
| `POST /tts` | `{"text": …}` | `audio/wav` |
| `GET /health` | | `{"ready","voice","asr"}` |

Über den Manager als `/api/stt` und `/api/tts` — für die Weboberfläche, die App
und (per Positivliste freigeschaltet) für die Agenten selbst.

`ffmpeg` im Container wandelt jedes Eingangsformat auf 16-kHz-Mono-PCM. Ohne
diesen Schritt scheitert die Erkennung an allem, was kein WAV ist — Signal
liefert Opus, Android AAC.
