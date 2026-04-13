"""HA-MCP: Flask web server for Home Assistant Ingress.

Full dashboard with:
- MCP listing with status (available/excluded/unlockable)
- API key management to unlock authenticated MCPs
- PDF upload inputs
- Pipeline execution and results
- Session history
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string

from .mcp_orchestrator import MCPOrchestrator
from .mcp_orchestrator.discovery import MCPDiscovery
from .mcp_orchestrator.capability import CapabilityCategory
from .pipeline import PipelineEngine
from .interface.ingestion import PDFIngestion
from .interface.results import ResultsFormatter

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("HA_MCP_LOG_LEVEL", "info").upper()
STORAGE_PATH = Path(os.environ.get("HA_MCP_STORAGE_PATH", "/share/ha-mcp"))
INGRESS_ENTRY = os.environ.get("HA_MCP_INGRESS_ENTRY", "")
SCHEMAS_DIR = Path("/schemas")
CONFIG_DIR = Path("/config")
KEYS_FILE = STORAGE_PATH / "api_keys.json"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ── Known MCP catalog ─────────────────────────────────────────────────────────
# All MCPs the system knows about — both local (no auth) and external (auth needed)
MCP_CATALOG = [
    {
        "id": "local_filesystem",
        "name": "Filesystem Local",
        "category": "ingestion",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Lecture/ecriture de fichiers locaux",
        "tools": [
            {"name": "read_file", "description": "Read a file from the local filesystem"},
            {"name": "write_file", "description": "Write a file to the local filesystem"},
            {"name": "search_files", "description": "Search for files by pattern"},
        ],
    },
    {
        "id": "local_pdf",
        "name": "PDF Extractor (PyMuPDF)",
        "category": "ingestion",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Extraction de texte depuis les fichiers PDF via PyMuPDF",
        "tools": [
            {"name": "extract_pdf_text", "description": "Extract text from a PDF file"},
            {"name": "parse_pdf_structure", "description": "Parse PDF document structure"},
        ],
    },
    {
        "id": "local_reasoning",
        "name": "Raisonnement Local",
        "category": "raisonnement",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Analyse, comparaison et evaluation locale",
        "tools": [
            {"name": "analyze_text", "description": "Analyze and reason about text content"},
            {"name": "compare_structures", "description": "Compare two structured objects"},
            {"name": "evaluate_alignment", "description": "Evaluate alignment between requirements and evidence"},
            {"name": "prioritize_items", "description": "Prioritize a list of items by criteria"},
        ],
    },
    {
        "id": "local_validation",
        "name": "Schema Validator",
        "category": "validation",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Validation JSON Schema locale",
        "tools": [
            {"name": "validate_json_schema", "description": "Validate JSON data against a schema"},
            {"name": "check_conformity", "description": "Check data conformity to constraints"},
        ],
    },
    {
        "id": "local_generation",
        "name": "Generateur Local",
        "category": "generation",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Generation de rapports et artefacts",
        "tools": [
            {"name": "generate_report", "description": "Generate a formatted report"},
            {"name": "produce_artifact", "description": "Produce an output artifact"},
            {"name": "format_output", "description": "Format data for output"},
        ],
    },
    {
        "id": "anthropic_claude",
        "name": "Anthropic Claude API",
        "category": "raisonnement",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "ANTHROPIC_API_KEY",
        "description": "LLM Claude pour structuration, analyse et generation avancee",
        "tools": [
            {"name": "chat_completion", "description": "Generate structured analysis via Claude"},
            {"name": "extract_entities", "description": "Extract structured entities from text"},
        ],
    },
    {
        "id": "openai_gpt",
        "name": "OpenAI GPT API",
        "category": "raisonnement",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "OPENAI_API_KEY",
        "description": "LLM GPT pour structuration et analyse alternative",
        "tools": [
            {"name": "chat_completion", "description": "Generate analysis via GPT"},
        ],
    },
    {
        "id": "google_gemini",
        "name": "Google Gemini API",
        "category": "raisonnement",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "GOOGLE_API_KEY",
        "description": "LLM Gemini pour structuration et analyse alternative",
        "tools": [
            {"name": "generate_content", "description": "Generate content via Gemini"},
        ],
    },
    {
        "id": "mistral_ai",
        "name": "Mistral AI API",
        "category": "raisonnement",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "MISTRAL_API_KEY",
        "description": "LLM Mistral pour structuration et analyse (modeles europeens)",
        "tools": [
            {"name": "chat_completion", "description": "Generate analysis via Mistral"},
        ],
    },
    {
        "id": "duckduckgo_search",
        "name": "DuckDuckGo Search",
        "category": "enrichissement",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Recherche web publique pour enrichir les donnees (entreprise, contexte)",
        "tools": [
            {"name": "web_search", "description": "Search the web for public information"},
        ],
    },
    {
        "id": "sequential_thinking",
        "name": "Sequential Thinking",
        "category": "raisonnement",
        "requires_auth": False,
        "auth_type": None,
        "auth_key_name": None,
        "description": "Raisonnement sequentiel structure pour analyse complexe",
        "tools": [
            {"name": "sequentialthinking", "description": "Step-by-step structured reasoning"},
        ],
    },
    {
        "id": "notion_api",
        "name": "Notion",
        "category": "enrichissement",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "NOTION_API_KEY",
        "description": "Acces aux bases Notion pour stocker/lire les analyses",
        "tools": [
            {"name": "notion_search", "description": "Search Notion pages"},
            {"name": "notion_create_page", "description": "Create a Notion page"},
        ],
    },
    {
        "id": "google_drive",
        "name": "Google Drive",
        "category": "ingestion",
        "requires_auth": True,
        "auth_type": "oauth",
        "auth_key_name": "GOOGLE_DRIVE_TOKEN",
        "description": "Lecture de PDFs depuis Google Drive",
        "tools": [
            {"name": "read_file", "description": "Read a file from Google Drive"},
            {"name": "list_files", "description": "List files in Drive"},
        ],
    },
    {
        "id": "huggingface",
        "name": "Hugging Face Inference",
        "category": "structuration",
        "requires_auth": True,
        "auth_type": "api_key",
        "auth_key_name": "HF_API_KEY",
        "description": "Modeles NLP pour extraction d'entites et classification",
        "tools": [
            {"name": "ner_extraction", "description": "Named entity recognition"},
            {"name": "text_classification", "description": "Classify text segments"},
        ],
    },
]


# ── API Keys storage ──────────────────────────────────────────────────────────

def _load_api_keys() -> dict[str, str]:
    if KEYS_FILE.exists():
        with open(KEYS_FILE) as f:
            return json.load(f)
    return {}


def _save_api_keys(keys: dict[str, str]) -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


def _get_mcp_status(mcp: dict, api_keys: dict) -> str:
    """Determine MCP status: available, excluded, unlocked."""
    if not mcp["requires_auth"]:
        return "available"
    key_name = mcp.get("auth_key_name", "")
    if key_name and api_keys.get(key_name):
        return "unlocked"
    return "excluded"


def _build_active_mcp_tools(api_keys: dict) -> dict[str, list[dict[str, str]]]:
    """Build MCP tool map with only available/unlocked MCPs."""
    result = {}
    for mcp in MCP_CATALOG:
        status = _get_mcp_status(mcp, api_keys)
        if status in ("available", "unlocked"):
            result[mcp["id"]] = mcp["tools"]
    return result


# ── HTML Dashboard ────────────────────────────────────────────────────────────

INDEX_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HA-MCP Dashboard</title>
  <style>
    :root {
      --bg: #1c1c1c; --bg2: #2c2c2c; --bg3: #3c3c3c; --bg-input: #141414;
      --blue: #03a9f4; --blue-d: #0288d1; --green: #4caf50; --green-bg: #1a3a2a;
      --red: #f44336; --red-bg: #3a1a1a; --orange: #ff9800; --orange-bg: #3a2a1a;
      --text: #e1e1e1; --text2: #aaa; --text3: #666;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); }

    /* ── Navigation tabs ── */
    .nav { display: flex; background: var(--bg2); border-bottom: 1px solid var(--bg3); padding: 0 24px; position: sticky; top: 0; z-index: 10; }
    .nav-tab {
      padding: 14px 20px; cursor: pointer; color: var(--text2); font-size: 0.85rem;
      font-weight: 600; border-bottom: 3px solid transparent; transition: all 0.2s;
    }
    .nav-tab:hover { color: var(--text); }
    .nav-tab.active { color: var(--blue); border-bottom-color: var(--blue); }
    .nav-title { padding: 12px 20px 12px 0; font-weight: 700; color: var(--blue); font-size: 1rem; margin-right: auto; }

    /* ── Page sections ── */
    .page { display: none; padding: 24px; max-width: 1000px; margin: 0 auto; }
    .page.active { display: block; }

    /* ── Cards ── */
    .card { background: var(--bg2); border-radius: 12px; padding: 20px; margin-bottom: 16px; border: 1px solid var(--bg3); }
    .card h2 { font-size: 1rem; margin-bottom: 14px; color: #fff; }
    .card h3 { font-size: 0.9rem; margin-bottom: 10px; color: var(--text2); }

    /* ── Forms ── */
    label { display: block; margin-bottom: 5px; font-weight: 500; color: var(--text2); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; }
    input[type="text"], input[type="password"] {
      width: 100%; padding: 10px 14px; background: var(--bg-input); border: 1px solid var(--bg3);
      border-radius: 8px; color: var(--text); font-size: 0.9rem; margin-bottom: 12px; outline: none;
    }
    input[type="text"]:focus, input[type="password"]:focus { border-color: var(--blue); }
    input[type="file"] {
      width: 100%; padding: 14px; background: var(--bg-input); border: 2px dashed var(--bg3);
      border-radius: 8px; color: var(--text); margin-bottom: 14px; cursor: pointer;
    }
    input[type="file"]:hover { border-color: var(--blue); }
    button, .btn {
      background: var(--blue); color: #fff; border: none; padding: 10px 24px;
      border-radius: 8px; font-size: 0.9rem; cursor: pointer; font-weight: 600; transition: background 0.2s;
    }
    button:hover, .btn:hover { background: var(--blue-d); }
    button:disabled { background: var(--text3); cursor: not-allowed; }
    .btn-sm { padding: 6px 14px; font-size: 0.8rem; }
    .btn-danger { background: var(--red); }
    .btn-danger:hover { background: #d32f2f; }
    .btn-success { background: var(--green); }
    .btn-outline { background: transparent; border: 1px solid var(--bg3); color: var(--text2); }
    .btn-outline:hover { border-color: var(--blue); color: var(--blue); background: transparent; }

    /* ── MCP list ── */
    .mcp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
    .mcp-card {
      background: var(--bg-input); border-radius: 10px; padding: 16px; border: 1px solid var(--bg3);
      display: flex; flex-direction: column; gap: 8px;
    }
    .mcp-header { display: flex; justify-content: space-between; align-items: center; }
    .mcp-name { font-weight: 600; font-size: 0.95rem; }
    .mcp-desc { color: var(--text2); font-size: 0.8rem; line-height: 1.4; }
    .mcp-tools { display: flex; flex-wrap: wrap; gap: 4px; }
    .mcp-tool-tag { background: var(--bg3); padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; color: var(--text2); }
    .mcp-footer { display: flex; justify-content: space-between; align-items: center; margin-top: auto; }
    .mcp-cat { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text3); }

    /* ── Badges ── */
    .badge {
      display: inline-block; padding: 3px 10px; border-radius: 12px;
      font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
    }
    .badge-available { background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }
    .badge-unlocked { background: #1a2a3a; color: var(--blue); border: 1px solid var(--blue); }
    .badge-excluded { background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }

    /* ── Key config ── */
    .key-row { display: flex; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
    .key-row .field { flex: 1; }
    .key-row .field label { margin-bottom: 4px; }
    .key-row .field input { margin-bottom: 0; }
    .key-row .actions { display: flex; gap: 6px; padding-bottom: 1px; }
    .key-saved { color: var(--green); font-size: 0.8rem; display: none; }

    /* ── Pipeline status ── */
    .phase-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 14px; margin: 4px 0; border-radius: 8px; background: var(--bg-input);
    }
    .phase-name { font-size: 0.85rem; }
    .phase-status { font-weight: 600; font-size: 0.8rem; }
    .phase-ok { color: var(--green); }
    .phase-fail { color: var(--red); }
    .phase-skip { color: var(--orange); }
    .phase-pending { color: var(--text3); }

    /* ── Status bars ── */
    .status-bar { padding: 12px 16px; border-radius: 8px; margin-top: 12px; display: none; font-size: 0.85rem; }
    .status-bar.running { display: block; background: #1a3a4a; border: 1px solid var(--blue); }
    .status-bar.success { display: block; background: var(--green-bg); border: 1px solid var(--green); }
    .status-bar.error { display: block; background: var(--red-bg); border: 1px solid var(--red); }

    /* ── Info grid ── */
    .info-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }
    .info-cell { background: var(--bg-input); padding: 12px 16px; border-radius: 8px; }
    .info-cell .val { font-size: 1.2rem; font-weight: 700; color: var(--blue); }
    .info-cell .lbl { font-size: 0.7rem; color: var(--text3); text-transform: uppercase; margin-top: 2px; }

    /* ── Results ── */
    pre.json { background: var(--bg-input); padding: 16px; border-radius: 8px; overflow: auto; font-size: 0.75rem; max-height: 500px; }

    /* ── Sessions list ── */
    .session-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 14px; margin: 4px 0; border-radius: 8px; background: var(--bg-input); cursor: pointer;
    }
    .session-row:hover { border: 1px solid var(--blue); }
    .session-id { font-family: monospace; font-size: 0.8rem; }
    .session-date { color: var(--text3); font-size: 0.8rem; }

    /* ── Coverage bar ── */
    .coverage { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0; }
    .cov-item { padding: 6px 12px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; }
    .cov-ok { background: var(--green-bg); color: var(--green); }
    .cov-miss { background: var(--orange-bg); color: var(--orange); }
  </style>
</head>
<body>

<!-- ── Navigation ── -->
<div class="nav">
  <div class="nav-title">HA-MCP</div>
  <div class="nav-tab active" data-page="dashboard">Dashboard</div>
  <div class="nav-tab" data-page="mcps">MCPs</div>
  <div class="nav-tab" data-page="keys">Cles API</div>
  <div class="nav-tab" data-page="analyze">Analyser</div>
  <div class="nav-tab" data-page="history">Historique</div>
</div>

<!-- ═══════════════ DASHBOARD ═══════════════ -->
<div id="page-dashboard" class="page active">
  <div class="info-grid">
    <div class="info-cell"><div class="val" id="stat-mcps-total">-</div><div class="lbl">MCPs Total</div></div>
    <div class="info-cell"><div class="val" id="stat-mcps-active">-</div><div class="lbl">MCPs Actifs</div></div>
    <div class="info-cell"><div class="val" id="stat-mcps-excluded">-</div><div class="lbl">MCPs Exclus</div></div>
    <div class="info-cell"><div class="val" id="stat-schemas">{{ schema_count }}</div><div class="lbl">Schemas</div></div>
    <div class="info-cell"><div class="val" id="stat-sessions">-</div><div class="lbl">Analyses</div></div>
  </div>

  <div class="card">
    <h2>Couverture des capacites</h2>
    <div id="coverageBar" class="coverage"></div>
  </div>

  <div class="card">
    <h2>Systeme</h2>
    <div class="info-grid">
      <div class="info-cell"><div class="val" style="font-size:0.9rem">Raspberry Pi 5</div><div class="lbl">Host</div></div>
      <div class="info-cell"><div class="val" style="font-size:0.9rem">{{ storage }}</div><div class="lbl">Stockage</div></div>
      <div class="info-cell"><div class="val" style="font-size:0.9rem">HAOS 17.2</div><div class="lbl">OS</div></div>
      <div class="info-cell"><div class="val" style="font-size:0.9rem">HA 2026.4.1</div><div class="lbl">Core</div></div>
    </div>
  </div>
</div>

<!-- ═══════════════ MCPs ═══════════════ -->
<div id="page-mcps" class="page">
  <div class="card">
    <h2>Serveurs MCP disponibles</h2>
    <p style="color:var(--text2);font-size:0.8rem;margin-bottom:14px;">
      Les MCPs sans authentification sont actifs automatiquement. Ajoutez des cles API dans l'onglet "Cles API" pour debloquer les MCPs externes.
    </p>
    <div id="mcpGrid" class="mcp-grid"></div>
  </div>
</div>

<!-- ═══════════════ API KEYS ═══════════════ -->
<div id="page-keys" class="page">
  <div class="card">
    <h2>Configuration des cles API</h2>
    <p style="color:var(--text2);font-size:0.8rem;margin-bottom:16px;">
      Ajoutez vos cles API pour debloquer les MCPs externes. Les cles sont stockees localement dans <code>/share/ha-mcp/api_keys.json</code>.
    </p>
    <div id="keysContainer"></div>
    <div id="keySaved" class="key-saved" style="margin-top:8px;">Cles sauvegardees</div>
  </div>

  <div class="card">
    <h2>Ajouter une cle personnalisee</h2>
    <div class="key-row">
      <div class="field">
        <label>Nom de la cle</label>
        <input type="text" id="customKeyName" placeholder="MA_CLE_API">
      </div>
      <div class="field">
        <label>Valeur</label>
        <input type="password" id="customKeyValue" placeholder="sk-...">
      </div>
      <div class="actions">
        <button class="btn-sm" onclick="addCustomKey()">Ajouter</button>
      </div>
    </div>
  </div>
</div>

<!-- ═══════════════ ANALYZE ═══════════════ -->
<div id="page-analyze" class="page">
  <div class="card">
    <h2>Analyser une candidature</h2>
    <form id="analyzeForm" enctype="multipart/form-data">
      <label>Offre d'emploi (PDF)</label>
      <input type="file" id="offerFile" name="offer_pdf" accept=".pdf" required>
      <label>CV du candidat (PDF)</label>
      <input type="file" id="cvFile" name="cv_pdf" accept=".pdf" required>
      <button type="submit" id="submitBtn" style="width:100%;margin-top:8px;">Lancer l'analyse</button>
    </form>
    <div id="analyzeStatus" class="status-bar"></div>
  </div>

  <div id="resultsCard" class="card" style="display:none;">
    <h2>Resultats</h2>
    <div class="info-grid" id="resultSummary"></div>
    <h3>Phases du pipeline</h3>
    <div id="phasesContainer"></div>
    <h3 style="margin-top:14px;">Donnees brutes</h3>
    <pre class="json" id="jsonOutput"></pre>
  </div>
</div>

<!-- ═══════════════ HISTORY ═══════════════ -->
<div id="page-history" class="page">
  <div class="card">
    <h2>Historique des analyses</h2>
    <div id="sessionsContainer"><p style="color:var(--text3);font-size:0.85rem;">Chargement...</p></div>
  </div>
  <div id="sessionDetail" class="card" style="display:none;">
    <h2>Detail de la session</h2>
    <pre class="json" id="sessionJson"></pre>
  </div>
</div>

<script>
const BASE = '{{ ingress }}';
const CATALOG = {{ catalog_json|safe }};
let API_KEYS = {{ keys_json|safe }};

// ── Navigation ──
document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('page-' + tab.dataset.page).classList.add('active');
    if (tab.dataset.page === 'history') loadSessions();
  });
});

// ── MCP status helper ──
function mcpStatus(mcp) {
  if (!mcp.requires_auth) return 'available';
  if (mcp.auth_key_name && API_KEYS[mcp.auth_key_name]) return 'unlocked';
  return 'excluded';
}

// ── Render MCPs ──
function renderMCPs() {
  const grid = document.getElementById('mcpGrid');
  let html = '';
  let active = 0, excluded = 0;
  CATALOG.forEach(mcp => {
    const st = mcpStatus(mcp);
    if (st !== 'excluded') active++; else excluded++;
    const badgeCls = st === 'available' ? 'badge-available' : st === 'unlocked' ? 'badge-unlocked' : 'badge-excluded';
    const badgeTxt = st === 'available' ? 'Actif' : st === 'unlocked' ? 'Debloque' : 'Cle requise';
    html += '<div class="mcp-card">';
    html += '<div class="mcp-header"><span class="mcp-name">' + mcp.name + '</span><span class="badge ' + badgeCls + '">' + badgeTxt + '</span></div>';
    html += '<div class="mcp-desc">' + mcp.description + '</div>';
    html += '<div class="mcp-tools">';
    mcp.tools.forEach(t => { html += '<span class="mcp-tool-tag">' + t.name + '</span>'; });
    html += '</div>';
    html += '<div class="mcp-footer"><span class="mcp-cat">' + mcp.category + '</span>';
    if (mcp.auth_key_name) html += '<span style="font-size:0.7rem;color:var(--text3)">' + mcp.auth_key_name + '</span>';
    html += '</div></div>';
  });
  grid.innerHTML = html;
  document.getElementById('stat-mcps-total').textContent = CATALOG.length;
  document.getElementById('stat-mcps-active').textContent = active;
  document.getElementById('stat-mcps-excluded').textContent = excluded;
  renderCoverage();
}

// ── Coverage ──
function renderCoverage() {
  const cats = ['ingestion','structuration','enrichissement','raisonnement','validation','generation'];
  const covered = {};
  cats.forEach(c => covered[c] = false);
  CATALOG.forEach(mcp => {
    if (mcpStatus(mcp) !== 'excluded') covered[mcp.category] = true;
  });
  const bar = document.getElementById('coverageBar');
  bar.innerHTML = cats.map(c =>
    '<span class="cov-item ' + (covered[c] ? 'cov-ok' : 'cov-miss') + '">' + c + '</span>'
  ).join('');
}

// ── Render API keys form ──
function renderKeys() {
  const container = document.getElementById('keysContainer');
  const authMcps = CATALOG.filter(m => m.requires_auth && m.auth_key_name);
  // Deduplicate by key name
  const seen = new Set();
  const keys = [];
  authMcps.forEach(m => {
    if (!seen.has(m.auth_key_name)) { seen.add(m.auth_key_name); keys.push(m); }
  });
  let html = '';
  keys.forEach(mcp => {
    const keyName = mcp.auth_key_name;
    const hasKey = !!API_KEYS[keyName];
    html += '<div class="key-row">';
    html += '<div class="field"><label>' + keyName + ' <span style="font-weight:400;text-transform:none;letter-spacing:0">(' + mcp.name + ')</span></label>';
    html += '<input type="password" id="key-' + keyName + '" value="' + (API_KEYS[keyName] || '') + '" placeholder="Entrez votre cle..."></div>';
    html += '<div class="actions">';
    html += '<button class="btn-sm' + (hasKey ? ' btn-success' : '') + '" onclick="saveKey(\'' + keyName + '\')">' + (hasKey ? 'Modifier' : 'Sauver') + '</button>';
    if (hasKey) html += '<button class="btn-sm btn-danger" onclick="deleteKey(\'' + keyName + '\')">Suppr</button>';
    html += '</div></div>';
  });

  // Show any custom keys not in catalog
  Object.keys(API_KEYS).forEach(k => {
    if (!seen.has(k)) {
      html += '<div class="key-row">';
      html += '<div class="field"><label>' + k + ' <span style="font-weight:400;text-transform:none;letter-spacing:0">(personnalisee)</span></label>';
      html += '<input type="password" id="key-' + k + '" value="' + API_KEYS[k] + '" placeholder="..."></div>';
      html += '<div class="actions">';
      html += '<button class="btn-sm btn-success" onclick="saveKey(\'' + k + '\')">Modifier</button>';
      html += '<button class="btn-sm btn-danger" onclick="deleteKey(\'' + k + '\')">Suppr</button>';
      html += '</div></div>';
    }
  });
  container.innerHTML = html || '<p style="color:var(--text3)">Aucun MCP authentifie dans le catalogue.</p>';
}

// ── Key actions ──
async function saveKey(keyName) {
  const val = document.getElementById('key-' + keyName).value.trim();
  if (!val) return;
  API_KEYS[keyName] = val;
  await fetch(BASE + '/api/keys', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(API_KEYS) });
  renderKeys(); renderMCPs();
  flash('keySaved');
}

async function deleteKey(keyName) {
  delete API_KEYS[keyName];
  await fetch(BASE + '/api/keys', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(API_KEYS) });
  renderKeys(); renderMCPs();
}

function addCustomKey() {
  const name = document.getElementById('customKeyName').value.trim().toUpperCase().replace(/[^A-Z0-9_]/g, '_');
  const val = document.getElementById('customKeyValue').value.trim();
  if (!name || !val) return;
  API_KEYS[name] = val;
  fetch(BASE + '/api/keys', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(API_KEYS) });
  document.getElementById('customKeyName').value = '';
  document.getElementById('customKeyValue').value = '';
  renderKeys(); renderMCPs();
  flash('keySaved');
}

function flash(id) {
  const el = document.getElementById(id);
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 2000);
}

// ── Analyze ──
document.getElementById('analyzeForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const status = document.getElementById('analyzeStatus');
  const resultsCard = document.getElementById('resultsCard');
  btn.disabled = true; btn.textContent = 'Analyse en cours...';
  status.className = 'status-bar running'; status.textContent = 'Pipeline en cours d\\'execution...';
  resultsCard.style.display = 'none';

  const formData = new FormData(e.target);
  try {
    const resp = await fetch(BASE + '/api/analyze', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) {
      status.className = 'status-bar error'; status.textContent = 'Erreur: ' + data.error;
    } else {
      status.className = 'status-bar success';
      status.textContent = 'Analyse terminee — Recommandation: ' + (data.recommendation || 'N/A');

      // Summary
      const sum = document.getElementById('resultSummary');
      sum.innerHTML = '<div class="info-cell"><div class="val">' + (data.recommendation || '-') + '</div><div class="lbl">Recommandation</div></div>'
        + '<div class="info-cell"><div class="val">' + (data.artifacts_count || 0) + '</div><div class="lbl">Artefacts</div></div>'
        + '<div class="info-cell"><div class="val">' + data.session_id.substring(0,8) + '...</div><div class="lbl">Session</div></div>';

      // Phases
      const phases = document.getElementById('phasesContainer');
      phases.innerHTML = '';
      for (const [name, info] of Object.entries(data.phases || {})) {
        const s = info.status || '?';
        const cls = s === 'completed' ? 'phase-ok' : s === 'failed' ? 'phase-fail' : 'phase-pending';
        phases.innerHTML += '<div class="phase-row"><span class="phase-name">' + name + '</span><span class="phase-status ' + cls + '">' + s.toUpperCase() + '</span></div>';
      }
      document.getElementById('jsonOutput').textContent = JSON.stringify(data, null, 2);
      resultsCard.style.display = 'block';
    }
  } catch (err) {
    status.className = 'status-bar error'; status.textContent = 'Erreur: ' + err.message;
  }
  btn.disabled = false; btn.textContent = 'Lancer l\\'analyse';
});

// ── Sessions ──
async function loadSessions() {
  try {
    const resp = await fetch(BASE + '/api/sessions');
    const data = await resp.json();
    const c = document.getElementById('sessionsContainer');
    document.getElementById('stat-sessions').textContent = (data.sessions || []).length;
    if (!data.sessions || data.sessions.length === 0) {
      c.innerHTML = '<p style="color:var(--text3)">Aucune analyse precedente.</p>';
      return;
    }
    c.innerHTML = data.sessions.map(s =>
      '<div class="session-row" onclick="loadSession(\'' + s.session_id + '\')">'
      + '<span class="session-id">' + s.session_id + '</span>'
      + '<span class="session-date">' + new Date(s.created_at).toLocaleString('fr-FR') + '</span>'
      + '</div>'
    ).join('');
  } catch(e) {
    document.getElementById('sessionsContainer').innerHTML = '<p style="color:var(--red)">Erreur de chargement.</p>';
  }
}

async function loadSession(sid) {
  try {
    const resp = await fetch(BASE + '/api/results/' + sid);
    const data = await resp.json();
    document.getElementById('sessionJson').textContent = JSON.stringify(data, null, 2);
    document.getElementById('sessionDetail').style.display = 'block';
  } catch(e) {
    document.getElementById('sessionJson').textContent = 'Erreur: ' + e.message;
    document.getElementById('sessionDetail').style.display = 'block';
  }
}

// ── Init ──
renderMCPs();
renderKeys();
loadSessions();
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the full dashboard."""
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()

    api_keys = _load_api_keys()

    return render_template_string(
        INDEX_HTML,
        ingress=INGRESS_ENTRY,
        storage=str(STORAGE_PATH),
        schema_count=len(registry.list_schemas()),
        catalog_json=json.dumps(MCP_CATALOG),
        keys_json=json.dumps(api_keys),
    )


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "addon": "ha-mcp",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/schemas")
def api_list_schemas():
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(SCHEMAS_DIR)
    registry.load()
    return jsonify({"schemas": registry.list_schemas()})


