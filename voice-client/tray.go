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

// Top-bar icon via StatusNotifierItem (DBus, pure Go — no GTK/cgo).
// GNOME needs the AppIndicator extension for it, KDE does it natively; exactly
// like any other tray program.

// stateIcon draws a 22x22 PNG: a filled circle in the state colour with a
// dark rim. No asset needed, and the state is visible at a glance.
func stateIcon(state string) []byte {
	fill := map[string]color.RGBA{
		stateListening: {0x4c, 0xaf, 0x50, 0xff}, // green
		stateThinking:  {0xff, 0xb3, 0x00, 0xff}, // amber
		stateSpeaking:  {0x42, 0xa5, 0xf5, 0xff}, // blue
		stateOff:       {0x9e, 0x9e, 0x9e, 0xff}, // grey
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

// runTray blocks until "Quit". done is closed when the audio loop dies, so
// the icon does not linger as a corpse.
func runTray(c *VoiceClient, done <-chan struct{}) {
	systray.Run(func() { trayReady(c, done) }, func() { c.Quit() })
}

func trayReady(c *VoiceClient, done <-chan struct{}) {
	systray.SetIcon(stateIcon(stateListening))
	systray.SetTitle("")
	systray.SetTooltip("kAIm56 Voice")

	status := systray.AddMenuItem("…", "")
	status.Disable()
	systray.AddSeparator()
	toggle := systray.AddMenuItem("Listening off", "")
	stop := systray.AddMenuItem("Stop speaking", "")
	fresh := systray.AddMenuItem("New conversation (/reset)", "Sends /reset to the agent")
	instRoot := systray.AddMenuItem("Instance", "Choose the target instance")
	systray.AddSeparator()
	quit := systray.AddMenuItem("Quit", "")

	refresh := func() {
		state, inst, heard, listening := c.State()
		label := fmt.Sprintf("%s — %s", inst, state)
		if heard != "" {
			r := []rune(heard)
			if len(r) > 40 {
				r = r[:40]
			}
			label += fmt.Sprintf("  “%s”", string(r))
		}
		status.SetTitle(label)
		instRoot.SetTitle("Instance: " + inst)
		if listening {
			toggle.SetTitle("Listening off")
		} else {
			toggle.SetTitle("Listening on")
		}
		systray.SetIcon(stateIcon(state))
		systray.SetTooltip("kAIm56 Voice — " + label)
	}
	c.OnState = refresh

	// The instance list once at start; radio behaviour by hand.
	names, err := c.mgr.Instances()
	if err != nil {
		c.notify("Instance list failed", err.Error())
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
