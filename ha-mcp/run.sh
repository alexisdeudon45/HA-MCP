#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# HA-MCP Addon — Entry Point
# ==============================================================================

declare LOG_LEVEL
declare STORAGE_PATH

LOG_LEVEL=$(bashio::config 'log_level')
STORAGE_PATH=$(bashio::config 'storage_path')

bashio::log.info "Starting HA-MCP v2.0.0..."
bashio::log.info "Storage: ${STORAGE_PATH}"

# ── Répertoires de stockage ────────────────────────────────────────────────────
mkdir -p "${STORAGE_PATH}"/{inputs,outputs,logs,intermediate}

# ── Variables d'environnement ─────────────────────────────────────────────────
export HA_MCP_LOG_LEVEL="${LOG_LEVEL}"
export HA_MCP_STORAGE_PATH="${STORAGE_PATH}"
export HA_MCP_INGRESS_ENTRY="$(bashio::addon.ingress_entry)"

# Clés API depuis la config HA (options.yaml — pas de .env en production)
if bashio::config.has_value 'anthropic_api_key'; then
    export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"
    bashio::log.info "Anthropic API key configured"
fi
if bashio::config.has_value 'openai_api_key'; then
    export OPENAI_API_KEY="$(bashio::config 'openai_api_key')"
fi
if bashio::config.has_value 'mistral_api_key'; then
    export MISTRAL_API_KEY="$(bashio::config 'mistral_api_key')"
fi
if bashio::config.has_value 'hf_api_key'; then
    export HF_API_KEY="$(bashio::config 'hf_api_key')"
fi

# ── DB path dans storage persistant ──────────────────────────────────────────
export HA_MCP_DB_PATH="${STORAGE_PATH}/tool_v2.db"

# Initialiser la DB si elle n'existe pas encore
if [ ! -f "${HA_MCP_DB_PATH}" ]; then
    bashio::log.info "Initializing database..."
    python3 -c "
import sqlite3, os
db = os.environ['HA_MCP_DB_PATH']
schema = open('/database/schema_v2.sql').read()
conn = sqlite3.connect(db)
conn.executescript(schema)
conn.commit()
conn.close()
print('Database initialized:', db)
"
fi

# ── Lancement du serveur ──────────────────────────────────────────────────────
bashio::log.info "Starting web server on port 8099..."
exec python3 -m app
