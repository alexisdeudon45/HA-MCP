"""HA-MCP v2: FastAPI web server with 2-stage pipeline, dynamic MCPs, and live dashboard."""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Registre des sessions en cours ────────────────────────────────────────────
_sessions: dict[str, dict] = {}   # session_id → {status, result, error}

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── Sentry (optionnel — activé si SENTRY_DSN présent) ────────────────────────
import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.1,
        environment=os.environ.get("HA_MCP_ENV", "production"),
    )
    logging.getLogger(__name__).info("Sentry initialized")

import uvicorn
from fastapi import FastAPI, BackgroundTasks, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .mcp_orchestrator import MCPOrchestrator
from .pipeline import PipelineEngine
from .mcp_orchestrator.mcp_manager import MCPManager

logger = logging.getLogger(__name__)

LOG_LEVEL    = os.environ.get("HA_MCP_LOG_LEVEL", "info").upper()
STORAGE_PATH = Path(os.environ.get("HA_MCP_STORAGE_PATH", "/share/ha-mcp"))
INGRESS_ENTRY= os.environ.get("HA_MCP_INGRESS_ENTRY", "")
SCHEMAS_DIR  = Path(os.environ.get("HA_MCP_SCHEMAS_DIR", "/schemas"))
KEYS_FILE    = STORAGE_PATH / "api_keys.json"
# DB : en prod HA → /share/ha-mcp/tool_v2.db  (via HA_MCP_DB_PATH)
#      en dev local → database/tool_v2.db
DB_PATH      = Path(os.environ.get("HA_MCP_DB_PATH",
               str(Path(__file__).resolve().parent.parent / "database" / "tool_v2.db")))
SCHEMA_DIR   = Path(os.environ.get("HA_MCP_SCHEMA_DIR",
               str(Path(__file__).resolve().parent.parent / "schemas" / "mcp")))
CLAUDE_CONFIG= Path.home() / ".claude" / "settings.json"

app = FastAPI(title="HA-MCP", version="2.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
        SELECT m.mcp_id, m.name,
               COALESCE(m.registry_category, 'enrichissement') as category,
               m.requires_auth,
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

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    api_keys = _load_api_keys()
    # Starlette >= 0.21 : request en kwarg, context séparé
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "ingress":      INGRESS_ENTRY,
            "storage":      str(STORAGE_PATH),
            "schema_count": len(registry.list_schemas()),
            "catalog_json": json.dumps(_load_mcp_registry(api_keys)),
            "keys_json":    json.dumps(api_keys),
        },
    )


