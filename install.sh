#!/bin/sh
# kAIm56 — Installer fuer eine frische Maschine.
#
#   curl -fsSL https://raw.githubusercontent.com/<user>/kaim56/main/install.sh | sh
#   (oder aus einem Klon:  ./install.sh)
#
# Was er tut: Voraussetzungen pruefen, Repo holen (falls noetig), Laufzeit-
# Layout unter $KAIM56_BASE anlegen, Firecracker-Binary laden, Kernel (vmlinux)
# beziehen, Rootfs/Container bauen, systemd-Dienst einrichten, Smoke-Test.
# Idempotent: ein zweiter Lauf aktualisiert.
#
# Optionen / Env:
#   --check            nur Voraussetzungen pruefen, nichts installieren
#   --files-only       nur Layout+Binaries (kein Build, kein Dienst) — fuer Tests/Updates
#   --no-build         Docker-Builds ueberspringen (nur Dateien/Dienst)
#   --with-voice       Sprachdienst (STT/TTS, ~2 GB Docker-Build) mitinstallieren
#   --with-agents      auch pi/prime/claude-Rootfs bauen (gross)
#   KAIM56_BASE=<dir>  Zielverzeichnis (Default: $HOME)
#   VMLINUX_URL=<url>  Download-Quelle fuer den Gast-Kernel (Release-Asset)
#   GUEST_DNS=<ip>     DNS fuer die microVMs (Default: 1.1.1.1)
#   REPO_URL=<url>     Git-Quelle (Default: github kaim56)
set -eu

FC_VERSION="v1.16.1"                       # gleiche Version wie die Referenz-Installation
REPO_URL="${REPO_URL:-https://github.com/ulrich-kat56/kaim56.git}"
BASE="${KAIM56_BASE:-$HOME}"
FC_DIR="$BASE/firecracker"
GUEST_DNS="${GUEST_DNS:-1.1.1.1}"
CHECK_ONLY=0; NO_BUILD=0; WITH_VOICE=0; WITH_AGENTS=0; FILES_ONLY=0
for a in "$@"; do case "$a" in
  --check) CHECK_ONLY=1;;
  --files-only) FILES_ONLY=1; NO_BUILD=1;;
  --no-build) NO_BUILD=1;;
  --with-voice) WITH_VOICE=1;;
  --with-agents) WITH_AGENTS=1;;
  *) echo "unbekannte Option: $a"; exit 2;;
esac; done

