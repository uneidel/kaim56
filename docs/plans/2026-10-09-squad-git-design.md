# Squad git remote — design

Date: 2026-10-09. Status: built (`manager/mgr/squadgit.py`, `mgr/routes_squadgit.py`).

## Goal
A squad's agents push their branches and open PRs and issues on GitHub or Gitea, but every remote
operation runs host-side in the manager with a token that never enters a VM. Merging stays with the
user (on the forge, or via the squad merge gate into the local base).

## Settings (per project, admin only)
Stored in `projects.json` under the project's `remote` key (a project save keeps it):
`provider` github|gitea, `url` (https://host[/path]/owner/repo), `token_secret` (the NAME of a secret in
the host store, `~/.config/kat56/secrets.env`), `base` (default `main`), `label` (default `kaim56-squad`,
empty = none), `daily_limit` (PRs + issues per member and day, default 20, 0–200).
Admin API: `GET/POST /api/squad-git/<project>` (`{"remove": true}` drops it), `POST …/<project>/fetch`.
UI: Projects → Squad panel → "Git remote". OIDC role `user` is refused (default deny).

## Token hygiene
- The token secret is **reserved**: `secrets.allowed_secret_keys` drops every project's token secret, so no
  policy release (`by_template`, `by_instance`, `guest_readable`) and no MCP config substitution reaches a VM.
- git: the token rides `http.extraHeader` via `GIT_CONFIG_COUNT/KEY/VALUE` env (never argv), the remote
  URL is passed per call (no `[remote]` in the repo config), global/system config and credential helpers
  are off, redirects are not followed; git output is scrubbed of the token.
- API: stdlib urllib with redirects disabled (a redirect would carry `Authorization` elsewhere); error
  texts scrubbed; the API answers are normalised to `{number, title, url, state, author, labels, head, base, merged}`.

## Git behaviour
- `git_push_branch`: snapshots the writer's worktree folder (`projwt.snapshot`), pushes the commit with
  `+<sha>:refs/heads/squad/<writer>/<name>` (`name` default = project name, `[A-Za-z0-9._-]`, no slash).
  Force within the squad's own prefix: a discard resets the writer's branch, and the namespace is the
  squad's. The base branch and anything outside `squad/<writer>/` cannot be targeted. Writers push their
  own branch; the lead names a writer (`writer=`); readers are refused. Worktree projects only.
- `git_fetch`: `git fetch --prune <url> +refs/heads/*:refs/remotes/kaim56/*` in the project repo. No checkout
  change and no fast-forward of the local base: the operator's checkout may be dirty or elsewhere, and the
  base is what new writer branches start from — moving it is the operator's (or the merge gate's) decision.

## API (both providers)
| tool | route | who | GitHub / Gitea endpoint |
|---|---|---|---|
| pr_create | POST /api/git/pr-create | writer, lead | push, then `POST /pulls {title, body, head, base}` |
| pr_list | POST /api/git/prs | member | `GET /pulls?state=` |
| pr_view | POST /api/git/pr | member | `GET /pulls/{n}` + `GET /issues/{n}/comments` |
| pr_comment | POST /api/git/pr-comment | member | `POST /issues/{n}/comments` |
| issue_create | POST /api/git/issue-create | member | `POST /issues` |
| issue_list | POST /api/git/issues | member | `GET /issues` (GitHub: PRs filtered out; Gitea: `type=issues`) |
| issue_comment | POST /api/git/issue-comment | member | `POST /issues/{n}/comments` |

Provider differences: API base `https://api.github.com/repos/o/r` (GitHub Enterprise: `https://host/api/v3`),
Gitea `https://host[/path]/api/v1/repos/o/r`; auth `Bearer` (GitHub) vs `token` (Gitea); git Basic user
`x-access-token` vs any (token as password); labels — GitHub adds by name (created via `POST /labels` when `GET /labels/{name}`
is 404), Gitea by id (looked up in `GET /labels`, created when missing). A label failure is only noted.
Bodies are capped (title 200, body 20 000), a footer names the member; text is sent as JSON data.
Every call is audited in the instance's trail (tool, project, repo, number); created PRs/issues are recorded
in `squadgit.json` (the panel's list and the daily limit). Merging PRs is not offered.

## What the user sets up
- **GitHub**: a fine-grained PAT limited to the squad repos with *Contents*, *Pull requests*, *Issues*:
  read and write (Metadata read is implied). Store it in the Secrets tab (e.g. `SQUAD_GIT_TOKEN`).
- **Gitea**: an access token with scopes `write:repository` and `write:issue` (older Gitea: `repo` + `issue`).
- **Branch protection** on `main` (and any release branch): require a PR + review, no force push, no
  deletion. The manager only pushes to `squad/<writer>/*`; protection makes the forge refuse a push to
  `main` with this token even if the manager were wrong.
- Optionally restrict the token's repo to the squad repo only and rotate it like any other secret.
