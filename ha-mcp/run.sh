#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# HA-MCP Addon — Entry Point
# ==============================================================================

# ── Lockfile PID : évite le double-spawn, auto-nettoyé si stale ──────────────
LOCKFILE="/tmp/ha-mcp.lock"
if [ -f "${LOCKFILE}" ]; then
    OLD_PID=$(cat "${LOCKFILE}" 2>/dev/null)
    if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
        # Flask tourne déjà — on attend qu'il s'arrête (évite le restart loop s6)
        bashio::log.info "HA-MCP already running (PID ${OLD_PID}), waiting..."
        while kill -0 "${OLD_PID}" 2>/dev/null; do
            sleep 5
        done
        bashio::log.info "PID ${OLD_PID} stopped, restarting..."
        rm -f "${LOCKFILE}"
    else
        bashio::log.info "Removing stale lockfile (PID ${OLD_PID} not found)."
        rm -f "${LOCKFILE}"
    fi
fi
# Écrire le PID courant — après exec, Python hérite du même PID
echo $$ > "${LOCKFILE}"

# ── Config ────────────────────────────────────────────────────────────────────
declare LOG_LEVEL
declare STORAGE_PATH

LOG_LEVEL=$(bashio::config 'log_level')
STORAGE_PATH=$(bashio::config 'storage_path')

bashio::log.info "Starting HA-MCP v2.0.0..."
bashio::log.info "Storage: ${STORAGE_PATH}"

# ── Répertoires de stockage ───────────────────────────────────────────────────
mkdir -p "${STORAGE_PATH}"/{inputs,outputs,logs,intermediate}

# ── Variables d'environnement ─────────────────────────────────────────────────
export HA_MCP_PORT=8765
export HA_MCP_LOG_LEVEL="${LOG_LEVEL}"
export HA_MCP_STORAGE_PATH="${STORAGE_PATH}"
export HA_MCP_INGRESS_ENTRY="$(bashio::addon.ingress_entry)"

# Clés API depuis la config HA
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

# ── DB path ───────────────────────────────────────────────────────────────────
export HA_MCP_DB_PATH="${STORAGE_PATH}/tool_v2.db"

bashio::log.info "Initializing database..."
python3 -c "
import sqlite3, os
db = os.environ['HA_MCP_DB_PATH']
schema = open('/database/schema_v2.sql').read()
conn = sqlite3.connect(db)
conn.executescript(schema)
conn.commit()
conn.close()
print('Database ready:', db)
"

# ── Lancement du serveur ──────────────────────────────────────────────────────
bashio::log.info "Starting web server on port 8765..."
cd /
export PYTHONPATH=/
exec python3 -m app
