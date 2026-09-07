# MCP hub: the MCP server processes (mcp-remote for Home Assistant,
# mcp-portainer, caldav-mcp) run HERE instead of in every microVM. Same dependencies
# as previously in the guest image, but once on the host.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g mcp-remote caldav-mcp && npm cache clean --force
# caldav-mcp 0.10.0: list-calendars returns the calendar URLs RELATIVE (sabre/
# Nextcloud hrefs like /cloud/remote.php/caldav/...), but list-events & co.
# only accept absolute ones -> "Failed to retrieve vevents from the CalDAV
# server." (2026-09-07, voicecommand). Resolve them against CALDAV_BASE_URL
# at the source, so every URL the model sees is usable as-is.
RUN node -e 'const fs=require("fs");const f=require("child_process").execSync("npm root -g").toString().trim()+"/caldav-mcp/dist/tools/list-calendars.js";let s=fs.readFileSync(f,"utf8");const a="const calendars = await client.getCalendars();";if(!s.includes(a))throw new Error("caldav-mcp: patch anchor not found, check the version");s=s.replace(a,"const calendars = (await client.getCalendars()).map((c) => ({ ...c, url: new URL(c.url, process.env.CALDAV_BASE_URL || \"http://localhost/\").href }));");fs.writeFileSync(f,s);console.log("caldav-mcp: list-calendars now returns absolute URLs");'
RUN pip install --no-cache-dir --break-system-packages mcp-portainer
COPY hub.py /app/hub.py
EXPOSE 8771
CMD ["python3", "-u", "/app/hub.py"]
