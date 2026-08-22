# MCP hub: the MCP server processes (mcp-remote for Home Assistant,
# mcp-portainer) run HERE instead of in every microVM. Same dependencies
# as previously in the guest image, but once on the host.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g mcp-remote && npm cache clean --force
RUN pip install --no-cache-dir --break-system-packages mcp-portainer
COPY hub.py /app/hub.py
EXPOSE 8771
CMD ["python3", "-u", "/app/hub.py"]
