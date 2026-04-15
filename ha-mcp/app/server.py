"""HA-MCP v2: Flask web server with 2-stage pipeline, dynamic MCPs, and live dashboard."""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from flask import Flask, Response, jsonify, request, render_template

from .mcp_orchestrator import MCPOrchestrator
from .pipeline import PipelineEngine
from .mcp_orchestrator.mcp_manager import MCPManager

logger = logging.getLogger(__name__)

LOG_LEVEL    = os.environ.get("HA_MCP_LOG_LEVEL", "info").upper()
STORAGE_PATH = Path(os.environ.get("HA_MCP_STORAGE_PATH", "/share/ha-mcp"))
INGRESS_ENTRY= os.environ.get("HA_MCP_INGRESS_ENTRY", "")
SCHEMAS_DIR  = Path("/schemas")
KEYS_FILE    = STORAGE_PATH / "api_keys.json"
# DB : en prod HA → /share/ha-mcp/tool_v2.db  (via HA_MCP_DB_PATH)
#      en dev local → database/tool_v2.db
DB_PATH      = Path(os.environ.get("HA_MCP_DB_PATH",
               str(Path(__file__).resolve().parent.parent / "database" / "tool_v2.db")))
SCHEMA_DIR   = Path(os.environ.get("HA_MCP_SCHEMA_DIR",
               str(Path(__file__).resolve().parent.parent / "schemas" / "mcp")))
CLAUDE_CONFIG= Path.home() / ".claude" / "settings.json"

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


# ── Registry dynamique (lit la DB à chaque appel — hot reload) ────────────────

def _load_mcp_registry(api_keys: dict | None = None) -> list[dict]:
    """
    Lit le catalogue MCP depuis tool_v2.db.
    Remplace le MCP_CATALOG hardcodé — mis à jour sans redémarrage.
    """
    if not DB_PATH.exists():
        return []
    api_keys = api_keys or {}
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT m.mcp_id, m.name, m.category, m.requires_auth,
               m.auth_type, m.auth_key_name, m.description,
               t.type, t.command, t.args_json, t.url,
               GROUP_CONCAT(tl.name, '|') as tool_names
        FROM mcp m
        JOIN transport t ON t.mcp_id = m.mcp_id
        LEFT JOIN tool tl ON tl.mcp_id = m.mcp_id
        GROUP BY m.mcp_id
        ORDER BY m.mcp_id
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        mcp_id, name, category, requires_auth, auth_type, auth_key_name, \
        desc, transport_type, command, args_json, url, tool_names = r

        tools = [{"name": t, "description": ""} for t in (tool_names or "").split("|") if t]
        unlocked = not requires_auth or (auth_key_name and api_keys.get(auth_key_name))

        result.append({
            "id":            mcp_id,
            "name":          name,
            "category":      category or "enrichissement",
            "requires_auth": bool(requires_auth),
            "auth_type":     auth_type,
            "auth_key_name": auth_key_name,
            "description":   desc or "",
            "transport":     transport_type,
            "command":       command,
            "args":          json.loads(args_json) if args_json else [],
            "url":           url,
            "tools":         tools,
            "status":        "unlocked" if unlocked else "excluded",
        })
    return result


def _build_active_mcp_tools(api_keys: dict) -> dict[str, list[dict]]:
    """Retourne les tools des MCPs accessibles (sans auth ou clé présente)."""
    result = {}
    for mcp in _load_mcp_registry(api_keys):
        if mcp["status"] == "unlocked":
            result[mcp["id"]] = mcp["tools"]
    return result


# ── Helpers clés API ──────────────────────────────────────────────────────────

def _load_api_keys() -> dict[str, str]:
    if KEYS_FILE.exists():
        with open(KEYS_FILE) as f: return json.load(f)
    return {}

def _save_api_keys(keys: dict[str, str]) -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KEYS_FILE, "w") as f: json.dump(keys, f, indent=2)


# ── Sync config Claude ─────────────────────────────────────────────────────────