say()  { printf '\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

# ── [1] Voraussetzungen ──────────────────────────────────────────────────────
say "[1/7] Voraussetzungen pruefen"
[ "$(uname -m)" = "x86_64" ] || fail "x86_64 noetig (ist: $(uname -m))"
[ -e /dev/kvm ] || fail "/dev/kvm fehlt — KVM aktivieren (BIOS/nested virt); ohne KVM keine microVMs"
[ -w /dev/kvm ] || echo "  Hinweis: /dev/kvm nicht schreibbar fuer $(id -un) — Manager laeuft als root, ok"
command -v python3 >/dev/null || fail "python3 fehlt"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || fail "python3 >= 3.9 noetig"
command -v docker >/dev/null || fail "docker fehlt (wird fuer Rootfs-/Dienst-Builds gebraucht)"
docker info >/dev/null 2>&1 || fail "docker-Daemon nicht erreichbar (Gruppe 'docker'? sudo?)"
command -v git >/dev/null || fail "git fehlt"
command -v curl >/dev/null || fail "curl fehlt"
command -v iptables >/dev/null 2>&1 || [ -x /sbin/iptables ] || [ -x /usr/sbin/iptables ] || fail "iptables fehlt (NAT fuer die Gaeste)"
MKFS="$(command -v mkfs.ext4 || echo /sbin/mkfs.ext4)"; [ -x "$MKFS" ] || fail "mkfs.ext4 fehlt (e2fsprogs)"
command -v systemctl >/dev/null || fail "systemd noetig (Dienst-Installation)"
command -v rsync >/dev/null || fail "rsync fehlt"
echo "  alles da."
[ "$CHECK_ONLY" = "1" ] && { say "Check ok — Installation wuerde nach $BASE gehen."; exit 0; }

# ── [2] Quellcode ────────────────────────────────────────────────────────────
say "[2/7] Quellcode"
SELF_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/manager/manager.py" ]; then
  SRC="$SELF_DIR"; echo "  nutze lokalen Klon: $SRC"
else
  SRC="$BASE/kaim56"
  if [ -d "$SRC/.git" ]; then (cd "$SRC" && git pull --ff-only); else git clone --depth 1 "$REPO_URL" "$SRC"; fi
fi

# ── [3] Laufzeit-Layout (Repo -> Arbeitsverzeichnisse) ──────────────────────
say "[3/7] Laufzeit-Layout unter $BASE"
mkdir -p "$FC_DIR/bin" "$FC_DIR/instances" "$FC_DIR/run" "$FC_DIR/audit"
rsync -a "$SRC/manager/manager.py" "$SRC/manager/chatui.py" "$SRC/manager/webterm.py" \
         "$SRC/manager/setup-nfs-host.sh" "$SRC/manager/logo.svg" \
         "$SRC/manager/mcp-catalog.json" "$SRC/manager/personas.json" \
         "$SRC/manager/secret-policy.json" "$SRC/manager/run-tests.sh" "$FC_DIR/" 2>/dev/null || true
rsync -a "$SRC/manager/templates/" "$FC_DIR/templates/"
rsync -a "$SRC/manager/tests/" "$FC_DIR/tests/" 2>/dev/null || true
for pair in "openrouter:openrouter-agent" "pi:pi-agent" "prime:prime-agent" "claude:claude-signal-firecracker"; do
  from="${pair%%:*}"; to="${pair##*:}"
  [ -d "$SRC/agents/$from" ] && rsync -a --exclude '__pycache__' "$SRC/agents/$from/" "$BASE/$to/"
done
for d in voice embed mcp-hub; do [ -d "$SRC/$d" ] && rsync -a "$SRC/$d/" "$BASE/$d/"; done
chmod +x "$FC_DIR/run-tests.sh" 2>/dev/null || true

# ── [4] Binaries: firecracker + Gast-Kernel ─────────────────────────────────
say "[4/7] Firecracker $FC_VERSION + Kernel"
if [ ! -x "$FC_DIR/bin/firecracker" ]; then
  T="$(mktemp -d)"
  curl -fsSL -o "$T/fc.tgz" \
    "https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}/firecracker-${FC_VERSION}-x86_64.tgz"
  tar -xzf "$T/fc.tgz" -C "$T"
  install -m 0755 "$T"/release-*/firecracker-*-x86_64 "$FC_DIR/bin/firecracker"
  rm -rf "$T"
fi
"$FC_DIR/bin/firecracker" --version | head -1
if [ ! -f "$FC_DIR/bin/vmlinux" ]; then
  if [ -n "${VMLINUX_URL:-}" ]; then
    curl -fsSL -o "$FC_DIR/bin/vmlinux" "$VMLINUX_URL"
  elif [ -f "$SRC/manager/bin/vmlinux" ]; then
    cp "$SRC/manager/bin/vmlinux" "$FC_DIR/bin/vmlinux"
  else
    fail "Gast-Kernel fehlt: VMLINUX_URL=<Release-Asset-URL> setzen (oder bin/vmlinux manuell nach $FC_DIR/bin/ legen)"
  fi
fi
echo "  Kernel: $(du -h "$FC_DIR/bin/vmlinux" | cut -f1)"

# ── [5] Builds (Docker) ──────────────────────────────────────────────────────
if [ "$NO_BUILD" = "0" ]; then
  say "[5/7] Rootfs + Dienste bauen (dauert beim ersten Mal)"
  ( cd "$BASE/openrouter-agent" && FC_DIR="$FC_DIR" bash build-openrouter-rootfs.sh )
  ( cd "$BASE/embed"   && docker build -q -t kaim56-embed .   && docker rm -f kaim56-embed 2>/dev/null; \
    docker run -d --restart unless-stopped --name kaim56-embed -p 127.0.0.1:8772:8772 kaim56-embed )
  ( cd "$BASE/mcp-hub" && docker build -q -t kaim56-mcp-hub . && docker rm -f kaim56-mcp-hub 2>/dev/null; \
    docker run -d --restart unless-stopped --name kaim56-mcp-hub -p 127.0.0.1:8771:8771 kaim56-mcp-hub )
  if [ "$WITH_VOICE" = "1" ]; then
    ( cd "$BASE/voice" && docker build -t kaim56-voice . && docker rm -f kaim56-voice 2>/dev/null; \
      docker run -d --restart unless-stopped --name kaim56-voice -p 127.0.0.1:8770:8770 kaim56-voice )
  fi
  if [ "$WITH_AGENTS" = "1" ]; then
    ( cd "$BASE/pi-agent"    && FC_DIR="$FC_DIR" bash build-pi-rootfs.sh )
    ( cd "$BASE/prime-agent" && FC_DIR="$FC_DIR" bash build-prime-rootfs.sh )
    ( cd "$BASE/claude-signal-firecracker" && FC_DIR="$FC_DIR" PATH="$PATH:/sbin:/usr/sbin" bash build-rootfs.sh )
  fi
else
  say "[5/7] Builds uebersprungen (--no-build)"
fi

[ "$FILES_ONLY" = "1" ] && { say "FERTIG (files-only): Layout unter $BASE steht."; exit 0; }

# ── [6] systemd-Dienst ───────────────────────────────────────────────────────
say "[6/7] systemd-Dienst (braucht sudo)"
HOSTIF="$(ip route 2>/dev/null | awk '/default/{print $5; exit}')"
PASS_LINE=""
if [ ! -f /etc/systemd/system/firecracker-manager.service ]; then
  PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 20)"
  PASS_LINE="Environment=MANAGER_PASS=$PW"
  echo "  Web-Login: admin / $PW   (in der Unit aenderbar)"
