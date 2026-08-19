# MCP-Hub: die MCP-Serverprozesse (mcp-remote fuer Home Assistant,
# mcp-portainer) laufen HIER statt in jeder microVM. Gleiche Abhaengigkeiten
# wie vorher im Gast-Image, aber einmal am Host.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g mcp-remote && npm cache clean --force
RUN pip install --no-cache-dir --break-system-packages mcp-portainer
COPY hub.py /app/hub.py
EXPOSE 8771
CMD ["python3", "-u", "/app/hub.py"]
