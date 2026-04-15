"""
MCP Executor — point d'entrée universel pour appeler n'importe quel MCP.

Deux fonctionnalités :

1. call(mcp_id, tool, args)
   Lit schema.json → connecte via le bon transport → appelle l'outil
   Toujours le même appel, quel que soit le MCP.

2. build_schema_from_server(mcp_id, transport_conf)
   Demande DIRECTEMENT au serveur MCP tout ce qu'il sait de lui-même :
   - initialize()    → name, version, server_capabilities
   - tools/list      → tools + inputSchema bruts
   - resources/list  → ressources exposées
   - prompts/list    → prompts disponibles
   La config de transport (command/url) est le seul apport externe.

3. Tout appel est loggé dans call_history (paquet brut, réponse, timing).
"""

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import os
DB_PATH    = Path(os.environ.get("HA_MCP_DB_PATH",
             str(Path(__file__).resolve().parent.parent.parent / "database" / "tool_v2.db")))
SCHEMA_DIR = Path(os.environ.get("HA_MCP_SCHEMA_DIR",
             str(Path(__file__).resolve().parent.parent.parent / "schemas" / "mcp")))


# ══════════════════════════════════════════════════════════════════════════════
# 1. EXÉCUTEUR UNIVERSEL
# ══════════════════════════════════════════════════════════════════════════════

async def call(
    mcp_id:    str,
    tool_name: str,
    args:      dict[str, Any],
    session_id:str | None = None,
    caller:    str        = "manual",
    db_path:   Path       = DB_PATH,
    schema_dir:Path       = SCHEMA_DIR,
) -> dict[str, Any]:
    """
    Appel universel : charge le schema → connecte → appelle → log.

    Usage :
        result = await call("duckduckgo", "duckduckgo_web_search", {"query": "test"})
        result = await call("blockscout", "get_chains_list", {})
        result = await call("filesystem", "list_allowed_directories", {})
    """
    from .mcp_client import MCPClient

    schema = _load_schema(mcp_id, schema_dir)
    t      = schema["transport"]
    sid    = session_id or str(uuid.uuid4())

    # Paquet brut (ce qu'on enverrait en JSON-RPC nu)
    request_packet = {
        "jsonrpc": "2.0",
        "method":  "tools/call",
        "params":  {"name": tool_name, "arguments": args},
    }

    started_at = datetime.now(timezone.utc).isoformat()
    t_start    = time.monotonic()
    response   = None
    resp_type  = "error"

    try:
        if t["type"] == "stdio":
            ctx = MCPClient.stdio(t["command"], t["args"])
        else:
            ctx = MCPClient.sse(t["url"])

        async with ctx as client:
            response  = await client.call_tool(tool_name, args)
            resp_type = "success"

    except Exception as e:
        response  = {"error": str(e)}
        resp_type = "error"
        logger.warning("[%s/%s] call failed: %s", mcp_id, tool_name, e)

    duration_ms = int((time.monotonic() - t_start) * 1000)

    _log_call(
        db_path      = db_path,
        mcp_id       = mcp_id,
        tool_name    = tool_name,
        request_json = json.dumps(request_packet, ensure_ascii=False),
        response_json= json.dumps(response, ensure_ascii=False, default=str),
        response_type= resp_type,
        started_at   = started_at,
        duration_ms  = duration_ms,
        session_id   = sid,
        caller       = caller,
    )

    logger.info("[%s/%s] %s in %dms", mcp_id, tool_name, resp_type, duration_ms)
    return {"result": response, "duration_ms": duration_ms, "status": resp_type}