def _sync_claude_config(mcp_id: str, transport: dict) -> bool:
    """
    Ajoute le nouveau MCP dans ~/.claude/settings.json
    pour que Claude Code le voie sans redémarrage.
    Uniquement pour les MCPs stdio.
    """
    if transport.get("type") != "stdio":
        return False
    try:
        cfg = json.loads(CLAUDE_CONFIG.read_text()) if CLAUDE_CONFIG.exists() else {}
        cfg.setdefault("mcpServers", {})
        cfg["mcpServers"][mcp_id] = {
            "command": transport["command"],
            "args":    transport.get("args", []),
        }
        CLAUDE_CONFIG.write_text(json.dumps(cfg, indent=2))
        logger.info("Claude config updated: %s", mcp_id)
        return True
    except Exception as e:
        logger.warning("Claude config sync failed: %s", e)
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    api_keys = _load_api_keys()
    return render_template(
        "dashboard.html",
        ingress=INGRESS_ENTRY,
        storage=str(STORAGE_PATH),
        schema_count=len(registry.list_schemas()),
        catalog_json=json.dumps(_load_mcp_registry(api_keys)),
        keys_json=json.dumps(api_keys),
    )

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "addon": "ha-mcp", "version": "2.0.0", "timestamp": datetime.now(timezone.utc).isoformat()})

@app.route("/api/schemas")
def api_list_schemas():
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    return jsonify({"schemas": registry.list_schemas()})

@app.route("/api/mcps")
def api_list_mcps():
    """Liste tous les MCPs depuis la DB (hot reload — sans redémarrage)."""
    api_keys = _load_api_keys()
    return jsonify({"mcps": _load_mcp_registry(api_keys)})

@app.route("/api/mcps/dynamic")
def api_dynamic_mcps():
    manager = MCPManager(STORAGE_PATH, _load_api_keys())
    return jsonify({"mcps": manager.get_all_mcps(), "config": manager.get_config()})

@app.route("/api/mcps/register", methods=["POST"])
def api_register_mcp():
    """
    Enregistre un nouveau MCP serveur à chaud (sans redémarrage).

    Body JSON :
    {
      "mcp_id":   "my_server",           -- identifiant unique
      "transport": {
        "type":    "stdio",              -- stdio | sse | streamable-http
        "command": "npx",               -- (stdio) commande
        "args":    ["-y","my-mcp-pkg"], -- (stdio) arguments
        "url":     null                  -- (sse/http) URL distante
      },
      "sync_claude": true               -- optionnel: sync ~/.claude/settings.json
    }

    Processus :
      1. Connexion au serveur MCP via SDK
      2. Interrogation tools/list + resources/list + prompts/list
      3. INSERT mcp + transport + tools + capabilities en DB
      4. Écriture schema.json
      5. Sync ~/.claude/settings.json si demandé
    """
    body = request.get_json()
    if not body or "mcp_id" not in body or "transport" not in body:
        return jsonify({"error": "mcp_id and transport required"}), 400

    mcp_id    = body["mcp_id"]
    transport = body["transport"]
    sync      = body.get("sync_claude", False)

    async def _register():
        from .mcp_orchestrator.mcp_executor import build_schema_from_server
        return await build_schema_from_server(
            mcp_id=mcp_id,
            transport_conf=transport,
            db_path=DB_PATH,
            schema_dir=SCHEMA_DIR,
        )

    try:
        schema = asyncio.run(_register())
    except Exception as e:
        logger.exception("MCP registration failed")
        return jsonify({"error": str(e)}), 500

    # Sync Claude config si demandé
    claude_synced = _sync_claude_config(mcp_id, transport) if sync else False

    return jsonify({
        "status":       "registered",
        "mcp_id":       mcp_id,
        "name":         schema.get("name"),
        "version":      schema.get("version"),
        "tools_count":  len(schema.get("tools", [])),
        "capabilities": schema.get("capabilities", []),
        "transport":    schema.get("transport", {}).get("type"),
        "claude_synced":claude_synced,
        "message":      f"MCP '{mcp_id}' ajouté en DB et disponible immédiatement",
    })

@app.route("/api/keys", methods=["GET"])
def api_get_keys():
    keys = _load_api_keys()
    return jsonify({"keys": {k: "***" + v[-4:] if len(v) > 4 else "****" for k, v in keys.items()}})

