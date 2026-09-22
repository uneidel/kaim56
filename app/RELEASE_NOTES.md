# KatAgent release notes

User-facing, one section per version, newest first. The release pipeline
(`.github/workflows/apk.yml`) takes the section of the released version as the
body of the GitHub release, followed by the generated commit list. Write it
BEFORE bumping `versionName` — no section, no release.

## 5.39

- Skills tab: review the skills your agents propose right in the app. Tapping a
  “Skill proposal” notification now jumps straight to it, with Approve / Discard.

## 5.38

- Self-update: the app checks GitHub Releases at start (at most every 6 h),
  downloads a newer version and hands it to the installer. Android asks once
  to allow installs from KatAgent. Settings → App update: switch, Check now.

## 5.37

- Barge-in: talk over the spoken reply and it stops; what you said becomes the
  next input. The microphone stays open while the assistant speaks, its own
  voice is removed on the device (speexdsp echo canceller). Voice turns only,
  never for read-aloud. Settings → Chat → Barge-in (default on).
- Spoken replies play through AudioTrack instead of MediaPlayer.
