// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

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
	"time"

	"github.com/spf13/cobra"
	"golang.org/x/term"
)

// Updates from GitHub Releases cli-v<version> (asset kaim56-<os>-<arch>):
// `kaim56 self-update` installs; otherwise the CLI only hints. The hint costs
// no time: the check runs at most once a day, in the background, and its
// result (the latest version) is kept in the cache for the next runs.
const updateRepo = "uneidel/kaim56"

var (
	releasesURL = "https://api.github.com/repos/" + updateRepo + "/releases?per_page=30"
	updateHTTP  = &http.Client{Timeout: 90 * time.Second}
	updateTagRe = regexp.MustCompile(`^cli-v(\d+(?:\.\d+)*)$`)
	checkEvery  = 24 * time.Hour
)

type release struct {
	Version, URL string
	Size         int64
}

func assetName() string { return "kaim56-" + runtime.GOOS + "-" + runtime.GOARCH }

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

func latestRelease(url string, client *http.Client) (*release, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "kaim56-cli/"+version)
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d from the GitHub releases API", resp.StatusCode)
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
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&arr); err != nil {
		return nil, err
	}
	var best *release
	for _, r := range arr {
		m := updateTagRe.FindStringSubmatch(r.Tag)
		if m == nil || r.Draft {
			continue
		}
		for _, a := range r.Assets {
			if a.Name == assetName() && (best == nil || newerVersion(m[1], best.Version)) {
				best = &release{Version: m[1], URL: a.URL, Size: a.Size}
			}
		}
	}
	return best, nil
}

// applyUpdate: download next to the binary, check the size, rename over it
// (atomic; a broken download never replaces a working binary).
func applyUpdate(rel *release, exe string) error {
	req, err := http.NewRequest("GET", rel.URL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "kaim56-cli/"+version)
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
		return fmt.Errorf("%w — is %s writable?", err, filepath.Dir(exe))
	}
	n, err := io.Copy(f, resp.Body)
	f.Close()
	if err == nil && rel.Size > 0 && n != rel.Size {
		err = fmt.Errorf("download incomplete: %d of %d bytes", n, rel.Size)
	}
	if err == nil {
		err = os.Chmod(tmp, 0o755)
	}
	if err == nil {
		err = os.Rename(tmp, exe)
	}
	if err != nil {
		os.Remove(tmp)
	}
	return err
}

// ---- the hint ----------------------------------------------------------------
func hintFile() string {
	cache, err := os.UserCacheDir()
	if err != nil {
		cache = os.TempDir()
	}
	return filepath.Join(cache, "kaim56-cli", "latest")
}

// knownLatest: the version the last check found ("" = none), and whether a new check is due.
func knownLatest(path string, now time.Time) (string, bool) {
	st, err := os.Stat(path)
	if err != nil {
		return "", true
	}
	b, _ := os.ReadFile(path)
	return strings.TrimSpace(string(b)), now.Sub(st.ModTime()) >= checkEvery
}

func rememberLatest(path, v string) {
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path, []byte(v+"\n"), 0o644)
}

// startUpdateHint: kicks off the daily check in the background; the returned
// func prints the hint (to stderr, only on a terminal) after the command.
func startUpdateHint(skip bool) func() {
	if skip || os.Getenv("KAIM56_NO_UPDATE_CHECK") != "" || !term.IsTerminal(int(os.Stderr.Fd())) {
		return func() {}
	}
	path := hintFile()
	latest, due := knownLatest(path, time.Now())
	done := make(chan string, 1)
	if due {
		go func() {
			r, err := latestRelease(releasesURL, &http.Client{Timeout: 5 * time.Second})
			v := latest
			if err == nil {
				v = ""
				if r != nil {
					v = r.Version
				}
				rememberLatest(path, v)
			}
			done <- v
		}()
	} else {
		done <- latest
	}
	return func() {
		select {
		case v := <-done:
			latest = v
		case <-time.After(300 * time.Millisecond): // never hold a command up; next run knows
		}
		if latest != "" && newerVersion(latest, version) {
			fmt.Fprintf(os.Stderr, "\nkaim56 %s is available (you have %s) — run `kaim56 self-update`\n", latest, version)
		}
	}
}

func selfUpdateCmd() *cobra.Command {
	var check bool
	c := &cobra.Command{
		Use:   "self-update",
		Short: "Install the latest kaim56 release (GitHub, cli-v*)",
		RunE: func(cmd *cobra.Command, args []string) error {
			rel, err := latestRelease(releasesURL, updateHTTP)
			if err != nil {
				return fmt.Errorf("update check failed: %w", err)
			}
			if rel != nil {
				rememberLatest(hintFile(), rel.Version)
			}
			if rel == nil || !newerVersion(rel.Version, version) {
				fmt.Printf("kaim56 %s is the latest\n", version)
				return nil
			}
			if check {
				fmt.Printf("kaim56 %s is available (you have %s)\n", rel.Version, version)
				return nil
			}
			exe, err := os.Executable()
			if err != nil {
				return err
			}
			if exe, err = filepath.EvalSymlinks(exe); err != nil {
				return err
			}
			if err := applyUpdate(rel, exe); err != nil {
				return err
			}
			fmt.Printf("updated %s → %s (%s)\n", version, rel.Version, exe)
			return nil
		},
	}
	c.Flags().BoolVar(&check, "check", false, "only check, do not install")
	return c
}