@app.get("/api/health")
async def health():
    return JSONResponse({
        "status":    "ok",
        "addon":     "ha-mcp",
        "version":   "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/schemas")
async def api_list_schemas():
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    return JSONResponse({"schemas": registry.list_schemas()})


@app.get("/api/mcps")
async def api_list_mcps():
    """Liste tous les MCPs depuis la DB (hot reload — sans redémarrage)."""
    api_keys = _load_api_keys()
    return JSONResponse({"mcps": _load_mcp_registry(api_keys)})


@app.get("/api/mcps/dynamic")
async def api_dynamic_mcps():
    manager = MCPManager(STORAGE_PATH, _load_api_keys())
    return JSONResponse({"mcps": manager.get_all_mcps(), "config": manager.get_config()})


@app.post("/api/mcps/register")
async def api_register_mcp(request: Request):
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
    body = await request.json()
    if not body or "mcp_id" not in body or "transport" not in body:
        return JSONResponse({"error": "mcp_id and transport required"}, status_code=400)

    mcp_id    = body["mcp_id"]
    transport = body["transport"]
    sync      = body.get("sync_claude", False)

    try:
        from .mcp_orchestrator.mcp_executor import build_schema_from_server
        schema = await build_schema_from_server(
            mcp_id=mcp_id,
            transport_conf=transport,
            db_path=DB_PATH,
            schema_dir=SCHEMA_DIR,
        )
    except Exception as e:
        logger.exception("MCP registration failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Sync Claude config si demandé
    claude_synced = _sync_claude_config(mcp_id, transport) if sync else False

    return JSONResponse({
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


@app.get("/api/keys")
async def api_get_keys():
    keys = _load_api_keys()
    masked = {k: "***" + v[-4:] if len(v) > 4 else "****" for k, v in keys.items()}
    return JSONResponse({"keys": masked})


@app.post("/api/keys")
async def api_save_keys(request: Request):
    keys = await request.json()
    if not isinstance(keys, dict):
        return JSONResponse({"error": "Expected JSON object"}, status_code=400)
    _save_api_keys(keys)
    return JSONResponse({"status": "saved", "key_count": len(keys)})


@app.delete("/api/keys/{key_name}")
async def api_delete_key(key_name: str):
    keys = _load_api_keys()
    keys.pop(key_name, None)
    _save_api_keys(keys)
    return JSONResponse({"status": "deleted"})


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _log_pipeline_calls(session_id: str, events: list) -> None:
    """Enregistre les événements pipeline dans call_history."""
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        for ev in events:
            if ev.get("type") in ("step_complete", "step_failed"):
                conn.execute("""
                    INSERT OR IGNORE INTO call_history
                      (mcp_id, tool_name, request_json, response_json,
                       response_type, started_at, duration_ms, session_id, caller)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    "pipeline",
                    ev.get("step_name", ""),
                    json.dumps({"step_id": ev.get("step_id"), "stage": ev.get("stage")}),
                    json.dumps({"status": ev.get("type"), "error": ev.get("error", "")}),
                    "success" if ev.get("type") == "step_complete" else "error",
                    ev.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    ev.get("duration_ms", 0),
                    session_id,
                    "pipeline",
                ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("call_history log failed: %s", e)


def _run_pipeline_sync(session_id: str, offer_path: str, cv_path: str, api_keys: dict):
    """Version synchrone du pipeline — exécutée dans un thread pool via run_in_executor."""
    _sessions[session_id] = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        orchestrator = MCPOrchestrator(project_root=Path("/"))
        orchestrator.initialize(session_id)
        mcp_tools = _build_active_mcp_tools(api_keys)
        orchestrator.discover_mcps(mcp_tools)
        engine = PipelineEngine(orchestrator, STORAGE_PATH, api_keys=api_keys)
        results = engine.run(offer_path, cv_path)

        # Logger les appels MCP dans call_history
        _log_pipeline_calls(session_id, results.get("events", []))

        _sessions[session_id] = {
            "status": "completed" if results.get("steps", {}).get("2.6", {}).get("status") == "completed" else "failed",
            "session_id":     session_id,
            "steps":          results.get("steps", {}),
            "events":         results.get("events", []),
            "grand_meta":     results.get("grand_meta", {}),
            "recommendation": results.get("steps", {}).get("2.6", {}).get("recommendation", "N/A"),
            "overall_score":  results.get("steps", {}).get("2.6", {}).get("overall_score", 0),
            "artifacts_count":results.get("steps", {}).get("2.6", {}).get("artifacts_count", 0),
        }
    except Exception as e:
        logger.exception("Pipeline failed in executor")
        _sessions[session_id] = {"status": "failed", "error": str(e), "session_id": session_id}


async def _run_pipeline(session_id: str, offer_path: str, cv_path: str, api_keys: dict):
    """Lance _run_pipeline_sync dans le thread pool par défaut d'asyncio."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_pipeline_sync, session_id, offer_path, cv_path, api_keys)


@app.post("/api/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    offer_pdf: UploadFile = File(...),
    cv_pdf: UploadFile = File(...),
):
    """Lance le pipeline en background — retourne session_id immédiatement."""
    if not offer_pdf.filename or not cv_pdf.filename:
        return JSONResponse({"error": "Empty filenames"}, status_code=400)

    session_id = str(uuid.uuid4())
    upload_dir = STORAGE_PATH / "inputs" / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    offer_path = upload_dir / f"offer_{offer_pdf.filename}"
    cv_path    = upload_dir / f"cv_{cv_pdf.filename}"

    offer_path.write_bytes(await offer_pdf.read())
    cv_path.write_bytes(await cv_pdf.read())

    api_keys = _load_api_keys()

    background_tasks.add_task(_run_pipeline, session_id, str(offer_path), str(cv_path), api_keys)

    return JSONResponse({"session_id": session_id, "status": "running"})


@app.get("/api/analyze/status/{session_id}")
async def analyze_status(session_id: str):
    """Statut d'une analyse en cours ou terminée."""
    result = _sessions.get(session_id)
    if not result:
        result_path = STORAGE_PATH / "outputs" / f"result_{session_id}.json"
        if result_path.exists():
            with open(result_path) as f:
                return JSONResponse({"status": "completed", **json.load(f)})
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse(result)


@app.get("/api/events/{session_id}")
async def api_events(session_id: str):
    """SSE endpoint for live pipeline events."""
    async def event_stream():
        events_file = STORAGE_PATH / "logs" / f"pipeline_{session_id}.json"
        last_count = 0
        max_wait   = 600   # 10 min timeout
        waited     = 0.0

        while waited < max_wait:
            events: list = []
            if events_file.exists():
                with open(events_file) as f:
                    events = json.load(f)
                for ev in events[last_count:]:
                    yield f"data: {json.dumps(ev)}\n\n"
                last_count = len(events)

            # Check if done
            session = _sessions.get(session_id, {})
            if session.get("status") in ("completed", "failed") and last_count >= len(events):
                yield f"data: {json.dumps({'type': 'done', 'status': session.get('status')})}\n\n"
                break

            await asyncio.sleep(0.5)
            waited += 0.5

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/resources")
async def api_resources():
    manager = MCPManager(STORAGE_PATH, _load_api_keys())
    return JSONResponse({"resources": manager.get_resources()})


@app.get("/api/results/{session_id}")
async def get_results(session_id: str):
    result_path = STORAGE_PATH / "outputs" / f"result_{session_id}.json"
    if not result_path.exists():
        return JSONResponse({"error": f"No results for {session_id}"}, status_code=404)
    with open(result_path) as f:
        return JSONResponse(json.load(f))


@app.get("/api/sessions")
async def list_sessions():
    outputs_dir = STORAGE_PATH / "outputs"
    if not outputs_dir.exists():
        return JSONResponse({"sessions": []})
    sessions = []
    for f in sorted(outputs_dir.glob("result_*.json"), reverse=True):
        sid  = f.stem.replace("result_", "")
        stat = f.stat()
        sessions.append({
            "session_id":  sid,
            "created_at":  datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes":  stat.st_size,
        })
    return JSONResponse({"sessions": sessions})


# ── DB API endpoints ──────────────────────────────────────────────────────────

@app.get("/api/db/summary")
async def api_db_summary():
    """Stats globales depuis tool_v2.db"""
    if not DB_PATH.exists():
        return JSONResponse({"error": "DB not found"}, status_code=404)
    conn = sqlite3.connect(DB_PATH)
    try:
        nb_mcps  = conn.execute("SELECT COUNT(*) FROM mcp").fetchone()[0]
        nb_tools = conn.execute("SELECT COUNT(*) FROM tool").fetchone()[0]
        nb_caps  = conn.execute("SELECT COUNT(*) FROM capability").fetchone()[0]
        try:
            nb_calls = conn.execute("SELECT COUNT(*) FROM call_history").fetchone()[0]
        except Exception:
            nb_calls = 0
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({
        "mcps":         nb_mcps,
        "tools":        nb_tools,
        "capabilities": nb_caps,
        "calls":        nb_calls,
    })


@app.get("/api/db/mcps")
async def api_db_mcps():
    """Liste MCPs avec transport + capabilities"""
    if not DB_PATH.exists():
        return JSONResponse({"mcps": []})
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT m.mcp_id, m.name,
                   COALESCE(m.registry_category, 'enrichissement') as category,
                   m.description,
                   COALESCE(m.plug_and_play, 0) as plug_and_play,
                   m.discovered_from,
                   t.type  AS transport_type,
                   COUNT(DISTINCT tl.id) AS tools_count,
                   GROUP_CONCAT(DISTINCT c.name) AS capabilities
            FROM mcp m
            LEFT JOIN transport t ON t.mcp_id = m.mcp_id
            LEFT JOIN tool tl     ON tl.mcp_id = m.mcp_id
            LEFT JOIN mcp_capability mc ON mc.mcp_id = m.mcp_id
            LEFT JOIN capability c      ON c.id = mc.cap_id
            GROUP BY m.mcp_id
            ORDER BY m.mcp_id
        """).fetchall()
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    conn.close()
    result = []
    for r in rows:
        result.append({
            "mcp_id":          r["mcp_id"],
            "name":            r["name"],
            "category":        r["category"],
            "description":     r["description"],
            "plug_and_play":   bool(r["plug_and_play"]),
            "discovered_from": r["discovered_from"],
            "transport":       r["transport_type"],
            "tools_count":     r["tools_count"],
            "capabilities":    [c for c in (r["capabilities"] or "").split(",") if c],
        })
    return JSONResponse({"mcps": result})


@app.get("/api/db/tools")
async def api_db_tools(mcp_id: str | None = Query(default=None)):
    """Tools avec paramètres. ?mcp_id=xxx pour filtrer"""
    if not DB_PATH.exists():
        return JSONResponse({"tools": []})
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT tl.id, tl.mcp_id, tl.name, tl.description,
                   COUNT(tp.id) AS params_count
            FROM tool tl
            LEFT JOIN tool_parameter tp ON tp.tool_id = tl.id
        """
        params: list = []
        if mcp_id:
            query += " WHERE tl.mcp_id = ?"
            params.append(mcp_id)
        query += " GROUP BY tl.id ORDER BY tl.mcp_id, tl.name"
        rows = conn.execute(query, params).fetchall()
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({"tools": [dict(r) for r in rows]})


@app.get("/api/db/history")
async def api_db_history(
    limit:  int         = Query(default=100),
    mcp_id: str | None  = Query(default=None),
):
    """call_history. ?limit=50&mcp_id=xxx"""
    if not DB_PATH.exists():
        return JSONResponse({"history": []})
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT id, mcp_id, tool_name, response_type AS status,
                   duration_ms, started_at, request_json, response_json
            FROM call_history
        """
        params: list = []
        if mcp_id:
            query += " WHERE mcp_id = ?"
            params.append(mcp_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({"history": [dict(r) for r in rows]})


@app.get("/api/db/graph")
async def api_db_graph():
    """Données pour le graphe D3 de découverte.
    Retourne nodes[] et edges[].
    Node: {id, name, transport, plug_and_play, tools_count, discovered_count, size}
    Edge: {source, target} (discovered_from)
    discovered_count = nombre de MCPs découverts PAR ce nœud
    size = 20 + discovered_count * 8 (le nœud grossit s'il a trouvé d'autres MCPs)
    """
    if not DB_PATH.exists():
        return JSONResponse({"nodes": [], "edges": []})
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT m.mcp_id, m.name,
                   COALESCE(m.plug_and_play, 0) as plug_and_play,
                   m.discovered_from,
                   t.type AS transport_type,
                   COUNT(DISTINCT tl.id) AS tools_count
            FROM mcp m
            LEFT JOIN transport t  ON t.mcp_id = m.mcp_id
            LEFT JOIN tool tl      ON tl.mcp_id = m.mcp_id
            GROUP BY m.mcp_id
        """).fetchall()

        caps_rows = conn.execute("""
            SELECT mc.mcp_id, GROUP_CONCAT(c.name) AS capabilities
            FROM mcp_capability mc
            JOIN capability c ON c.id = mc.cap_id
            GROUP BY mc.mcp_id
        """).fetchall()
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    conn.close()

    caps_map = {r["mcp_id"]: [c for c in (r["capabilities"] or "").split(",") if c]
                for r in caps_rows}

    # Count how many MCPs each node has discovered
    discovered_count: dict = {}
    edges = []
    for r in rows:
        discovered_count.setdefault(r["mcp_id"], 0)
        if r["discovered_from"]:
            discovered_count[r["discovered_from"]] = discovered_count.get(r["discovered_from"], 0) + 1
            edges.append({"source": r["discovered_from"], "target": r["mcp_id"]})

    nodes = []
    for r in rows:
        dc = discovered_count.get(r["mcp_id"], 0)
        nodes.append({
            "id":               r["mcp_id"],
            "name":             r["name"],
            "transport":        r["transport_type"],
            "plug_and_play":    bool(r["plug_and_play"]),
            "tools_count":      r["tools_count"],
            "capabilities":     caps_map.get(r["mcp_id"], []),
            "discovered_count": dc,
            "size":             20 + dc * 8,
        })

    return JSONResponse({"nodes": nodes, "edges": edges})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    port = int(os.environ.get("HA_MCP_PORT", "8765"))
    logger.info("HA-MCP v2 server starting on port %d (FastAPI/uvicorn)", port)
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("HA_MCP_LOG_LEVEL", "info").lower(),
    )

if __name__ == "__main__":
    main()
