// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/spf13/cobra"
)

// Memory, personas, prompts, playbooks, iroh pairing, MCP catalog, secrets.
func init() { commandGroups = append(commandGroups, miscCmds) }

func miscCmds() []*cobra.Command {
	// ---- memory ----
	mem := &cobra.Command{Use: "memory", Aliases: []string{"mem"}, Short: "An instance's long-term memory", PersistentPreRunE: needAPI}
	mem.AddCommand(&cobra.Command{
		Use: "get <instance> [key]", Short: "All memory entries, or one", Args: cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 2 {
				var r struct {
					Value any `json:"value"`
				}
				raw, err := api.Get("/api/memory/"+esc(args[0])+"/"+args[1], &r)
				if err == nil {
					done(raw, "%s", str(r.Value))
				}
				return err
			}
			var m map[string]any
			raw, err := api.Get("/api/memory/"+esc(args[0]), &m)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			keys := make([]string, 0, len(m))
			for k := range m {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				fmt.Printf("%s: %s\n", k, clip(str(m[k]), 160))
			}
			return nil
		},
	})
	mem.AddCommand(&cobra.Command{
		Use: "set <instance> <key> <value…>", Short: "Store a memory entry", Args: cobra.MinimumNArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/memory/"+esc(args[0]), map[string]any{"key": args[1], "value": strings.Join(args[2:], " ")})
		},
	})
	mem.AddCommand(&cobra.Command{
		Use: "delete <instance> <key>", Aliases: []string{"rm"}, Short: "Delete a memory entry", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/memory/"+esc(args[0]), map[string]any{"key": args[1], "value": nil})
		},
	})
	mem.AddCommand(&cobra.Command{
		Use: "search <instance> <query…>", Short: "Semantic search in an instance's memory", Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Hits []map[string]any `json:"hits"`
			}
			raw, err := api.Post("/api/memory-search", map[string]any{"instance": args[0], "query": strings.Join(args[1:], " "), "k": 8}, &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			for _, h := range r.Hits {
				fmt.Printf("%.2f  %s\n", num(h["score"]), clip(str(h["text"]), 160))
			}
			return nil
		},
	})

	// ---- personas ----
	pers := &cobra.Command{Use: "personas", Aliases: []string{"persona"}, Short: "Reusable agent personas (system prompt, tools, model)", PersistentPreRunE: needAPI}
	pers.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "All personas",
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			raw, err := api.Get("/api/personas", &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, p := range all {
				rows = append(rows, []string{str(p["name"]), str(p["model"]), clip(str(p["prompt"]), 70)})
			}
			table([]string{"NAME", "MODEL", "PROMPT"}, rows)
			return nil
		},
	})
	var pmodel, pfile string
	var ptools []string
	psave := &cobra.Command{
		Use: "save <name>", Short: "Create or update a persona (prompt from --file or stdin)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var b []byte
			var err error
			if pfile != "" {
				b, err = os.ReadFile(pfile)
			} else {
				b, err = readAllStdin()
			}
			if err != nil {
				return err
			}
			body := map[string]any{"name": args[0], "prompt": strings.TrimSpace(string(b))}
			if cmd.Flags().Changed("model") {
				body["model"] = pmodel
			}
			if cmd.Flags().Changed("tools") {
				body["tools"] = ptools
			}
			return msgPost("/api/personas", body)
		},
	}
	psave.Flags().StringVarP(&pfile, "file", "f", "", "system prompt file (default stdin)")
	psave.Flags().StringVarP(&pmodel, "model", "m", "", "model")
	psave.Flags().StringSliceVar(&ptools, "tools", nil, "allowed tools")
	pers.AddCommand(psave)
	pers.AddCommand(&cobra.Command{
		Use: "delete <name>", Aliases: []string{"rm"}, Short: "Delete a persona", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/personas/"+esc(args[0])+"/delete", nil)
		},
	})

	// ---- prompts ----
	prompts := &cobra.Command{Use: "prompts", Short: "Your /commands: saved prompts usable as /<name> in any chat", PersistentPreRunE: needAPI}
	prompts.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "All saved prompts",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Prompts []map[string]any `json:"prompts"`
			}
			raw, err := api.Get("/api/prompts", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			for _, p := range r.Prompts {
				fmt.Printf("/%-16s %s\n", str(p["name"]), clip(str(p["text"]), 90))
			}
			return nil
		},
	})
	prompts.AddCommand(&cobra.Command{
		Use: "save <name> <text…>", Short: "Create or replace a prompt", Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/prompts", map[string]any{"name": args[0], "text": strings.Join(args[1:], " ")})
		},
	})
	prompts.AddCommand(&cobra.Command{
		Use: "delete <name>", Aliases: []string{"rm"}, Short: "Delete a prompt", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/prompts", map[string]any{"name": args[0], "delete": true})
		},
	})

	// ---- playbooks ----
	pb := &cobra.Command{Use: "playbook", Aliases: []string{"playbooks"}, Short: "An instance's standing rules (playbook)", PersistentPreRunE: needAPI}
	pb.AddCommand(&cobra.Command{
		Use: "list <instance>", Aliases: []string{"ls"}, Short: "The rules", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Playbooks []map[string]any `json:"playbooks"`
			}
			raw, err := api.Get("/api/playbooks?instance="+esc(args[0]), &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			for _, p := range r.Playbooks {
				fmt.Printf("%s  %s\n", str(p["id"]), str(p["text"]))
			}
			return nil
		},
	})
	pb.AddCommand(&cobra.Command{
		Use: "add <instance> <rule…>", Short: "Add a rule", Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r map[string]any
			raw, err := api.Post("/api/playbook-add", map[string]string{"instance": args[0], "text": strings.Join(args[1:], " ")}, &r)
			if err == nil {
				done(raw, "added: %s", str(r["id"]))
			}
			return err
		},
	})
	pb.AddCommand(&cobra.Command{
		Use: "remove <instance> <id>", Aliases: []string{"rm"}, Short: "Remove a rule", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Removed int `json:"removed"`
			}
			raw, err := api.Post("/api/playbook-remove", map[string]string{"instance": args[0], "id": args[1]}, &r)
			if err == nil {
				done(raw, "%d removed", r.Removed)
			}
			return err
		},
	})

	// ---- iroh ----
	iroh := &cobra.Command{Use: "iroh", Short: "The iroh gateway: its node id and the paired devices", PersistentPreRunE: needAPI}
	iroh.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Gateway node id and allowlist",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				NodeID    string           `json:"node_id"`
				Allow     []map[string]any `json:"allow"`
				Available bool             `json:"available"`
			}
			raw, err := api.Get("/api/iroh", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			fmt.Printf("gateway: %s (running: %v)\n", r.NodeID, r.Available)
			rows := [][]string{}
			for _, a := range r.Allow {
				rows = append(rows, []string{str(a["id"]), str(a["label"])})
			}
			table([]string{"DEVICE ID", "LABEL"}, rows)
			return nil
		},
	})
	iroh.AddCommand(&cobra.Command{
		Use: "add <device-id> [label…]", Short: "Pair a device (its 64-hex iroh id; `kaim56 config id` prints this one's)", Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return irohAction("add", args[0], strings.Join(args[1:], " "))
		},
	})
	iroh.AddCommand(&cobra.Command{
		Use: "remove <device-id>", Aliases: []string{"rm"}, Short: "Unpair a device", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return irohAction("remove", args[0], "") },
	})

	// ---- MCP catalog ----
	mcp := &cobra.Command{Use: "mcp", Short: "The MCP server catalog instances can use", PersistentPreRunE: needAPI}
	mcp.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Catalog entries",
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			raw, err := api.Get("/api/mcps", &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, m := range all {
				rows = append(rows, []string{str(m["name"]), clip(str(m["command"])+" "+str(m["args"]), 50), clip(str(m["description"]), 50)})
			}
			table([]string{"NAME", "COMMAND", "DESCRIPTION"}, rows)
			return nil
		},
	})
	mcp.AddCommand(&cobra.Command{
		Use: "delete <name>", Aliases: []string{"rm"}, Short: "Remove a catalog entry", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/mcps/"+esc(args[0])+"/delete", nil)
		},
	})

	// ---- secrets ----
	sec := &cobra.Command{Use: "secrets", Short: "The secret store (values are never shown)", PersistentPreRunE: needAPI}
	sec.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Secret names and where they come from",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Keys    []string          `json:"keys"`
				Sources map[string]string `json:"sources"`
			}
			raw, err := api.Get("/api/secret-keys", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, k := range r.Keys {
				rows = append(rows, []string{k, r.Sources[k]})
			}
			table([]string{"NAME", "SOURCE"}, rows)
			return nil
		},
	})
	sec.AddCommand(&cobra.Command{
		Use: "set <NAME>", Short: "Store a secret (typed hidden, or piped on stdin)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			v, err := readSecret(args[0])
			if err != nil {
				return err
			}
			if v == "" {
				return fmt.Errorf("empty value")
			}
			return msgPost("/api/secret-store", map[string]string{"name": args[0], "value": v})
		},
	})
	sec.AddCommand(&cobra.Command{
		Use: "delete <NAME>", Aliases: []string{"rm"}, Short: "Delete a stored secret", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/secret-store/"+esc(args[0])+"/delete", nil)
		},
	})
	return []*cobra.Command{mem, pers, prompts, pb, iroh, mcp, sec}
}

func irohAction(action, id, label string) error {
	var r struct {
		OK  bool   `json:"ok"`
		Msg string `json:"msg"`
	}
	raw, err := api.Post("/api/iroh", map[string]string{"action": action, "id": id, "label": label}, &r)
	if err != nil {
		return err
	}
	done(raw, "%s", r.Msg)
	if !r.OK {
		return fmt.Errorf("the manager said: %s", r.Msg)
	}
	return nil
}
