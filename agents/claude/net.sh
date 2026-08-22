#!/usr/bin/env bash
# Sets up tap0 + NAT so the microVM reaches the LAN/internet. Needs ROOT.
set -euo pipefail
TAP=${TAP:-tap0}
HOSTIF=${HOSTIF:-eth0}      # LAN interface of this host
GUEST_NET=172.30.0.0/30

ip link del "$TAP" 2>/dev/null || true
ip tuntap add "$TAP" mode tap
ip addr add 172.30.0.1/30 dev "$TAP"
ip link set "$TAP" up

sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -t nat -C POSTROUTING -s "$GUEST_NET" -o "$HOSTIF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$GUEST_NET" -o "$HOSTIF" -j MASQUERADE
iptables -C FORWARD -i "$TAP" -o "$HOSTIF" -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -i "$TAP" -o "$HOSTIF" -j ACCEPT
iptables -C FORWARD -i "$HOSTIF" -o "$TAP" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -i "$HOSTIF" -o "$TAP" -m state --state RELATED,ESTABLISHED -j ACCEPT

echo "$TAP up (172.30.0.1/30), NAT via $HOSTIF"