@app.route("/api/keys", methods=["POST"])
def api_save_keys():
    keys = request.get_json()
    if not isinstance(keys, dict): return jsonify({"error": "Expected JSON object"}), 400
    _save_api_keys(keys)
    return jsonify({"status": "saved", "key_count": len(keys)})

@app.route("/api/keys/<key_name>", methods=["DELETE"])
def api_delete_key(key_name: str):
    keys = _load_api_keys()
    keys.pop(key_name, None)
    _save_api_keys(keys)
    return jsonify({"status": "deleted"})

@app.route("/api/analyze", methods=["POST"])
def analyze():
    if "offer_pdf" not in request.files or "cv_pdf" not in request.files:
        return jsonify({"error": "Both PDF files required"}), 400
    offer_file = request.files["offer_pdf"]
    cv_file = request.files["cv_pdf"]
    if not offer_file.filename or not cv_file.filename:
        return jsonify({"error": "Empty filenames"}), 400

    session_id = str(uuid.uuid4())
    upload_dir = STORAGE_PATH / "inputs" / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    offer_path = upload_dir / f"offer_{offer_file.filename}"
    cv_path = upload_dir / f"cv_{cv_file.filename}"
    offer_file.save(str(offer_path))
    cv_file.save(str(cv_path))

    try:
        orchestrator = MCPOrchestrator(project_root=Path("/"))
        orchestrator.initialize(session_id)

        api_keys = _load_api_keys()
        mcp_tools = _build_active_mcp_tools(api_keys)
        orchestrator.discover_mcps(mcp_tools)

        engine = PipelineEngine(orchestrator, STORAGE_PATH, api_keys=api_keys)
        results = engine.run(str(offer_path), str(cv_path))

        gen = results.get("steps", {}).get("2.6", {})
        return jsonify({
            "session_id": session_id,
            "status": "completed" if gen.get("status") == "completed" else "failed",
            "steps": results.get("steps", {}),
            "stages": results.get("stages", {}),
            "events": results.get("events", []),
            "resources": results.get("resources", []),
            "mcp_config": results.get("mcp_config", {}),
            "grand_meta": results.get("grand_meta", {}),
            "recommendation": gen.get("recommendation", "N/A"),
            "overall_score": gen.get("overall_score", 0),
            "artifacts_count": gen.get("artifacts_count", 0),
        })
    except Exception as e:
        logger.exception("Pipeline failed")
        return jsonify({"error": str(e), "session_id": session_id}), 500

@app.route("/api/events/<session_id>")
def api_events(session_id: str):
    """SSE endpoint for live pipeline events."""
    def stream():
        events_file = STORAGE_PATH / "logs" / f"pipeline_{session_id}.json"
        last_count = 0
        while True:
            if events_file.exists():
                with open(events_file) as f:
                    events = json.load(f)
                new_events = events[last_count:]
                for ev in new_events:
                    yield f"data: {json.dumps(ev)}\n\n"
                last_count = len(events)
            import time
            time.sleep(0.5)
    return Response(stream(), mimetype="text/event-stream")

@app.route("/api/resources")
def api_resources():
    manager = MCPManager(STORAGE_PATH, _load_api_keys())
    return jsonify({"resources": manager.get_resources()})

@app.route("/api/results/<session_id>")
def get_results(session_id: str):
    result_path = STORAGE_PATH / "outputs" / f"result_{session_id}.json"
    if not result_path.exists(): return jsonify({"error": f"No results for {session_id}"}), 404
    with open(result_path) as f: return jsonify(json.load(f))

@app.route("/api/sessions")
def list_sessions():
    outputs_dir = STORAGE_PATH / "outputs"
    if not outputs_dir.exists(): return jsonify({"sessions": []})
    sessions = []
    for f in sorted(outputs_dir.glob("result_*.json"), reverse=True):
        sid = f.stem.replace("result_", "")
        stat = f.stat()
        sessions.append({"session_id": sid, "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(), "size_bytes": stat.st_size})
    return jsonify({"sessions": sessions})


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info("HA-MCP v2 server starting on port 8099")
    app.run(host="0.0.0.0", port=8099, debug=False)

if __name__ == "__main__":
    main()
