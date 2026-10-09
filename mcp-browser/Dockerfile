# Headless browser for the agents (Playwright MCP + Chromium) in its OWN
# container, not in the hub: the hub keeps LAN access for portainer/HA, the
# browser gets internet only. Network + firewall: see run.sh and
# manager/mgr/netfw.py ensure_browser_rules. The hub reaches it over MCP
# streamable HTTP via mcp-remote (catalog entry "browser").
FROM node:24-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npm install -g @playwright/mcp@0.0.83 && npm cache clean --force \
    && cd "$(npm root -g)/@playwright/mcp" && npx playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY patch-tools.js /app/patch-tools.js
RUN node /app/patch-tools.js
# The server pings each HTTP client every few seconds and closes the session
# when no pong comes within 5 s. The hub answers no server->client requests
# (see hub.py _read_until), so a ping would kill every call that takes longer
# than that — a navigation to a dead host then never returns. 0 = no ping.
ENV PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0
USER node
WORKDIR /home/node
EXPOSE 8931
# --allowed-hosts: only the address the hub uses, so a page cannot DNS-rebind
# its way into this server from inside the browser.
ENTRYPOINT ["playwright-mcp", "--headless", "--isolated", "--no-sandbox", \
    "--browser", "chromium", "--viewport-size", "1280,800", "--output-dir", "/tmp/pw", \
    "--host", "0.0.0.0", "--port", "8931", "--allowed-hosts", "kaim56-browser:8931"]
