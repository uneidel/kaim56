# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Networking and firewall (security boundary): the tap per instance, anti-spoof rules, what a guest may reach on the host, the per-instance egress rules (internet on/off, MCP and llama endpoints), and the opt-in host firewall (HOST_FIREWALL).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import socket

from mgr import host as _host
from mgr import instances as _instances
from mgr import mcp as _mcp
from mgr import settings as _settings
from mgr import util as _util


POOL = "172.30.0.0/16"


# ---- networking ------------------------------------------------------------
def ensure_net_base():
    _util.sh("sysctl", "-w", "net.ipv4.ip_forward=1", check=False)
    r = _util.sh("iptables", "-t", "nat", "-C", "POSTROUTING", "-s", POOL, "-o", _host.HOSTIF,
           "-j", "MASQUERADE", check=False)
    if r.returncode != 0:
        _util.sh("iptables", "-t", "nat", "-A", "POSTROUTING", "-s", POOL, "-o", _host.HOSTIF,
           "-j", "MASQUERADE", check=False)
    # Guest isolation: microVMs must NOT route to each other. A compromised
    # agent could otherwise reach another instance's chat/term ports (8080/7682,
    # bound to 0.0.0.0, no auth). Backstop DROP for pool->pool; the tap ACCEPTs
    # below are additionally scoped so they never even match guest-to-guest.
    # Guest->gateway (8700 broker) is host-local (INPUT) and unaffected by this.
    if _util.sh("iptables", "-C", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP",
          check=False).returncode != 0:
        _util.sh("iptables", "-A", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP", check=False)
    ensure_guest_input_rules()
    ensure_browser_rules()
    ensure_host_firewall()


# The headless browser (mcp-browser/run.sh) runs in its own container on its own
# bridge: it loads whatever page an agent names, so it gets the internet but
# nothing private — no LAN (portainer, HA), no host (manager, its own gateway),
# no VM, no hub; only the hub may open its MCP port. mangle/PREROUTING on
# purpose: it sees forwarded AND host-bound packets before any filter rule, so
# the per-tap ACCEPTs that apply_internet keeps inserting on top of FORWARD
# (e.g. "-o fcN ! -s POOL") cannot open a path from the browser to a VM.
BROWSER_BRIDGE, BROWSER_IP, BROWSER_HUB_IP, BROWSER_PORT = "kaim56-br", "172.31.56.2", "172.31.56.3", "8931"


def browser_rules():
    b, tcp = BROWSER_BRIDGE, ("-p", "tcp")
    return ([("-i", b, "-s", BROWSER_HUB_IP, "-d", BROWSER_IP, *tcp, "--dport", BROWSER_PORT, "-j", "ACCEPT"),
             ("-i", b, "-s", BROWSER_IP, "-d", BROWSER_HUB_IP, *tcp, "--sport", BROWSER_PORT,
              "-m", "conntrack", "--ctstate", "ESTABLISHED", "-j", "ACCEPT")]
            + [("-i", b, "-d", net, "-j", "DROP") for net in _BROWSER_BLOCK])


def ensure_browser_rules():
    """Idempotent, order-preserving: every rule is removed and re-inserted on top."""
    for spec in reversed(browser_rules()):
        while _util.sh("iptables", "-t", "mangle", "-C", "PREROUTING", *spec, check=False).returncode == 0:
            _util.sh("iptables", "-t", "mangle", "-D", "PREROUTING", *spec, check=False)
        _util.sh("iptables", "-t", "mangle", "-I", "PREROUTING", "1", *spec, check=False)


# Guest -> host: what a VM legitimately needs from its gateway (.1 of the /30).
GUEST_INPUT_ACCEPT = (
    ("-p", "tcp", "--dport", str(_host.LISTEN[1])),          # manager: API, broker, LLM proxy
    ("-p", "tcp", "--dport", "2049"),                  # NFS workspace
    ("-p", "icmp"),                                    # ping the gateway
    ("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED"),  # replies to host->guest (proxy)
)


def ensure_guest_input_rules():
    """Guest -> host is limited to the manager port and NFS. Without this every
    host service listening on 0.0.0.0 (sshd, rpcbind, …) is one hop away from
    each VM. The DROP goes in first so the ACCEPTs inserted afterwards sit
    above it; idempotent, so a restart adds nothing twice."""
    if _util.sh("iptables", "-C", "INPUT", "-i", "fc+", "-j", "DROP", check=False).returncode != 0:
        _util.sh("iptables", "-I", "INPUT", "1", "-i", "fc+", "-j", "DROP", check=False)
    for spec in GUEST_INPUT_ACCEPT:
        # Position matters, not just presence: an ACCEPT appended BELOW the
        # DROP (an older setup script did that for NFS) never matches, and a
        # presence check would leave it there. Remove every copy, insert on top.
        for _ in range(8):
            if _util.sh("iptables", "-D", "INPUT", "-i", "fc+", *spec, "-j", "ACCEPT", check=False).returncode != 0:
                break
        _util.sh("iptables", "-I", "INPUT", "1", "-i", "fc+", *spec, "-j", "ACCEPT", check=False)
    # A pool address arriving on the LAN interface is forged (a LAN box posing
    # as a stopped VM would pass every by-IP check): drop it first.
    if _host.HOSTIF and _util.sh("iptables", "-C", "INPUT", "-i", _host.HOSTIF, "-s", POOL, "-j", "DROP",
                     check=False).returncode != 0:
        _util.sh("iptables", "-I", "INPUT", "1", "-i", _host.HOSTIF, "-s", POOL, "-j", "DROP", check=False)


def _antispoof_rules(n):
    return [(chain, ("-i", n["tap"], "!", "-s", n["guest"], "-j", "DROP"))
            for chain in ("INPUT", "FORWARD")]


def ensure_antispoof(inst):
    """A VM's packets must carry its own /30 address: the source IP is the
    guest's identity for the manager (instance_by_ip), so a forged source would
    be a forged identity. Always on top — above the instance's FORWARD chain."""
    for chain, spec in _antispoof_rules(_instances.net_of(inst)):
        while _util.sh("iptables", "-C", chain, *spec, check=False).returncode == 0:
            _util.sh("iptables", "-D", chain, *spec, check=False)
        _util.sh("iptables", "-I", chain, "1", *spec, check=False)


def clear_antispoof(inst):
    for chain, spec in _antispoof_rules(_instances.net_of(inst)):
        while _util.sh("iptables", "-C", chain, *spec, check=False).returncode == 0:
            _util.sh("iptables", "-D", chain, *spec, check=False)


def setup_tap(inst, uid=None, gid=None):
    """The VM's tap; owned by its jail uid when jailed (mgr/vm.py), so the
    unprivileged Firecracker may attach to it."""
    n = _instances.net_of(inst)
    _util.sh("ip", "link", "del", n["tap"], check=False)
    _util.sh("ip", "tuntap", "add", n["tap"], "mode", "tap",
             *(("user", str(uid), "group", str(gid)) if uid is not None else ()))
    _util.sh("ip", "addr", "add", f"{n['host']}/30", "dev", n["tap"])
    _util.sh("ip", "link", "set", n["tap"], "up")
    # The host has FORWARD policy DROP + Docker chains in front of it -> generic
    # rules don't apply reliably. So allow tap traffic RIGHT AT THE TOP (before
    # DROP/Docker) — but ONLY to/from outside the pool. This lets the guest reach
    # the internet (destination not in the pool) and replies back (source not in
    # the pool), while guest-to-guest (both in the pool) matches no ACCEPT rule
    # and gets caught by the pool->pool DROP or the DROP policy.
    # Clear old, unrestricted ACCEPTs of the same tap first (the tap name is
    # reused on restart, otherwise the old hole would stay open).
    for spec in (["-i", n["tap"]], ["-o", n["tap"]]):
        while _util.sh("iptables", "-C", "FORWARD", *spec, "-j", "ACCEPT", check=False).returncode == 0:
            _util.sh("iptables", "-D", "FORWARD", *spec, "-j", "ACCEPT", check=False)
    apply_internet(inst, inst.get("internet", True))


# Until now, guests with internet=on could go anywhere — including the whole
# LAN. Home Assistant and Portainer were thus reachable from EVERY VM, whether
# the MCP was assigned to it or not (the broker protects the tokens, but the
# door stood open anyway). Now: internet yes, LAN no — except the endpoints of
# the MCPs listed in the instance's MCP_SERVERS, and the guests' DNS.
# DNS for the guests (ends up in resolv.conf via guest-init). Site-specific —
# set it via env on other installations; 1.1.1.1 works everywhere.
GUEST_DNS = os.environ.get("GUEST_DNS") or _settings.SITE.get("GUEST_DNS") or "1.1.1.1"
_PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                 "100.64.0.0/10", "169.254.0.0/16")     # CGNAT/Tailscale, link-local too
_BROWSER_BLOCK = _PRIVATE_NETS + ("127.0.0.0/8", "0.0.0.0/8", "192.0.0.0/24",
                                  "198.18.0.0/15", "224.0.0.0/3")   # + loopback, benchmark, multicast/reserved


