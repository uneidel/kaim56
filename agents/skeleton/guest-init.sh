#!/bin/sh
# PID 1 of the skeleton agent. What the manager hands a VM and what this
# script does with it — the contract every agent type implements:
#
#   boot arg fc_upper=/dev/vdX  the per-instance write layer ("overlay": true
#                               in template.json): mount it, overlay it on the
#                               read-only base, pivot_root, re-exec this script
#   /dev/vdb                    the config disk: config.env with the instance
#                               params plus what the manager adds per boot
#                               (FC_INSTANCE, TZ, GUEST_DNS, AGENT_EXPORT,
#                               KEY_PROXY, PATH) and plugins/ (tool plugins)
#   boot arg ip=...             the kernel brings eth0 up; the default gateway
#                               is the manager (port 8700)
#   NFS $GW:$AGENT_EXPORT       this instance's workspace, mount it read-write
#   http://$GW:8700/api/mounts  extra host folders for this VM ("sub|guest|mode"
#                               lines; see agents/openrouter for a reconciler)
#
# The agent must then serve :8080 (POST /api/chat) — see agent.py.

# --- 1. overlay root ---------------------------------------------------------
mount -t proc proc /proc 2>/dev/null
if [ ! -f /.fc-overlay ]; then
  UP=$(sed -n 's/.*fc_upper=\([^ ]*\).*/\1/p' /proc/cmdline)
  if [ -n "$UP" ]; then
    mount -t devtmpfs devtmpfs /dev 2>/dev/null
    # -o sync: a stop pulls the plug, unsynced writes would be lost.
    if mount -o sync "$UP" /mnt 2>/dev/null; then
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
export HOME=/home/agent
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

mkdir -p /config
mount -o ro /dev/vdb /config 2>/dev/null
set -a
[ -f /app/config.env ]    && . /app/config.env
[ -f /config/config.env ] && . /config/config.env
set +a
echo "nameserver ${GUEST_DNS:-1.1.1.1}" > /etc/resolv.conf

# --- 3. workspace over NFS ---------------------------------------------------
GW=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
WORKDIR="${WORKDIR:-/home/agent/workspace}"
mkdir -p "$WORKDIR"; chown agent:agent "$WORKDIR" 2>/dev/null
if [ -n "$GW" ] && [ -n "$AGENT_EXPORT" ]; then
  mount -t nfs4 -o nolock,soft,timeo=30,retrans=3 "${GW}:${AGENT_EXPORT}" "$WORKDIR" \
    || echo "[init] WARN: NFS mount failed (AGENT_EXPORT=$AGENT_EXPORT)"
fi
export WORKDIR MANAGER_URL="http://${GW}:8700"

# --- 4. the agent, as uid 1000 ----------------------------------------------
echo "[init] skeleton agent instance=${FC_INSTANCE} transport=${TRANSPORT:-web} workdir=$WORKDIR manager=$MANAGER_URL"
cd /app
setpriv --reuid=1000 --regid=1000 --init-groups python3 -u /app/agent.py

echo "[init] agent exited -> poweroff"
poweroff -f 2>/dev/null
while true; do sleep 3600; done
