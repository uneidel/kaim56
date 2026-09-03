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
