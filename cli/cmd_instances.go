// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/spf13/cobra"
)

func init() { commandGroups = append(commandGroups, instanceCmds) }

type instance struct {
	Name        string            `json:"name"`
	Template    string            `json:"template"`
	Description string            `json:"description"`
	VCPUs       int               `json:"vcpus"`
	MemMiB      int               `json:"mem_mib"`
	Internet    bool              `json:"internet"`
	Running     bool              `json:"running"`
	Stale       bool              `json:"stale"`
	Config      map[string]string `json:"config"`
}

var modelKeys = []string{"OPENROUTER_MODEL", "ORCAROUTER_MODEL", "ANTHROPIC_MODEL", "PI_MODEL", "PRIME_MODEL", "LLAMA_MODEL"}

func (i instance) model() string {
	for _, k := range modelKeys {
		if v := i.Config[k]; v != "" {
			return v
		}
	}
	return ""
}

func listInstances() ([]instance, []byte, error) {
	var out []instance
	raw, err := api.Get("/api/instances", &out)
	return out, raw, err
}

func findInstance(name string) (instance, error) {
	all, _, err := listInstances()
	if err != nil {
		return instance{}, err
	}
	for _, i := range all {
		if i.Name == name {
			return i, nil
		}
	}
	return instance{}, fmt.Errorf("no instance %q", name)
}

// instOp: POST /api/instances/<name>/<op> (a msg route).
func instOp(name, op string, body any) error {
	return msgPost("/api/instances/"+esc(name)+"/"+op, body)
}

func onOff(s string) (bool, error) {
	switch strings.ToLower(s) {
	case "on", "true", "1", "yes":
		return true, nil
	case "off", "false", "0", "no":
		return false, nil
	}
	return false, fmt.Errorf("on or off, not %q", s)
}

