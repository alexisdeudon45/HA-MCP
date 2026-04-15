"""
MCP Schema Builder — génère un schema.json unifié par MCP.

Format unique quel que soit le transport (stdio / streamable-http / sse) :
{
  mcp_id, name, version, description,
  transport : { type, executor, command, args, url },
  auth      : { required, type, key_name },
  probe     : { tool, args, timeout_ms },
  capabilities: [...],
  tools: [{
    name, description,
    inputSchema  : JSON brut depuis tools/list (SDK),
    prompt_template: { system, user, variables, example_call }
  }]
}

Après génération, test réel sur chaque MCP via SDK.
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import os
DB_PATH    = Path(os.environ.get("HA_MCP_DB_PATH",
             str(Path(__file__).resolve().parent.parent.parent / "database" / "tool_v2.db")))
SCHEMA_DIR = Path(os.environ.get("HA_MCP_SCHEMA_DIR",
             str(Path(__file__).resolve().parent.parent.parent / "schemas" / "mcp")))

STDIO_COMMANDS = {
    "duckduckgo":          {"command": "npx", "args": ["-y", "duckduckgo-mcp-server"]},
    "sequential-thinking": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
    "playwright":          {"command": "npx", "args": ["-y", "@playwright/mcp"]},
    "filesystem":          {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/"]},
    "memory":              {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    "fetch":               {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"]},
    "puppeteer":           {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]},
    "everything":          {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"]},
}

SSE_URLS = {
    "blockscout":    "https://mcp.blockscout.com/mcp",
    "mermaid-chart": "https://chatgpt.mermaid.ai/anthropic/mcp",
    "dice":          "https://mcp.dice.com/mcp",
}


# ── Génération des schemas ─────────────────────────────────────────────────────

async def build_all_schemas(db_path: Path = DB_PATH,
                             schema_dir: Path = SCHEMA_DIR) -> dict[str, bool]:
    """Génère schema.json pour chaque MCP connu. Retourne {mcp_id: ok}."""
    schema_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    # stdio
    for mcp_id, cfg in STDIO_COMMANDS.items():
        ok = await _build_schema_stdio(mcp_id, cfg["command"], cfg["args"],
                                        db_path, schema_dir)
        results[mcp_id] = ok
        status = "✓" if ok else "✗"
        logger.info("  %s %s (stdio)", status, mcp_id)

    # SSE / streamable-http
    for mcp_id, url in SSE_URLS.items():
        ok = await _build_schema_sse(mcp_id, url, db_path, schema_dir)
        results[mcp_id] = ok
        status = "✓" if ok else "✗"
        logger.info("  %s %s (sse)", status, mcp_id)

    return results


async def _build_schema_stdio(mcp_id: str, command: str, args: list,
                               db_path: Path, schema_dir: Path) -> bool:
    from .mcp_client import MCPClient
    try:
        async with MCPClient.stdio(command, args) as client:
            schema = _assemble_schema(
                mcp_id=mcp_id,
                client=client,
                transport={
                    "type":     "stdio",
                    "executor": _detect_executor(command, args),
                    "command":  command,
                    "args":     args,
                    "url":      None,
                },
                tools_raw=await client.list_tools(),
                db_path=db_path,
            )
        _write_schema(schema, schema_dir / mcp_id / "schema.json")
        return True
    except Exception as e:
        logger.warning("Schema build failed %s: %s", mcp_id, e)
        return False


async def _build_schema_sse(mcp_id: str, url: str,
                             db_path: Path, schema_dir: Path) -> bool:
    from .mcp_client import MCPClient
    try:
        async with MCPClient.sse(url) as client:
            schema = _assemble_schema(
                mcp_id=mcp_id,
                client=client,
                transport={
                    "type":     client.server_info.get("transport", "streamable-http"),
                    "executor": None,
                    "command":  None,
                    "args":     [],
                    "url":      url,
                },
                tools_raw=await client.list_tools(),
                db_path=db_path,
            )
        _write_schema(schema, schema_dir / mcp_id / "schema.json")
        return True
    except Exception as e:
        logger.warning("Schema build failed %s: %s", mcp_id, e)
        return False


def _assemble_schema(mcp_id: str, client, transport: dict,
                     tools_raw: list, db_path: Path) -> dict:
    """Construit le dict schema complet pour un MCP."""
    # Auth depuis DB
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT requires_auth, auth_type, auth_key_name FROM mcp WHERE mcp_id=?", (mcp_id,))
    row = cur.fetchone()
    auth = {"required": bool(row[0]), "type": row[1], "key_name": row[2]} if row \
           else {"required": False, "type": "none", "key_name": None}

    # Capabilities depuis DB
    cur.execute("""
        SELECT DISTINCT c.name FROM capability c
        JOIN mcp_capability mc ON mc.cap_id=c.id WHERE mc.mcp_id=?
    """, (mcp_id,))
    capabilities = [r[0] for r in cur.fetchall()]

    # Probe = premier tool disponible
    first_tool = tools_raw[0] if tools_raw else None
    probe_args = {}
    if first_tool:
        schema = first_tool.inputSchema
        props  = schema.get("properties", {}) if isinstance(schema, dict) \
                 else (schema.model_dump().get("properties") or {})
        req    = schema.get("required", []) if isinstance(schema, dict) \
                 else (schema.model_dump().get("required") or [])
        for p in req[:2]:
            if p in props:
                probe_args[p] = _example_value(props[p])

    # Tools avec inputSchema brut + prompt_template depuis DB
    tools_out = []
    for tool in tools_raw:
        raw_schema = tool.inputSchema
        if hasattr(raw_schema, "model_dump"):
            input_schema = raw_schema.model_dump()
        else:
            input_schema = dict(raw_schema)

        cur.execute("""
            SELECT pt.system_prompt, pt.user_template, pt.variables, pt.example_call
            FROM prompt_template pt JOIN tool t ON t.id=pt.tool_id
            WHERE t.mcp_id=? AND t.name=?
        """, (mcp_id, tool.name))
        pt_row = cur.fetchone()
        prompt_template = None
        if pt_row:
            prompt_template = {
                "system":      pt_row[0],
                "user":        pt_row[1],
                "variables":   json.loads(pt_row[2]) if pt_row[2] else [],
                "example_call":json.loads(pt_row[3]) if pt_row[3] else None,
            }

        tools_out.append({
            "name":            tool.name,
            "description":     tool.description or "",
            "inputSchema":     input_schema,
            "prompt_template": prompt_template,
        })

    conn.close()

    return {
        "$id":         f"urn:ha-mcp:mcp:{mcp_id}:v1",
        "mcp_id":      mcp_id,
        "name":        client.server_info.get("name", mcp_id),
        "version":     client.server_info.get("version", "1.0.0"),
        "description": tools_raw[0].description[:200] if tools_raw else "",
        "transport":   transport,
        "auth":        auth,
        "probe": {
            "tool":       first_tool.name if first_tool else "",
            "args":       probe_args,
            "timeout_ms": 5000,
        },
        "capabilities": capabilities,
        "tools":        tools_out,
    }


def _write_schema(schema: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False))


def _detect_executor(command: str, args: list) -> str:
    pkg = " ".join(args).lower()
    if any(k in pkg for k in ["python", "uvx", "pip", ".py"]):
        return "uvx"
    return command  # npx ou node


def _example_value(prop: dict) -> Any:
    t = prop.get("type", "string")
    if "enum" in prop:    return prop["enum"][0]
    if "default" in prop: return prop["default"]
    return {"string": "test", "integer": 1, "number": 1.0,
            "boolean": True, "array": [], "object": {}}.get(t, "test")


# ── Tests ──────────────────────────────────────────────────────────────────────

async def test_all_schemas(schema_dir: Path = SCHEMA_DIR) -> dict[str, dict]:
    """
    Charge chaque schema.json et effectue un vrai appel probe via SDK.
    Retourne {mcp_id: {ok, tool, args, response_preview, error}}.
    """
    from .mcp_client import MCPClient
    results = {}

    for schema_path in sorted(schema_dir.glob("*/schema.json")):
        schema = json.loads(schema_path.read_text())
        if "transport" not in schema:   # ignorer anciens schemas sans transport
            continue
        mcp_id  = schema["mcp_id"]
        probe   = schema["probe"]
        t       = schema["transport"]

        logger.info("Testing %s [%s] → %s(%s)",
                    mcp_id, t["type"], probe["tool"], probe["args"])

        try:
            if t["type"] == "stdio":
                ctx = MCPClient.stdio(t["command"], t["args"])
            else:
                ctx = MCPClient.sse(t["url"])

            async with asyncio.timeout(15):
                async with ctx as client:
                    server_caps  = await client.get_server_capabilities()
                    tools        = await client.list_tools()
                    probe_result = None
                    probe_error  = None
                    # Probe isolé — une erreur ici ne doit pas killer le test
                    try:
                        probe_result = await client.call_tool(probe["tool"], probe["args"])
                    except Exception as pe:
                        probe_error = str(pe)[:120]

            results[mcp_id] = {
                "ok":               True,
                "transport":        t["type"],
                "server_name":      client.server_info.get("name"),
                "tools_count":      len(tools),
                "probe_tool":       probe["tool"],
                "probe_args":       probe["args"],
                "response_preview": str(probe_result)[:120] if probe_result else f"[probe error] {probe_error}",
                "has_tools_cap":    bool(server_caps.get("tools")),
            }

        except Exception as e:
            results[mcp_id] = {
                "ok":       False,
                "transport":t["type"],
                "error":    str(e)[:200],
            }

    return results