def _mcp_endpoints(inst):
    """LAN targets (ip, port) that this instance needs according to MCP_SERVERS.
    Read from the catalog, not from the instance — the latter only holds names.
    IP literals only: a hostname in the catalog that resolves into the LAN would
    NOT be allowed here (deliberately; enter the IP instead)."""
    names = {x for x in (inst.get("config", {}).get("MCP_SERVERS", "") or "").split(",") if x}
    if not names:
        return []
    out = []
    for m in _mcp.load_mcps():
        if m.get("name") not in names:
            continue
        for scheme, host, port in re.findall(
                r"(https?)://(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?", json.dumps(m)):
            try:
                import ipaddress
                if not ipaddress.ip_address(host).is_private:
                    continue          # public targets are covered by the internet rule
            except ValueError:
                continue
            out.append((host, int(port or (443 if scheme == "https" else 80))))
    return sorted(set(out))


def _llama_endpoint(inst):
    """(ip, port) of the llama.cpp server, if the instance uses it AND it is on
    the private network — then the gating must let it through. An endpoint on the
    host (reachable via the gateway) or on the internet needs no special rule."""
    ep = (inst.get("config", {}).get("LLAMA_ENDPOINT") or "").strip()
    if not ep:
        return None
    m = re.search(r"(https?)://(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?", ep)
    if not m:
        return None
    import ipaddress
    scheme, host, port = m.group(1), m.group(2), m.group(3)
    try:
        if not ipaddress.ip_address(host).is_private:
            return None
    except ValueError:
        return None
    return (host, int(port or (443 if scheme == "https" else 80)))


