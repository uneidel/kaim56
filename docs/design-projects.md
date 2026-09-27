# Design: projects — one folder set, many agents, in parallel

Status: **accepted**, phase 1 (parallel worker) built. Owner: Ulrich. Written 2026-09-27.

## Why

Today every instance gets its folders one by one: host folders (📁 → Folders) and one
katfs share. Letting a frontier model plan while a local model executes — or several
agents work at once — means injecting the same folders into several instances and
keeping them in step by hand. And nothing stops two agents from writing the same file.

Two separate gaps:

1. **Folders are per instance.** There is no object for "the code these agents work on".
2. **The task worker is one thread.** Every task of every instance runs one after the
   other (`mgr/tasks.py: _task_worker`). Three executors never run in parallel; a
   30-minute Code flow round even delays the 07:00 job search.

## The idea

A **project** names a folder set once. Instances join it with a role. The project
carries a **strategy** for concurrent writers:

| strategy   | writers see                                   | how changes come back               | needs            |
|------------|-----------------------------------------------|-------------------------------------|------------------|
| `shared`   | the one folder, read-write                    | they are already there              | nothing          |
| `worktree` | their own git worktree on their own branch    | the lead (or Ulrich) merges branches| a git repo       |
| `overlay`  | the folder + a private copy-on-write layer    | changed files are applied per member| host overlayfs   |

Roles: **lead** (sees everything read-only: the source and every writer's tree; may
merge), **writer** (own tree read-write), **reader** (source read-only).

Planner/executor is then: one project, `planner` as lead, `coder-local` (and more) as
writers — the folder is defined once.

## Data model

`projects.json` in the manager dir (root-only state, like the other stores):

```json
{"name": "codeflow-dev",
 "strategy": "worktree",
 "source": {"type": "host", "path": "/home/ulrich/kaim56", "subdir": "apps/codeflow"},
 "base": "main",
 "members": {"planner": {"role": "lead"}, "coder-a": {"role": "writer"}, "coder-b": {"role": "writer"}}}
```

- `source.type`: `host` (a host folder, checked with the existing `mounts.mount_error`
  deny list) or `katfs` (a share id). **katfs works with `shared` only** — worktrees and
  overlays need the files on the host.
- `subdir` narrows what the agents see (the repo stays whole for git).
- Membership lives in the project, not in the instance config — one place to change.

Runtime state under `AGENT_ROOT/.projects/<name>/` (guest-owned, exportable):
`wt/<member>/` (worktrees), `ov/<member>/{upper,work,merged}` (overlays).

## How it plugs in (existing seams, no new mechanism in the VM)

