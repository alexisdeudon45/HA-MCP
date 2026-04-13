#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# HA-MCP: MCP-Poste Addon Entry Point
# ==============================================================================

declare LOG_LEVEL
declare STORAGE_PATH

LOG_LEVEL=$(bashio::config 'log_level')
STORAGE_PATH=$(bashio::config 'storage_path')

bashio::log.info "Starting MCP-Poste Analyzer addon..."
bashio::log.info "Log level: ${LOG_LEVEL}"
bashio::log.info "Storage path: ${STORAGE_PATH}"

# Create storage directories
mkdir -p "${STORAGE_PATH}"/{inputs,intermediate,outputs,logs}

# Export config as environment variables for the Python app
export HA_MCP_LOG_LEVEL="${LOG_LEVEL}"
export HA_MCP_STORAGE_PATH="${STORAGE_PATH}"
export HA_MCP_VALIDATION_STRICT=$(bashio::config 'validation_strict')
export HA_MCP_AUTO_DISCOVER=$(bashio::config 'auto_discover_mcps')
export HA_MCP_INGRESS_ENTRY=$(bashio::addon.ingress_entry)

bashio::log.info "Ingress entry: ${HA_MCP_INGRESS_ENTRY}"
bashio::log.info "Starting web server on port 8099..."

# Run the Flask web server
exec python3 -m app.server
