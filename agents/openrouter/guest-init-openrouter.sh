#!/bin/sh
# PID 1 des OpenRouter-Agenten. Laeuft als node (uid 1000). run_agent.py waehlt
# picks the transport (signal|web) itself based on TRANSPORT.
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
export HOME=/home/node
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

mkdir -p /config
mount -o ro /dev/vdb /config 2>/dev/null
set -a
[ -f /app/config.env ] && . /app/config.env
[ -f /config/config.env ] && . /config/config.env
echo "nameserver ${GUEST_DNS:-1.1.1.1}" > /etc/resolv.conf   # site.json via the config disk, not the image

# Agent code: the read-only harness drive (manager: AGENT_SRC) wins over the
# copy baked into the rootfs; without the drive the VM boots as before.
APP=/app
HD=$(sed -n 's/.*fc_harness=\([^ ]*\).*/\1/p' /proc/cmdline)
if [ -n "$HD" ]; then
  mkdir -p /harness
  if mount -o ro "$HD" /harness 2>/dev/null && [ -f /harness/run_agent.py ]; then
    APP=/harness; echo "[init] harness from $HD"
  else
    echo "[init] WARN: harness $HD not mountable — agent from /app"
  fi
fi
set +a

GW=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
WORKDIR="${CLAUDE_WORKDIR:-/home/node/workspace}"
mkdir -p "$WORKDIR"; chown node:node "$WORKDIR" 2>/dev/null
if [ "${AGENT_NFS:-1}" = "1" ] && [ -n "$GW" ]; then
  mount -t nfs4 -o nolock,soft,timeo=30,retrans=3 \
    "${GW}:${AGENT_EXPORT:-/}" "$WORKDIR" || echo "[init] WARN: NFS mount failed"
fi
# --- dynamic host folders (reconciler) ----------------------------------------
# The manager maintains .fcmnt/<inst>/desired.list (visible in the workspace) and
# exports each folder per guest IP. fc_reconcile reconciles the mounts with the
# desired guest paths: immediately + then every 5s in the background -> live.
# The list comes from the manager (/api/mounts, this VM identified by its IP),
# NOT from the shared workspace: there any other VM could write our list and
# have us mount its files over /bin. Belt and braces: only our own export
# subtree, never a system directory as the mount point.
fc_reconcile() {
  LIST=/tmp/fc-desired.list; want=" "
  if curl -sf -m 5 "http://${GW}:8700/api/mounts" -o "$LIST.tmp" 2>/dev/null; then mv "$LIST.tmp" "$LIST"; else rm -f "$LIST.tmp"; fi
  if [ -f "$LIST" ]; then
    while IFS='|' read -r sub gp mode; do
      [ -n "$sub" ] && [ -n "$gp" ] || continue
      case "$sub" in "/.fcmnt/$FC_INSTANCE/"*) : ;; *) echo "[init] refuse foreign export $sub"; continue ;; esac
      case "$gp" in
        /|/bin|/bin/*|/sbin|/sbin/*|/usr|/usr/*|/lib*|/etc|/etc/*|/proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/boot|/boot/*|/run|/run/*|/var|/var/*|/tmp|/tmp/*|/app|/app/*|/harness|/harness/*|/config|/config/*|/init|/root|/root/*|*/../*|*[!/a-zA-Z0-9._-]*)
          echo "[init] refuse mount at $gp"; continue ;;
      esac
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

export CLAUDE_WORKDIR="$WORKDIR"
# Browser-Terminal (webterm) im Hintergrund, als Agent-User (uid 1000).
setpriv --reuid=1000 --regid=1000 --init-groups \
  env HOME=/home/node CLAUDE_WORKDIR="$WORKDIR" TERM_PORT=7682 \
  python3 -u "$APP/webterm.py" >/var/log/webterm.log 2>&1 &
echo "[init] webterm auf :7682 gestartet"
echo "[init] openrouter-agent transport=${TRANSPORT:-signal} model=${OPENROUTER_MODEL} workdir=$WORKDIR"
cd "$APP"
setpriv --reuid=1000 --regid=1000 --init-groups python3 -u "$APP/run_agent.py"

echo "[init] agent beendet -> poweroff"
poweroff -f 2>/dev/null
while true; do sleep 3600; done