- **Mounts:** `mounts.mount_specs(inst)` gains the project entries of the instance.
  Everything downstream already works and applies live: per-VM-IP NFS exports,
  `/api/mounts`, the 5-second reconciler in the guest. Guest paths are fixed:
  `/project/<name>` (the member's own tree) and, for the lead, `/project/<name>/.members/<member>` read-only.
- **katfs:** `katfs.katfs_share_for(inst)` falls back to the share of a `shared`
  katfs project the instance belongs to.
- **Worker:** see "Parallel tasks" — independent of projects.

## Strategies in detail

### shared
The source exported to writers rw, to readers and the lead ro. Optional (phase 3):
advisory path locks — a writer's `write_file`/`remote_write` on a path another member
holds for a running task is refused with the holder's name.

### worktree
- On join: branch `proj/<name>/<member>` from `base`, `git worktree add` into
  `.projects/<name>/wt/<member>` — **as the guest user, hardened** (the checkout.py
  flags: no hooks, no fsmonitor, no symlinks, no file/ext transport).
- **Every git call of the manager names `GIT_DIR`/`GIT_WORK_TREE` explicitly** (the
  main repo's `.git/worktrees/<member>`, the worktree path). A worktree's `.git` file
  is VM-writable; trusting it would let an agent point git at a planted config
  (`core.fsmonitor`, filters) that then runs on the host — the class of bug memfs fixed
  as C-1.
- Review: `GET` diff and log per member (vs. `base`), changed-file list, ahead/behind.
- Merge: `POST` merge member → `base` (`--no-ff`); on conflict abort, report the files,
  change nothing. Discard: delete branch + worktree, recreate from `base`.
- A writer can commit in its worktree from the VM? No git inside the VM needed: the
  manager commits the member's working tree on merge ("snapshot" commit), so agents
  only edit files.

### overlay
- On join: host overlayfs `lower=source, upper=ov/<m>/upper` mounted at `ov/<m>/merged`,
  exported to the member. Deletions are whiteouts in `upper`.
- Review: the files in `upper` = exactly what the member changed; diff against `lower`.
- Apply: copy `upper` into the source, per member. Conflict = a file the member changed
  that changed in the source since the member joined (hash snapshot at join) → reported,
  not overwritten.
- **To verify first (prototype):** NFS export of an overlayfs needs `index=on,nfs_export=on`
  (the host kernel 6.12 has overlay as a module, index off by default). If the kernel
  NFS server does not export it reliably, the fallback is exporting `upper` plus a
  read-only `lower` and letting a small in-guest overlay mount combine them.

## Parallel tasks (worker)

- One lane per instance, lanes run concurrently, **serial within an instance**
  (an agent has one conversation), a global cap `WORKER_PARALLEL` (default 3),
  ephemeral VMs keep their own `EPHEMERAL_MAX` slots.
- The claim stays inside `store.with_tasks` (the lost-update fix); a lane claims only
  tasks of instances without a running task.
- Unchanged: orphan reset, heartbeats, mission advance, task-frequency guard.
- The local model is still one GPU: with llama.cpp `-np 1` parallel local tasks queue on
  the server, with `-np 2` they share it at half speed. Real parallelism = cloud
  executors, or one local plus cheap cloud ones.

## API and UI

- Admin routes (`routes_admin`): `GET/POST /api/projects`, `POST /api/projects/<p>/members`,
  `GET /api/projects/<p>/diff/<member>`, `POST …/merge/<member>`, `POST …/discard/<member>`.
- Guest routes (`routes_guest`, only for the project's **lead**): `project_status`,
  `project_diff(member)`, optional `project_merge(member)` — so a planner agent can
  review and integrate without a human, if Ulrich allows it per project (`lead_may_merge`).
- UI: a **Projects** tab — source picker (📁 host / katfs share), strategy, members with
  roles; per writer: changed files, diff, Merge / Discard. The instance's Folders dialog
  and the Policy tab show "project X (writer)".
- Agent side: nothing new for access (plain folders); two small tools for the lead.

## Security

- Exports stay per VM IP; a member never sees another member's tree except the lead,
  read-only.
- Source paths go through the existing deny list (`mounts.mount_error`).
- git: guest user, hardened flags, explicit `GIT_DIR`/`GIT_WORK_TREE`, never inside a
  VM-writable `.git`.
- A merge into `base` changes the operator's repo: human-only unless `lead_may_merge`.

## Phases (each with tests, each shippable)

1. **Parallel worker** — lanes, cap, tests with concurrent fake tasks (and a mutant that
   runs two tasks of one instance at once must fail).
2. **Projects, `shared`** — model, routes, Projects tab, mount_specs + katfs integration,
   live join/leave.
3. **`worktree`** — hardened git layer, diff/merge/discard, lead view, UI.
4. **`overlay`** — after the NFS-export prototype; plus advisory path locks; lead tools.
5. A live run: frontier planner + two executors (one local) on `apps/codeflow`.

## Decisions (Ulrich, 2026-09-27)

1. Own **Projects** tab.
2. A lead agent may merge too — per project, `lead_may_merge`.
3. katfs only with `shared` — accepted.
4. `WORKER_PARALLEL` default 3.
