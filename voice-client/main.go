// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Voice client for the Linux desktop: top-bar icon, VAD, hands-free talking.
//
// Always-on microphone with an energy VAD: a detected utterance goes as audio
// to the manager (/api/stt), the text to the chosen instance
// (/api/chat/<name>), and the reply comes back spoken (/api/tts, Piper WAV).
// While thinking and speaking the microphone is muted — otherwise the client
// listens to itself. The client keeps NO conversation: the agent in the VM
// carries its own history; "New conversation" simply sends /reset.
//
// Audio runs through PipeWire tools as subprocesses (parec/pw-record/arecord
// and paplay/pw-play/aplay — the first one available); the top-bar icon
// through StatusNotifierItem in pure Go. One static binary, no cgo, no
// Python environment.
package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	cfgPath := flag.String("config", configPath(), "path to the config file")
	instance := flag.String("instance", "", "target instance (overrides the config value)")
	prompt := flag.String("prompt", "", "custom prompt prepended to every spoken message (overrides the config value; \"-\" = none)")
	headless := flag.Bool("headless", false, "no top-bar icon, status on stdout")
	once := flag.Bool("once", false, "process one utterance, then exit (test)")
	probe := flag.String("probe", "", "self-test without a microphone: synthesize the text via TTS, run it back through STT, send it to the instance, speak the reply")
	enroll := flag.Bool("enroll", false, "record the local wake word (3 takes) and save the model")
	wakeTest := flag.Bool("wake-test", false, "test the wake model: show the score per utterance, nothing is sent")
	flag.Parse()

	if _, err := os.Stat(*cfgPath); err != nil {
		if werr := writeConfigTemplate(*cfgPath); werr != nil {
			fmt.Fprintf(os.Stderr, "writing the config template: %v\n", werr)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "Config template written to %s — please fill in "+
			"base_url/user/pass and start again.\n", *cfgPath)
		os.Exit(2)
	}
	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if *instance != "" {
		cfg.Instance = *instance
	}
	if *prompt == "-" {
		cfg.Prompt = ""
	} else if *prompt != "" {
		cfg.Prompt = *prompt
	}

	// Enrollment and the wake test need neither the manager nor the tunnel.
	if *enroll {
		if err := runEnroll(cfg); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *wakeTest {
		if err := runWakeTest(cfg); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	// iroh transport: the tunnel runs as a child process, base_url points at
	// its local port. Pdeathsig takes it down even on a hard exit.
	if cfg.Iroh != "" {
		base, stop, err := startTunnel(cfg.Iroh, cfg.IrohListen)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer stop()
		cfg.BaseURL = base
	}

	client := NewVoiceClient(cfg, *headless || *once || *probe != "")
	if cfg.WakeMode == "local" {
		model, err := loadWakeModel(wakeModelPath())
		if err != nil {
			fmt.Fprintf(os.Stderr, "wake_mode \"local\", but no model: %v\n"+
				"Record it once with: kaim56-voice --enroll\n", err)
			os.Exit(1)
		}
		if cfg.WakeThresh > 0 {
			model.Threshold = cfg.WakeThresh
		}
		client.wakeModel = model
		fmt.Fprintf(os.Stderr, "[kaim56-voice] local wake gate active "+
			"(%d templates, threshold %.2f) — audio reaches the server only after the word\n",
			len(model.Templates), model.Threshold)
	}

	if *probe != "" {
		if err := runProbe(client, *probe); err != nil {
			fmt.Fprintln(os.Stderr, "probe failed:", err)
			os.Exit(1)
		}
		return
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() { <-sig; client.Quit(); os.Exit(0) }()

	if *headless || *once {
		if err := client.Run(*once); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	// Top-bar mode: the audio loop runs in the background, the tray blocks until Quit.
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := client.Run(false); err != nil {
			client.notify("Audio loop ended", err.Error())
		}
	}()
	runTray(client, done)
}

// runEnroll: record the wake word 3 times, build the model, save it.
func runEnroll(cfg Config) error {
	const takesN = 3
	var takes [][]byte
	fmt.Println("Say the wake word — three times, with a short pause in between.")
	err := captureSegments(cfg.Vad, takesN,
		func(i int) { fmt.Printf("Take %d/%d: speak now …\n", i+1, takesN) },
		func(i int, pcm []byte) {
			fmt.Printf("  recorded (%.1f s)\n", float64(len(pcm))/2/sampleRate)
			takes = append(takes, pcm)
		})
	if err != nil {
		return err
	}
	model, err := buildWakeModel(takes)
	if err != nil {
		return fmt.Errorf("%w — please repeat --enroll", err)
	}
	if err := model.save(wakeModelPath()); err != nil {
		return err
	}
	fmt.Printf("Model saved: %s (threshold %.2f)\n", wakeModelPath(), model.Threshold)
	fmt.Println("Enable it with \"wake_mode\": \"local\" in the config; check it with --wake-test.")
	return nil
}

// runWakeTest: record utterances and only show the scores — nothing leaves
// the machine. For calibrating the threshold.
func runWakeTest(cfg Config) error {
	model, err := loadWakeModel(wakeModelPath())
	if err != nil {
		return err
	}
	if cfg.WakeThresh > 0 {
		model.Threshold = cfg.WakeThresh
	}
	fmt.Printf("Wake test (threshold %.2f) — say the wake word and counter-examples; Ctrl-C quits.\n",
		model.Threshold)
	return captureSegments(cfg.Vad, 1<<30,
		func(i int) {},
		func(i int, pcm []byte) {
			score, cut, hit := model.Match(pcm)
			mark := "✕"
			if hit {
				mark = fmt.Sprintf("✓ WAKE (word ends at %.2f s)",
					float64(cut)/2/sampleRate)
			}
			fmt.Printf("  score %.3f  %s\n", score, mark)
		})
}

// runProbe proves the chain without a microphone: text -> TTS -> STT -> chat ->
// TTS -> playback. Handy as an installation test on a new machine.
func runProbe(c *VoiceClient, text string) error {
	fmt.Println("probe: TTS synthesized:", text)
	wav, err := c.mgr.TTS(text)
	if err != nil {
		return fmt.Errorf("tts: %w", err)
	}
	heard, err := c.mgr.STT(wav)
	if err != nil {
		return fmt.Errorf("stt: %w", err)
	}
	fmt.Println("probe: STT understood:", heard)
	_, inst, _, _ := c.State()
	reply, err := c.mgr.Chat(inst, withPrompt(c.prompt, heard), "voice-probe")
	if err != nil {
		return fmt.Errorf("chat: %w", err)
	}
	say := speakable(reply)
	fmt.Printf("probe: %s replies: %s\n", inst, say)
	if err := c.Speak(say); err != nil {
		fmt.Fprintln(os.Stderr, "probe: playback skipped:", err)
	}
	return nil
}
