// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"golang.org/x/term"
)

func init() { commandGroups = append(commandGroups, chatCmds) }

func newTurnID() string {
	b := make([]byte, 6)
	rand.Read(b)
	return hex.EncodeToString(b)
}

var (
	flagThink, flagNoStart bool
	flagImage              string
)

// sendTurn streams one message to the instance and prints the answer. A turn
// id of our own lets us fetch the answer from the trace when the connection
// drops (the agent keeps working).
func sendTurn(inst, msg, imageB64 string) error {
	turn := newTurnID()
	body := map[string]any{"message": msg, "turn": turn}
	if imageB64 != "" {
		body["image"] = imageB64
	}
	path := "/i/" + esc(inst) + "/api/chat/stream"
	resp, err := api.Stream("POST", path, body)
	var ae *APIError
	if errors.As(err, &ae) && ae.Status == 503 && !flagNoStart {
		fmt.Fprintf(os.Stderr, "%s is not running — starting it…\n", inst)
		if err := instOp(inst, "start", nil); err != nil {
			return err
		}
		for i := 0; i < 60; i++ { // the guest bridge needs a moment after boot
			time.Sleep(2 * time.Second)
			if resp, err = api.Stream("POST", path, body); err == nil || !errors.As(err, &ae) || ae.Status != 503 {
				break
			}
		}
	}
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if t := resp.Header.Get("X-Kaim-Turn"); t != "" {
		turn = t
	}
	r := &renderer{w: os.Stdout, showThink: flagThink, color: term.IsTerminal(int(os.Stdout.Fd()))}
	_, cerr := io.Copy(r, resp.Body)
	r.Flush()
	if cerr != nil {
		fmt.Fprintf(os.Stderr, "\n(connection lost: %v — the agent keeps working, fetching its answer…)\n", cerr)
		return recoverTurn(inst, turn)
	}
	if !strings.HasSuffix(r.answer.String(), "\n") {
		fmt.Println()
	}
	return nil
}

// recoverTurn polls the trace until the turn has ended, then prints its answer.
func recoverTurn(inst, turn string) error {
	deadline := time.Now().Add(15 * time.Minute)
	for time.Now().Before(deadline) {
		var t struct {
			Turn *struct {
				TsEnd *float64 `json:"ts_end"`
			} `json:"turn"`
			Answer *string `json:"answer"`
		}
		if _, err := api.Get("/api/trace/"+esc(inst)+"?turn="+turn, &t); err == nil && t.Turn != nil && t.Turn.TsEnd != nil {
			if t.Answer == nil {
				return fmt.Errorf("the turn ended, but no answer was kept (turn %s)", turn)
			}
			fmt.Println(strings.TrimSpace(stripThink(*t.Answer)))
			return nil
		}
		time.Sleep(4 * time.Second)
	}
	return fmt.Errorf("no answer after 15 min (turn %s — `kaim56 trace %s %s`)", turn, inst, turn)
}

func readImage(p string) (string, error) {
	if p == "" {
		return "", nil
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return "", err
	}
	return encodeB64(b), nil
}

