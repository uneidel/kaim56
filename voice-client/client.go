// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"
)

// ---- Config ----------------------------------------------------------------
// Dieselbe Datei wie die fruehere Python-Fassung: ~/.config/kaim56-voice.json.
type Config struct {
	BaseURL  string    `json:"base_url"`
	User     string    `json:"user"`
	Pass     string    `json:"pass"`
	Instance string    `json:"instance"`
	WakeWord string    `json:"wake_word"` // leer = jede Aeusserung geht durch
	Vad      VadConfig `json:"vad"`
}

func configPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "kaim56-voice.json")
}

// loadConfig liest die Config; fehlende VAD-Schluessel behalten die Defaults
// (die Struktur ist mit ihnen vorbelegt, Unmarshal ueberschreibt nur, was in
// der Datei steht).
func loadConfig(path string) (Config, error) {
	cfg := Config{Instance: "myassistant", Vad: defaultVadConfig()}
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, fmt.Errorf("config %s: %w", path, err)
	}
	for k, v := range map[string]string{"base_url": cfg.BaseURL,
		"user": cfg.User, "pass": cfg.Pass} {
		if v == "" {
			return cfg, fmt.Errorf("config: '%s' fehlt in %s", k, path)
		}
	}
	if cfg.Instance == "" {
		cfg.Instance = "myassistant"
	}
	return cfg, nil
}

func writeConfigTemplate(path string) error {
	tpl := Config{BaseURL: "http://manager.example:8700", User: "admin",
		Pass: "geheim", Instance: "myassistant", WakeWord: "Kat",
		Vad: defaultVadConfig()}
	b, _ := json.MarshalIndent(tpl, "", "  ")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o600) // da steht ein Passwort drin
}

// ---- Audio-Subprozesse -----------------------------------------------------
var recorders = [][]string{
	{"parec", "--rate=16000", "--channels=1", "--format=s16le", "--latency-msec=30"},
	{"pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"},
	{"arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"},
}
var players = [][]string{{"paplay"}, {"pw-play"}, {"aplay", "-q"}}

func pickCmd(candidates [][]string) []string {
	for _, c := range candidates {
		if _, err := exec.LookPath(c[0]); err == nil {
			return c
		}
	}
	return nil
}

// ---- Der Client ------------------------------------------------------------
// Zustaende: aus | hört | denkt | spricht. Der Audioleser laeuft immer;
// ausserhalb von "hört" werden Frames nur verworfen (kein Nachlauf alter
// Sprache, und der Client hoert sich beim Sprechen nicht selbst zu).
type VoiceClient struct {
	mu        sync.Mutex
	mgr       *Manager
	vad       *Vad
	wakeWord  string
	instance  string
	chatID    string
	listening bool
	state     string
	lastHeard string
	headless  bool
	stopped   chan struct{}
	stopOnce  sync.Once
	playCmd   *exec.Cmd
	OnState   func() // Tray haengt sich hier ein
}

func NewVoiceClient(cfg Config, headless bool) *VoiceClient {
	return &VoiceClient{
		mgr:       NewManager(cfg.BaseURL, cfg.User, cfg.Pass),
		vad:       NewVad(cfg.Vad),
		wakeWord:  cfg.WakeWord,
		instance:  cfg.Instance,
		chatID:    fmt.Sprintf("voice-%d", time.Now().Unix()),
		listening: true,
		state:     "hört",
		headless:  headless,
		stopped:   make(chan struct{}),
		OnState:   func() {},
	}
}

func (c *VoiceClient) setState(s string) {
	c.mu.Lock()
	c.state = s
	c.mu.Unlock()
	if c.headless {
		fmt.Printf("[%s] %s\n", time.Now().Format("15:04:05"), s)
	}
	c.OnState()
}

func (c *VoiceClient) State() (state, instance, lastHeard string, listening bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.state, c.instance, c.lastHeard, c.listening
}

func (c *VoiceClient) notify(title, msg string) {
	if _, err := exec.LookPath("notify-send"); err == nil {
		exec.Command("notify-send", "-a", "kAIm56", title, msg).Run()
	}
	fmt.Fprintf(os.Stderr, "[kaim56-voice] %s: %s\n", title, msg)
}

