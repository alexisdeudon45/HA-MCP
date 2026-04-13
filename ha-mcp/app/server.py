"""HA-MCP v2: Flask web server with 2-stage pipeline, dynamic MCPs, and live dashboard."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, render_template

from .mcp_orchestrator import MCPOrchestrator
from .pipeline import PipelineEngine
from .mcp_orchestrator.mcp_manager import MCPManager

logger = logging.getLogger(__name__)

LOG_LEVEL = os.environ.get("HA_MCP_LOG_LEVEL", "info").upper()
STORAGE_PATH = Path(os.environ.get("HA_MCP_STORAGE_PATH", "/share/ha-mcp"))
INGRESS_ENTRY = os.environ.get("HA_MCP_INGRESS_ENTRY", "")
SCHEMAS_DIR = Path("/schemas")
KEYS_FILE = STORAGE_PATH / "api_keys.json"

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ── MCP Catalog ───────────────────────────────────────────────────────────────
MCP_CATALOG = [
    {"id":"local_filesystem","name":"Filesystem Local","category":"ingestion","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Lecture/ecriture fichiers locaux","tools":[{"name":"read_file","description":"Read file"},{"name":"write_file","description":"Write file"}]},
    {"id":"local_pdf","name":"PDF Extractor","category":"ingestion","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Extraction texte PDF via PyMuPDF","tools":[{"name":"extract_pdf_text","description":"Extract PDF text"}]},
    {"id":"local_reasoning","name":"Raisonnement Local","category":"raisonnement","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Analyse et comparaison locale","tools":[{"name":"analyze_text","description":"Analyze text"},{"name":"compare_structures","description":"Compare structures"}]},
    {"id":"local_validation","name":"Schema Validator","category":"validation","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Validation JSON Schema","tools":[{"name":"validate_json_schema","description":"Validate JSON"}]},
    {"id":"local_generation","name":"Generateur Local","category":"generation","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Generation rapports","tools":[{"name":"generate_report","description":"Generate report"}]},
    {"id":"duckduckgo","name":"DuckDuckGo Search","category":"enrichissement","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Recherche web publique sans cle","tools":[{"name":"web_search","description":"Web search"}]},
    {"id":"sequential-thinking","name":"Sequential Thinking","category":"raisonnement","requires_auth":False,"auth_type":None,"auth_key_name":None,"description":"Raisonnement sequentiel structure","tools":[{"name":"sequentialthinking","description":"Step-by-step reasoning"}]},
    {"id":"anthropic_claude","name":"Anthropic Claude API","category":"raisonnement","requires_auth":True,"auth_type":"api_key","auth_key_name":"ANTHROPIC_API_KEY","description":"LLM Claude pour structuration, analyse, generation","tools":[{"name":"chat_completion","description":"Claude analysis"},{"name":"nlp_extract","description":"NLP extraction"}]},
    {"id":"openai_gpt","name":"OpenAI GPT API","category":"raisonnement","requires_auth":True,"auth_type":"api_key","auth_key_name":"OPENAI_API_KEY","description":"LLM GPT alternatif","tools":[{"name":"chat_completion","description":"GPT analysis"}]},
    {"id":"google_gemini","name":"Google Gemini","category":"raisonnement","requires_auth":True,"auth_type":"api_key","auth_key_name":"GOOGLE_API_KEY","description":"LLM Gemini alternatif","tools":[{"name":"generate_content","description":"Gemini content"}]},
    {"id":"mistral_ai","name":"Mistral AI","category":"raisonnement","requires_auth":True,"auth_type":"api_key","auth_key_name":"MISTRAL_API_KEY","description":"LLM Mistral (europeen)","tools":[{"name":"chat_completion","description":"Mistral analysis"}]},
    {"id":"huggingface","name":"Hugging Face","category":"structuration","requires_auth":True,"auth_type":"api_key","auth_key_name":"HF_API_KEY","description":"Modeles NLP pour NER et classification","tools":[{"name":"ner_extraction","description":"Named entity recognition"}]},
    {"id":"notion_api","name":"Notion","category":"enrichissement","requires_auth":True,"auth_type":"api_key","auth_key_name":"NOTION_API_KEY","description":"Stockage/lecture analyses dans Notion","tools":[{"name":"notion_search","description":"Search Notion"}]},
    {"id":"google_drive","name":"Google Drive","category":"ingestion","requires_auth":True,"auth_type":"oauth","auth_key_name":"GOOGLE_DRIVE_TOKEN","description":"Lecture PDFs depuis Drive","tools":[{"name":"read_file","description":"Read from Drive"}]},
]

# ── Helpers ────────────────────────────────────────────────────────────────────
def _load_api_keys() -> dict[str, str]:
    if KEYS_FILE.exists():
        with open(KEYS_FILE) as f: return json.load(f)
    return {}

def _save_api_keys(keys: dict[str, str]) -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KEYS_FILE, "w") as f: json.dump(keys, f, indent=2)

def _build_active_mcp_tools(api_keys: dict) -> dict[str, list[dict[str, str]]]:
    result = {}
    for mcp in MCP_CATALOG:
        if not mcp["requires_auth"] or (mcp.get("auth_key_name") and api_keys.get(mcp["auth_key_name"])):
            result[mcp["id"]] = mcp["tools"]
    return result


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
        catalog_json=json.dumps(MCP_CATALOG),
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
    api_keys = _load_api_keys()
    return jsonify({"mcps": [{**m, "status": "available" if not m["requires_auth"] else ("unlocked" if api_keys.get(m.get("auth_key_name","")) else "excluded")} for m in MCP_CATALOG]})

@app.route("/api/mcps/dynamic")
def api_dynamic_mcps():
    manager = MCPManager(STORAGE_PATH, _load_api_keys())
    return jsonify({"mcps": manager.get_all_mcps(), "config": manager.get_config()})

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
