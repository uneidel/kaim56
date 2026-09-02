// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"fmt"
	"image"
	"image/color"
	"image/png"

	"fyne.io/systray"
)

// Topbar-Icon ueber StatusNotifierItem (DBus, pures Go — kein GTK/cgo).
// GNOME braucht dafuer die AppIndicator-Extension, KDE kann es nativ; genau
// wie bei jedem anderen Tray-Programm.

// stateIcon malt ein 22x22-PNG: gefuellter Kreis in der Zustandsfarbe mit
// dunklem Rand. Kein Asset noetig, und der Zustand ist auf einen Blick da.
func stateIcon(state string) []byte {
	fill := map[string]color.RGBA{
		"hört":    {0x4c, 0xaf, 0x50, 0xff}, // gruen
		"denkt":   {0xff, 0xb3, 0x00, 0xff}, // bernstein
		"spricht": {0x42, 0xa5, 0xf5, 0xff}, // blau
		"aus":     {0x9e, 0x9e, 0x9e, 0xff}, // grau
	}[state]
	if fill.A == 0 {
		fill = color.RGBA{0x9e, 0x9e, 0x9e, 0xff}
	}
	const s, cx, cy, r = 22, 11.0, 11.0, 8.5
	img := image.NewRGBA(image.Rect(0, 0, s, s))
	for y := 0; y < s; y++ {
		for x := 0; x < s; x++ {
			dx, dy := float64(x)+0.5-cx, float64(y)+0.5-cy
			d := dx*dx + dy*dy
			switch {
			case d <= (r-1.5)*(r-1.5):
				img.Set(x, y, fill)
			case d <= r*r:
				img.Set(x, y, color.RGBA{0x21, 0x21, 0x21, 0xff})
			}
		}
	}
	var buf bytes.Buffer
	png.Encode(&buf, img)
	return buf.Bytes()
}

// runTray blockiert bis "Beenden". done wird geschlossen, wenn die
// Audioschleife stirbt, damit das Icon nicht als Leiche haengenbleibt.
func runTray(c *VoiceClient, done <-chan struct{}) {
	systray.Run(func() { trayReady(c, done) }, func() { c.Quit() })
}

func trayReady(c *VoiceClient, done <-chan struct{}) {
	systray.SetIcon(stateIcon("hört"))
	systray.SetTitle("")
	systray.SetTooltip("kAIm56 Voice")

	status := systray.AddMenuItem("…", "")
	status.Disable()
	systray.AddSeparator()
	toggle := systray.AddMenuItem("Hören aus", "")
	stop := systray.AddMenuItem("Sprechen stoppen", "")
	fresh := systray.AddMenuItem("Neues Gespräch (/reset)", "Schickt /reset an den Agenten")
	instRoot := systray.AddMenuItem("Instanz", "Zielinstanz waehlen")
	systray.AddSeparator()
	quit := systray.AddMenuItem("Beenden", "")

	refresh := func() {
		state, inst, heard, listening := c.State()
		label := fmt.Sprintf("%s — %s", inst, state)
		if heard != "" {
			r := []rune(heard)
			if len(r) > 40 {
				r = r[:40]
			}
			label += fmt.Sprintf("  „%s“", string(r))
		}
		status.SetTitle(label)
		instRoot.SetTitle("Instanz: " + inst)
		if listening {
			toggle.SetTitle("Hören aus")
		} else {
			toggle.SetTitle("Hören an")
		}
		systray.SetIcon(stateIcon(state))
		systray.SetTooltip("kAIm56 Voice — " + label)
	}
	c.OnState = refresh

	// Instanzliste einmal beim Start; Radio-Verhalten von Hand.
	names, err := c.mgr.Instances()
	if err != nil {
		c.notify("Instanzliste fehlgeschlagen", err.Error())
	}
	if len(names) == 0 {
		_, inst, _, _ := c.State()
		names = []string{inst}
	}
	items := make([]*systray.MenuItem, len(names))
	for i, n := range names {
		_, inst, _, _ := c.State()
		items[i] = instRoot.AddSubMenuItemCheckbox(n, "", n == inst)
		go func(idx int, name string) {
			for range items[idx].ClickedCh {
				c.SetInstance(name)
				for j, it := range items {
					if j == idx {
						it.Check()
					} else {
						it.Uncheck()
					}
				}
			}
		}(i, n)
	}
	refresh()

	go func() {
		for {
			select {
			case <-toggle.ClickedCh:
				c.ToggleListening()
			case <-stop.ClickedCh:
				c.StopSpeaking()
			case <-fresh.ClickedCh:
				c.NewConversation()
			case <-quit.ClickedCh:
				systray.Quit()
				return
			case <-done:
				systray.Quit()
				return
			}
		}
	}()
}
