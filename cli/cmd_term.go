// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/coder/websocket"
	"github.com/spf13/cobra"
	"golang.org/x/term"
)

// The instance's shell, as in the web UI's terminal: a WebSocket to
// /i/<name>/term/ws — binary frames are PTY output, input goes as binary
// frames, {"type":"resize",…} sets the window size. The manager wants an
// Origin it trusts: the manager's own address.
func init() { commandGroups = append(commandGroups, termCmds) }

func termCmds() []*cobra.Command {
	return []*cobra.Command{{
		Use:               "term [instance]",
		Aliases:           []string{"shell", "ssh"},
		Short:             "Open a shell in an instance (Ctrl-] to leave)",
		Args:              cobra.MaximumNArgs(1),
		PersistentPreRunE: needAPI,
		RunE: func(cmd *cobra.Command, args []string) error {
			inst, err := instanceArg(args, 0)
			if err != nil {
				return err
			}
			return runTerm(inst)
		},
	}}
}

func runTerm(inst string) error {
	if !term.IsTerminal(int(os.Stdin.Fd())) {
		return fmt.Errorf("term needs an interactive terminal")
	}
	base, err := url.Parse(api.Base)
	if err != nil {
		return err
	}
	ws := *base
	ws.Scheme = map[string]string{"https": "wss"}[base.Scheme]
	if ws.Scheme == "" {
		ws.Scheme = "ws"
	}
	ws.Path = strings.TrimRight(base.Path, "/") + "/i/" + url.PathEscape(inst) + "/term/ws"
	h := http.Header{}
	h.Set("Origin", base.Scheme+"://"+base.Hostname())
	if api.Pass != "" {
		r, _ := http.NewRequest("GET", "/", nil)
		r.SetBasicAuth(api.User, api.Pass)
		h.Set("Authorization", r.Header.Get("Authorization"))
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c, resp, err := websocket.Dial(ctx, ws.String(), &websocket.DialOptions{HTTPHeader: h})
	if err != nil {
		if resp != nil && resp.StatusCode == 503 {
			return fmt.Errorf("%s is not running (`kaim56 inst start %s`)", inst, inst)
		}
		if resp != nil {
			return &APIError{Status: resp.StatusCode, Path: ws.Path, Msg: "terminal refused"}
		}
		return err
	}
	c.SetReadLimit(1 << 24)
	defer c.Close(websocket.StatusNormalClosure, "")

	old, err := term.MakeRaw(int(os.Stdin.Fd()))
	if err != nil {
		return err
	}
	defer term.Restore(int(os.Stdin.Fd()), old)

	resize := func() {
		w, hgt, err := term.GetSize(int(os.Stdout.Fd()))
		if err != nil {
			return
		}
		b, _ := json.Marshal(map[string]any{"type": "resize", "cols": w, "rows": hgt})
		c.Write(ctx, websocket.MessageText, b)
	}
	resize()
	winch := make(chan os.Signal, 1)
	signal.Notify(winch, syscall.SIGWINCH)
	defer signal.Stop(winch)
	go func() {
		for range winch {
			resize()
		}
	}()

	go func() { // keyboard -> shell; Ctrl-] (0x1d) leaves
		buf := make([]byte, 4096)
		for {
			n, err := os.Stdin.Read(buf)
			if err != nil {
				cancel()
				return
			}
			for i := 0; i < n; i++ {
				if buf[i] == 0x1d {
					cancel()
					return
				}
			}
			if c.Write(ctx, websocket.MessageBinary, buf[:n]) != nil {
				cancel()
				return
			}
		}
	}()
	for { // shell -> screen
		_, data, err := c.Read(ctx)
		if err != nil {
			term.Restore(int(os.Stdin.Fd()), old)
			fmt.Fprintln(os.Stderr, "\r\n[terminal closed]")
			return nil
		}
		os.Stdout.Write(data)
	}
}
