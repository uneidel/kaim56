# claude-signal-firecracker

The **Signal↔Claude Code bridge** in a **Firecracker microVM** instead of a Docker
container — real HW VM isolation. Claude runs in the VM, reaches your services
only over the network (signalapi/portainer/pihole), no host filesystem, no Docker socket.

```
Signal (direct chat) ─▶ signalapi ─▶ [ microVM: bridge.py + claude -p ] ─▶ response
                                        eth0(172.30.0.2) ── tap0(172.30.0.1) ── NAT ── LAN
```

## Prerequisites (present on this box)
- `/dev/kvm` (KVM) ✅, VT-x ✅ — checked.
- `docker`, `curl`, `e2fsprogs` (mkfs.ext4 -d), `iproute2`, `iptables`.
- The bridge code lives in `../claude-signal-bridge/bridge.py` (gets baked in).

## Files
| File | Purpose |
|---|---|
| `Dockerfile.rootfs` | contents of the guest rootfs (Debian + Node + Claude Code + python) |
| `config.env` | bridge configuration in the guest (number, ALLOWED_SENDERS, …) |
| `guest-init.sh` | PID 1 in the VM: mounts + DNS + starts the bridge |
| `build.sh` | fetches firecracker + kernel, builds `rootfs.ext4`, injects the login |
| `net.sh` | tap0 + NAT (ROOT) |
| `vmconfig.json` | Firecracker machine config (2 vCPU, 1536 MB) |
| `run.sh` | starts the microVM |
| `claude-fc.service` | systemd autostart |

## Setup (order)

**1) Build artifacts** (as your user; docker group is enough):
```bash
cd /home/ulrich/claude-signal-firecracker
./build.sh
```
Produces `firecracker`, `vmlinux`, `rootfs.ext4` and places `~/.claude/.credentials.json`
into the guest (the "use subscription" variant). For your own API key, instead set
`ANTHROPIC_API_KEY=…` in `config.env` and omit the credentials step in `build.sh`.

**2) Network + start** (ROOT — tap/iptables/kvm):
```bash
sudo ./net.sh
sudo ./run.sh          # foreground; VM console. Ctrl-C ends it.
```
or permanently via systemd:
```bash
sudo cp claude-fc.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now claude-fc.service
journalctl -u claude-fc -f
```

**3) Test:** message katbot **directly** with `/help` → "🤖 katbot online".

## Network details
- tap0 = `172.30.0.1/30` (host), guest = `172.30.0.2/30`, NAT via `eth0`.
- Guest DNS = `1.1.1.1` (Pi-hole). The kernel configures eth0 via the `ip=` boot param.

## Security
- **Stronger isolation** than a container: a compromise stays in the VM; access only
  over the network. No `/home` mount, no Docker socket.
- The trust boundary remains `ALLOWED_SENDERS` (only your number).
- The login lives in `rootfs.ext4` (OAuth token) — protect the image accordingly.
  Host and VM share the token (token-rotation note as with the container).

## Limits / iteration
- I could **run none of this here** (root/kvm + build are locked) — so the kit
  is untested. Typical stumbling blocks: kernel URL (the S3 path changes), `mkfs.ext4 -d`
  (needs e2fsprogs ≥1.43), firewall/`FORWARD` policy. Send me the output of
  `build.sh` / `run.sh` / `journalctl`, and I'll fix it specifically.
- signalapi in `native` mode → receiving via polling (latency ~1–10 s).
