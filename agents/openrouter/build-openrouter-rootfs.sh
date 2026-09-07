#!/usr/bin/env bash
# Baut das OpenRouter-Agent-Rootfs -> instances/openrouter-rootfs.ext4.
cd "$(dirname "$0")"
# mkfs.ext4 liegt in /usr/sbin — das steht in der PATH einer normalen
# Nutzer-Shell nicht drin, und root braucht es fuer ein Datei-Image nicht.
export PATH="$PATH:/usr/sbin:/sbin"
INST="${FC_DIR:-/home/ulrich/firecracker}"/instances/openrouter-rootfs.ext4
fail(){ echo "❌ FEHLER in: $1"; exit 1; }

echo "== [1] docker build =="
docker build -f Dockerfile.openrouter -t openrouter-fc-rootfs . || fail "docker build"

echo "== [2] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create openrouter-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null
mkdir -p rootfs/home/node/workspace

echo "== [3] ext4-Image =="
SIZE_MB=${ROOTFS_MB:-2048}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail truncate
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d"
rm -rf rootfs

echo "== [4] als Instanz-Rootfs ablegen =="
# NIE per cp in die Zieldatei hineinschreiben: haelt eine laufende microVM sie
# als Blockgeraet offen, mischen sich altes und neues Image und der Gast faellt
# beim naechsten Boot in einen ext4-Checksum-Panic. Erst danebenlegen, dann
# atomar umhaengen — eine laufende VM behaelt ihren alten Inode bis zum Stop.
for pid in "${FC_DIR:-/home/ulrich/firecracker}"/run/*.pid; do
  [ -e "$pid" ] || continue
  p=$(cat "$pid" 2>/dev/null)
  # /proc statt kill -0: firecracker laeuft als root, ein Signal-Test aus einer
  # Nutzer-Shell schlaegt dort fehl und die Warnung bliebe stumm.
  if [ -n "$p" ] && [ -d "/proc/$p" ]; then
    n=$(basename "$pid" .pid)
    grep -q "openrouter-rootfs" ""${FC_DIR:-/home/ulrich/firecracker}"/instances/$n.json" 2>/dev/null &&
      echo "⚠️  Instanz '$n' laeuft auf diesem Rootfs — sie sieht das neue Image erst nach Stop/Start."
  fi
done
cp rootfs.ext4 "$INST.new" || fail "cp -> instances/"
mv -f "$INST.new" "$INST" || fail "mv -> instances/"
echo "✅ FERTIG:"; ls -lh "$INST"

# --smoke <instanz>: die genannte Instanz auf das neue Image neu starten und
# den modellfreien Registry-Test (/tools) dagegen laufen lassen. Ein kaputtes
# Image faellt so HIER auf, nicht erst beim naechsten Sprachbefehl.
if [ "${1:-}" = "--smoke" ] && [ -n "${2:-}" ]; then
  MGR="${MANAGER_URL:-http://127.0.0.1:8700}"
  echo "== [5] smoke: Instanz '$2' neu starten =="
  curl -sf -m 200 -X POST "$MGR/api/instances/$2/restart" >/dev/null || fail "restart $2"
  # "running" heisst nur: der VM-Prozess lebt. Der Agent drin braucht noch
  # ~20 s (MCP-Init) — warten, bis /tools ueber den Proxy mit 200 antwortet.
  for i in $(seq 1 40); do
    curl -sf -m 30 -X POST "$MGR/i/$2/api/chat" -H 'Content-Type: application/json' \
      -d '{"message":"/tools"}' -o /dev/null && break
    sleep 3
  done
  echo "== [6] smoke: /tools-Registry pruefen =="
  MANAGER_URL="$MGR" python3 "${FC_DIR:-/home/ulrich/firecracker}/tests/e2e.py" \
    LiveAgent.test_tools_registry_after_rebuild || fail "smoke test"
  echo "✅ SMOKE OK ($2 laeuft auf dem neuen Image, Registry vollstaendig)"
fi
