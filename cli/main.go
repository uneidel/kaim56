// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later

// kaim56 — command line for the kAIm56 manager: instances, chat, tasks,
// missions, skills, apps, projects, models, settings … over iroh (the same
// route as the app and the voice client) or a direct HTTP(S) URL.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"
)

var (
	flagConfig, flagURL, flagIroh, flagUser, flagPass, flagInstance string
	flagJSON, flagVerbose                                           bool

	cfg     Config
	cfgFrom string
	api     *Client
	stopTun = func() {}
)

// connect: the client, built once before a command that needs the manager.
func connect() error {
	if api != nil {
		return nil
	}
	c, from, err := loadConfig(flagConfig)
	if err != nil {
		return err
	}
	c.applyEnv()
	for _, f := range []struct {
		v   string
		dst *string
	}{{flagURL, &c.BaseURL}, {flagIroh, &c.Iroh},
		{flagUser, &c.User}, {flagPass, &c.Pass}, {flagInstance, &c.Instance}} {
		if f.v != "" {
			*f.dst = f.v
		}
	}
	cfg, cfgFrom = c, from
	base := c.BaseURL
	switch {
	case flagURL != "":
	case c.Iroh != "":
		b, stop, err := startTunnel(c.Iroh, c.IrohListen, flagVerbose)
		if err != nil {
			return err
		}
		base, stopTun = b, stop
	case base == "":
		return fmt.Errorf("no manager configured — run `kaim56 config init` (or set KAIM56_URL / KAIM56_IROH)")
	}
	api = newClient(base, c.User, c.Pass)
	api.Iroh = flagURL == "" && c.Iroh != ""
	return nil
}

func needAPI(cmd *cobra.Command, _ []string) error { return connect() }

// instanceArg: the given instance, else the configured default.
func instanceArg(args []string, i int) (string, error) {
	if len(args) > i && args[i] != "" {
		return args[i], nil
	}
	if cfg.Instance != "" {
		return cfg.Instance, nil
	}
	return "", fmt.Errorf("which instance? give its name or set a default (`kaim56 config set instance <name>`)")
}

func main() {
	root := &cobra.Command{
		Use:   "kaim56",
		Short: "Control a kAIm56 manager from the command line (over iroh or HTTP)",
		Long: `kaim56 controls a kAIm56 manager: instances, chat, tasks, missions, skills,
apps, projects, models, settings, notifications and more.

Connection: ~/.config/kaim56-cli.json, else the voice client's
~/.config/kaim56-voice.json. With "iroh" set, the CLI starts kaim56-tunnel
(embedded) and reaches the manager peer-to-peer; with "base_url" it talks
HTTP(S) directly. KAIM56_URL / KAIM56_IROH / KAIM56_USER / KAIM56_PASS /
KAIM56_INSTANCE and the flags below override the file.`,
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	pf := root.PersistentFlags()
	pf.StringVar(&flagConfig, "config", "", "config file (default ~/.config/kaim56-cli.json)")
	pf.StringVar(&flagURL, "url", "", "manager URL, e.g. https://agents.example.org (skips iroh)")
	pf.StringVar(&flagIroh, "iroh", "", "gateway node id (iroh route)")
	pf.StringVar(&flagUser, "user", "", "login user (default admin)")
	pf.StringVar(&flagPass, "pass", "", "login password (prefer KAIM56_PASS or the config file)")
	pf.StringVarP(&flagInstance, "instance", "i", "", "instance to act on (default from the config)")
	pf.BoolVar(&flagJSON, "json", false, "print the manager's raw JSON")
	pf.BoolVarP(&flagVerbose, "verbose", "v", false, "show the tunnel's log")

	root.Version = version
	root.AddCommand(versionCmd(), configCmd(), selfUpdateCmd())
	for _, add := range commandGroups {
		root.AddCommand(add()...)
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() { <-sig; stopTun(); os.Exit(130) }()

	hint := startUpdateHint(len(os.Args) > 1 && (os.Args[1] == "self-update" || os.Args[1] == "completion"))
	err := root.Execute()
	stopTun()
	hint()
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

// commandGroups: each file adds its commands here (init), so main stays short.
var commandGroups []func() []*cobra.Command
