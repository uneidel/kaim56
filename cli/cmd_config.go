// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"golang.org/x/term"
)

func versionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Show the CLI's and the manager's version",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("kaim56 CLI", version)
			if err := connect(); err != nil {
				return err
			}
			var v map[string]any
			raw, err := api.Get("/api/version", &v)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			fmt.Printf("manager    %s\n", str(v["version"]))
			return nil
		},
	}
}

func prompt(r *bufio.Reader, q, def string) string {
	if def != "" {
		fmt.Printf("%s [%s]: ", q, def)
	} else {
		fmt.Printf("%s: ", q)
	}
	s, _ := r.ReadString('\n')
	if s = strings.TrimSpace(s); s == "" {
		return def
	}
	return s
}

func configCmd() *cobra.Command {
	c := &cobra.Command{Use: "config", Short: "Set up and show the connection to the manager"}
	c.AddCommand(&cobra.Command{
		Use:   "init",
		Short: "Write ~/.config/kaim56-cli.json interactively and test it",
		RunE: func(cmd *cobra.Command, args []string) error {
			old, _, _ := loadConfig("")
			r := bufio.NewReader(os.Stdin)
			fmt.Println("Route: an iroh gateway node id (peer-to-peer, no open port) or a URL.")
			route := prompt(r, "Gateway node id or https:// URL", firstNonEmpty(old.Iroh, old.BaseURL))
			n := Config{User: prompt(r, "User", firstNonEmpty(old.User, "admin")), Instance: old.Instance}
			if strings.HasPrefix(route, "http://") || strings.HasPrefix(route, "https://") {
				n.BaseURL = route
			} else {
				n.Iroh = strings.TrimPrefix(route, "iroh://")
			}
			fmt.Print("Password (empty = keep the current one): ")
			pw, err := term.ReadPassword(int(os.Stdin.Fd()))
			fmt.Println()
			if err != nil {
				s, _ := r.ReadString('\n')
				pw = []byte(strings.TrimSpace(s))
			}
			n.Pass = firstNonEmpty(string(pw), old.Pass)
			n.Instance = prompt(r, "Default instance (optional)", n.Instance)
			p, err := saveConfig(n)
			if err != nil {
				return err
			}
			fmt.Println("written:", p, "(0600)")
			if n.Iroh != "" {
				if id, err := tunnelID(); err == nil {
					fmt.Println("this device's iroh id:", id, "— it must be in the gateway allowlist (web UI → iroh)")
				}
			}
			flagConfig = p
			if err := connect(); err != nil {
				return fmt.Errorf("saved, but the connection failed: %w", err)
			}
			var v map[string]any
			if _, err := api.Get("/api/version", &v); err != nil {
				return fmt.Errorf("saved, but the manager refused: %w", err)
			}
			fmt.Println("connected — manager", str(v["version"]))
			return nil
		},
	})
	c.AddCommand(&cobra.Command{
		Use:   "show",
		Short: "Show the active connection settings (password hidden)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, from, err := loadConfig(flagConfig)
			if err != nil {
				return err
			}
			c.applyEnv()
			if from == "" {
				from = "(none — environment and flags only)"
			}
			pw := "(not set)"
			if c.Pass != "" {
				pw = "(set)"
			}
			table([]string{"KEY", "VALUE"}, [][]string{{"file", from}, {"iroh", c.Iroh}, {"iroh_listen", c.IrohListen},
				{"base_url", c.BaseURL}, {"user", c.User}, {"pass", pw}, {"instance", c.Instance}})
			return nil
		},
	})
	c.AddCommand(&cobra.Command{
		Use:       "set <key> <value>",
		Short:     "Change one setting in ~/.config/kaim56-cli.json (iroh, base_url, user, pass, instance, iroh_listen)",
		Args:      cobra.ExactArgs(2),
		ValidArgs: []string{"iroh", "base_url", "user", "pass", "instance", "iroh_listen"},
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := loadConfig("")
			if err != nil {
				return err
			}
			m := map[string]*string{"iroh": &c.Iroh, "base_url": &c.BaseURL, "user": &c.User, "pass": &c.Pass,
				"instance": &c.Instance, "iroh_listen": &c.IrohListen}
			dst, ok := m[args[0]]
			if !ok {
				return fmt.Errorf("unknown key %q", args[0])
			}
			*dst = args[1]
			p, err := saveConfig(c)
			if err == nil {
				fmt.Println("written:", p)
			}
			return err
		},
	})
	c.AddCommand(&cobra.Command{
		Use:   "id",
		Short: "Print this device's iroh id (add it to the gateway allowlist to pair)",
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := tunnelID()
			if err != nil {
				return err
			}
			fmt.Println(id)
			return nil
		},
	})
	return c
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}