fi
sudo tee /etc/systemd/system/firecracker-manager.service >/dev/null <<UNIT
[Unit]
Description=Firecracker Manager (kAIm56)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$FC_DIR
Environment=PORT=8700
Environment=HOSTIF=$HOSTIF
Environment=GUEST_DNS=$GUEST_DNS
$PASS_LINE
ExecStart=/usr/bin/python3 $FC_DIR/manager.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
sudo sysctl -qw net.ipv4.ip_forward=1
echo net.ipv4.ip_forward=1 | sudo tee /etc/sysctl.d/99-kaim56.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now firecracker-manager

# ── [7] Smoke-Test ───────────────────────────────────────────────────────────
say "[7/7] Smoke-Test"
sleep 3
curl -fsS -o /dev/null "http://127.0.0.1:8700/" && echo "  Manager antwortet ✓" || fail "Manager antwortet nicht — journalctl -u firecracker-manager"
FC_DIR="$FC_DIR" AGENT_PATH="$BASE/openrouter-agent/agent.py" \
  python3 "$FC_DIR/tests/e2e.py" AgentLogic ManagerFunctions 2>&1 | tail -2 || true

say "FERTIG"
cat <<EOF

  Web-UI:    http://$(hostname -I 2>/dev/null | awk '{print $1}'):8700
  Naechste Schritte:
    1. Settings-Tab: OpenRouter- oder OrcaRouter-API-Key eintragen
    2. Instances-Tab: erste Instanz aus dem openrouter-Template anlegen
    3. optional: sudo $FC_DIR/setup-nfs-host.sh  (Host-Ordner-Mounts)
EOF
