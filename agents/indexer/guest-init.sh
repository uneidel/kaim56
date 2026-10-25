#!/bin/sh
# PID 1 of the indexer microVM (template "indexer", mgr/indexer.py).
#   fc_upper=/dev/vdX   the persistent write layer: the project clones and
#                       their .repowise/ indexes live in /var/lib/indexer
#   /dev/vdb            config disk (config.env)
#   /api/mounts         the projects this instance reads, at /project/<name>
# Then the index service on :7340 as uid 1000. No workspace mount, no agent.

# --- 1. overlay root ---------------------------------------------------------
# Unlike the agents, the upper is mounted WITHOUT -o sync: an index run is
# thousands of small sqlite writes. The service calls sync() after every run;
# a stop in the middle of one costs that run, the next reindex repeats it.
mount -t proc proc /proc 2>/dev/null
if [ ! -f /.fc-overlay ]; then
  UP=$(sed -n 's/.*fc_upper=\([^ ]*\).*/\1/p' /proc/cmdline)
  if [ -n "$UP" ]; then
    mount -t devtmpfs devtmpfs /dev 2>/dev/null
    if mount "$UP" /mnt 2>/dev/null; then
      mkdir -p /mnt/upper /mnt/work /mnt/root
      if mount -t overlay overlay -o lowerdir=/,upperdir=/mnt/upper,workdir=/mnt/work /mnt/root; then
        touch /mnt/root/.fc-overlay
        mkdir -p /mnt/root/oldroot
        cd /mnt/root
        pivot_root . oldroot && exec chroot . /init
        echo "[init] WARN: pivot_root failed — continuing without overlay"; cd /
      else
        echo "[init] WARN: overlay mount failed — continuing without overlay"
      fi
    else
      echo "[init] WARN: upper $UP not mountable — continuing without overlay"
    fi
  fi
fi

# --- 2. kernel filesystems, config ------------------------------------------
mount -t proc     proc     /proc 2>/dev/null
mount -t sysfs    sysfs    /sys  2>/dev/null
mount -t devtmpfs devtmpfs /dev  2>/dev/null
mount -t tmpfs    tmpfs    /tmp  2>/dev/null
export HOME=/home/indexer
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mkdir -p /config
mount -o ro /dev/vdb /config 2>/dev/null
set -a
[ -f /app/config.env ]    && . /app/config.env
[ -f /config/config.env ] && . /config/config.env
set +a
echo "nameserver ${GUEST_DNS:-1.1.1.1}" > /etc/resolv.conf
GW=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
mkdir -p "${INDEXER_DATA:-/var/lib/indexer}" && chown indexer:indexer "${INDEXER_DATA:-/var/lib/indexer}"

# --- 3. project folders (read-only, from the manager by this VM's IP) -------
# Same reconciler as the agents (agents/claude/guest-init.sh): only our own
# export subtree, only under /project.
fc_reconcile() {
  LIST=/tmp/fc-desired.list; want=" "
  if curl -sf -m 5 "http://${GW}:8700/api/mounts" -o "$LIST.tmp" 2>/dev/null; then mv "$LIST.tmp" "$LIST"; else rm -f "$LIST.tmp"; fi
  if [ -f "$LIST" ]; then
    while IFS='|' read -r sub gp mode; do
      [ -n "$sub" ] && [ -n "$gp" ] || continue
      case "$sub" in */.fcmnt/"$FC_INSTANCE"/*) : ;; *) echo "[init] refuse foreign export $sub"; continue ;; esac
      case "$gp" in
        /project/*) : ;;
        *) echo "[init] refuse mount at $gp"; continue ;;
      esac
      case "$gp" in */../*|*[!/a-zA-Z0-9._-]*) echo "[init] refuse mount at $gp"; continue ;; esac
      want="$want$gp "
      if ! awk -v p="$gp" '$2==p{f=1} END{exit !f}' /proc/mounts; then
        mkdir -p "$gp"
        mount -t nfs4 -o nolock,soft,timeo=30,retrans=3,ro "${GW}:${sub}" "$gp" 2>/dev/null \
          && echo "[init] + project folder $gp"
      fi
    done < "$LIST"
  fi
  awk -v s="/.fcmnt/$FC_INSTANCE/" 'index($1,s){print $2}' /proc/mounts | while read -r mp; do
    case "$want" in *" $mp "*) : ;; *) umount -l "$mp" 2>/dev/null && echo "[init] - project folder $mp" ;; esac
  done
}
if [ -n "$FC_INSTANCE" ] && [ -n "$GW" ]; then
  fc_reconcile
  ( while true; do sleep 5; fc_reconcile; done ) &
fi

# --- 4. the index service, as uid 1000 ---------------------------------------
echo "[init] indexer instance=${FC_INSTANCE} port=${INDEXER_PORT:-7340} data=${INDEXER_DATA:-/var/lib/indexer}"
cd /app
# Stop from the host (manager: vm.halt): Ctrl+Alt+Del = SIGINT to PID 1 -> flush
# the disks and reboot, which ends Firecracker (reboot=k). The images carry no
# reboot/poweroff binary: the syscall through python.
fc_down() { sync; python3 -c 'import ctypes; ctypes.CDLL(None).reboot(0x01234567)'; }
trap 'echo "[init] stop requested -> sync, reboot"; fc_down' INT TERM
echo 0 > /proc/sys/kernel/ctrl-alt-del 2>/dev/null   # the kernel's default reboots at once, unsynced
setpriv --reuid=1000 --regid=1000 --init-groups python3 -u /app/indexer_service.py &
MAIN=$!
while kill -0 $MAIN 2>/dev/null; do wait $MAIN; done

echo "[init] service exited -> reboot"
fc_down
while true; do sleep 3600; done
