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

// Tasks, missions, skills, notifications.
func init() { commandGroups = append(commandGroups, workCmds) }

func workCmds() []*cobra.Command {
	// ---- tasks ----
	tasks := &cobra.Command{Use: "tasks", Aliases: []string{"task", "t"}, Short: "Background and scheduled tasks", PersistentPreRunE: needAPI}
	tasks.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "All tasks",
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			raw, err := api.Get("/api/tasks", &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			sortByNum(all, "updated")
			rows := [][]string{}
			for _, t := range all {
				rows = append(rows, []string{str(t["id"]), str(t["instance"]), str(t["status"]), str(t["schedule"]),
					ago(t["updated"]), clip(str(t["message"]), 60)})
			}
			table([]string{"ID", "INSTANCE", "STATUS", "SCHEDULE", "UPDATED", "MESSAGE"}, rows)
			return nil
		},
	})
	tasks.AddCommand(&cobra.Command{
		Use: "show <id>", Short: "One task with its last result", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			if _, err := api.Get("/api/tasks", &all); err != nil {
				return err
			}
			for _, t := range all {
				if str(t["id"]) == args[0] {
					fmt.Printf("task %s on %s — %s %s\n\n%s\n\n--- result (%s) ---\n%s\n", args[0], str(t["instance"]), str(t["status"]),
						str(t["schedule"]), str(t["message"]), ago(t["updated"]), strings.TrimSpace(str(t["result"])))
					return nil
				}
			}
			return fmt.Errorf("no task %q", args[0])
		},
	})
	var sched, tmodel string
	var steps int
	add := &cobra.Command{
		Use:   "add <instance|ephemeral> <message…>",
		Short: "Create a task: one-off in the background, or scheduled (--every 2h, --at \"daily 07:30\")",
		Example: `  kaim56 tasks add jobresearcher "search new CISO jobs" --at "daily 07:00"
  kaim56 tasks add ephemeral "summarize https://…" --model google/gemini-2.5-flash --steps 10`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{"instance": args[0], "message": strings.Join(args[1:], " "), "schedule": sched}
			if tmodel != "" {
				body["model"] = tmodel
			}
			if steps > 0 {
				body["max_steps"] = steps
			}
			return msgPost("/api/tasks", body)
		},
	}
	add.Flags().StringVar(&sched, "at", "", `schedule: "every 30m|2h|1d", "daily HH:MM", "hourly" (empty = once, now)`)
	add.Flags().StringVar(&sched, "every", "", "shorthand for --at \"every …\" (e.g. 2h)")
	add.Flags().StringVar(&tmodel, "model", "", "model (ephemeral tasks only)")
	add.Flags().IntVar(&steps, "steps", 0, "max tool steps 1-50 (ephemeral tasks only)")
	add.PreRun = func(cmd *cobra.Command, args []string) {
		if cmd.Flags().Changed("every") && !strings.HasPrefix(sched, "every") {
			sched = "every " + sched
		}
	}
	tasks.AddCommand(add)
	var newMsg, newSched, newInst string
	edit := &cobra.Command{
		Use: "edit <id>", Short: "Change a task's message, schedule or instance", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("message") {
				body["message"] = newMsg
			}
			if cmd.Flags().Changed("at") {
				body["schedule"] = newSched
			}
			if cmd.Flags().Changed("instance") {
				body["instance"] = newInst
			}
			if len(body) == 0 {
				return fmt.Errorf("nothing to change (--message, --at, --instance)")
			}
			return msgPost("/api/tasks/"+esc(args[0])+"/update", body)
		},
	}
	edit.Flags().StringVar(&newMsg, "message", "", "new message")
	edit.Flags().StringVar(&newSched, "at", "", "new schedule (\"\" = one-off)")
	edit.Flags().StringVar(&newInst, "instance", "", "move to another instance")
	tasks.AddCommand(edit)
	tasks.AddCommand(&cobra.Command{
		Use: "run <id>", Short: "Run a task now", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return msgPost("/api/tasks/"+esc(args[0])+"/run", nil) },
	})
	tasks.AddCommand(&cobra.Command{
		Use: "delete <id>", Aliases: []string{"rm"}, Short: "Delete a task", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Deleted bool `json:"deleted"`
			}
			raw, err := api.Post("/api/task-delete", map[string]string{"id": args[0]}, &r)
			if err != nil {
				return err
			}
			if !r.Deleted {
				return fmt.Errorf("no task %q", args[0])
			}
			done(raw, "task %s deleted", args[0])
			return nil
		},
	})
	var hq string
	hist := &cobra.Command{
		Use: "history", Short: "Past task runs (newest first)",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Rows []map[string]any `json:"rows"`
			}
			raw, err := api.Get("/api/history?limit=50&q="+esc(hq), &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, x := range r.Rows {
				ok := "ok"
				if num(x["ok"]) == 0 {
					ok = "FAILED"
				}
				rows = append(rows, []string{ago(x["ts"]), str(x["target"]), ok, clip(str(x["task"]), 40), clip(str(x["result"]), 60)})
			}
			table([]string{"WHEN", "INSTANCE", "", "TASK", "RESULT"}, rows)
			return nil
		},
	}
	hist.Flags().StringVarP(&hq, "query", "q", "", "filter")
	tasks.AddCommand(hist)

	// ---- missions ----
	missions := &cobra.Command{Use: "missions", Aliases: []string{"mission", "m"}, Short: "Multi-step missions of the agents", PersistentPreRunE: needAPI}
	missions.AddCommand(&cobra.Command{
		Use: "list [instance]", Aliases: []string{"ls"}, Short: "Missions with their progress", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				ByInstance map[string][]map[string]any `json:"by_instance"`
			}
			raw, err := api.Get("/api/missions", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			insts := make([]string, 0, len(r.ByInstance))
			for k := range r.ByInstance {
				insts = append(insts, k)
			}
			sort.Strings(insts)
			for _, inst := range insts {
				if len(args) == 1 && inst != args[0] {
					continue
				}
				for _, m := range r.ByInstance[inst] {
					steps, _ := m["steps"].([]any)
					d := 0
					for _, s := range steps {
						if sm, _ := s.(map[string]any); str(sm["status"]) == "done" {
							d++
						}
					}
					rows = append(rows, []string{str(m["id"]), inst, str(m["status"]), fmt.Sprintf("%d/%d", d, len(steps)), ago(m["updated"]), clip(str(m["goal"]), 60)})
				}
			}
			table([]string{"ID", "INSTANCE", "STATUS", "STEPS", "UPDATED", "GOAL"}, rows)
			return nil
		},
	})
	missions.AddCommand(&cobra.Command{
		Use: "show <id>", Short: "A mission's steps and log", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				ByInstance map[string][]map[string]any `json:"by_instance"`
			}
			if _, err := api.Get("/api/missions", &r); err != nil {
				return err
			}
			for inst, ms := range r.ByInstance {
				for _, m := range ms {
					if str(m["id"]) != args[0] {
						continue
					}
					fmt.Printf("%s on %s — %s\n%s\n\n", args[0], inst, str(m["status"]), str(m["goal"]))
					steps, _ := m["steps"].([]any)
					for _, s := range steps {
						sm, _ := s.(map[string]any)
						fmt.Printf("  %s. [%s] %s\n", str(sm["n"]), str(sm["status"]), str(sm["text"]))
						if res := str(sm["result"]); res != "" {
							fmt.Printf("       → %s\n", clip(res, 140))
						}
					}
					if lg, ok := m["log"].([]any); ok && len(lg) > 0 {
						fmt.Println("\nlog:")
						for _, l := range lg {
							fmt.Println(" ", str(l))
						}
					}
					if s := str(m["summary"]); s != "" {
						fmt.Println("\nsummary:", s)
					}
					return nil
				}
			}
			return fmt.Errorf("no mission %q", args[0])
		},
	})
	for _, a := range []string{"pause", "resume", "abort", "delete"} {
		a := a
		missions.AddCommand(&cobra.Command{
			Use: a + " <id>", Short: strings.ToUpper(a[:1]) + a[1:] + " a mission", Args: cobra.ExactArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				var r struct {
					Msg string `json:"msg"`
				}
				raw, err := api.Post("/api/mission-admin", map[string]string{"action": a, "id": args[0]}, &r)
				if err != nil {
					return err
				}
				if r.Msg != "ok" {
					return fmt.Errorf("the manager said: %s", r.Msg)
				}
				done(raw, "mission %s: %s", args[0], a)
				return nil
			},
		})
	}

	// ---- skills ----
	skills := &cobra.Command{Use: "skills", Aliases: []string{"skill"}, Short: "Skills the agents can load, and proposals to review", PersistentPreRunE: needAPI}
	skills.AddCommand(&cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "All skills with usage verdicts (30 days)",
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			raw, err := api.Get("/api/skills?meta=1", &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			var st struct {
				Skills map[string]map[string]any `json:"skills"`
			}
			api.Get("/api/skill-stats", &st)
			rows := [][]string{}
			for _, s := range all {
				n := str(s["name"])
				x := st.Skills[n]
				rows = append(rows, []string{n, str(x["verdict"]), str(x["uses"]), clip(str(s["description"]), 70)})
			}
			table([]string{"NAME", "VERDICT", "USES", "DESCRIPTION"}, rows)
			return nil
		},
	})
	skills.AddCommand(&cobra.Command{
		Use: "show <name>", Short: "A skill's content", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := api.req("GET", "/api/skills/"+esc(args[0]), nil, "", api.HTTP.Timeout)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			_, err = os.Stdout.ReadFrom(resp.Body)
			fmt.Println()
			return err
		},
	})
	var sdesc, sfile string
	sadd := &cobra.Command{
		Use: "save <name>", Short: "Create or replace a skill (content from --file or stdin)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var b []byte
			var err error
			if sfile != "" {
				b, err = os.ReadFile(sfile)
			} else {
				b, err = readAllStdin()
			}
			if err != nil {
				return err
			}
			return msgPost("/api/skills", map[string]string{"name": args[0], "description": sdesc, "content": string(b)})
		},
	}
	sadd.Flags().StringVarP(&sdesc, "description", "d", "", "one line: when to use it")
	sadd.Flags().StringVarP(&sfile, "file", "f", "", "content file (default stdin)")
	skills.AddCommand(sadd)
	skills.AddCommand(&cobra.Command{
		Use: "delete <name>", Aliases: []string{"rm"}, Short: "Delete a skill", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/skills/"+esc(args[0])+"/delete", nil)
		},
	})
	skills.AddCommand(&cobra.Command{
		Use: "proposals", Short: "Skills the agents proposed, waiting for review",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Proposals []map[string]any `json:"proposals"`
			}
			raw, err := api.Get("/api/skill-proposals", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, p := range r.Proposals {
				kind := "new"
				if p["update"] == true {
					kind = "update"
				}
				rows = append(rows, []string{str(p["id"]), str(p["name"]), kind, str(p["instance"]), ago(p["ts"]), clip(str(p["description"]), 60)})
			}
			table([]string{"ID", "NAME", "KIND", "FROM", "WHEN", "DESCRIPTION"}, rows)
			return nil
		},
	})
	skills.AddCommand(&cobra.Command{
		Use: "approve <proposal-id>", Short: "Accept a proposed skill", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/skill-proposals/"+esc(args[0])+"/approve", nil)
		},
	})
	skills.AddCommand(&cobra.Command{
		Use: "discard <proposal-id> [reason…]", Short: "Reject a proposed skill (the reason is remembered)", Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return msgPost("/api/skill-proposals/"+esc(args[0])+"/discard", map[string]string{"reason": strings.Join(args[1:], " ")})
		},
	})

	// ---- notifications ----
	notif := &cobra.Command{Use: "notifications", Aliases: []string{"notif", "n"}, Short: "What the agents reported", PersistentPreRunE: needAPI}
	var unreadOnly bool
	nl := &cobra.Command{
		Use: "list", Aliases: []string{"ls"}, Short: "Notifications, newest first",
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Notifications []map[string]any `json:"notifications"`
				Unread        int              `json:"unread"`
			}
			raw, err := api.Get("/api/notifications", &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			sortByNum(r.Notifications, "ts")
			rows := [][]string{}
			for _, n := range r.Notifications {
				if unreadOnly && n["read"] == true {
					continue
				}
				mark := " "
				if n["read"] != true {
					mark = "•"
				}
				rows = append(rows, []string{mark, str(n["id"]), ago(n["ts"]), str(n["instance"]), clip(str(n["title"])+" — "+str(n["body"]), 80)})
			}
			fmt.Printf("%d unread\n", r.Unread)
			table([]string{"", "ID", "WHEN", "FROM", "TEXT"}, rows)
			return nil
		},
	}
	nl.Flags().BoolVarP(&unreadOnly, "unread", "u", false, "unread only")
	notif.AddCommand(nl)
	notif.AddCommand(&cobra.Command{
		Use: "read [id]", Short: "Mark one notification (or all) as read", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{"all": true}
			if len(args) == 1 {
				body = map[string]any{"id": args[0]}
			}
			var r struct {
				Marked int `json:"marked"`
			}
			raw, err := api.Post("/api/notifications/read", body, &r)
			if err == nil {
				done(raw, "%d marked as read", r.Marked)
			}
			return err
		},
	})
	notif.AddCommand(&cobra.Command{
		Use: "clear", Short: "Delete all notifications",
		RunE: func(cmd *cobra.Command, args []string) error {
			raw, err := api.Post("/api/notifications/read", map[string]any{"clear": true}, nil)
			if err == nil {
				done(raw, "cleared")
			}
			return err
		},
	})
	notif.AddCommand(&cobra.Command{
		Use: "watch", Short: "Print new notifications as they arrive (Ctrl-C to stop)",
		RunE: func(cmd *cobra.Command, args []string) error {
			seen := map[string]bool{}
			rev := 0.0
			first := true
			old := api.HTTP.Timeout
			defer func() { api.HTTP.Timeout = old }()
			api.HTTP.Timeout = old + 40e9
			for {
				var r struct {
					Rev           float64          `json:"rev"`
					Notifications []map[string]any `json:"notifications"`
				}
				if _, err := api.Get(fmt.Sprintf("/api/notifications?since=%d&wait=25", int64(rev)), &r); err != nil {
					return err
				}
				rev = r.Rev
				sortByNum(r.Notifications, "ts")
				for i := len(r.Notifications) - 1; i >= 0; i-- {
					n := r.Notifications[i]
					id := str(n["id"])
					if seen[id] {
						continue
					}
					seen[id] = true
					if !first {
						fmt.Printf("[%s] %s: %s — %s\n", str(n["instance"]), ago(n["ts"]), str(n["title"]), str(n["body"]))
					}
				}
				first = false
			}
		},
	})
	return []*cobra.Command{tasks, missions, skills, notif}
}