// Run ist die Hauptschleife: Frames lesen, VAD fuettern, Segmente verarbeiten.
func (c *VoiceClient) Run(once bool) error {
	cmd := pickCmd(recorders)
	if cmd == nil {
		return fmt.Errorf("kein Aufnahmewerkzeug: parec, pw-record oder arecord installieren")
	}
	rec := exec.Command(cmd[0], cmd[1:]...)
	out, err := rec.StdoutPipe()
	if err != nil {
		return err
	}
	if err := rec.Start(); err != nil {
		return fmt.Errorf("%s: %w", cmd[0], err)
	}
	defer func() { rec.Process.Kill(); rec.Wait() }()

	frame := make([]byte, frameBytes)
	for {
		select {
		case <-c.stopped:
			return nil
		default:
		}
		if _, err := io.ReadFull(out, frame); err != nil {
			select {
			case <-c.stopped:
				return nil
			default:
				return fmt.Errorf("Aufnahme abgerissen (%s): %w", cmd[0], err)
			}
		}
		c.mu.Lock()
		active := c.listening && c.state == "hört"
		c.mu.Unlock()
		if !active {
			c.vad.Reset()
			continue
		}
		seg := c.vad.Feed(frame)
		if seg == nil {
			continue
		}
		c.handleUtterance(seg)
		if once {
			return nil
		}
	}
}

func (c *VoiceClient) handleUtterance(pcm []byte) {
	c.setState("denkt")
	defer func() {
		c.mu.Lock()
		listening := c.listening
		c.mu.Unlock()
		if listening {
			c.setState("hört")
		} else {
			c.setState("aus")
		}
	}()
	text, err := c.mgr.STT(wavWrap(pcm))
	if err != nil {
		c.notify("STT fehlgeschlagen", err.Error())
		return
	}
	if len([]rune(text)) < 2 {
		return
	}
	// Wake-Word-Gate: in Telefonkonferenzen hoert das Mikro dauernd Sprache —
	// nur was den Agenten anspricht, erreicht ihn auch. Das Wort allein
	// ("Kat?") bekommt ein kurzes "Ja?" als Lebenszeichen.
	msg, ok := wakeMatch(text, c.wakeWord)
	if !ok {
		if c.headless {
			fmt.Printf("  (ignoriert: %s)\n", text)
		}
		return
	}
	if msg == "" {
		if err := c.Speak("Ja?"); err != nil {
			c.notify("Wiedergabe fehlgeschlagen", err.Error())
		}
		return
	}
	text = msg
	c.mu.Lock()
	c.lastHeard = text
	inst, chatID := c.instance, c.chatID
	c.mu.Unlock()
	if c.headless {
		fmt.Printf("  > %s\n", text)
	}
	reply, err := c.mgr.Chat(inst, text, chatID)
	if err != nil {
		c.notify("Chat fehlgeschlagen", err.Error())
		return
	}
	say := speakable(reply)
	if c.headless {
		fmt.Printf("  < %s\n", say)
	}
	if err := c.Speak(say); err != nil {
		c.notify("Wiedergabe fehlgeschlagen", err.Error())
	}
}

// Speak synthetisiert und spielt ab. Ueber Tempfile statt stdin: paplay und
// aplay lesen WAV-Header aus Dateien zuverlaessig, und "Stopp" ist ein
// schlichtes Kill des Players.
func (c *VoiceClient) Speak(text string) error {
	if text == "" {
		return nil
	}
	wav, err := c.mgr.TTS(text)
	if err != nil {
		return err
	}
	cmd := pickCmd(players)
	if cmd == nil {
		return fmt.Errorf("kein Abspielwerkzeug: paplay, pw-play oder aplay installieren")
	}
	f, err := os.CreateTemp("", "kaim56-voice-*.wav")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	f.Write(wav)
	f.Close()
	c.setState("spricht")
	play := exec.Command(cmd[0], append(cmd[1:], f.Name())...)
	c.mu.Lock()
	c.playCmd = play
	c.mu.Unlock()
	err = play.Run()
	c.mu.Lock()
	c.playCmd = nil
	c.mu.Unlock()
	return err
}

// ---- Menue-Aktionen --------------------------------------------------------
func (c *VoiceClient) ToggleListening() {
	c.mu.Lock()
	c.listening = !c.listening
	on := c.listening
	c.mu.Unlock()
	c.vad.Reset()
	if on {
		c.setState("hört")
	} else {
		c.setState("aus")
	}
}

func (c *VoiceClient) StopSpeaking() {
	c.mu.Lock()
	p := c.playCmd
	c.mu.Unlock()
	if p != nil && p.Process != nil {
		p.Process.Kill()
	}
}

// NewConversation vergibt eine frische Chat-ID und schickt /reset — der
// Verlauf lebt im Agenten, nicht hier.
func (c *VoiceClient) NewConversation() {
	c.mu.Lock()
	c.chatID = fmt.Sprintf("voice-%d", time.Now().Unix())
	inst, chatID := c.instance, c.chatID
	c.mu.Unlock()
	go func() {
		if _, err := c.mgr.Chat(inst, "/reset", chatID); err != nil {
			c.notify("/reset fehlgeschlagen", err.Error())
		}
	}()
}

func (c *VoiceClient) SetInstance(name string) {
	c.mu.Lock()
	c.instance = name
	c.mu.Unlock()
	c.OnState()
}

func (c *VoiceClient) Quit() {
	c.stopOnce.Do(func() { close(c.stopped) })
	c.StopSpeaking()
}