@app.route("/api/mcps")
def api_list_mcps():
    """List all MCPs with their current status."""
    api_keys = _load_api_keys()
    result = []
    for mcp in MCP_CATALOG:
        status = _get_mcp_status(mcp, api_keys)
        result.append({**mcp, "status": status})
    return jsonify({"mcps": result})


@app.route("/api/keys", methods=["GET"])
def api_get_keys():
    """Get stored API key names (not values)."""
    keys = _load_api_keys()
    return jsonify({"keys": {k: "***" + v[-4:] if len(v) > 4 else "****" for k, v in keys.items()}})


@app.route("/api/keys", methods=["POST"])
def api_save_keys():
    """Save API keys."""
    keys = request.get_json()
    if not isinstance(keys, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    _save_api_keys(keys)
    return jsonify({"status": "saved", "key_count": len(keys)})


@app.route("/api/keys/<key_name>", methods=["DELETE"])
def api_delete_key(key_name: str):
    """Delete a specific API key."""
    keys = _load_api_keys()
    if key_name in keys:
        del keys[key_name]
        _save_api_keys(keys)
    return jsonify({"status": "deleted"})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Run the full analysis pipeline on uploaded PDFs."""
    if "offer_pdf" not in request.files or "cv_pdf" not in request.files:
        return jsonify({"error": "Les deux fichiers PDF (offre et CV) sont requis"}), 400

    offer_file = request.files["offer_pdf"]
    cv_file = request.files["cv_pdf"]

    if not offer_file.filename or not cv_file.filename:
        return jsonify({"error": "Noms de fichiers vides"}), 400

    session_id = str(uuid.uuid4())
    upload_dir = STORAGE_PATH / "inputs" / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    offer_path = upload_dir / f"offer_{offer_file.filename}"
    cv_path = upload_dir / f"cv_{cv_file.filename}"
    offer_file.save(str(offer_path))
    cv_file.save(str(cv_path))

    logger.info("Files uploaded: %s, %s", offer_path, cv_path)

    try:
        orchestrator = MCPOrchestrator(project_root=Path("/"))
        orchestrator.initialize(session_id)

        # Build tool map with unlocked MCPs
        api_keys = _load_api_keys()
        mcp_tools = _build_active_mcp_tools(api_keys)
        orchestrator.discover_mcps(mcp_tools)

        engine = PipelineEngine(orchestrator, STORAGE_PATH, api_keys=api_keys)
        results = engine.run(str(offer_path), str(cv_path))

        gen_phase = results.get("phases", {}).get("generation", {})
        return jsonify({
            "session_id": session_id,
            "status": results.get("plan", {}).get("pipeline", {}).get("status", "unknown"),
            "phases": results.get("phases", {}),
            "recommendation": gen_phase.get("recommendation", "N/A"),
            "artifacts_count": gen_phase.get("artifacts_count", 0),
        })

    except Exception as e:
        logger.exception("Pipeline failed")
        return jsonify({"error": str(e), "session_id": session_id}), 500


@app.route("/api/results/<session_id>")
def get_results(session_id: str):
    result_path = STORAGE_PATH / "outputs" / f"result_{session_id}.json"
    if not result_path.exists():
        return jsonify({"error": f"Aucun resultat pour la session {session_id}"}), 404
    with open(result_path) as f:
        return jsonify(json.load(f))


@app.route("/api/sessions")
def list_sessions():
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