# ══════════════════════════════════════════════════════════════════════════════
# 2. SCHEMA DEPUIS LE SERVEUR DIRECTEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def build_schema_from_server(
    mcp_id:         str,
    transport_conf: dict,           # {"type":"stdio","command":"npx","args":[...]}
                                    # ou {"type":"sse","url":"https://..."}
    db_path:        Path = DB_PATH,
    schema_dir:     Path = SCHEMA_DIR,
) -> dict[str, Any]:
    """
    Demande DIRECTEMENT au serveur MCP tout ce qu'il sait de lui-même.

    On interroge 4 endpoints MCP officiels :
      initialize()    → name, version, server_capabilities
      tools/list      → tools + inputSchema bruts
      resources/list  → ressources exposées (fichiers, données)
      prompts/list    → prompts définis par le serveur

    Le seul apport externe : transport_conf (comment se connecter).
    Tout le reste vient du serveur.
    """
    from .mcp_client import MCPClient
    from .mcp_detector import detect_auth

    t = transport_conf
    if t["type"] == "stdio":
        ctx = MCPClient.stdio(t["command"], t["args"])
    else:
        ctx = MCPClient.sse(t["url"])

    async with ctx as client:
        # ── Ce que le serveur nous dit de lui-même ────────────────────────────
        server_name = client.server_info.get("name", mcp_id)
        server_ver  = client.server_info.get("version", "1.0.0")
        server_caps = await client.get_server_capabilities()   # initialize()

        tools_raw   = await client.list_tools()                # tools/list
        resources   = await client.list_resources_as_dict()    # resources/list

        # prompts/list (optionnel — tous les serveurs ne l'implémentent pas)
        prompts = []
        try:
            p_result = await client._session.list_prompts()
            prompts  = [{"name": p.name, "description": p.description or ""}
                        for p in p_result.prompts]
        except Exception:
            pass

        # ── inputSchema brut pour chaque tool ─────────────────────────────────
        tools_out = []
        for tool in tools_raw:
            raw = tool.inputSchema
            input_schema = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)

            # Probe args = valeurs minimales des params requis
            props    = input_schema.get("properties") or {}
            required = input_schema.get("required") or []
            probe_args = {p: _example_value(props.get(p, {})) for p in required[:2]}

            tools_out.append({
                "name":        tool.name,
                "description": tool.description or "",
                "inputSchema": input_schema,
                "probe_args":  probe_args,
            })

        # ── Auth déduite des noms d'outils ────────────────────────────────────
        auth_info = detect_auth(
            server_name,
            " ".join(t.get("description","") for t in tools_out),
            [{"name": t["name"]} for t in tools_out],
        )

        # ── Capabilities ──────────────────────────────────────────────────────
        caps = await client.classify_capabilities()

    # ── Assemblage du schema ──────────────────────────────────────────────────
    first = tools_out[0] if tools_out else {}
    schema = {
        "$id":      f"urn:ha-mcp:mcp:{mcp_id}:v1",
        "mcp_id":   mcp_id,
        "name":     server_name,
        "version":  server_ver,

        # Ce que le serveur a déclaré dans initialize()
        "server_capabilities": server_caps,

        "transport": transport_conf,
        "auth": {
            "required": auth_info.requires_auth,
            "type":     auth_info.auth_type,
            "key_name": auth_info.auth_key_name,
        },
        "probe": {
            "tool":       first.get("name", ""),
            "args":       first.get("probe_args", {}),
            "timeout_ms": 5000,
        },
        "capabilities": caps,

        # Tout vient directement du serveur
        "tools":     [{k:v for k,v in t.items() if k != "probe_args"}
                      for t in tools_out],
        "resources": resources,   # depuis resources/list
        "prompts":   prompts,     # depuis prompts/list
    }

    # Écriture du schema
    out = schema_dir / mcp_id / "schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False))
    logger.info("Schema written: %s (%d tools, %d resources, %d prompts)",
                mcp_id, len(tools_out), len(resources), len(prompts))
    return schema


# ══════════════════════════════════════════════════════════════════════════════
# 3. HISTORIQUE
# ══════════════════════════════════════════════════════════════════════════════

def _log_call(db_path: Path, mcp_id: str, tool_name: str,
              request_json: str, response_json: str, response_type: str,
              started_at: str, duration_ms: int,
              session_id: str, caller: str) -> None:
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            INSERT INTO call_history
              (mcp_id, tool_name, request_json, response_json, response_type,
               started_at, duration_ms, session_id, caller)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (mcp_id, tool_name, request_json, response_json, response_type,
              started_at, duration_ms, session_id, caller))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("History log failed: %s", e)


def get_history(
    mcp_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retourne l'historique des appels, filtrable par MCP et/ou tool."""
    conn  = sqlite3.connect(db_path)
    where = []
    params= []
    if mcp_id:
        where.append("mcp_id=?");    params.append(mcp_id)
    if tool_name:
        where.append("tool_name=?"); params.append(tool_name)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"""
        SELECT id, mcp_id, tool_name, request_json, response_json,
               response_type, started_at, duration_ms, session_id, caller
        FROM call_history {clause}
        ORDER BY started_at DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [
        {"id": r[0], "mcp_id": r[1], "tool_name": r[2],
         "request": json.loads(r[3]), "response": json.loads(r[4]) if r[4] else None,
         "status": r[5], "started_at": r[6], "duration_ms": r[7],
         "session_id": r[8], "caller": r[9]}
        for r in rows
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_schema(mcp_id: str, schema_dir: Path) -> dict:
    p = schema_dir / mcp_id / "schema.json"
    if not p.exists():
        raise FileNotFoundError(f"Schema not found: {p}")
    return json.loads(p.read_text())


def _example_value(prop: dict) -> Any:
    if "enum"    in prop: return prop["enum"][0]
    if "default" in prop: return prop["default"]
    return {"string":"test","integer":1,"number":1.0,
            "boolean":True,"array":[],"object":{}}.get(prop.get("type","string"),"test")
