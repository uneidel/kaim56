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
	"strings"
	"sync"
	"time"
)

// ---- Config ----------------------------------------------------------------
// The same file as the earlier Python version: ~/.config/kaim56-voice.json.
type Config struct {
	Iroh       string    `json:"iroh"`                  // gateway NodeId: the client starts kaim56-tunnel itself
	IrohListen string    `json:"iroh_listen,omitempty"` // local tunnel port
	BaseURL    string    `json:"base_url"`              // alternative: direct HTTP(S) route
	User       string    `json:"user"`
	Pass       string    `json:"pass"`
	Instance   string    `json:"instance"`
	Prompt     string    `json:"prompt,omitempty"`         // prepended to every spoken message
	WakeWord   string    `json:"wake_word"`                // empty = every utterance passes
	WakeMode   string    `json:"wake_mode,omitempty"`      // "local" = MFCC/DTW gate BEFORE the upload
	WakeThresh float64   `json:"wake_threshold,omitempty"` // override; 0 = from the enrollment
	AutoUpdate bool      `json:"auto_update"`              // check GitHub Releases at start (update.go)
	Vad        VadConfig `json:"vad"`
}

func configPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "kaim56-voice.json")
}

// loadConfig reads the config; missing VAD keys keep their defaults (the
// struct is pre-filled with them, Unmarshal only overwrites what the file
// contains).
func loadConfig(path string) (Config, error) {
	cfg := Config{Instance: "myassistant", AutoUpdate: true, Vad: defaultVadConfig()}
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, fmt.Errorf("config %s: %w", path, err)
	}
	// One transport must really be configured: either iroh (gateway NodeId)
	// or a base_url that is no longer the template placeholder. Starting with
	// the placeholder would mean resolving "manager.example" and showing the
	// error only at the first sentence.
	if cfg.Iroh == "" {
		if cfg.BaseURL == "" || strings.Contains(cfg.BaseURL, "manager.example") {
			return cfg, fmt.Errorf("config %s: please set 'iroh' (the manager NodeId from the "+
				"web UI, iroh tab) OR a real 'base_url' — "+
				"'%s' is still the placeholder", path, cfg.BaseURL)
		}
		if cfg.User == "" || cfg.Pass == "" {
			return cfg, fmt.Errorf("config %s: 'user'/'pass' are missing (required for "+
				"the HTTP(S) route; the iroh route does not need them)", path)
		}
	}
	if cfg.IrohListen == "" {
		cfg.IrohListen = "127.0.0.1:8701"
	}
	if cfg.Instance == "" {
		cfg.Instance = "myassistant"
	}
	return cfg, nil
}

func writeConfigTemplate(path string) error {
	tpl := Config{Iroh: "", BaseURL: "http://manager.example:8700",
		User: "admin", Pass: "secret", Instance: "myassistant",
		Prompt: "You are being used through a voice client: answer briefly and " +
			"in prose that reads aloud well, without lists, links or code.",
		WakeWord: "Kati, Katharina", AutoUpdate: true, Vad: defaultVadConfig()}
	b, _ := json.MarshalIndent(tpl, "", "  ")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o600) // it contains a password
}

// ---- Audio subprocesses ----------------------------------------------------
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

// ---- The client ------------------------------------------------------------
// States: off | listening | thinking | speaking. The audio reader always
// runs; outside "listening" frames are just discarded (no tail of old speech,
// and the client does not listen to itself while speaking).
const (
	stateListening = "listening"
	stateThinking  = "thinking"
	stateSpeaking  = "speaking"
	stateOff       = "off"
)

// ackWord is spoken when only the wake word was heard, without a message.
const ackWord = "Yes?"

type VoiceClient struct {
	mu        sync.Mutex
	mgr       *Manager
	vad       *Vad
	wakeWord  string
	wakeModel *WakeModel // nil = no local gate
	prompt    string     // prefix for every spoken message
	instance  string
	chatID    string
	listening bool
	state     string
	lastHeard string
	headless  bool
	stopped   chan struct{}
	stopOnce  sync.Once
	playCmd   *exec.Cmd
	OnState   func() // the tray hooks in here
}

