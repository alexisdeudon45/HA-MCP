"""HA-MCP: Flask web server for Home Assistant Ingress.

Provides a web UI accessible via the HA sidebar to upload PDFs and run analysis.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string

from .mcp_orchestrator import MCPOrchestrator
from .pipeline import PipelineEngine
from .interface.ingestion import PDFIngestion
from .interface.results import ResultsFormatter

logger = logging.getLogger(__name__)

# ── Configuration from environment (set by run.sh from HA options) ────────────
LOG_LEVEL = os.environ.get("HA_MCP_LOG_LEVEL", "info").upper()
STORAGE_PATH = Path(os.environ.get("HA_MCP_STORAGE_PATH", "/share/ha-mcp"))
INGRESS_ENTRY = os.environ.get("HA_MCP_INGRESS_ENTRY", "")
SCHEMAS_DIR = Path("/schemas")
CONFIG_DIR = Path("/config")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload

# ── HTML Template ─────────────────────────────────────────────────────────────
INDEX_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MCP-Poste Analyzer</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #1c1c1c; color: #e1e1e1; padding: 24px;
    }
    .container { max-width: 800px; margin: 0 auto; }
    h1 { font-size: 1.5rem; margin-bottom: 8px; color: #03a9f4; }
    .subtitle { color: #888; margin-bottom: 24px; font-size: 0.9rem; }
    .card {
      background: #2c2c2c; border-radius: 12px; padding: 24px;
      margin-bottom: 16px; border: 1px solid #3c3c3c;
    }
    .card h2 { font-size: 1.1rem; margin-bottom: 16px; color: #fff; }
    label { display: block; margin-bottom: 6px; font-weight: 500; color: #aaa; font-size: 0.85rem; }
    input[type="file"] {
      width: 100%; padding: 12px; background: #1c1c1c; border: 1px dashed #555;
      border-radius: 8px; color: #e1e1e1; margin-bottom: 16px; cursor: pointer;
    }
    input[type="file"]:hover { border-color: #03a9f4; }
    button {
      background: #03a9f4; color: #fff; border: none; padding: 12px 32px;
      border-radius: 8px; font-size: 1rem; cursor: pointer; width: 100%;
      font-weight: 600; transition: background 0.2s;
    }
    button:hover { background: #0288d1; }
    button:disabled { background: #555; cursor: not-allowed; }
    .status { margin-top: 16px; padding: 12px; border-radius: 8px; display: none; }
    .status.running { display: block; background: #1a3a4a; border: 1px solid #03a9f4; }
    .status.success { display: block; background: #1a3a2a; border: 1px solid #4caf50; }
    .status.error { display: block; background: #3a1a1a; border: 1px solid #f44336; }
    .results { margin-top: 16px; }
    .phase { padding: 8px 12px; margin: 4px 0; border-radius: 6px; background: #1c1c1c; display: flex; justify-content: space-between; }
    .phase .ok { color: #4caf50; } .phase .fail { color: #f44336; } .phase .skip { color: #ff9800; }
    pre { background: #1c1c1c; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 0.8rem; margin-top: 12px; max-height: 400px; overflow-y: auto; }
    .info-bar { display: flex; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
    .info-item { background: #1c1c1c; padding: 8px 16px; border-radius: 8px; font-size: 0.8rem; }
    .info-item strong { color: #03a9f4; }
  </style>
</head>
<body>
  <div class="container">
    <h1>MCP-Poste Analyzer</h1>
    <p class="subtitle">Schema-Driven MCP Orchestrated Candidacy Analyzer</p>

    <div class="info-bar">
      <div class="info-item"><strong>Host:</strong> {{ host }}</div>
      <div class="info-item"><strong>Storage:</strong> {{ storage }}</div>
      <div class="info-item"><strong>Schemas:</strong> {{ schema_count }}</div>
    </div>

    <div class="card">
      <h2>Analyser une candidature</h2>
      <form id="analyzeForm" enctype="multipart/form-data">
        <label for="offer">Offre d'emploi (PDF)</label>
        <input type="file" id="offer" name="offer_pdf" accept=".pdf" required>
        <label for="cv">CV du candidat (PDF)</label>
        <input type="file" id="cv" name="cv_pdf" accept=".pdf" required>
        <button type="submit" id="submitBtn">Lancer l'analyse</button>
      </form>
      <div id="status" class="status"></div>
    </div>

    <div id="resultsCard" class="card" style="display:none;">
      <h2>Resultats</h2>
      <div id="phases" class="results"></div>
      <pre id="jsonOutput"></pre>
    </div>
  </div>

  <script>
    const base = '{{ ingress }}';
    document.getElementById('analyzeForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('submitBtn');
      const status = document.getElementById('status');
      const resultsCard = document.getElementById('resultsCard');
      btn.disabled = true; btn.textContent = 'Analyse en cours...';
      status.className = 'status running'; status.textContent = 'Pipeline en cours...';
      resultsCard.style.display = 'none';

      const formData = new FormData(e.target);
      try {
        const resp = await fetch(base + '/api/analyze', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.error) {
          status.className = 'status error'; status.textContent = 'Erreur: ' + data.error;
        } else {
          status.className = 'status success'; status.textContent = 'Analyse terminee - ' + (data.recommendation || 'done');
          const phasesDiv = document.getElementById('phases');
          phasesDiv.innerHTML = '';
          for (const [name, info] of Object.entries(data.phases || {})) {
            const s = info.status || '?';
            const cls = s === 'completed' ? 'ok' : s === 'failed' ? 'fail' : 'skip';
            phasesDiv.innerHTML += '<div class="phase"><span>' + name + '</span><span class="' + cls + '">' + s.toUpperCase() + '</span></div>';
          }
          document.getElementById('jsonOutput').textContent = JSON.stringify(data, null, 2);
          resultsCard.style.display = 'block';
        }
      } catch (err) {
        status.className = 'status error'; status.textContent = 'Erreur: ' + err.message;
      }
      btn.disabled = false; btn.textContent = 'Lancer l\\'analyse';
    });
  </script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main UI."""
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()

    return render_template_string(
        INDEX_HTML,
        ingress=INGRESS_ENTRY,
        host="Raspberry Pi 5",
        storage=str(STORAGE_PATH),
        schema_count=len(registry.list_schemas()),
    )


