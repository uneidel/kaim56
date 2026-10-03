// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// Config: where the manager is and how to log in. Read from
// ~/.config/kaim56-cli.json; without it the voice client's
// ~/.config/kaim56-voice.json is used (same fields), so a desktop that runs
// the voice client needs no setup. Flags and KAIM56_* variables override.
type Config struct {
	Iroh       string `json:"iroh,omitempty"`        // gateway NodeId: the CLI starts kaim56-tunnel itself
	IrohListen string `json:"iroh_listen,omitempty"` // local tunnel port (default 127.0.0.1:8701)
	BaseURL    string `json:"base_url,omitempty"`    // alternative: direct HTTP(S) route to the manager
	User       string `json:"user,omitempty"`
	Pass       string `json:"pass,omitempty"`
	Instance   string `json:"instance,omitempty"` // default instance for chat and instance commands
}

func configDir() string {
	if d, err := os.UserConfigDir(); err == nil {
		return d
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config")
}

func cliConfigPath() string   { return filepath.Join(configDir(), "kaim56-cli.json") }
func voiceConfigPath() string { return filepath.Join(configDir(), "kaim56-voice.json") }

// loadConfig -> the config and the file it came from ("" = none found).
func loadConfig(explicit string) (Config, string, error) {
	paths := []string{cliConfigPath(), voiceConfigPath()}
	if explicit != "" {
		paths = []string{explicit}
	}
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if errors.Is(err, os.ErrNotExist) && explicit == "" {
			continue
		}
		if err != nil {
			return Config{}, p, err
		}
		var c Config
		if err := json.Unmarshal(b, &c); err != nil {
			return Config{}, p, fmt.Errorf("%s: %w", p, err)
		}
		return c, p, nil
	}
	return Config{}, "", nil
}

func (c *Config) applyEnv() {
	for env, dst := range map[string]*string{"KAIM56_URL": &c.BaseURL, "KAIM56_IROH": &c.Iroh,
		"KAIM56_USER": &c.User, "KAIM56_PASS": &c.Pass, "KAIM56_INSTANCE": &c.Instance} {
		if v := os.Getenv(env); v != "" {
			*dst = v
		}
	}
	if c.User == "" {
		c.User = "admin"
	}
	if c.IrohListen == "" {
		c.IrohListen = "127.0.0.1:8701"
	}
}

// saveConfig writes the CLI config 0600 (it holds the password).
func saveConfig(c Config) (string, error) {
	p := cliConfigPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return p, err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o600); err != nil {
		return p, err
	}
	return p, os.Rename(tmp, p)
}