def _fc_chain(inst):
    return "FC-" + re.sub(r"[^a-zA-Z0-9_.-]", "", inst["name"])[:24]


def apply_internet(inst, allow):
    """Set/remove the instance's egress rules. `allow=False` means: the VM may
    not leave its own /30 — no LAN, no internet. The manager broker at the
    gateway (8700) stays reachable (host-local, INPUT). And with it the LLM
    endpoint: an agent without internet CANNOT think.

    With allow=True the instance gets its own FORWARD chain:
      1. its MCP endpoints (tcp, targeted)     -> ACCEPT
      2. the guest DNS (53)                     -> ACCEPT
      3. private networks                       -> REJECT (not DROP: the
         agent should fail immediately, not run into a 30 s timeout)
      4. everything outside the pool (internet) -> ACCEPT
    The return path stays the generic rule: through NAT, replies are only
    possible for connections the guest opened itself."""
    n = _instances.net_of(inst)
    chain = _fc_chain(inst)

    # Clear out leftovers, idempotent: jump rule, chain, old direct rule.
    _util.sh("iptables", "-D", "FORWARD", "-i", n["tap"], "-j", chain, check=False)
    _util.sh("iptables", "-F", chain, check=False)
    _util.sh("iptables", "-X", chain, check=False)
    while _util.sh("iptables", "-C", "FORWARD", "-i", n["tap"], "!", "-d", POOL,
             "-j", "ACCEPT", check=False).returncode == 0:
        _util.sh("iptables", "-D", "FORWARD", "-i", n["tap"], "!", "-d", POOL,
           "-j", "ACCEPT", check=False)

    back = ["-o", n["tap"], "!", "-s", POOL]
    have_back = _util.sh("iptables", "-C", "FORWARD", *back, "-j", "ACCEPT", check=False).returncode == 0
    if not allow:
        if have_back:
            _util.sh("iptables", "-D", "FORWARD", *back, "-j", "ACCEPT", check=False)
        # Explicit, not by omission: "no network" used to rely on the FORWARD
        # policy being DROP — on a host where it is ACCEPT the switch did
        # nothing (found by a sandboxed sub-agent that curled the internet with
        # egress=none). The chain rejects everything outside the pool; the
        # manager at the gateway is INPUT, not FORWARD, and stays reachable.
        _util.sh("iptables", "-N", chain, check=False)
        _util.sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "REJECT", check=False)
        _util.sh("iptables", "-I", "FORWARD", "1", "-i", n["tap"], "-j", chain, check=False)
        ensure_antispoof(inst)
        return

    _util.sh("iptables", "-N", chain, check=False)
    allow = list(_mcp_endpoints(inst))
    lp = _llama_endpoint(inst)
    if lp:
        allow.append(lp)
    for ip, port in allow:
        _util.sh("iptables", "-A", chain, "-d", ip, "-p", "tcp", "--dport", str(port),
           "-j", "ACCEPT", check=False)
    for proto in ("udp", "tcp"):
        _util.sh("iptables", "-A", chain, "-d", GUEST_DNS, "-p", proto, "--dport", "53",
           "-j", "ACCEPT", check=False)
    for net in _PRIVATE_NETS:
        _util.sh("iptables", "-A", chain, "-d", net, "-j", "REJECT", check=False)
    # Egress allowlist (guardrail): if EGRESS_ALLOW is in the instance config
    # (comma list of domains/IPs), the VM may go ONLY there — instead of
    # "everything except private". Domains are resolved at start (A records); a
    # stop/start is needed if the target's DNS changes. Empty = as before.
    egress = (inst.get("config", {}).get("EGRESS_ALLOW", "") or "").strip()
    if egress:
        seen = set()
        for host in [h.strip() for h in egress.split(",") if h.strip()]:
            try:
                infos = socket.getaddrinfo(host, None, socket.AF_INET)
                ips = sorted({i[4][0] for i in infos})
            except OSError:
                print(f"[egress] {inst['name']}: '{host}' not resolvable — skipped",
                      flush=True)
                continue
            for ip in ips:
                if ip not in seen:
                    seen.add(ip)
                    _util.sh("iptables", "-A", chain, "-d", ip, "-j", "ACCEPT", check=False)
        _util.sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "REJECT", check=False)
    else:
        _util.sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "ACCEPT", check=False)
    _util.sh("iptables", "-I", "FORWARD", "1", "-i", n["tap"], "-j", chain, check=False)
    if not have_back:
        _util.sh("iptables", "-I", "FORWARD", "1", *back, "-j", "ACCEPT", check=False)
    ensure_antispoof(inst)


