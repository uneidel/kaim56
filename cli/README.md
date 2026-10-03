# kaim56 — command line for the kAIm56 manager

One static binary (Go + Cobra) that does from a terminal what the web UI and
the app do: instances, chat, tasks, missions, skills, apps, projects, models
and the router, settings, notifications, memory, personas, prompts, playbooks,
iroh pairing, MCP catalog, secrets, the instance shell.

## Connect

    kaim56 config init      # gateway node id (iroh) or https:// URL, user, password

- **iroh** (recommended, no open port): the binary embeds `kaim56-tunnel`
  (from `iroh-gw/`) and starts it itself. The device identity is
  `~/.config/kaim56-tunnel.key` — the same one the voice client uses, so a
  desktop paired for voice works at once. A new device: `kaim56 config id`,
  then add that id in the web UI (iroh) or with `kaim56 iroh add <id>` from a
  paired device. If the voice client's tunnel already runs, it is reused.
- **direct**: `base_url` = the manager's URL (behind your reverse proxy).

Config file: `~/.config/kaim56-cli.json` (0600), else the voice client's
`~/.config/kaim56-voice.json`. `KAIM56_URL`, `KAIM56_IROH`, `KAIM56_USER`,
`KAIM56_PASS`, `KAIM56_INSTANCE` and `--url/--iroh/--user/--pass/-i` override.

## Examples

    kaim56 instances list
    kaim56 chat myassistant "what's new in my inbox?"
    kaim56 chat myassistant                      # interactive; /quit, /think
    git diff | kaim56 chat coder "review this"
    kaim56 inst create helper -t openrouter -m google/gemini-2.5-flash --start
    kaim56 tasks add jobresearcher "new CISO jobs" --at "daily 07:00"
    kaim56 skills proposals && kaim56 skills approve <id>
    kaim56 apps upload corewar
    kaim56 notifications watch
    kaim56 term myassistant                      # shell in the VM, Ctrl-] leaves
    kaim56 status --json | jq .

`kaim56 <command> --help` lists everything. `--json` prints the manager's raw
answer for scripting.

Chat streams the answer; reasoning stays hidden unless `--think`, tool calls
show dimmed. Each message carries its own turn id: if the connection drops,
the CLI fetches the finished answer from the trace. A stopped instance is
started first (`--no-start` to refuse).

## Update

    kaim56 self-update            # install the latest release (cli-v*)
    kaim56 self-update --check    # only look

After a command the CLI prints a one-line hint when a newer release exists
(checked at most once a day, in the background; `KAIM56_NO_UPDATE_CHECK=1` = off).
Releases: bump `version.go`, add a `## <version>` section to `RELEASE_NOTES.md`,
push to main — `.github/workflows/cli.yml` builds and publishes.

## Build

    ./build.sh              # tests, then ./kaim56 with the tunnel embedded
    go build -o kaim56 .    # without the tunnel (needs kaim56-tunnel in PATH or base_url)

Linux (the tunnel is a Linux binary); the direct URL route works anywhere Go runs.
