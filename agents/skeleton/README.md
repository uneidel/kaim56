# Skeleton agent

The smallest agent type that runs on the platform. Copy this folder, rename
it, and you have your own template in **Create instance** — the manager scans
`agents/*/template.json` (the folder named by `AGENTS_DIR` in `site.json`,
or `agents/` next to `manager/`) and lists every folder that has one.

```
agents/skeleton/
├── template.json     what Create instance shows: name, description, rootfs,
│                     size, "overlay": true, and the params that become the
│                     instance's config (env keys in the VM)
├── Dockerfile        the rootfs: Debian + python3, the agent as uid 1000
├── guest-init.sh     PID 1 in the VM: overlay root, config disk, network,
│                     workspace over NFS, then the agent
├── agent.py          the agent: serves POST /api/chat on :8080
├── config.env        baked defaults; the manager's config disk overrides them
└── build-rootfs.sh   docker build -> ext4 image -> instances/<folder>-rootfs.ext4
```

## Make your own

```bash
cp -r agents/skeleton agents/myagent
cd agents/myagent
$EDITOR template.json          # "template": "myagent", "rootfs": "instances/myagent-rootfs.ext4"
$EDITOR agent.py               # your logic in answer(); keep the two routes
./build-rootfs.sh              # FC_DIR=/path/to/manager if not found
```

Then **Create instance → template myagent** in the web UI, transport web,
start it, and talk to it from the chat page, the app or the voice client.
A change to the agent = rebuild the image + restart the instance (the
Instances tab flags stale VMs).

## The contract

What the manager gives every VM, and what it expects back:

| From the manager | Where |
|---|---|
| Instance config (template params) + `FC_INSTANCE`, `TZ`, `GUEST_DNS`, `AGENT_EXPORT`, `KEY_PROXY`, `PATH` | config disk `/dev/vdb`, file `config.env`, sourced by `/init` |
| Tool plugins from the Plugins tab | `/config/plugins/*.py` |
| Write layer per instance (`"overlay": true`) | boot arg `fc_upper=/dev/vdX`, assembled by `guest-init.sh` |
| Network | kernel `ip=` boot arg; the default gateway is the manager, port 8700 |
| Workspace (read-write, per instance) | NFS `$GW:$AGENT_EXPORT`, mounted at `$WORKDIR` |
| Extra host folders | `GET http://$GW:8700/api/mounts` (`sub|guest|mode` lines; `agents/openrouter/guest-init-openrouter.sh` has the reconciler) |
| LLM keys — never in the VM | `POST $MANAGER_URL/api/llm/openrouter/chat/completions`, OpenAI body, no Authorization header (with `LLM_KEY_PROXY` on in Settings) |
| Memory, missions, tasks, notifications, secrets, MCP hub | the guest routes of the manager (`manager/mgr/routes_guest.py`); `agents/openrouter/agent/tools_manager.py` shows every call |

| To the manager | Contract |
|---|---|
| `POST :8080/api/chat` | body `{"message", "chat"?, "image"?}` → `{"reply": "…"}` |
| `POST :8080/api/chat/stream` | same body → `text/plain`, written as produced (optional; without it the manager uses `/api/chat`) |
| `/reset` as a message | start a fresh conversation |
| `:7682` | optional browser terminal (`agents/openrouter/webterm.py` is reusable as is) |

The VM is identified by its IP: guest routes need no credentials, and the
manager scopes what each VM may read to that instance. The host firewall
lets a VM reach only the manager (8700) and NFS (2049) on the host, plus the
internet when the instance has it enabled.