def teardown_tap(inst):
    # Rules point at the tap NAME and survive deletion of the device — without
    # cleanup, dead chains pile up.
    n = _instances.net_of(inst)
    chain = _fc_chain(inst)
    _util.sh("iptables", "-D", "FORWARD", "-i", n["tap"], "-j", chain, check=False)
    _util.sh("iptables", "-F", chain, check=False)
    _util.sh("iptables", "-X", chain, check=False)
    clear_antispoof(inst)
    _util.sh("ip", "link", "del", n["tap"], check=False)


# ---- host firewall (opt-in: site.json HOST_FIREWALL=1) ---------------------
# INPUT and FORWARD policy are ACCEPT on the host; the rules above only guard
# the taps. This adds three own chains that can only DROP, never open
# (anything allowed just RETURNs to the existing rules), so turning it on cannot
# widen access and SSH (22, any source) stays reachable:
#   KAIM56-IN   (top of INPUT)     docker bridges -> host only :8700; manager
#               :8700 only from lo/taps/docker/HOST_FW_MANAGER_FROM; rpcbind
#               and NFS (111/2049) never from outside the taps
#   KAIM56-FWD  (end of FORWARD)   forwarded traffic from the LAN that no rule
#               accepted (e.g. a LAN box routing into docker's 172.17/16)
#   KAIM56-PRE  (mangle PREROUTING) new connections from the LAN to the VM pool
#               (the per-tap "-o fcN ! -s POOL" ACCEPT has no state match)
# The taps (fc+) RETURN at once: ensure_guest_input_rules owns them.
# Rollback: remove_host_firewall() — unhooks and deletes only these chains.
FW_IN, FW_FWD, FW_PRE = "KAIM56-IN", "KAIM56-FWD", "KAIM56-PRE"
DOCKER_IFS = ("docker0", "br-+")


def host_firewall_on():
    return str(os.environ.get("HOST_FIREWALL") or _settings.SITE.get("HOST_FIREWALL") or "").strip() in ("1", "true", "on", "yes")


def _lan_net():
    """The HOSTIF's own IPv4 subnet ("192.168.1.0/24"), "" if unknown."""
    import ipaddress
    try:
        out = _util.sh("ip", "-o", "-4", "addr", "show", "dev", _host.HOSTIF, check=False).stdout or ""
        m = re.search(r"inet (\S+)", out)
        return str(ipaddress.ip_interface(m.group(1)).network) if m else ""
    except Exception:
        return ""


