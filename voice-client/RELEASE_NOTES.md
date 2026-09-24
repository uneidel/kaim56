# kAIm56 Voice release notes

User-facing, one section per version, newest first. The release pipeline
(`.github/workflows/voice.yml`) takes the section of the released version as
the body of the GitHub release. Write it BEFORE bumping `version.go` — no
section, no release.

## 1.1.0

- Self-update: the client checks GitHub Releases at start (at most every 6 h),
  downloads a newer version over itself and restarts. `"auto_update": false`
  in the config switches it off; `--update` checks right now; the top-bar
  menu has "Check for update"; `--version` prints the version.
- Everything the client says or prints is English now (menu, errors, help,
  the spoken "Yes?" acknowledgement).
