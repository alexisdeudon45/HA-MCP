ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

# ── Dépendances système ───────────────────────────────────────────────────────
# gcc/musl-dev pour la compilation de certains packages Python (ex: ddgs, httpx)
# Node.js + npm pour les serveurs MCP stdio (npx)
RUN apk add --no-cache \
    gcc musl-dev python3-dev \
    nodejs npm \
    curl bash

# ── npm global cache dans un path writeable ───────────────────────────────────
ENV NPM_CONFIG_CACHE=/tmp/npm-cache
ENV NPM_CONFIG_PREFIX=/usr/local

# ── Pré-installer les serveurs MCP stdio ─────────────────────────────────────
RUN npm install -g \
    duckduckgo-mcp-server \
    @modelcontextprotocol/server-sequential-thinking \
    @modelcontextprotocol/server-filesystem \
    @modelcontextprotocol/server-memory \
    @modelcontextprotocol/server-everything \
    @playwright/mcp \
    && npm cache clean --force

# ── Dépendances Python ────────────────────────────────────────────────────────
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# ── Application ───────────────────────────────────────────────────────────────
COPY app/      /app/
COPY schemas/  /schemas/
COPY config/   /config/
COPY database/ /database/
COPY rootfs/   /

COPY run.sh /
RUN chmod a+x /run.sh \
    && chmod a+x /etc/services.d/ha-mcp/run \
    && chmod a+x /etc/services.d/ha-mcp/finish

WORKDIR /

