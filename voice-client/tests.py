#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit-Tests fuer den Sprachclient — alles, was ohne Mikrofon und Manager
testbar ist: VAD-Segmentierung an synthetischem PCM, WAV-Header, Vorlese-
Filter, Config-Handling."""
import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kaim56_voice as vc  # noqa: E402


def tone(ms, amp=8000, freq=440):
    n = vc.RATE * ms // 1000
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / vc.RATE)))
                    for i in range(n))


def silence(ms, amp=50):
    n = vc.RATE * ms // 1000
    return b"".join(struct.pack("<h", (amp if i % 2 else -amp)) for i in range(n))


def feed_all(vad, pcm):
    segs = []
    for i in range(0, len(pcm) - vc.FRAME_BYTES + 1, vc.FRAME_BYTES):
        seg = vad.feed(pcm[i:i + vc.FRAME_BYTES])
        if seg:
            segs.append(seg)
    return segs


class VadTest(unittest.TestCase):
    def test_utterance_is_segmented_with_preroll(self):
        vad = vc.Vad(end_ms=600, min_ms=300)
        pcm = silence(1500) + tone(900) + silence(1200)
        segs = feed_all(vad, pcm)
        self.assertEqual(len(segs), 1)
        # Segment enthaelt mindestens die Sprachdauer, plus Vorlauf/Nachlauf.
        self.assertGreaterEqual(len(segs[0]), 900 * vc.RATE * 2 // 1000)

    def test_short_burst_is_dropped(self):
        vad = vc.Vad(end_ms=600, min_ms=400)
        pcm = silence(1500) + tone(200) + silence(1200)     # Tuerknallen
        self.assertEqual(feed_all(vad, pcm), [])

    def test_silence_alone_never_triggers(self):
        vad = vc.Vad()
        self.assertEqual(feed_all(vad, silence(4000)), [])
        self.assertIsNone(vad.talk)

    def test_two_utterances_two_segments(self):
        vad = vc.Vad(end_ms=600, min_ms=300)
        pcm = (silence(1200) + tone(800) + silence(1200)
               + tone(800) + silence(1200))
        self.assertEqual(len(feed_all(vad, pcm)), 2)

    def test_speech_cannot_raise_threshold_above_itself(self):
        # Der Teppich darf aus Sprache nicht lernen: nach einem langen Satz
        # muss derselbe Pegel immer noch klar als stimmhaft gelten.
        vad = vc.Vad(end_ms=600, min_ms=300)
        feed_all(vad, silence(900) + tone(3000))
        self.assertLess(vad.threshold, vc.frame_rms(tone(30)) * 0.5)

    def test_max_length_chunks_continuous_speech(self):
        # Dauersprechen wird in Max-Laengen-Stuecke zerteilt, keins darueber.
        vad = vc.Vad(end_ms=600, min_ms=300, max_s=2)
        segs = feed_all(vad, silence(900) + tone(4000))
        self.assertEqual(len(segs), 2)
        for s in segs:
            self.assertLessEqual(len(s), (2000 // vc.FRAME_MS + 1) * vc.FRAME_BYTES)


class WavTest(unittest.TestCase):
    def test_header_fields(self):
        pcm = tone(100)
        wav = vc.wav_wrap(pcm)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(struct.unpack("<I", wav[24:28])[0], vc.RATE)
        self.assertEqual(struct.unpack("<I", wav[40:44])[0], len(pcm))
        self.assertEqual(wav[44:], pcm)


class SpeakableTest(unittest.TestCase):
    def test_think_blocks_are_stripped(self):
        s = "⟦think⟧inneres Gemurmel⟦/think⟧Hallo **Ulrich**!"
        self.assertEqual(vc.speakable(s), "Hallo Ulrich!")

    def test_open_think_block_is_stripped(self):
        self.assertEqual(vc.speakable("Antwort.⟦think⟧noch offen"), "Antwort.")

    def test_tool_status_lines_are_dropped(self):
        s = "🔧 listagents …\nEs laufen 5 Instanzen."
        self.assertEqual(vc.speakable(s), "Es laufen 5 Instanzen.")

    def test_code_and_links(self):
        s = "Nimm [die Doku](https://x.example/a) und:\n```py\nprint(1)\n```\nfertig"
        out = vc.speakable(s)
        self.assertIn("die Doku", out)
        self.assertNotIn("https://", out)
        self.assertNotIn("print(1)", out)
        self.assertIn("Codeblock übersprungen", out)


class ConfigTest(unittest.TestCase):
    def test_template_roundtrip_and_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cfg.json")
            vc.write_config_template(p)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            cfg = vc.load_config(p)
            self.assertEqual(cfg["instance"], "myassistant")
            self.assertEqual(cfg["vad"]["start_frames"], 5)

    def test_missing_credentials_fail_loud(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cfg.json")
            with open(p, "w") as fh:
                json.dump({"base_url": "http://x"}, fh)
            with self.assertRaises(ValueError):
                vc.load_config(p)

    def test_partial_vad_config_is_filled(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cfg.json")
            with open(p, "w") as fh:
                json.dump({"base_url": "http://x", "user": "u", "pass": "p",
                           "vad": {"end_ms": 500}}, fh)
            cfg = vc.load_config(p)
            self.assertEqual(cfg["vad"]["end_ms"], 500)
            self.assertEqual(cfg["vad"]["min_ms"], 400)
            vc.Vad(**cfg["vad"])            # Namen passen zum Konstruktor


if __name__ == "__main__":
    unittest.main(verbosity=2)
