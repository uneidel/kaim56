// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Client: the manager's HTTP API with the admin login (Basic).
type Client struct {
	Base, User, Pass string
	Iroh             bool // through kaim56-tunnel (errors then point at pairing)
	HTTP             *http.Client
}

func newClient(base, user, pass string) *Client {
	return &Client{Base: strings.TrimRight(base, "/"), User: user, Pass: pass,
		HTTP: &http.Client{Timeout: 120 * time.Second}}
}

// APIError: a non-2xx answer, with what to do about it where that is clear.
type APIError struct {
	Status int
	Path   string
	Msg    string
}

func (e *APIError) Error() string {
	hint := ""
	switch e.Status {
	case 401:
		hint = " — login refused: check user/pass (`kaim56 config show`). Ten wrong logins lock this route for 15 min."
	case 429:
		hint = " — locked after too many wrong logins; it lifts 15 min after the last one (or restart the manager)."
	case 404:
		hint = " — not found (unknown name, or an older manager without this endpoint)"
	}
	msg := e.Msg
	if msg == "" {
		msg = http.StatusText(e.Status)
	}
	return fmt.Sprintf("%s: HTTP %d: %s%s", e.Path, e.Status, msg, hint)
}

func (c *Client) req(method, path string, body io.Reader, ctype string, timeout time.Duration) (*http.Response, error) {
	r, err := http.NewRequest(method, c.Base+path, body)
	if err != nil {
		return nil, err
	}
	if c.Pass != "" {
		r.SetBasicAuth(c.User, c.Pass)
	}
	if ctype != "" {
		r.Header.Set("Content-Type", ctype)
	}
	r.Header.Set("User-Agent", "kaim56-cli/"+version)
	hc := c.HTTP
	if timeout != c.HTTP.Timeout {
		hc = &http.Client{Timeout: timeout}
	}
	resp, err := hc.Do(r)
	if err != nil {
		if c.Iroh && (errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || strings.Contains(err.Error(), "connection reset")) {
			id, _ := tunnelID()
			return nil, fmt.Errorf("%s %s: the iroh gateway closed the connection — is this device paired? "+
				"Its id is %s: add it in the manager web UI (iroh) or with `kaim56 iroh add %s` from a paired device", method, path, id, id)
		}
		return nil, fmt.Errorf("%s %s: %w", method, path, err)
	}
	if resp.StatusCode/100 != 2 {
		defer resp.Body.Close()
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, &APIError{Status: resp.StatusCode, Path: path, Msg: errText(b)}
	}
	return resp, nil
}

// errText: the manager answers errors as {"error": …} or {"msg": …}.
func errText(b []byte) string {
	var m map[string]any
	if json.Unmarshal(b, &m) == nil {
		for _, k := range []string{"error", "msg", "message"} {
			if s, ok := m[k].(string); ok && s != "" {
				return s
			}
		}
	}
	s := strings.TrimSpace(string(b))
	if len(s) > 300 {
		s = s[:300] + "…"
	}
	return s
}

// Call: JSON in (nil = no body), JSON out into `out` (nil = ignore). Returns
// the raw body too, for --json.
func (c *Client) Call(method, path string, in any, out any) ([]byte, error) {
	var body io.Reader
	ctype := ""
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return nil, err
		}
		body, ctype = bytes.NewReader(b), "application/json"
	} else if method == "POST" {
		body, ctype = strings.NewReader("{}"), "application/json"
	}
	resp, err := c.req(method, path, body, ctype, c.HTTP.Timeout)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if out != nil && len(bytes.TrimSpace(raw)) > 0 {
		if err := json.Unmarshal(raw, out); err != nil {
			return raw, fmt.Errorf("%s: unexpected answer: %w", path, err)
		}
	}
	// Some handlers answer 200 with {"error": …}; surface it.
	var probe map[string]any
	if json.Unmarshal(raw, &probe) == nil {
		if e, ok := probe["error"].(string); ok && e != "" {
			if okv, has := probe["ok"].(bool); !has || !okv {
				return raw, &APIError{Status: 200, Path: path, Msg: e}
			}
		}
	}
	return raw, nil
}

func (c *Client) Get(path string, out any) ([]byte, error) { return c.Call("GET", path, nil, out) }
func (c *Client) Post(path string, in any, out any) ([]byte, error) {
	return c.Call("POST", path, in, out)
}

// Stream: a request whose answer is read as it arrives (chat). No overall
// timeout — an agent turn may take minutes.
func (c *Client) Stream(method, path string, in any) (*http.Response, error) {
	b, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	return c.req(method, path, bytes.NewReader(b), "application/json", 0)
}
