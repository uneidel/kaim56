# Squad toolchain layer — design

Date: 2026-10-12. Status: built (`manager/mgr/toolchain.py`), needs the claude + openrouter rootfs rebuilt.

## Goal
Members of a squad stop installing the same toolchain (Go, gcc, GL libs …) each into its own upper disk:
one spec per squad, built once per base image in a throwaway VM, attached read-only to every member.

## Spec
Squad setting `toolchain` = `{"apt": [...], "script": "...", "env": {"K": "V"}}` plus `toolchain_version`
(bumped on every change), validated in `mgr/squads.toolchain_spec`: package names
`^[a-z0-9][a-z0-9+.-]{0,62}$` (≤ 100), script ≤ 8 KB, env names `^[A-Z][A-Z0-9_]{0,40}$` (≤ 50),
values printable ≤ 512 with `$NAME` / `${NAME}` as the only expansion (no `` ` ``, no `$(…)`).
It changes only by the operator: the Squads tab "Toolchain → Edit spec" (saving = approving), or
approving the lead's `toolchain_propose` card (kind `toolchain` in `mgr/squadgate.py`; one pending,
revisable, the card shows the diff against the current spec and is editable before Approve).

## Build
`toolchain.ensure(proj)` starts a build when a base image the members boot (claude / openrouter rootfs)
has no layer of this spec version on the current base image; each (version, base image) is tried once by
itself (approval, settings save, idle sweep), "Rebuild" forces. Builds run one at a time.

The builder `tc-<squad_id>-<base>` is not an instance (no JSON, unknown by IP → no guest API, no
credentials): base rootfs ro, a config disk with only `TOOLCHAIN_BUILD=1`, `GUEST_DNS`, `PATH`,
`toolchain.sh` (generated: `set -e`, `apt-get update`, `apt-get install -y --no-install-recommends <pkgs>`,
`bash -e toolchain-user.sh`, `apt-get clean`, lists removed, env to `/etc/kaim56-toolchain.env` +
`/etc/profile.d/kaim56-toolchain.sh`, marker `/.kaim56-toolchain-ok`), an empty 16 GiB sparse upper,
tap index 250–254 with internet but no host (mangle `--dst-type LOCAL` DROP), boot arg `fc_build=1`.

guest-init in build mode (right after the config, before sudo/NFS/credentials/skills/agent): runs the
script as root (`timeout 1800`), removes `/.fc-overlay`, `/root/.cache`, `/tmp/*`, syncs, `fsfreeze`s the
upper (clean journal), prints `KAIM56_TOOLCHAIN_RESULT=ok|fail rc=N` on the console and reboots via
sysrq (the images have no `poweroff`; `reboot=k` ends Firecracker). The manager reads the console log
(`run/tc-….log`), kills the VM 15 s after the verdict or at 35 min, then on success copies the upper
(`cp --sparse=always`) to `instances/toolchains/<squad_id>-<base>-v<N>.ext4`, runs `e2fsck -fy` (journal
replay if the freeze failed) and `e2fsck -fn` (must be clean), renames it into place and records
`{version, template, base_image_id, built, size, ok, log_tail}` in `toolchains.json` (root-only). On failure
the log tail is kept and the previous layer stays active. `base_image_id` = base image mtime + size
(what `vm.image_state` already uses). A build cut off by a manager restart is failed by the sweep.

## Use
`vm.gen_config`: a member of exactly one squad-enabled project with a non-empty spec, whose base image
has a ready layer built on the current base image, gets it as an extra read-only drive and `fc_layer=<dev>`.
guest-init mounts it ro at `/mnt/layer` and assembles `lowerdir=/mnt/layer/upper:/` — base (ro) <
toolchain (ro) < member upper (rw); not mountable or refused by overlayfs → `lowerdir=/` with a WARN.
The env file is sourced for the agent processes. A stale layer (base rebuilt) is skipped, shown
"stale — rebuild" and rebuilt once. Members pick up a new layer with "Restart members".

Known caveat: after a member installs a package itself, dpkg's status file in its upper shadows the
layer's (dpkg/apt no longer "see" the layer's packages; the files are still there). Acceptable; the
member's "Reset disk" fixes it.

## Security
- The builder gets no secrets, no Claude credentials, no skills/MCP, no mounts, cannot reach the host.
- Builds start only from operator actions (approval, settings save, Rebuild) or the sweep re-running an
  already approved spec; the script runs only inside the VM.
- Host-side the layer image is only copied and e2fsck'd, never mounted; it is attached read-only.
- File paths come from the validated squad id (`^[a-z0-9]{16}$`), a fixed base name and an int.
- The layer can change anything in a member's root — the same power a member already has via sudo.
