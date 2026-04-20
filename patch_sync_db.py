#!/usr/bin/env python3
"""
patch_sync_db.py — Injecte la sync mcp_config.json → DB dans server.py
Usage: python3 patch_sync_db.py
       (depuis ~/MCP/HA-MCP)
"""
import re, sys
from pathlib import Path

SERVER = Path("ha-mcp/app/server.py")
if not SERVER.exists():
    print(f"ERREUR: {SERVER} introuvable. Lance depuis ~/MCP/HA-MCP", file=sys.stderr)
    sys.exit(1)

src = SERVER.read_text()

# ── Garde-fou : déjà patché ? ─────────────────────────────────────────────────
if "_sync_mcp_config_to_db" in src:
    print("Déjà patché. Rien à faire.")
    sys.exit(0)

# ── Bloc à injecter ───────────────────────────────────────────────────────────
PATCH = '''

# ── Sync mcp_config.json → DB (bridge dashboard) ─────────────────────────────

def _sync_mcp_config_to_db() -> int:
    """
    Lit mcp_config.json (source de vérité du pipeline)
    et upsert les MCPs / transports / tools dans tool_v2.db
    pour que le dashboard les affiche.
    """
    config_file = STORAGE_PATH / "mcp_config.json"
    if not config_file.exists():
        logger.warning("sync_config_to_db: mcp_config.json introuvable")
        return 0

    with open(config_file) as f:
        config = json.load(f)

    mcps = config.get("mcps", [])
    if not mcps:
        return 0

    # Créer le schéma si la DB n'existait pas
    schema_file = Path(__file__).resolve().parent.parent / "database" / "schema_v2.sql"
    need_schema = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    if need_schema and schema_file.exists():
        conn.executescript(schema_file.read_text())
        conn.commit()
        logger.info("DB schema initialisé: %s", DB_PATH)

    count = 0
    for mcp in mcps:
        mcp_id = mcp.get("mcp_id", "")
        if not mcp_id or mcp.get("status") != "active":
            continue

        name         = mcp.get("name", mcp_id)
        req_auth     = mcp.get("requires_auth", False)
        auth_key     = mcp.get("auth_key_name", "") or ""
        ttype        = mcp.get("transport", "stdio")
        command      = mcp.get("command", "")
        args         = mcp.get("args", [])
        description  = mcp.get("description", "")
        caps         = mcp.get("capabilities", [])
        category     = caps[0] if caps else "enrichissement"
        now          = datetime.now(timezone.utc).isoformat()

        # ── MCP ──
        conn.execute("""
            INSERT INTO mcp
              (mcp_id, name, version, description, plug_and_play, requires_auth,
               auth_type, auth_key_name, source, registry_category, discovered_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mcp_id) DO UPDATE SET
              name=excluded.name, description=excluded.description,
              plug_and_play=excluded.plug_and_play,
              requires_auth=excluded.requires_auth,
              auth_type=excluded.auth_type,
              auth_key_name=excluded.auth_key_name,
              registry_category=excluded.registry_category
        """, (
            mcp_id, name, "1.0.0", description,
            int(not req_auth), int(req_auth),
            "api_key" if req_auth else "none",
            auth_key if req_auth else None,
            "discovered", category, now,
        ))

        # ── Transport ──
        test_logs = mcp.get("test_log", [])
        probe_ok  = any(t.get("status") == "pass" for t in test_logs)
        probe_at  = test_logs[-1].get("timestamp", now) if test_logs else now

        conn.execute("""
            INSERT INTO transport
              (mcp_id, type, executor, command, args_json, url,
               last_probe_at, last_probe_ok, last_probe_error)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mcp_id, type) DO UPDATE SET
              executor=excluded.executor, command=excluded.command,
              args_json=excluded.args_json,
              last_probe_at=excluded.last_probe_at,
              last_probe_ok=excluded.last_probe_ok
        """, (
            mcp_id, ttype, command.split("/")[-1] if command else "npx",
            command, json.dumps(args), None,
            probe_at, int(probe_ok), None,
        ))

        # ── Tools ──
        tools = mcp.get("tools", [])
        if not tools:
            for tl in test_logs:
                if tl.get("tools"):
                    tools = tl["tools"]
                    break
        for tool in tools:
            tname = tool if isinstance(tool, str) else tool.get("name", "")
            tdesc = "" if isinstance(tool, str) else tool.get("description", "")
            if tname:
                conn.execute("""
                    INSERT INTO tool (mcp_id, name, description, timeout_ms)
                    VALUES (?,?,?,?)
                    ON CONFLICT(mcp_id, name) DO UPDATE SET
                      description=excluded.description
                """, (mcp_id, tname, tdesc, 10000))

        count += 1

    conn.commit()
    conn.close()
    logger.info("sync_config_to_db: %d MCPs synchronisés", count)
    return count


@app.on_event("startup")
async def on_startup():
    """Sync mcp_config.json → DB au démarrage."""
    try:
        synced = _sync_mcp_config_to_db()
        logger.info("Startup sync: %d MCPs en DB", synced)
    except Exception as e:
        logger.error("Startup sync failed: %s", e, exc_info=True)

'''

# ── Point d'insertion : après "templates = Jinja2Templates(...)" ──────────────
anchor = re.search(
    r'^(templates\s*=\s*Jinja2Templates\([^)]+\))\s*$',
    src, re.MULTILINE
)
if not anchor:
    print("ERREUR: ligne 'templates = Jinja2Templates(...)' introuvable", file=sys.stderr)
    sys.exit(1)

insert_pos = anchor.end()
patched = src[:insert_pos] + PATCH + src[insert_pos:]

# ── Écriture ──────────────────────────────────────────────────────────────────
SERVER.write_text(patched)
print(f"OK — server.py patché ({len(PATCH)} chars insérés après ligne {src[:insert_pos].count(chr(10))+1})")
print("Maintenant: git add -A && git commit -m 'fix: sync mcp_config→DB at startup' && git push")
print("Puis rebuild l'add-on dans HA.")
