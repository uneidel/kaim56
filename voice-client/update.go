// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

// Self-update from GitHub Releases — the same idea as the Android app: the
// release pipeline (.github/workflows/voice.yml) publishes every version as
// a release tagged voice-v<version> with the binary attached as
// kaim56-voice-<os>-<arch>. At start (at most every 6 h, or always with
// --update) the newest such release is looked up; a newer one is downloaded
// next to the running binary, checked against the asset size, renamed over
// it and the process re-executes itself. Linux allows replacing a running
// executable, so no installer step is needed. Off with "auto_update": false
// in the config; the tray menu has "Check for update" as well.

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	updateRepo  = "uneidel/kaim56"
	updateEvery = 6 * time.Hour
)

var (
	releasesURL = "https://api.github.com/repos/" + updateRepo + "/releases?per_page=20"
	updateHTTP  = &http.Client{Timeout: 90 * time.Second}
	updateTagRe = regexp.MustCompile(`^voice-v(\d+(?:\.\d+)*)$`)
)

type release struct {
	Version string
	URL     string
	Size    int64
}

// assetName is the release asset this build looks for.
func assetName() string {
	return "kaim56-voice-" + runtime.GOOS + "-" + runtime.GOARCH
}

// newerVersion: is a newer than b? Dotted numbers, compared component-wise
// ("1.10" > "1.9"; a missing component counts as 0).
func newerVersion(a, b string) bool {
	as, bs := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < len(as) || i < len(bs); i++ {
		var x, y int
		if i < len(as) {
			x, _ = strconv.Atoi(as[i])
		}
		if i < len(bs) {
			y, _ = strconv.Atoi(bs[i])
		}
		if x != y {
			return x > y
		}
	}
	return false
}

// latestRelease returns the newest voice-v* release that carries the asset
// for this platform, or nil when there is none.
func latestRelease(url string) (*release, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "kaim56-voice/"+version)
	resp, err := updateHTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d from the releases API", resp.StatusCode)
	}
	var arr []struct {
		Tag    string `json:"tag_name"`
		Draft  bool   `json:"draft"`
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
			Size int64  `json:"size"`
		} `json:"assets"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(&arr); err != nil {
		return nil, err
	}
	var best *release
	want := assetName()
	for _, r := range arr {
		m := updateTagRe.FindStringSubmatch(r.Tag)
		if m == nil || r.Draft {
			continue
		}
		for _, a := range r.Assets {
			if a.Name != want {
				continue
			}
			if best == nil || newerVersion(m[1], best.Version) {
				best = &release{Version: m[1], URL: a.URL, Size: a.Size}
			}
		}
	}
	return best, nil
}

// stampPath: when the last check ran (mtime), so a start does not hit
// GitHub every time.
func stampPath() string {
	cache, err := os.UserCacheDir()
	if err != nil {
		cache = os.TempDir()
	}
	return filepath.Join(cache, "kaim56-voice", "update-check")
}

func checkDue(stamp string, now time.Time) bool {
	st, err := os.Stat(stamp)
	return err != nil || now.Sub(st.ModTime()) >= updateEvery
}

func touchStamp(stamp string) {
	os.MkdirAll(filepath.Dir(stamp), 0o755)
	if f, err := os.OpenFile(stamp, os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		f.Close()
	}
	now := time.Now()
	os.Chtimes(stamp, now, now)
}

// applyUpdate downloads the release binary next to exe and renames it over
// exe. Written as <exe>.new first and checked against the asset size, so a
// broken download never replaces a working binary; the rename is atomic and
// the running process keeps its old inode.
func applyUpdate(rel *release, exe string) error {
	req, err := http.NewRequest("GET", rel.URL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "kaim56-voice/"+version)
	resp, err := updateHTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d downloading %s", resp.StatusCode, rel.URL)
	}
	tmp := exe + ".new"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return fmt.Errorf("%w — is the directory of the binary writable?", err)
	}
	n, err := io.Copy(f, resp.Body)
	f.Close()
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if rel.Size > 0 && n != rel.Size {
		os.Remove(tmp)
		return fmt.Errorf("download incomplete: %d of %d bytes", n, rel.Size)
	}
	if err := os.Chmod(tmp, 0o755); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, exe); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// selfUpdate checks for a newer release and installs it. force skips the
// 6-hour stamp. Returns (installed, message); installed = true means the
// binary on disk is the new one and the caller should relaunch.
func selfUpdate(force bool) (bool, string) {
	stamp := stampPath()
	if !force && !checkDue(stamp, time.Now()) {
		return false, ""
	}
	touchStamp(stamp)
	rel, err := latestRelease(releasesURL)
	if err != nil {
		return false, "update check failed: " + err.Error()
	}
	if rel == nil || !newerVersion(rel.Version, version) {
		return false, "up to date (" + version + ")"
	}
	exe, err := os.Executable()
	if err != nil {
		return false, "update: " + err.Error()
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return false, "update: " + err.Error()
	}
	if err := applyUpdate(rel, exe); err != nil {
		return false, fmt.Sprintf("update to %s failed: %v", rel.Version, err)
	}
	return true, fmt.Sprintf("updated %s -> %s, restarting", version, rel.Version)
}

// relaunch replaces this process with the (updated) binary, same arguments.
func relaunch() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	return syscall.Exec(exe, os.Args, os.Environ())
}
