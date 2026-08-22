#!/bin/sh
# PID 1 of the Claude microVM. Reads instance config from the config disk (vdb).
#
# --- Overlay root (read-only base + per-instance upper) -----------------------
# The manager mounts the shared base ro and passes the rw upper device via the
# boot arg fc_upper=. Here: mount the upper, assemble overlayfs, pivot_root,
# re-exec this script in the new root. The marker /.fc-overlay exists only IN
# the overlay (lives in the upper) and prevents infinite recursion. If anything
# fails, the VM keeps booting on the ro base (degraded, but reachable) instead
# of not at all.
mount -t proc proc /proc 2>/dev/null
if [ ! -f /.fc-overlay ]; then
  UP=$(sed -n 's/.*fc_upper=\([^ ]*\).*/\1/p' /proc/cmdline)
  if [ -n "$UP" ]; then
    mount -t devtmpfs devtmpfs /dev 2>/dev/null
    # The root is read-only -> the mountpoint MUST be a directory that exists
    # in the image (/mnt); mkdir on / would fail.
    # -o sync: stop() pulls the plug on the VM (SIGTERM to Firecracker) —
    # without sync the last writes would still sit in the page cache and be
    # lost (observed: 0-byte file). Synchronous is fine for our write load.
    if mount -o sync "$UP" /mnt 2>/dev/null; then
      mkdir -p /mnt/upper /mnt/work /mnt/root
      if mount -t overlay overlay \
           -o lowerdir=/,upperdir=/mnt/upper,workdir=/mnt/work /mnt/root; then
        touch /mnt/root/.fc-overlay
        mkdir -p /mnt/root/oldroot
        cd /mnt/root
        pivot_root . oldroot && exec chroot . /init
        echo "[init] WARN: pivot_root failed — continuing without overlay"
        cd /
      else
        echo "[init] WARN: overlay mount failed — continuing without overlay"
      fi
    else
      echo "[init] WARN: upper $UP not mountable — continuing without overlay"
    fi
  fi
fi
mount -t proc     proc     /proc  2>/dev/null
mount -t sysfs    sysfs    /sys   2>/dev/null
mount -t devtmpfs devtmpfs /dev   2>/dev/null
mkdir -p /dev/pts
mount -t devpts   devpts   /dev/pts 2>/dev/null   # PTYs (webterm/Browser-Terminal)
mount -t tmpfs    tmpfs    /tmp   2>/dev/null
echo "nameserver 1.1.1.1" > /etc/resolv.conf
export HOME=/root
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Defaults (baked) + instance config (config disk vdb, overrides defaults)
mkdir -p /config
mount -o ro /dev/vdb /config 2>/dev/null
set -a
[ -f /app/config.env ] && . /app/config.env
[ -f /config/config.env ] && . /config/config.env
set +a

GW=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
WORKDIR="${CLAUDE_WORKDIR:-/home/node/workspace}"
mkdir -p "$WORKDIR"; chown node:node "$WORKDIR" 2>/dev/null

# NFS agent folder (default gateway = tap host); writes are mapped to uid 1000
if [ "${AGENT_NFS:-1}" = "1" ] && [ -n "$GW" ]; then
  mount -t nfs4 -o nolock,soft,timeo=30,retrans=3 \
    "${GW}:${AGENT_EXPORT:-/}" "$WORKDIR" || echo "[init] WARN: NFS mount failed"
fi
# --- dynamic host folders (reconciler) ----------------------------------------
# The manager maintains .fcmnt/<inst>/desired.list (visible in the workspace) and
# exports each folder per guest IP. fc_reconcile reconciles the mounts with the
# desired guest paths: immediately + then every 5s in the background -> live.
fc_reconcile() {
  LIST="$WORKDIR/.fcmnt/$FC_INSTANCE/desired.list"; want=" "
  if [ -f "$LIST" ]; then
    while IFS='|' read -r sub gp mode; do
      [ -n "$sub" ] && [ -n "$gp" ] || continue
      want="$want$gp "
      if ! awk -v p="$gp" '$2==p{f=1} END{exit !f}' /proc/mounts; then
        mkdir -p "$gp"; ro=""; [ "$mode" = "ro" ] && ro=",ro"
        mount -t nfs4 -o "nolock,soft,timeo=30,retrans=3$ro" "${GW}:${sub}" "$gp" 2>/dev/null \
          && echo "[init] + host folder $gp"
      fi
    done < "$LIST"
  fi
  awk -v s=":/.fcmnt/$FC_INSTANCE/" 'index($1,s){print $2}' /proc/mounts | while read -r mp; do
    case "$want" in *" $mp "*) : ;; *) umount -l "$mp" 2>/dev/null && echo "[init] - host folder $mp" ;; esac
  done
}
if [ -n "$FC_INSTANCE" ] && [ -n "$GW" ]; then
  fc_reconcile
  ( while true; do sleep 5; fc_reconcile; done ) &
fi

# Subscription login (Claude Max/Pro): fetch the host's live credential at boot
# so `claude -p` runs as a subscription instead of reporting "Not logged in".
# Comes via the manager (claude template only, by source IP); Claude Code then
# does the short-lived accessToken renewal itself per session.
if [ -n "$GW" ]; then
  mkdir -p /home/node/.claude
  # No curl in the image -> python3 (present anyway). Writes the file only if
  # the claudeAiOauth block really arrived, never an error message.
  if python3 - "$GW" <<'PY'
import json, sys, urllib.request
gw = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(f"http://{gw}:8700/api/claude-credentials", timeout=10))
    if "claudeAiOauth" not in d:
        raise ValueError("no oauth block")
    open("/home/node/.claude/.credentials.json", "w").write(json.dumps(d))
except Exception as e:
    print("[init] claude-cred:", e); sys.exit(1)
PY
  then
    chown -R node:node /home/node/.claude
    chmod 600 /home/node/.claude/.credentials.json
    echo "[init] Claude subscription login loaded"
  else
    echo "[init] WARN: no Claude login from the manager (claude -p reports 'Not logged in')"
  fi
fi

# Agent runs as node (uid 1000) — claude-code does not allow actions as root
export HOME=/home/node
export CLAUDE_WORKDIR="$WORKDIR"
TRANSPORT="${TRANSPORT:-signal}"
echo "[init] agent=claude(node) transport=$TRANSPORT workdir=$WORKDIR"
cd "$WORKDIR"
RUNAS="setpriv --reuid=1000 --regid=1000 --init-groups"
# Browser-Terminal (webterm) im Hintergrund, als Agent-User (uid 1000).
env HOME=/home/node CLAUDE_WORKDIR="$WORKDIR" TERM_PORT=7682 \
  $RUNAS python3 -u /app/webterm.py >/var/log/webterm.log 2>&1 &
echo "[init] webterm auf :7682 gestartet"
case "$TRANSPORT" in
  signal) $RUNAS python3 -u /app/bridge.py ;;
  web)    AGENT=claude $RUNAS python3 -u /app/web_bridge.py ;;
  *) echo "[init] transport '$TRANSPORT' unknown"; sleep 15 ;;
esac

echo "[init] bridge beendet -> poweroff"
poweroff -f 2>/dev/null
while true; do sleep 3600; done
