// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// Manager spricht die Plattform-APIs: /api/stt, /api/chat/<inst>, /api/tts,
// /api/instances. Alles laeuft mit Basic-Auth; schwer ist hier nichts, das
// Schwere (Parakeet, LLM, Piper) wohnt auf dem Server.
type Manager struct {
	base string
	user string
	pass string
	http *http.Client
}

func NewManager(base, user, pass string) *Manager {
	return &Manager{base: strings.TrimRight(base, "/"), user: user, pass: pass,
		http: &http.Client{Timeout: 10 * time.Minute}}
}

func (m *Manager) req(method, path, ctype string, body []byte) (*http.Response, error) {
	req, err := http.NewRequest(method, m.base+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(m.user, m.pass)
	if ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	resp, err := m.http.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != 200 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 300))
		resp.Body.Close()
		return nil, fmt.Errorf("HTTP %d von %s: %s", resp.StatusCode, path, b)
	}
	return resp, nil
}

// Instances liefert die Namen aller Web-Instanzen (mit denen laesst sich chatten).
func (m *Manager) Instances() ([]string, error) {
	resp, err := m.req("GET", "/api/instances", "", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var arr []struct {
		Name   string `json:"name"`
		Config struct {
			Transport string `json:"TRANSPORT"`
		} `json:"config"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&arr); err != nil {
		return nil, err
	}
	var names []string
	for _, i := range arr {
		if i.Config.Transport == "web" {
			names = append(names, i.Name)
		}
	}
	return names, nil
}

func (m *Manager) STT(wav []byte) (string, error) {
	resp, err := m.req("POST", "/api/stt", "audio/wav", wav)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var out struct {
		Text string `json:"text"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", err
	}
	return strings.TrimSpace(out.Text), nil
}

// ChatStream schickt einen Turn und reicht Tokens durch, sobald sie kommen —
// die Latenz bis zum ersten gesprochenen Satz haengt daran, nicht an der
// Gesamtlaenge der Antwort.
func (m *Manager) ChatStream(instance, message, chatID string, onTok func(string)) (string, error) {
	body, _ := json.Marshal(map[string]string{"message": message, "chat": chatID})
	resp, err := m.req("POST", "/api/chat/"+url.PathEscape(instance),
		"application/json", body)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var all []byte
	buf := make([]byte, 1024)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			all = append(all, buf[:n]...)
			if onTok != nil {
				onTok(string(buf[:n]))
			}
		}
		if err != nil {
			if err == io.EOF {
				return string(all), nil
			}
			return string(all), err
		}
	}
}

// Chat wartet die komplette Antwort ab (Probe, /reset).
func (m *Manager) Chat(instance, message, chatID string) (string, error) {
	return m.ChatStream(instance, message, chatID, nil)
}

func (m *Manager) TTS(text string) ([]byte, error) {
	body, _ := json.Marshal(map[string]string{"text": text})
	resp, err := m.req("POST", "/api/tts", "application/json", body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

// ---- Text fuers Vorlesen ---------------------------------------------------
var (
	reThink = regexp.MustCompile(`(?s)⟦think⟧.*?(⟦/think⟧|$)`)
	reTool  = regexp.MustCompile(`(?m)^[ \t]*🔧.*$`)
	reFence = regexp.MustCompile("(?s)```.*?(```|$)")
	reLink  = regexp.MustCompile(`\[([^\]]*)\]\([^)]*\)`)
	reURL   = regexp.MustCompile(`https?://\S+`)
	reDecor = regexp.MustCompile("[*_`#>|]")
	reSpace = regexp.MustCompile(`[ \t]+`)
	reNL    = regexp.MustCompile(`\n{2,}`)
)

// speakable macht aus dem Antworttext vorlesbaren Text: Denk-Bloecke (auch
// unvollstaendige), Tool-Statuszeilen, Codebloecke und Markdown-Dekor raus.
// Links behalten den Linktext, die URL faellt weg.
func speakable(text string) string {
	text = reThink.ReplaceAllString(text, "")
	text = reTool.ReplaceAllString(text, "")
	text = reFence.ReplaceAllString(text, " Codeblock übersprungen. ")
	text = reLink.ReplaceAllString(text, "$1")
	text = reURL.ReplaceAllString(text, "")
	text = reDecor.ReplaceAllString(text, "")
	text = reSpace.ReplaceAllString(text, " ")
	text = reNL.ReplaceAllString(text, "\n")
	return strings.TrimSpace(text)
}
