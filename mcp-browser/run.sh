#!/bin/sh
# (Re)build and start the browser container on its own network and attach the
# hub to that network (fixed IPs: the firewall in mgr/netfw.py names them).
# Idempotent; called by install.sh. The egress firewall (internet yes, private
# ranges/host no) is applied by the manager (netfw.ensure_browser_rules).
set -e
cd "$(dirname "$0")"
NET=kaim56-browser-net
docker network inspect $NET >/dev/null 2>&1 || docker network create \
  --subnet 172.31.56.0/24 --gateway 172.31.56.1 \
  -o com.docker.network.bridge.name=kaim56-br $NET
docker build -q -t kaim56-browser .
docker rm -f kaim56-browser 2>/dev/null || true
# IPv6 off: no v6 route here, and Chromium does not fall back (ERR_ABORTED).
# Public DNS: the LAN resolver is a private address and therefore blocked.
docker run -d --restart unless-stopped --name kaim56-browser \
  --network $NET --ip 172.31.56.2 --dns 1.1.1.1 --dns 9.9.9.9 \
  --sysctl net.ipv6.conf.all.disable_ipv6=1 \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --pids-limit 1024 --shm-size 512m kaim56-browser
docker inspect -f '{{json .NetworkSettings.Networks}}' kaim56-mcp-hub | grep -q "\"$NET\"" \
  || docker network connect --ip 172.31.56.3 $NET kaim56-mcp-hub