def _manager_from():
    """Who may reach :8700 over the LAN: HOST_FW_MANAGER_FROM (comma list of
    IPs/nets, e.g. the reverse proxy), default the HOSTIF subnet."""
    v = os.environ.get("HOST_FW_MANAGER_FROM") or _settings.SITE.get("HOST_FW_MANAGER_FROM") or ""
    nets = [x.strip() for x in str(v).split(",") if x.strip()]
    return nets or [n for n in (_lan_net(),) if n]


def host_firewall_rules():
    """[(table, chain, spec), …] — the content of the three chains, in order."""
    port = str(_host.LISTEN[1])
    tcp = ("-p", "tcp", "--dport")
    inp = [("-i", "fc+", "-j", "RETURN"),
           ("-i", "lo", "-j", "RETURN"),
           ("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"),
           (*tcp, "22", "-j", "RETURN")]
    for i in DOCKER_IFS:
        inp += [("-i", i, *tcp, port, "-j", "RETURN"), ("-i", i, "-j", "DROP")]
    inp.append(("-i", BROWSER_BRIDGE, "-j", "DROP"))
    srcs = _manager_from()
    if srcs:        # no known source (subnet unreadable): leave :8700 open rather than lock the UI out
        inp += [("-i", _host.HOSTIF, "-s", src, *tcp, port, "-j", "RETURN") for src in srcs]
        inp.append((*tcp, port, "-j", "DROP"))
    inp += [("-p", p, "--dport", d, "-j", "DROP") for d in ("111", "2049") for p in ("tcp", "udp")]
    fwd = [("-i", _host.HOSTIF, "-j", "DROP")]
    pre = [("-i", _host.HOSTIF, "-d", POOL, "-m", "conntrack", "--ctstate", "NEW", "-j", "DROP")]
    return ([("filter", FW_IN, s) for s in inp] + [("filter", FW_FWD, s) for s in fwd]
            + [("mangle", FW_PRE, s) for s in pre])


# (table, parent chain, own chain, where): "top" = -I 1, "end" = -A
_FW_HOOKS = (("filter", "INPUT", FW_IN, "top"), ("filter", "FORWARD", FW_FWD, "end"),
             ("mangle", "PREROUTING", FW_PRE, "top"))


def _fw_hook(table, parent, chain, where):
    pos = ("-I", parent, "1") if where == "top" else ("-A", parent)
    return ("iptables", "-t", table, *pos, "-j", chain)


def host_firewall_cmds():
    """The exact iptables commands ensure_host_firewall runs; the last three
    (the jumps) only when missing."""
    cmds = []
    for table, _parent, chain, _w in _FW_HOOKS:
        cmds += [("iptables", "-t", table, "-N", chain), ("iptables", "-t", table, "-F", chain)]
    cmds += [("iptables", "-t", t, "-A", c, *s) for t, c, s in host_firewall_rules()]
    return cmds + [_fw_hook(*h) for h in _FW_HOOKS]


def ensure_host_firewall():
    """Idempotent: the own chains are refilled (flush + append), a jump is added
    only when missing. Off (the default) = not a single iptables call."""
    if not host_firewall_on():
        return False
    for c in host_firewall_cmds()[:-len(_FW_HOOKS)]:
        _util.sh(*c, check=False)
    for table, parent, chain, where in _FW_HOOKS:
        if _util.sh("iptables", "-t", table, "-C", parent, "-j", chain, check=False).returncode != 0:
            _util.sh(*_fw_hook(table, parent, chain, where), check=False)
    return True


def remove_host_firewall():
    """Rollback: unhook and delete the own chains; nothing else is touched."""
    for table, parent, chain, _w in _FW_HOOKS:
        while _util.sh("iptables", "-t", table, "-C", parent, "-j", chain, check=False).returncode == 0:
            _util.sh("iptables", "-t", table, "-D", parent, "-j", chain, check=False)
        _util.sh("iptables", "-t", table, "-F", chain, check=False)
        _util.sh("iptables", "-t", table, "-X", chain, check=False)


if __name__ == "__main__":      # python3 -m mgr.netfw --dry-run | --apply | --rollback
    import sys
    arg = (sys.argv[1:] or ["--dry-run"])[0]
    if arg == "--apply":
        print("applied" if ensure_host_firewall() else "HOST_FIREWALL is off — nothing done")
    elif arg == "--rollback":
        remove_host_firewall()
        print("removed", FW_IN, FW_FWD, FW_PRE)
    else:
        print(f"# HOST_FIREWALL={'on' if host_firewall_on() else 'off'} HOSTIF={_host.HOSTIF}")
        print("\n".join(" ".join(c) for c in host_firewall_cmds()))
