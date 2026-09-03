// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Sprachclient fuer den Linux-Desktop: Topbar-Icon, VAD, freihaendig reden.
//
// Immer-an-Mikrofon mit Energie-VAD: eine erkannte Aeusserung geht als Audio
// an den Manager (/api/stt), der Text an die gewaehlte Instanz
// (/api/chat/<name>), die Antwort kommt gesprochen zurueck (/api/tts,
// Piper-WAV). Waehrend Denken und Sprechen ist das Mikrofon stumm — sonst
// hoert sich der Client selbst zu. Der Client haelt KEINE Konversation: der
// Agent in der VM traegt seinen eigenen Verlauf; "Neues Gespraech" schickt
// schlicht /reset.
//
// Audio laeuft ueber PipeWire-Werkzeuge als Subprozess (parec/pw-record/
// arecord bzw. paplay/pw-play/aplay — das erste, das da ist); das Topbar-Icon
// ueber StatusNotifierItem in purem Go. Ein einziges statisches Binary, kein
// cgo, keine Python-Umgebung.
package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	cfgPath := flag.String("config", configPath(), "Pfad zur Config-Datei")
	instance := flag.String("instance", "", "Zielinstanz (statt Config-Wert)")
	headless := flag.Bool("headless", false, "ohne Topbar-Icon, Status auf stdout")
	once := flag.Bool("once", false, "eine Aeusserung verarbeiten, dann beenden (Test)")
	probe := flag.String("probe", "", "Selbsttest ohne Mikrofon: Text per TTS erzeugen, durch STT zurueck, an die Instanz schicken, Antwort sprechen")
	enroll := flag.Bool("enroll", false, "lokales Wake-Word einsprechen (3 Aufnahmen) und Modell speichern")
	wakeTest := flag.Bool("wake-test", false, "Wake-Modell testen: Scores je Aeusserung anzeigen, nichts wird gesendet")
	flag.Parse()

	if _, err := os.Stat(*cfgPath); err != nil {
		if werr := writeConfigTemplate(*cfgPath); werr != nil {
			fmt.Fprintf(os.Stderr, "Config-Vorlage schreiben: %v\n", werr)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "Config-Vorlage nach %s geschrieben — bitte "+
			"base_url/user/pass eintragen und neu starten.\n", *cfgPath)
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

	// Enrollment und Wake-Test brauchen weder Manager noch Tunnel.
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

	// iroh-Transport: Tunnel als Kindprozess, base_url zeigt auf dessen
	// lokalen Port. Pdeathsig raeumt ihn auch bei hartem Exit mit ab.
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
			fmt.Fprintf(os.Stderr, "wake_mode \"local\", aber kein Modell: %v\n"+
				"Einmal einsprechen mit: kaim56-voice --enroll\n", err)
			os.Exit(1)
		}
		if cfg.WakeThresh > 0 {
			model.Threshold = cfg.WakeThresh
		}
		client.wakeModel = model
		fmt.Fprintf(os.Stderr, "[kaim56-voice] lokales Wake-Gate aktiv "+
			"(%d Templates, Schwelle %.2f) — Audio geht erst nach dem Wort zum Server\n",
			len(model.Templates), model.Threshold)
	}

	if *probe != "" {
		if err := runProbe(client, *probe); err != nil {
			fmt.Fprintln(os.Stderr, "Probe fehlgeschlagen:", err)
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

	// Topbar-Modus: Audioschleife im Hintergrund, Tray blockiert bis Beenden.
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := client.Run(false); err != nil {
			client.notify("Audioschleife beendet", err.Error())
		}
	}()
	runTray(client, done)
}

// runEnroll: das Wake-Word 3x einsprechen, Modell bauen, speichern.
func runEnroll(cfg Config) error {
	const takesN = 3
	var takes [][]byte
	fmt.Println("Wake-Word einsprechen — dreimal, mit kurzer Pause dazwischen.")
	err := captureSegments(cfg.Vad, takesN,
		func(i int) { fmt.Printf("Aufnahme %d/%d: jetzt sprechen …\n", i+1, takesN) },
		func(i int, pcm []byte) {
			fmt.Printf("  aufgenommen (%.1f s)\n", float64(len(pcm))/2/sampleRate)
			takes = append(takes, pcm)
		})
	if err != nil {
		return err
	}
	model, err := buildWakeModel(takes)
	if err != nil {
		return fmt.Errorf("%w — bitte --enroll wiederholen", err)
	}
	if err := model.save(wakeModelPath()); err != nil {
		return err
	}
	fmt.Printf("Modell gespeichert: %s (Schwelle %.2f)\n", wakeModelPath(), model.Threshold)
	fmt.Println("Aktivieren mit \"wake_mode\": \"local\" in der Config; pruefen mit --wake-test.")
	return nil
}

// runWakeTest: Aeusserungen aufnehmen und nur die Scores zeigen — nichts
// verlaesst den Rechner. Zum Kalibrieren der Schwelle.
func runWakeTest(cfg Config) error {
	model, err := loadWakeModel(wakeModelPath())
	if err != nil {
		return err
	}
	if cfg.WakeThresh > 0 {
		model.Threshold = cfg.WakeThresh
	}
	fmt.Printf("Wake-Test (Schwelle %.2f) — sprich Wake-Word und Gegenbeispiele; Ctrl-C beendet.\n",
		model.Threshold)
	return captureSegments(cfg.Vad, 1<<30,
		func(i int) {},
		func(i int, pcm []byte) {
			score, cut, hit := model.Match(pcm)
			mark := "✕"
			if hit {
				mark = fmt.Sprintf("✓ WAKE (Wort endet bei %.2f s)",
					float64(cut)/2/sampleRate)
			}
			fmt.Printf("  Score %.3f  %s\n", score, mark)
		})
}

// runProbe beweist die Kette ohne Mikrofon: Text -> TTS -> STT -> Chat ->
// TTS -> Wiedergabe. Praktisch als Installationstest auf einem neuen Rechner.
func runProbe(c *VoiceClient, text string) error {
	fmt.Println("probe: TTS erzeugt:", text)
	wav, err := c.mgr.TTS(text)
	if err != nil {
		return fmt.Errorf("tts: %w", err)
	}
	heard, err := c.mgr.STT(wav)
	if err != nil {
		return fmt.Errorf("stt: %w", err)
	}
	fmt.Println("probe: STT verstand:", heard)
	_, inst, _, _ := c.State()
	reply, err := c.mgr.Chat(inst, heard, "voice-probe")
	if err != nil {
		return fmt.Errorf("chat: %w", err)
	}
	say := speakable(reply)
	fmt.Printf("probe: %s antwortet: %s\n", inst, say)
	if err := c.Speak(say); err != nil {
		fmt.Fprintln(os.Stderr, "probe: Wiedergabe uebersprungen:", err)
	}
	return nil
}
