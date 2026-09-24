# Rootfs of the skeleton agent: a small Debian with python3 (the agent is
# stdlib only), iproute2 (the init reads the gateway), nfs-common (the
# workspace share) and curl (host-folder list, key proxy from the shell).
# The agent runs as uid 1000 ("agent"), never as root.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 iproute2 nfs-common curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/sh agent

COPY agent.py      /app/agent.py
COPY config.env    /app/config.env
COPY guest-init.sh /init
RUN chmod +x /init && mkdir -p /home/agent/workspace && chown -R agent:agent /home/agent
