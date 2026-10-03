// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// Apps, projects, models & router, settings, system.
func init() { commandGroups = append(commandGroups, adminCmds) }

func okPost(path string, body any, line string, a ...any) error {
	raw, err := api.Post(path, body, nil)
	if err != nil {
		return err
	}
	done(raw, line, a...)
	return nil
}

func adminCmds() []*cobra.Command {
	// ---- apps ----
	apps := &cobra.Command{Use: "apps", Short: "Browser apps: local (celld/manager) and on Cloudflare", PersistentPreRunE: needAPI}
	apps.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Local apps and every Worker on the Cloudflare account",
		RunE: func(cmd *cobra.Command, args []string) error {
			var a struct {
				Apps []map[string]any `json:"apps"`
			}
			raw, err := api.Get("/api/apps", &a)
			if err != nil {
				return err
			}
			var cf struct {
				Workers    []map[string]any `json:"workers"`
				Error      string           `json:"error"`
				Configured bool             `json:"configured"`
			}
			rawCF, _ := api.Get("/api/apps/cloudflare", &cf)
			if flagJSON {
				fmt.Printf("{\"apps\": %s, \"cloudflare\": %s}\n", raw, firstNonEmpty(string(rawCF), "null"))
				return nil
			}
			rows := [][]string{}
			for _, x := range a.Apps {
				cl, _ := x["cloud"].(map[string]any)
				where := str(x["served_by"])
				if j, _ := x["job"].(map[string]any); j["done"] == false {
					where += " (" + str(j["op"]) + ": " + str(j["step"]) + ")"
				}
				url := "/apps/" + str(x["name"]) + "/"
				if str(x["served_by"]) == "cloudflare" {
					url = str(cl["url"])
				}
				rows = append(rows, []string{str(x["name"]), where, url, clip(str(x["title"]), 30)})
			}
			for _, w := range cf.Workers {
				if str(w["app"]) != "" {
					continue
				}
				rows = append(rows, []string{str(w["name"]), "cloudflare only", str(w["url"]), "(created outside the manager)"})
			}
			table([]string{"NAME", "SERVED BY", "URL", "TITLE"}, rows)
			if cf.Error != "" {
				fmt.Fprintln(os.Stderr, "Cloudflare:", cf.Error)
			}
			return nil
		},
	})
	for _, op := range []struct{ use, short string }{{"upload", "Move an app (files + state) to Cloudflare"},
		{"restore", "Bring an app back from Cloudflare to celld (the worker stays)"}} {
		op := op
		var wait bool
		c := &cobra.Command{
			Use: op.use + " <app>", Short: op.short, Args: cobra.ExactArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				if _, err := api.Post("/api/apps/"+esc(args[0])+"/"+op.use, nil, nil); err != nil {
					return err
				}
				fmt.Printf("%s of %s started\n", op.use, args[0])
				if !wait {
					return nil
				}
				last := ""
				for {
					time.Sleep(2 * time.Second)
					var a struct {
						Apps []map[string]any `json:"apps"`
					}
					if _, err := api.Get("/api/apps", &a); err != nil {
						return err
					}
					for _, x := range a.Apps {
						if str(x["name"]) != args[0] {
							continue
						}
						j, _ := x["job"].(map[string]any)
						if s := str(j["step"]); s != last {
							fmt.Println(" ", s)
							last = s
						}
						if j["done"] == true {
							if e := str(j["error"]); e != "" {
								return fmt.Errorf("%s failed: %s", op.use, e)
							}
							cl, _ := x["cloud"].(map[string]any)
							fmt.Println("done", str(cl["url"]))
							return nil
						}
					}
				}
			},
		}
		c.Flags().BoolVarP(&wait, "wait", "w", true, "follow the progress until it is done")
		apps.AddCommand(c)
	}
	apps.AddCommand(&cobra.Command{
		Use: "rename <app> <new-name>", Short: "Rename a local app (not for apps with a celld server side, not while on Cloudflare)",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return okPost("/api/apps/"+esc(args[0])+"/rename", map[string]string{"to": args[1]}, "renamed %s → %s", args[0], args[1])
		},
	})
	var ayes bool
	adel := &cobra.Command{
		Use: "delete <app>", Short: "Delete an app: its Cloudflare worker, then the folder into apps/.trash/", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !ayes && !confirm("Delete app "+args[0]+" (folder to apps/.trash/, Cloudflare worker deleted)? Type its name", args[0]) {
				return fmt.Errorf("not deleted")
			}
			var r map[string]any
			raw, err := api.Post("/api/apps/"+esc(args[0])+"/delete", nil, &r)
			if err == nil {
				done(raw, "deleted — folder: %s, Cloudflare worker: %s", str(r["trashed"]), str(r["worker"]))
			}
			return err
		},
	}
	adel.Flags().BoolVarP(&ayes, "yes", "y", false, "do not ask")
	apps.AddCommand(adel)
	var wyes bool
	wdel := &cobra.Command{
		Use: "worker-delete <worker>", Short: "Delete a Cloudflare worker created outside the manager (with its data)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !wyes && !confirm("Delete Cloudflare worker "+args[0]+" and its Durable Object data? Type its name", args[0]) {
				return fmt.Errorf("not deleted")
			}
			return okPost("/api/cfworkers/"+esc(args[0])+"/delete", nil, "worker %s deleted", args[0])
		},
	}
	wdel.Flags().BoolVarP(&wyes, "yes", "y", false, "do not ask")
	apps.AddCommand(wdel)

	// ---- projects ----
	proj := &cobra.Command{Use: "projects", Aliases: []string{"project", "p"}, Short: "Shared code projects across instances", PersistentPreRunE: needAPI}
	proj.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Projects with strategy, source and members",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Projects []map[string]any `json:"projects"`
			}
			raw, err := api.Get("/api/projects", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, p := range r.Projects {
				src, _ := p["source"].(map[string]any)
				s := str(src["path"])
				if str(src["type"]) == "katfs" {
					s = "katfs:" + str(src["share"])
				}
				mem := []string{}
				if ms, ok := p["members"].(map[string]any); ok {
					for n, v := range ms {
						vm, _ := v.(map[string]any)
						mem = append(mem, n+"("+str(vm["role"])+")")
					}
				}
				sort.Strings(mem)
				rows = append(rows, []string{str(p["name"]), str(p["strategy"]), s, strings.Join(mem, " ")})
			}
			table([]string{"NAME", "STRATEGY", "SOURCE", "MEMBERS"}, rows)
			return nil
		},
	})
	var pstrat, ppath, pshare, pbase string
	var pmembers []string
	var pmerge bool
	pcreate := &cobra.Command{
		Use: "create <name>", Short: "Create or replace a project",
		Example: `  kaim56 projects create web --path /srv/web --member lead=coder --member writer=helper --strategy worktree`,
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			src := map[string]any{"type": "host", "path": ppath}
			if pshare != "" {
				src = map[string]any{"type": "katfs", "share": pshare}
			}
			members := map[string]any{}
			for _, m := range pmembers {
				role, inst, ok := strings.Cut(m, "=")
				if !ok {
					return fmt.Errorf("--member wants role=instance, not %q", m)
				}
				members[inst] = map[string]string{"role": role}
			}
			body := map[string]any{"name": args[0], "strategy": pstrat, "source": src, "members": members, "lead_may_merge": pmerge}
			if pbase != "" {
				body["base"] = pbase
			}
			var r map[string]any
			raw, err := api.Post("/api/projects", body, &r)
			if err != nil {
				return err
			}
			done(raw, "project %s saved %s", args[0], str(r["warn"]))
			return nil
		},
	}
	pf := pcreate.Flags()
	pf.StringVar(&pstrat, "strategy", "shared", "shared | worktree")
	pf.StringVar(&ppath, "path", "", "host folder (source)")
	pf.StringVar(&pshare, "katfs", "", "katfs share (source, instead of --path)")
	pf.StringVar(&pbase, "base", "", "base branch (worktree)")
	pf.StringArrayVar(&pmembers, "member", nil, "role=instance (lead|writer|reader), repeatable")
	pf.BoolVar(&pmerge, "lead-may-merge", false, "the lead may merge writers' branches")
	proj.AddCommand(pcreate)
	proj.AddCommand(&cobra.Command{
		Use: "status <project>", Short: "Writers' branches: ahead/behind and changed files (worktree)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Members map[string]map[string]any `json:"members"`
			}
			raw, err := api.Get("/api/projects/"+esc(args[0])+"/status", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			for m, s := range r.Members {
				if e := str(s["error"]); e != "" {
					fmt.Printf("%s: %s\n", m, e)
					continue
				}
				fmt.Printf("%s  %s  ahead %s behind %s\n", m, str(s["branch"]), str(s["ahead"]), str(s["behind"]))
				if fs, ok := s["files"].([]any); ok {
					for _, f := range fs {
						ff, _ := f.([]any)
						if len(ff) == 2 {
							fmt.Printf("   %s %s\n", str(ff[0]), str(ff[1]))
						}
					}
				}
			}
			return nil
		},
	})
	proj.AddCommand(&cobra.Command{
		Use: "diff <project> <member>", Short: "A writer's changes as a patch", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := api.req("GET", "/api/projects/"+esc(args[0])+"/diff/"+esc(args[1]), nil, "", api.HTTP.Timeout)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			_, err = io.Copy(os.Stdout, resp.Body)
			return err
		},
	})
	proj.AddCommand(&cobra.Command{
		Use: "merge <project> <member>", Short: "Merge a writer's branch into the project", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r map[string]any
			raw, err := api.Post("/api/projects/"+esc(args[0])+"/merge/"+esc(args[1]), nil, &r)
			if err != nil {
				return err
			}
			done(raw, "merged: %s %s", str(r["commit"]), str(r["note"]))
			return nil
		},
	})
	proj.AddCommand(&cobra.Command{
		Use: "discard <project> <member>", Short: "Throw away a writer's branch", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return okPost("/api/projects/"+esc(args[0])+"/discard/"+esc(args[1]), nil, "discarded %s's changes", args[1])
		},
	})
	proj.AddCommand(&cobra.Command{
		Use: "delete <project>", Aliases: []string{"rm"}, Short: "Delete a project (the source folder stays)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return okPost("/api/projects/"+esc(args[0])+"/delete", nil, "project %s deleted", args[0])
		},
	})

	// ---- models & router ----
	models := &cobra.Command{Use: "models", Short: "Model shortlist, OpenRouter catalog, model router", PersistentPreRunE: needAPI}
	var mall, mtools bool
	ml := &cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "The curated shortlist (--all: the OpenRouter catalog with prices)",
		RunE: func(cmd *cobra.Command, args []string) error {
			if !mall {
				var r struct {
					Curated []string `json:"curated"`
				}
				raw, err := api.Get("/api/models", &r)
				if err != nil {
					return err
				}
				if flagJSON {
					printJSON(raw)
					return nil
				}
				for _, m := range r.Curated {
					fmt.Println(m)
				}
				return nil
			}
			q := "/api/openrouter-models"
			if mtools {
				q += "?tools=1"
			}
			var all []map[string]any
			raw, err := api.Get(q, &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, m := range all {
				rows = append(rows, []string{str(m["id"]), str(m["price"]), str(m["ctx"]), str(m["tools"])})
			}
			table([]string{"MODEL", "PRICE", "CONTEXT", "TOOLS"}, rows)
			return nil
		},
	}
	ml.Flags().BoolVar(&mall, "all", false, "the whole OpenRouter catalog")
	ml.Flags().BoolVar(&mtools, "tools", false, "only models with tool calling (with --all)")
	models.AddCommand(ml)
	models.AddCommand(&cobra.Command{
		Use: "set <model…>", Short: "Replace the curated shortlist", Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/models", map[string]any{"curated": args})
		},
	})
	router := &cobra.Command{Use: "router", Short: "The model router: classifier state, tiers, recent decisions"}
	router.AddCommand(&cobra.Command{
		Use: "status", Short: "Jev classifier, candidates with tiers, recent routed turns",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r map[string]any
			raw, err := api.Get("/api/router?limit=15", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			j, _ := r["jev"].(map[string]any)
			fmt.Printf("classifier: ok=%s ready=%s model=%s %s\n\n", str(j["ok"]), str(j["ready"]), str(j["model"]), str(j["error"]))
			tiers, _ := r["tiers"].(map[string]any)
			rows := [][]string{}
			if cs, ok := r["candidates"].([]any); ok {
				for _, c := range cs {
					cm, _ := c.(map[string]any)
					rows = append(rows, []string{str(cm["key"]), str(tiers[str(cm["key"])]), str(cm["instances"])})
				}
			}
			table([]string{"CANDIDATE", "TIER", "INSTANCES"}, rows)
			if rec, ok := r["recent"].([]any); ok && len(rec) > 0 {
				fmt.Println("\nrecent:")
				for _, x := range rec {
					m, _ := x.(map[string]any)
					fmt.Printf("  %-9s %-14s %s → %s  (%s)\n", ago(m["ts"]), str(m["instance"]), str(m["own"]), str(m["chosen"]), str(m["why"]))
				}
			}
			return nil
		},
	})
	router.AddCommand(&cobra.Command{
		Use: "tier <backend/model> <cheap|strong|code|local|none>", Short: "Give a running model a tier (none = not a routing target)",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Tiers map[string]string `json:"tiers"`
			}
			if _, err := api.Get("/api/router", &r); err != nil {
				return err
			}
			if r.Tiers == nil {
				r.Tiers = map[string]string{}
			}
			if args[1] == "none" {
				delete(r.Tiers, args[0])
			} else {
				r.Tiers[args[0]] = args[1]
			}
			return okPost("/api/router/tiers", map[string]any{"tiers": r.Tiers}, "%s: %s", args[0], args[1])
		},
	})
	router.AddCommand(&cobra.Command{
		Use: "use <instance> <policy|off>", Short: "Route an instance's turns with a policy (default) or turn it off", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			v := args[1]
			if v == "off" {
				v = ""
			}
			return instOp(args[0], "config", map[string]string{"key": "MODEL_ROUTER", "value": v})
		},
	})
	models.AddCommand(router)

	// ---- settings ----
	settings := &cobra.Command{Use: "settings", Short: "Platform settings (API keys, mail, Signal, Cloudflare, …)", PersistentPreRunE: needAPI}
	settings.AddCommand(&cobra.Command{
		Use: "get [KEY]", Short: "Show settings (secrets appear as \"set\")", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var s map[string]any
			raw, err := api.Get("/api/settings", &s)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			keys := make([]string, 0, len(s))
			for k := range s {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				v := str(s[k])
				if v == "__unchanged__" {
					v = "(set)"
				}
				if len(args) == 1 {
					if k == args[0] {
						fmt.Println(v)
					}
					continue
				}
				fmt.Printf("%s=%s\n", k, v)
			}
			return nil
		},
	})
	settings.AddCommand(&cobra.Command{
		Use: "set <KEY> <value>", Short: "Set one setting (for a secret, give - to type it hidden)", Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			v := args[1]
			if v == "-" {
				s, err := readSecret(args[0])
				if err != nil {
					return err
				}
				v = s
			}
			return msgPost("/api/settings", map[string]string{args[0]: v})
		},
	})

	// ---- system ----
	status := &cobra.Command{
		Use: "status", Short: "Manager version, instances' CPU/memory, today's token usage", PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			var v map[string]any
			if _, err := api.Get("/api/version", &v); err != nil {
				return err
			}
			upd := ""
			if v["available"] == true {
				upd = "  (update available: " + str(v["latest"]) + " — kaim56 update run)"
			}
			fmt.Printf("manager %s%s\n\n", str(v["installed"]), upd)
			var r struct {
				Resources []map[string]any `json:"resources"`
			}
			api.Get("/api/resources", &r)
			var u map[string]map[string]map[string]any
			api.Get("/api/usage", &u)
			rows := [][]string{}
			for _, x := range r.Resources {
				st := "off"
				if x["running"] == true {
					st = "running"
				}
				t := u[str(x["name"])]["today"]
				rows = append(rows, []string{str(x["name"]), st, str(x["cpu_pct"]), str(x["rss_mb"]), str(x["mem_mib"]),
					str(t["calls"]), str(t["in"]) + "/" + str(t["out"]), str(t["cost"])})
			}
			table([]string{"INSTANCE", "STATE", "CPU%", "RSS MB", "MEM MiB", "CALLS TODAY", "TOKENS IN/OUT", "COST"}, rows)
			return nil
		},
	}
	var usince int
	usage := &cobra.Command{
		Use: "usage", Short: "Token usage and cost by instance and model", PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			since := time.Now().AddDate(0, 0, -usince).Unix()
			var r struct {
				Rows []map[string]any `json:"rows"`
			}
			raw, err := api.Get(fmt.Sprintf("/api/usage-by-model?since=%d", since), &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			total := 0.0
			for _, x := range r.Rows {
				total += num(x["cost"])
				rows = append(rows, []string{str(x["instance"]), str(x["model"]), str(x["calls"]), str(x["in"]), str(x["out"]), str(x["cost"])})
			}
			fmt.Printf("last %d days — total cost %.2f\n", usince, total)
			table([]string{"INSTANCE", "MODEL", "CALLS", "IN", "OUT", "COST"}, rows)
			return nil
		},
	}
	usage.Flags().IntVar(&usince, "days", 7, "how many days back")
	update := &cobra.Command{Use: "update", Short: "Check for and install manager updates", PersistentPreRunE: needAPI}
	update.AddCommand(&cobra.Command{
		Use: "check", Short: "Is a newer release available? (with its notes)",
		RunE: func(cmd *cobra.Command, args []string) error {
			var v map[string]any
			raw, err := api.Get("/api/version?force=1", &v)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			fmt.Printf("installed %s, latest %s\n", str(v["installed"]), str(v["latest"]))
			if v["available"] == true {
				fmt.Printf("\n%s\n\n→ kaim56 update run\n", strings.TrimSpace(str(v["notes"])))
			} else if e := str(v["error"]); e != "" {
				fmt.Println("check failed:", e)
			} else {
				fmt.Println("up to date")
			}
			return nil
		},
	})
	update.AddCommand(&cobra.Command{
		Use: "run", Short: "Install the latest release (the manager restarts afterwards)",
		RunE: func(cmd *cobra.Command, args []string) error { return msgPost("/api/update", nil) },
	})
	changelog := &cobra.Command{
		Use: "changelog", Short: "The manager's changelog", PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Text string `json:"text"`
			}
			if _, err := api.Get("/api/changelog", &r); err != nil {
				return err
			}
			fmt.Println(r.Text)
			return nil
		},
	}
	var alim int
	audit := &cobra.Command{
		Use: "audit <instance>", Short: "The instance's tool calls (audit log), newest first", Args: cobra.ExactArgs(1), PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Events []map[string]any `json:"events"`
			}
			raw, err := api.Get("/api/audit/"+esc(args[0]), &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for i, e := range r.Events {
				if i >= alim {
					break
				}
				ok := "ok"
				if e["ok"] == false {
					ok = "FAILED " + clip(str(e["err"]), 40)
				}
				rows = append(rows, []string{ago(e["ts"]), str(e["tool"]), clip(str(e["target"]), 60), ok})
			}
			table([]string{"WHEN", "TOOL", "TARGET", "RESULT"}, rows)
			return nil
		},
	}
	audit.Flags().IntVarP(&alim, "limit", "n", 40, "how many")
	return []*cobra.Command{apps, proj, models, settings, status, usage, update, changelog, audit}
}
