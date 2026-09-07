# MCP hub: the MCP server processes (mcp-remote for Home Assistant,
# mcp-portainer, caldav-mcp) run HERE instead of in every microVM. Same dependencies
# as previously in the guest image, but once on the host.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g mcp-remote caldav-mcp && npm cache clean --force
# caldav-mcp 0.10.0 needs two build-time patches (see patch-caldav.js: absolute
# calendar URLs; event times in the process TZ instead of UTC). The script fails
# the build if its anchors moved with a newer version.
COPY patch-caldav.js /app/patch-caldav.js
RUN node /app/patch-caldav.js
RUN pip install --no-cache-dir --break-system-packages mcp-portainer
COPY hub.py /app/hub.py
EXPOSE 8771
CMD ["python3", "-u", "/app/hub.py"]