func chatCmds() []*cobra.Command {
	chat := &cobra.Command{
		Use:   "chat [instance] [message…]",
		Short: "Chat with an agent: one message, a pipe, or an interactive session",
		Long: `Chat with an agent instance.

  kaim56 chat myassistant "what's on my calendar?"   one message, streamed
  echo "summarize" | kaim56 chat myassistant         message from stdin
  kaim56 chat myassistant                            interactive (/quit to leave)

The instance is started if it is not running (--no-start to refuse).
Reasoning is hidden unless --think; tool calls are shown dimmed. Agent slash
commands (/reset, /model …, /tools, /goal …) are passed through.`,
		Args:              cobra.ArbitraryArgs,
		PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			inst, err := instanceArg(args, 0)
			if err != nil {
				return err
			}
			img, err := readImage(flagImage)
			if err != nil {
				return err
			}
			if len(args) > 1 {
				return sendTurn(inst, strings.Join(args[1:], " "), img)
			}
			if !term.IsTerminal(int(os.Stdin.Fd())) {
				b, err := io.ReadAll(os.Stdin)
				if err != nil {
					return err
				}
				if strings.TrimSpace(string(b)) == "" {
					return fmt.Errorf("empty message")
				}
				return sendTurn(inst, string(b), img)
			}
			fmt.Fprintf(os.Stderr, "chat with %s — /quit to leave, /think to toggle reasoning, /steer <msg> while it works\n", inst)
			in := bufio.NewReader(os.Stdin)
			for {
				fmt.Fprint(os.Stderr, "\x1b[1myou>\x1b[0m ")
				line, err := in.ReadString('\n')
				if err != nil {
					fmt.Println()
					return nil
				}
				line = strings.TrimSpace(line)
				switch {
				case line == "":
					continue
				case line == "/quit" || line == "/exit":
					return nil
				case line == "/think":
					flagThink = !flagThink
					fmt.Fprintln(os.Stderr, "reasoning shown:", flagThink)
					continue
				}
				if err := sendTurn(inst, line, img); err != nil {
					fmt.Fprintln(os.Stderr, "error:", err)
				}
				img = ""
			}
		},
	}
	chat.Flags().BoolVar(&flagThink, "think", false, "show the model's reasoning")
	chat.Flags().BoolVar(&flagNoStart, "no-start", false, "do not start a stopped instance")
	chat.Flags().StringVar(&flagImage, "image", "", "attach a JPEG image to the (first) message")

	steer := &cobra.Command{
		Use: "steer <instance> <message…>", Short: "Add a note to the agent's running turn (it reads it at the next step)",
		Args: cobra.MinimumNArgs(2), PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Queued bool `json:"queued"`
			}
			raw, err := api.Post("/i/"+esc(args[0])+"/api/steer", map[string]string{"message": strings.Join(args[1:], " ")}, &r)
			if err != nil {
				return err
			}
			if r.Queued {
				done(raw, "queued — the agent reads it at its next step")
			} else {
				done(raw, "no turn is running — send it with `kaim56 chat %s …` instead", args[0])
			}
			return nil
		},
	}

	chats := &cobra.Command{Use: "chats", Short: "The shared chat history (as in the app and the web chat)", PersistentPreRunE: needAPI}
	var lim int
	ls := &cobra.Command{
		Use: "list [instance]", Aliases: []string{"ls"}, Short: "Conversations, newest first", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			raw, err := api.Get("/api/chats", &all)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			sortByNum(all, "updatedAt")
			rows := [][]string{}
			for _, c := range all {
				if len(args) == 1 && str(c["instance"]) != args[0] {
					continue
				}
				n := 0
				if m, ok := c["messages"].([]any); ok {
					n = len(m)
				}
				rows = append(rows, []string{str(c["id"]), str(c["instance"]), clip(str(c["title"]), 50), fmt.Sprint(n), ago(c["updatedAt"])})
				if len(rows) >= lim {
					break
				}
			}
			table([]string{"ID", "INSTANCE", "TITLE", "MSGS", "UPDATED"}, rows)
			return nil
		},
	}
	ls.Flags().IntVarP(&lim, "limit", "n", 30, "how many")
	chats.AddCommand(ls)
	chats.AddCommand(&cobra.Command{
		Use: "show <id>", Short: "One conversation's messages", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var all []map[string]any
			if _, err := api.Get("/api/chats", &all); err != nil {
				return err
			}
			for _, c := range all {
				if str(c["id"]) != args[0] {
					continue
				}
				fmt.Printf("# %s  (%s)\n\n", str(c["title"]), str(c["instance"]))
				msgs, _ := c["messages"].([]any)
				for _, m := range msgs {
					mm, _ := m.(map[string]any)
					who := str(c["instance"])
					if mm["user"] == true {
						who = "you"
					}
					fmt.Printf("%s> %s\n\n", who, strings.TrimSpace(stripThink(str(mm["text"]))))
				}
				return nil
			}
			return fmt.Errorf("no chat %q", args[0])
		},
	})

	search := &cobra.Command{
		Use: "search <query…>", Short: "Full-text search over chats and task runs", Args: cobra.MinimumNArgs(1),
		PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			var r struct {
				Hits []map[string]any `json:"hits"`
			}
			body := map[string]any{"q": strings.Join(args, " "), "limit": 20}
			if flagInstance != "" {
				body["instance"] = flagInstance
			}
			raw, err := api.Post("/api/sessions-search", body, &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			rows := [][]string{}
			for _, h := range r.Hits {
				rows = append(rows, []string{str(h["instance"]), str(h["kind"]), str(h["ref"]), ago(h["ts"]), clip(str(h["snippet"]), 80)})
			}
			table([]string{"INSTANCE", "KIND", "REF", "WHEN", "SNIPPET"}, rows)
			return nil
		},
	}

	trace := &cobra.Command{
		Use: "trace <instance> [turn]", Short: "Recent turns of an instance, or one turn in detail (LLM calls, tools, answer)",
		Args: cobra.RangeArgs(1, 2), PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 {
				var r struct {
					Turns []map[string]any `json:"turns"`
				}
				raw, err := api.Get("/api/trace/"+esc(args[0])+"?limit=20", &r)
				if err != nil {
					return err
				}
				if flagJSON {
					printJSON(raw)
					return nil
				}
				rows := [][]string{}
				for _, t := range r.Turns {
					rows = append(rows, []string{str(t["turn"]), str(t["kind"]), ago(t["ts_start"]), str(t["ms"]), str(t["steps"]),
						str(t["llm_calls"]), str(t["in"]) + "/" + str(t["out"]), str(t["cost"]), str(t["outcome"])})
				}
				table([]string{"TURN", "KIND", "STARTED", "MS", "STEPS", "LLM", "TOK IN/OUT", "COST", "OUTCOME"}, rows)
				return nil
			}
			var r map[string]any
			raw, err := api.Get("/api/trace/"+esc(args[0])+"?turn="+esc(args[1]), &r)
			if err != nil {
				return err
			}
			if flagJSON {
				printJSON(raw)
				return nil
			}
			if t, ok := r["turn"].(map[string]any); ok {
				fmt.Printf("turn %s  %s  %s ms  %s steps  outcome %s\n", args[1], ago(t["ts_start"]), str(t["ms"]), str(t["steps"]), str(t["outcome"]))
			} else {
				fmt.Println("turn unknown")
			}
			if rt, ok := r["route"].(map[string]any); ok {
				fmt.Printf("router: %s -> %s (%s)\n", str(rt["own"]), str(rt["chosen"]), str(rt["why"]))
			}
			if l, ok := r["llm"].([]any); ok && len(l) > 0 {
				fmt.Println("\nLLM calls:")
				for _, x := range l {
					m, _ := x.(map[string]any)
					fmt.Printf("  step %-3s %-40s in %-6s out %-6s %s ms %s\n", str(m["step"]), str(m["model"]), str(m["in"]), str(m["out"]), str(m["ms"]), str(m["err"]))
				}
			}
			if l, ok := r["tools"].([]any); ok && len(l) > 0 {
				fmt.Println("\nTools:")
				for _, x := range l {
					m, _ := x.(map[string]any)
					okS := "ok"
					if m["ok"] == false {
						okS = "FAILED " + str(m["err"])
					}
					fmt.Printf("  %-16s %-40s %s\n", str(m["tool"]), clip(str(m["target"]), 40), okS)
				}
			}
			if a := str(r["answer"]); a != "" {
				fmt.Printf("\nAnswer:\n%s\n", strings.TrimSpace(stripThink(a)))
			}
			return nil
		},
	}
	return []*cobra.Command{chat, steer, chats, search, trace}
}