@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "addon": "ha-mcp",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/schemas")
def list_schemas():
    """List all schemas in the registry."""
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    return jsonify({"schemas": registry.list_schemas()})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Run the full analysis pipeline on uploaded PDFs."""
    if "offer_pdf" not in request.files or "cv_pdf" not in request.files:
        return jsonify({"error": "Both offer_pdf and cv_pdf files are required"}), 400

    offer_file = request.files["offer_pdf"]
    cv_file = request.files["cv_pdf"]

    if not offer_file.filename or not cv_file.filename:
        return jsonify({"error": "Empty filenames"}), 400

    # Save uploaded files
    session_id = str(uuid.uuid4())
    upload_dir = STORAGE_PATH / "inputs" / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    offer_path = upload_dir / f"offer_{offer_file.filename}"
    cv_path = upload_dir / f"cv_{cv_file.filename}"
    offer_file.save(str(offer_path))
    cv_file.save(str(cv_path))

    logger.info("Files uploaded: %s, %s", offer_path, cv_path)

    try:
        # Initialize orchestrator pointing to container paths
        orchestrator = MCPOrchestrator(project_root=Path("/"))
        init_result = orchestrator.initialize(session_id)

        # Discover local MCPs
        mcp_tools = _build_ha_mcp_tools()
        orchestrator.discover_mcps(mcp_tools)

        # Run pipeline
        engine = PipelineEngine(orchestrator, STORAGE_PATH)
        results = engine.run(str(offer_path), str(cv_path))

        # Extract summary for response
        gen_phase = results.get("phases", {}).get("generation", {})
        response = {
            "session_id": session_id,
            "status": results.get("plan", {}).get("pipeline", {}).get("status", "unknown"),
            "phases": results.get("phases", {}),
            "recommendation": gen_phase.get("recommendation", "N/A"),
            "artifacts_count": gen_phase.get("artifacts_count", 0),
        }

        return jsonify(response)

    except Exception as e:
        logger.exception("Pipeline failed")
        return jsonify({"error": str(e), "session_id": session_id}), 500


@app.route("/api/results/<session_id>")
def get_results(session_id: str):
    """Retrieve results for a previous session."""
    result_path = STORAGE_PATH / "outputs" / f"result_{session_id}.json"
    if not result_path.exists():
        return jsonify({"error": f"No results for session {session_id}"}), 404

    with open(result_path) as f:
        return jsonify(json.load(f))


@app.route("/api/sessions")
def list_sessions():
    """List all analysis sessions."""
    outputs_dir = STORAGE_PATH / "outputs"
    if not outputs_dir.exists():
        return jsonify({"sessions": []})

    sessions = []
    for f in sorted(outputs_dir.glob("result_*.json"), reverse=True):
        sid = f.stem.replace("result_", "")
        stat = f.stat()
        sessions.append({
            "session_id": sid,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes": stat.st_size,
        })

    return jsonify({"sessions": sessions})


def _build_ha_mcp_tools() -> dict[str, list[dict[str, str]]]:
    """Local MCP tools available inside the HA addon container."""
    return {
        "local_filesystem": [
            {"name": "read_file", "description": "Read a file from the local filesystem"},
            {"name": "write_file", "description": "Write a file to the local filesystem"},
        ],
        "local_pdf": [
            {"name": "extract_pdf_text", "description": "Extract text from a PDF file"},
            {"name": "parse_pdf_structure", "description": "Parse PDF document structure"},
        ],
        "local_reasoning": [
            {"name": "analyze_text", "description": "Analyze and reason about text content"},
            {"name": "compare_structures", "description": "Compare two structured objects"},
            {"name": "evaluate_alignment", "description": "Evaluate alignment between requirements and evidence"},
        ],
        "local_validation": [
            {"name": "validate_json_schema", "description": "Validate JSON data against a schema"},
        ],
        "local_generation": [
            {"name": "generate_report", "description": "Generate a formatted report"},
            {"name": "format_output", "description": "Format data for output"},
        ],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("HA-MCP server starting on port 8099")
    app.run(host="0.0.0.0", port=8099, debug=False)


if __name__ == "__main__":
    main()