func NewVoiceClient(cfg Config, headless bool) *VoiceClient {
	return &VoiceClient{
		mgr:       NewManager(cfg.BaseURL, cfg.User, cfg.Pass),
		vad:       NewVad(cfg.Vad),
		wakeWord:  cfg.WakeWord,
		prompt:    cfg.Prompt,
		instance:  cfg.Instance,
		chatID:    fmt.Sprintf("voice-%d", time.Now().Unix()),
		listening: true,
		state:     stateListening,
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

// Run is the main loop: read frames, feed the VAD, process segments.
func (c *VoiceClient) Run(once bool) error {
	cmd := pickCmd(recorders)
	if cmd == nil {
		return fmt.Errorf("no recording tool: install parec, pw-record or arecord")
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
				return fmt.Errorf("recording broke off (%s): %w", cmd[0], err)
			}
		}
		c.mu.Lock()
		active := c.listening && c.state == stateListening
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
	c.setState(stateThinking)
	defer func() {
		c.mu.Lock()
		listening := c.listening
		c.mu.Unlock()
		if listening {
			c.setState(stateListening)
		} else {
			c.setState(stateOff)
		}
	}()
	// Local wake gate first: NOT a single byte leaves the desktop when the
	// start of the utterance does not sound like the enrolled word. On a hit
	// the word is cut out of the AUDIO (DTW knows where the alignment ends) —
	// STT gets only the message and can no longer garble or swallow the word.
	if c.wakeModel != nil {
		score, cut, hit := c.wakeModel.Match(pcm)
		if !hit {
			c.mu.Lock()
			c.lastHeard = fmt.Sprintf("✕ wake %.2f (threshold %.2f)", score, c.wakeModel.Threshold)
			c.mu.Unlock()
			if c.headless {
				fmt.Printf("  (dropped locally: score %.3f, threshold %.3f)\n",
					score, c.wakeModel.Threshold)
			}
			return
		}
		if c.headless {
			fmt.Printf("  (wake: score %.3f)\n", score)
		}
		pcm = pcm[cut:]
		if len(pcm) < sampleRate/5*2 { // < 200 ms left: only the word -> "Yes?"
			if err := c.Speak(ackWord); err != nil {
				c.notify("Playback failed", err.Error())
			}
			return
		}
	}
	tSTT := time.Now()
	text, err := c.mgr.STT(wavWrap(pcm))
	if err != nil {
		c.notify("STT failed", err.Error())
		return
	}
	if c.headless {
		fmt.Printf("  (stt: %.1f s)\n", time.Since(tSTT).Seconds())
	}
	if len([]rune(text)) < 2 {
		if c.wakeModel != nil { // woken, but no usable sentence
			if err := c.Speak(ackWord); err != nil {
				c.notify("Playback failed", err.Error())
			}
		}
		return
	}
	// Text gate (only without a local model): in conference calls the
	// microphone hears speech all the time — only what addresses the agent
	// reaches it. The word alone ("Kati?") gets a short "Yes?".
	msg, ok := text, true
	if c.wakeModel == nil {
		msg, ok = wakeMatch(text, c.wakeWord)
	}
	if !ok {
		// Drop visibly: otherwise "hears but does not react" cannot be told
		// apart from a calibration problem (the tray shows it as ✕).
		c.mu.Lock()
		c.lastHeard = "✕ " + text
		c.mu.Unlock()
		if c.headless {
			fmt.Printf("  (ignored: %s)\n", text)
		}
		return
	}
	if msg == "" {
		if err := c.Speak(ackWord); err != nil {
			c.notify("Playback failed", err.Error())
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
	// Sentence-wise streaming: the first complete sentence is spoken while
	// the model is still writing — the perceived latency depends on the FIRST
	// sentence, not on the total length of the reply (gemini-pro likes to
	// think for a long time).
	tChat := time.Now()
	var speakErr error
	spoke := false
	ss := newSentenceStreamer(func(chunk string) {
		if c.headless {
			if !spoke {
				fmt.Printf("  (first sentence: %.1f s)\n", time.Since(tChat).Seconds())
			}
			fmt.Printf("  < %s\n", chunk)
		}
		spoke = true
		if err := c.Speak(chunk); err != nil && speakErr == nil {
			speakErr = err
			c.notify("Playback failed", err.Error())
		}
	})
	_, err = c.mgr.ChatStream(inst, withPrompt(c.prompt, text), chatID, ss.Feed)
	ss.Close()
	if err != nil {
		c.notify("Chat failed", err.Error())
	}
}

// Speak synthesizes and plays. Via a temp file instead of stdin: paplay and
// aplay read WAV headers from files reliably, and "stop" is a plain kill of
// the player.
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
		return fmt.Errorf("no playback tool: install paplay, pw-play or aplay")
	}
	f, err := os.CreateTemp("", "kaim56-voice-*.wav")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	f.Write(wav)
	f.Close()
	c.setState(stateSpeaking)
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

// captureSegments records n VAD utterances (for enrollment and the wake
// test) — its own VAD instance with a short minimum duration, a single word
// is shorter than a sentence after all.
func captureSegments(vadCfg VadConfig, n int, prompt func(i int), got func(i int, pcm []byte)) error {
	cmd := pickCmd(recorders)
	if cmd == nil {
		return fmt.Errorf("no recording tool: install parec, pw-record or arecord")
	}
	vadCfg.MinMs = 250
	vadCfg.EndMs = 600
	vad := NewVad(vadCfg)
	rec := exec.Command(cmd[0], cmd[1:]...)
	out, err := rec.StdoutPipe()
	if err != nil {
		return err
	}
	if err := rec.Start(); err != nil {
		return err
	}
	defer func() { rec.Process.Kill(); rec.Wait() }()
	frame := make([]byte, frameBytes)
	for i := 0; i < n; {
		prompt(i)
		vad.Reset()
		for {
			if _, err := io.ReadFull(out, frame); err != nil {
				return fmt.Errorf("recording broke off: %w", err)
			}
			if seg := vad.Feed(frame); seg != nil {
				got(i, seg)
				i++
				break
			}
		}
	}
	return nil
}

// ---- Menu actions ----------------------------------------------------------
func (c *VoiceClient) ToggleListening() {
	c.mu.Lock()
	c.listening = !c.listening
	on := c.listening
	c.mu.Unlock()
	c.vad.Reset()
	if on {
		c.setState(stateListening)
	} else {
		c.setState(stateOff)
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

// NewConversation assigns a fresh chat ID and sends /reset — the history
// lives in the agent, not here.
func (c *VoiceClient) NewConversation() {
	c.mu.Lock()
	c.chatID = fmt.Sprintf("voice-%d", time.Now().Unix())
	inst, chatID := c.instance, c.chatID
	c.mu.Unlock()
	go func() {
		if _, err := c.mgr.Chat(inst, "/reset", chatID); err != nil {
			c.notify("/reset failed", err.Error())
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
