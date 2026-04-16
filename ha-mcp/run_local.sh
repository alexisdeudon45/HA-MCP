#!/bin/bash
# ============================================================
# Test local HA-MCP — simule l'environnement Home Assistant
# Usage : ./run_local.sh
# Ouvre : http://localhost:8765
# ============================================================

set -e
cd "$(dirname "$0")"

# Charger la clé API depuis .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Variables d'environnement (simule bashio::config)
export HA_MCP_PORT=8765
export HA_MCP_SCHEMAS_DIR="$(pwd)/schemas"
export HA_MCP_LOG_LEVEL=debug
export HA_MCP_STORAGE_PATH=/tmp/ha-mcp-test
export HA_MCP_DB_PATH="$(pwd)/database/tool_v2.db"
export HA_MCP_INGRESS_ENTRY=""

mkdir -p /tmp/ha-mcp-test/{inputs,outputs,logs,intermediate}

echo "========================================"
echo "  HA-MCP — Test local"
echo "========================================"
echo "  DB      : $HA_MCP_DB_PATH"
echo "  Storage : $HA_MCP_STORAGE_PATH"
echo "  Port    : $HA_MCP_PORT"
echo "  API Key : ${ANTHROPIC_API_KEY:0:20}..."
echo "========================================"
echo "  → http://localhost:$HA_MCP_PORT"
echo "========================================"

# Activer le venv si disponible
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

PYTHONPATH=. uvicorn app.server:app --host 0.0.0.0 --port "${HA_MCP_PORT:-8765}" --log-level "${HA_MCP_LOG_LEVEL:-debug}"
