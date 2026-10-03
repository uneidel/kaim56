# kaim56 CLI release notes

One section per version, newest first. The release pipeline
(`.github/workflows/cli.yml`) uses the section of the released version as the
release body — no section, no release.

## 0.2.0

- `kaim56 self-update` installs the latest release; `--check` only looks.
- After a command, a one-line hint when a newer version exists (checked at most
  once a day, in the background; `KAIM56_NO_UPDATE_CHECK=1` turns it off).

## 0.1.0

- First version: instances, chat (streamed, with recovery), tasks, missions,
  skills, apps, projects, models and router, settings, notifications, memory,
  personas, prompts, playbooks, iroh pairing, MCP, secrets, shell — over iroh
  (tunnel embedded) or a URL.