func instanceCmds() []*cobra.Command {
	c := &cobra.Command{Use: "instances", Aliases: []string{"inst", "i"}, Short: "List and manage agent instances (microVMs)",
		PersistentPreRunE: needAPI}

	c.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "All instances with state, template and model",
		RunE: func(cmd *cobra.Command, args []string) error {
			all, raw, err := listInstances()
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			sort.Slice(all, func(a, b int) bool { return all[a].Name < all[b].Name })
			rows := [][]string{}
			for _, i := range all {
				st := "off"
				if i.Running {
					st = "running"
					if i.Stale {
						st = "running (stale)"
					}
				}
				rows = append(rows, []string{i.Name, st, i.Template, i.Config["TRANSPORT"], i.model(),
					fmt.Sprintf("%d/%dM", i.VCPUs, i.MemMiB)})
			}
			table([]string{"NAME", "STATE", "TEMPLATE", "TRANSPORT", "MODEL", "CPU/MEM"}, rows)
			return nil
		},
	})

	c.AddCommand(&cobra.Command{
		Use: "show [name]", Short: "Session details: runtime, model, uptime, MCP servers, config",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name, err := instanceArg(args, 0)
			if err != nil {
				return err
			}
			var s map[string]any
			raw, err := api.Get("/api/session/"+esc(name), &s)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			fmt.Printf("%s  (%s, %s)\n", name, str(s["template"]), map[bool]string{true: "running", false: "off"}[s["running"] == true])
			for _, k := range []string{"model", "runtime", "uptime", "login", "stale", "need_secret"} {
				if v := str(s[k]); v != "" && v != "0" && v != "no" {
					fmt.Printf("  %-11s %s\n", k, v)
				}
			}
			if mc, ok := s["mcps"].([]any); ok && len(mc) > 0 {
				fmt.Println("  mcp servers:")
				for _, m := range mc {
					mm, _ := m.(map[string]any)
					fmt.Printf("    %s ready=%s %s\n", str(mm["name"]), str(mm["ready"]), str(mm["missing"]))
				}
			}
			if i, err := findInstance(name); err == nil {
				keys := make([]string, 0, len(i.Config))
				for k := range i.Config {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				fmt.Println("  config:")
				for _, k := range keys {
					fmt.Printf("    %s=%s\n", k, clip(i.Config[k], 100))
				}
			}
			return nil
		},
	})

	c.AddCommand(&cobra.Command{
		Use: "log [name]", Short: "The last 64 KB of the instance's console log",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name, err := instanceArg(args, 0)
			if err != nil {
				return err
			}
			resp, err := api.req("GET", "/api/session/"+esc(name)+"/log", nil, "", api.HTTP.Timeout)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			_, err = io.Copy(os.Stdout, resp.Body)
			return err
		},
	})

	for _, op := range []struct{ use, short string }{
		{"start", "Start an instance"}, {"stop", "Stop an instance"},
		{"restart", "Stop and start (picks up a rebuilt image)"},
		{"diskreset", "Throw away the persistent disk layers (instance must be stopped)"},
	} {
		op := op
		c.AddCommand(&cobra.Command{
			Use: op.use + " [name]", Short: op.short, Args: cobra.MaximumNArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				name, err := instanceArg(args, 0)
				if err != nil {
					return err
				}
				return instOp(name, op.use, nil)
			},
		})
	}

	var yes bool
	del := &cobra.Command{
		Use: "delete <name>", Short: "Stop and delete an instance", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes && !confirm(fmt.Sprintf("Delete instance %s? Type its name", args[0]), args[0]) {
				return fmt.Errorf("not deleted")
			}
			return instOp(args[0], "delete", nil)
		},
	}
	del.Flags().BoolVarP(&yes, "yes", "y", false, "do not ask")
	c.AddCommand(del)

	var tpl, model, persona string
	var cfgKV, tools, mcps []string
	var noNet, start bool
	create := &cobra.Command{
		Use:   "create <name>",
		Short: "Create an instance from a template (openrouter, claude, llama, orcarouter, …)",
		Example: `  kaim56 inst create helper -t openrouter -m google/gemini-2.5-flash --start
  kaim56 inst create coder -t openrouter --persona coder --set TRANSPORT=web`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			conf := map[string]string{"TRANSPORT": "web"}
			if persona != "" {
				var ps []map[string]any
				if _, err := api.Get("/api/personas", &ps); err != nil {
					return err
				}
				found := false
				for _, p := range ps {
					if str(p["name"]) != persona {
						continue
					}
					found = true
					conf["AGENT_SYSTEM"] = str(p["prompt"])
					if t, ok := p["tools"].([]any); ok && len(t) > 0 {
						ts := []string{}
						for _, x := range t {
							ts = append(ts, str(x))
						}
						conf["AGENT_TOOLS"] = strings.Join(ts, ",")
					}
					if m := str(p["model"]); m != "" {
						conf["OPENROUTER_MODEL"] = m
					}
				}
				if !found {
					return fmt.Errorf("no persona %q (kaim56 personas)", persona)
				}
			}
			if model != "" {
				key := map[string]string{"openrouter": "OPENROUTER_MODEL", "claude": "ANTHROPIC_MODEL",
					"llama": "LLAMA_MODEL", "orcarouter": "ORCAROUTER_MODEL"}[tpl]
				if key == "" {
					key = "OPENROUTER_MODEL"
				}
				conf[key] = model
			}
			for _, kv := range cfgKV {
				k, v, ok := strings.Cut(kv, "=")
				if !ok {
					return fmt.Errorf("--set wants KEY=value, not %q", kv)
				}
				conf[k] = v
			}
			body := map[string]any{"name": args[0], "template": tpl, "config": conf, "internet": !noNet}
			if len(tools) > 0 {
				body["tools"] = tools
			}
			if len(mcps) > 0 {
				body["mcps"] = mcps
			}
			if err := msgPost("/api/create", body); err != nil {
				return err
			}
			if start {
				return instOp(strings.ToLower(args[0]), "start", nil)
			}
			return nil
		},
	}
	f := create.Flags()
	f.StringVarP(&tpl, "template", "t", "openrouter", "template")
	f.StringVarP(&model, "model", "m", "", "model (goes into the template's model key)")
	f.StringVar(&persona, "persona", "", "apply a persona (system prompt, tools, model)")
	f.StringArrayVar(&cfgKV, "set", nil, "config KEY=value (repeatable); TRANSPORT defaults to web")
	f.StringSliceVar(&tools, "tools", nil, "allowed tools (comma-separated; default all)")
	f.StringSliceVar(&mcps, "mcp", nil, "MCP servers from the catalog")
	f.BoolVar(&noNet, "no-internet", false, "no internet access")
	f.BoolVar(&start, "start", false, "start it right away")
	c.AddCommand(create)

	cfgC := &cobra.Command{Use: "config", Short: "Read and change an instance's config keys"}
	cfgC.AddCommand(&cobra.Command{
		Use: "get [name] [KEY]", Short: "Show the config (or one key)", Args: cobra.MaximumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			name, err := instanceArg(args, 0)
			if err != nil {
				return err
			}
			i, err := findInstance(name)
			if err != nil {
				return err
			}
			if len(args) == 2 {
				fmt.Println(i.Config[args[1]])
				return nil
			}
			keys := make([]string, 0, len(i.Config))
			for k := range i.Config {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				fmt.Printf("%s=%s\n", k, i.Config[k])
			}
			return nil
		},
	})
	cfgC.AddCommand(&cobra.Command{
		Use: "set <name> <KEY> <value>", Short: "Set a key (empty value removes it); some apply at once, others after a restart",
		Args: cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			return instOp(args[0], "config", map[string]string{"key": args[1], "value": args[2]})
		},
	})
	cfgC.AddCommand(&cobra.Command{
		Use: "unset <name> <KEY>", Short: "Remove a key", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return instOp(args[0], "config", map[string]string{"key": args[1], "value": ""})
		},
	})
	c.AddCommand(cfgC)

	c.AddCommand(&cobra.Command{
		Use: "model <name> <[provider:]model>", Short: "Switch the model (provider: openrouter, orcarouter, anthropic, pi, prime, llama)",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return instOp(args[0], "model", map[string]string{"model": args[1]})
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "tools <name> [tool…]", Short: "Set the allowed tools (none given = all)", Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return instOp(args[0], "tools", map[string]any{"tools": append([]string{}, args[1:]...)})
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "internet <name> <on|off>", Short: "Allow or block internet access (applies at once)", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			on, err := onOff(args[1])
			if err != nil {
				return err
			}
			return instOp(args[0], "internet", map[string]bool{"on": on})
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "persist <name> <on|off>", Short: "Keep the instance's disk across restarts", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			on, err := onOff(args[1])
			if err != nil {
				return err
			}
			return instOp(args[0], "persist", map[string]bool{"on": on})
		},
	})
	return []*cobra.Command{c}
}
