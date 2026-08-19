#!/bin/sh
# PID 1 der Pi-microVM. Laeuft den Agenten als node (uid 1000). pi_bridge.py
# waehlt den Transport (signal|web) selbst anhand TRANSPORT.
#
# --- Overlay-Wurzel (Basis read-only + Upper je Instanz) ----------------------
# Der Manager haengt die geteilte Basis ro an und gibt per Bootarg fc_upper=
# das rw-Upper-Geraet mit. Hier: Upper mounten, overlayfs zusammensetzen,
# pivot_root, dieses Skript im neuen Root neu ausfuehren. Der Marker
# /.fc-overlay existiert nur IM Overlay (liegt im Upper) und verhindert die
# Endlos-Rekursion. Schlaegt irgendetwas fehl, bootet die VM auf der ro-Basis
# weiter (degradiert, aber erreichbar) statt gar nicht.
mount -t proc proc /proc 2>/dev/null
if [ ! -f /.fc-overlay ]; then
  UP=$(sed -n 's/.*fc_upper=\([^ ]*\).*/\1/p' /proc/cmdline)
  if [ -n "$UP" ]; then
    mount -t devtmpfs devtmpfs /dev 2>/dev/null
    # Wurzel ist read-only -> als Mountpoint MUSS ein Verzeichnis dienen,
    # das im Image existiert (/mnt); mkdir auf / schluege fehl.
    # -o sync: stop() zieht der VM den Stecker (SIGTERM an Firecracker) —
    # ohne sync laegen die letzten Schreibungen noch im Page-Cache und waeren
    # weg (beobachtet: 0-Byte-Datei). Synchron ist bei unserer Schreiblast ok.
    if mount -o sync "$UP" /mnt 2>/dev/null; then
      mkdir -p /mnt/upper /mnt/work /mnt/root
      if mount -t overlay overlay \
           -o lowerdir=/,upperdir=/mnt/upper,workdir=/mnt/work /mnt/root; then
        touch /mnt/root/.fc-overlay
        mkdir -p /mnt/root/oldroot
        cd /mnt/root
        pivot_root . oldroot && exec chroot . /init
        echo "[init] WARN: pivot_root fehlgeschlagen — weiter ohne Overlay"
        cd /
      else
        echo "[init] WARN: overlay-Mount fehlgeschlagen — weiter ohne Overlay"
      fi
    else
      echo "[init] WARN: Upper $UP nicht mountbar — weiter ohne Overlay"
    fi
  fi
fi
mount -t proc     proc     /proc  2>/dev/null
mount -t sysfs    sysfs    /sys   2>/dev/null
mount -t devtmpfs devtmpfs /dev   2>/dev/null
mkdir -p /dev/pts
mount -t devpts   devpts   /dev/pts 2>/dev/null   # PTYs (webterm/Browser-Terminal)
mount -t tmpfs    tmpfs    /tmp   2>/dev/null
echo "nameserver 10.0.0.245" > /etc/resolv.conf
export HOME=/home/node
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

mkdir -p /config
mount -o ro /dev/vdb /config 2>/dev/null
set -a
[ -f /app/config.env ] && . /app/config.env
[ -f /config/config.env ] && . /config/config.env
set +a

GW=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
WORKDIR="${CLAUDE_WORKDIR:-/home/node/workspace}"
mkdir -p "$WORKDIR"; chown node:node "$WORKDIR" 2>/dev/null
if [ "${AGENT_NFS:-1}" = "1" ] && [ -n "$GW" ]; then
  mount -t nfs4 -o nolock,soft,timeo=30,retrans=3 \
    "${GW}:${AGENT_EXPORT:-/}" "$WORKDIR" || echo "[init] WARN: NFS-Mount fehlgeschlagen"
fi
# --- dynamische Host-Ordner (Reconciler) --------------------------------------
# Der Manager pflegt .fcmnt/<inst>/desired.list (im Workspace sichtbar) und gibt
# jeden Ordner pro Guest-IP frei. fc_reconcile gleicht die Mounts an den
# gewuenschten Gast-Pfaden ab: sofort + danach alle 5s im Hintergrund -> live.
fc_reconcile() {
  LIST="$WORKDIR/.fcmnt/$FC_INSTANCE/desired.list"; want=" "
  if [ -f "$LIST" ]; then
    while IFS='|' read -r sub gp mode; do
      [ -n "$sub" ] && [ -n "$gp" ] || continue
      want="$want$gp "
      if ! awk -v p="$gp" '$2==p{f=1} END{exit !f}' /proc/mounts; then
        mkdir -p "$gp"; ro=""; [ "$mode" = "ro" ] && ro=",ro"
        mount -t nfs4 -o "nolock,soft,timeo=30,retrans=3$ro" "${GW}:${sub}" "$gp" 2>/dev/null \
          && echo "[init] + Host-Ordner $gp"
      fi
    done < "$LIST"
  fi
  awk -v s=":/.fcmnt/$FC_INSTANCE/" 'index($1,s){print $2}' /proc/mounts | while read -r mp; do
    case "$want" in *" $mp "*) : ;; *) umount -l "$mp" 2>/dev/null && echo "[init] - Host-Ordner $mp" ;; esac
  done
}
if [ -n "$FC_INSTANCE" ] && [ -n "$GW" ]; then
  fc_reconcile
  ( while true; do sleep 5; fc_reconcile; done ) &
fi

export CLAUDE_WORKDIR="$WORKDIR"
echo "[init] prime-agent transport=${TRANSPORT:-signal} model=${PRIME_MODEL:-default} provider=${PRIME_PROVIDER:-auto} workdir=$WORKDIR"
cd "$WORKDIR"
# Browser-Terminal (webterm) im Hintergrund, als Agent-User (uid 1000).
setpriv --reuid=1000 --regid=1000 --init-groups \
  env HOME=/home/node CLAUDE_WORKDIR="$WORKDIR" TERM_PORT=7682 \
  python3 -u /app/webterm.py >/var/log/webterm.log 2>&1 &
echo "[init] webterm auf :7682 gestartet"
setpriv --reuid=1000 --regid=1000 --init-groups python3 -u /app/prime_bridge.py

echo "[init] bridge beendet -> poweroff"
poweroff -f 2>/dev/null
while true; do sleep 3600; done
