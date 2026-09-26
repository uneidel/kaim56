# KatAgent release notes

User-facing, one section per version, newest first. The release pipeline
(`.github/workflows/apk.yml`) takes the section of the released version as the
body of the GitHub release, followed by the generated commit list. Write it
BEFORE bumping `versionName` — no section, no release.

## 5.43

- Notifications screen (drawer → Notifications): review everything the agents
  sent, newest first, unread highlighted. Tap one to jump to its chat, tasks,
  missions or skills. Opening the list marks all as read.

## 5.42

- Notifications now reach the phone while the app is closed: a background
  check every 15 minutes (Android WorkManager, no permanent icon), and what
  arrived in between is shown right when the app opens. Before, only
  notifications created while the app was open were ever shown.

## 5.41

- New slash command /compact <focus>: fold the conversation into a short
  summary to save context; add a focus to keep detail on what matters.

## 5.40

- Tapping a notification from an agent now always opens that agent's chat,
  even if you had never opened it before (e.g. the daily job-search briefing).

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
