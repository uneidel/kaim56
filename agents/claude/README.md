# claude-signal-firecracker

Die **Signal↔Claude-Code-Bridge** in einer **Firecracker-microVM** statt im Docker-
Container — echte HW-VM-Isolation. Claude läuft in der VM, erreicht deine Dienste
nur übers Netz (signalapi/portainer/pihole), kein Host-Filesystem, kein Docker-Socket.

```
Signal (Direkt-Chat) ─▶ signalapi ─▶ [ microVM: bridge.py + claude -p ] ─▶ Antwort
                                        eth0(172.30.0.2) ── tap0(172.30.0.1) ── NAT ── LAN
```

## Voraussetzungen (auf dieser Box vorhanden)
- `/dev/kvm` (KVM) ✅, VT-x ✅ — geprüft.
- `docker`, `curl`, `e2fsprogs` (mkfs.ext4 -d), `iproute2`, `iptables`.
- Der Bridge-Code liegt in `../claude-signal-bridge/bridge.py` (wird eingebaut).

## Dateien
| Datei | Zweck |
|---|---|
| `Dockerfile.rootfs` | Inhalt des Gast-Rootfs (Debian + Node + Claude Code + python) |
| `config.env` | Bridge-Konfiguration im Gast (Nummer, ALLOWED_SENDERS, …) |
| `guest-init.sh` | PID 1 in der VM: Mounts + DNS + startet die Bridge |
| `build.sh` | holt firecracker + Kernel, baut `rootfs.ext4`, legt Anmeldung ein |
| `net.sh` | tap0 + NAT (ROOT) |
| `vmconfig.json` | Firecracker-Maschinenconfig (2 vCPU, 1536 MB) |
| `run.sh` | startet die microVM |
| `claude-fc.service` | systemd-Autostart |

## Setup (Reihenfolge)

**1) Artefakte bauen** (als dein User; docker-Gruppe reicht):
```bash
cd /home/ulrich/claude-signal-firecracker
./build.sh
```
Erzeugt `firecracker`, `vmlinux`, `rootfs.ext4` und legt `~/.claude/.credentials.json`
in den Gast (Variante „Abo nutzen"). Für einen eigenen API-Key stattdessen in
`config.env` `ANTHROPIC_API_KEY=…` setzen und den Credentials-Schritt in `build.sh` weglassen.

**2) Netz + Start** (ROOT — tap/iptables/kvm):
```bash
sudo ./net.sh
sudo ./run.sh          # Vordergrund; Konsole der VM. Strg-C beendet.
```
oder dauerhaft per systemd:
```bash
sudo cp claude-fc.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now claude-fc.service
journalctl -u claude-fc -f
```

**3) Test:** katbot **direkt** `/help` schreiben → „🤖 katbot online".

## Netz-Details
- tap0 = `172.30.0.1/30` (Host), Gast = `172.30.0.2/30`, NAT via `enp0s31f6`.
- Gast-DNS = `10.0.0.245` (Pi-hole). Kernel konfiguriert eth0 via `ip=`-Bootparam.

## Sicherheit
- **Stärkere Isolation** als Container: Kompromittierung bleibt in der VM; Zugriff nur
  übers Netz. Kein `/home`-Mount, kein Docker-Socket.
- Trust-Boundary bleibt `ALLOWED_SENDERS` (nur deine Nummer).
- Anmeldung liegt im `rootfs.ext4` (OAuth-Token) — Image entsprechend schützen.
  Host + VM teilen sich den Token (Token-Rotation-Hinweis wie beim Container).

## Grenzen / Iteration
- Ich konnte hier **nichts davon ausführen** (root/kvm + Build sind gesperrt) — das Kit
  ist ungetestet. Typische Stolpersteine: Kernel-URL (S3-Pfad ändert sich), `mkfs.ext4 -d`
  (braucht e2fsprogs ≥1.43), Firewall/`FORWARD`-Policy. Schick mir die Ausgaben von
  `build.sh` / `run.sh` / `journalctl`, dann fixe ich es gezielt.
- signalapi im `native`-Mode → Empfang per Polling (Latenz ~1–10 s).
