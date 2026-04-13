"""Pipeline Engine: schema-driven execution engine that runs all 7 phases.

Phases 4 (structuration), 6 (analysis), and 7 (generation) call Claude API.
Phase 5 (model_building) includes web enrichment for company context.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..mcp_orchestrator import MCPOrchestrator
from ..schema_registry import SchemaRegistry, SchemaValidator
from .state import PipelineState
from .llm import structure_job_offer, structure_candidate_cv, analyze_candidacy, generate_report
from .enrichment import search_company_info

logger = logging.getLogger(__name__)

PhaseHandler = Callable[[dict[str, Any], "PipelineEngine"], dict[str, Any]]


class PipelineEngine:
    """Schema-driven pipeline engine with Claude API integration.

    Phases:
    1. Initialization — load schemas, validate environment
    2. Planning — map capabilities to MCPs
    3. Ingestion — extract raw text from PDFs
    4. Structuration — Claude structures raw text into job/candidate models
    5. Model Building — enrich with web search (company info) + finalize models
    6. Analysis — Claude compares candidate vs job
    7. Generation — Claude produces the final report
    """

    def __init__(
        self,
        orchestrator: MCPOrchestrator,
        storage_dir: Path | None = None,
        api_keys: dict[str, str] | None = None,
    ):
        self._orchestrator = orchestrator
        self._registry = orchestrator.get_registry()
        self._validator = orchestrator.get_validator()
        self._storage_dir = storage_dir or Path(__file__).resolve().parent.parent / "storage"
        self._state = PipelineState(self._storage_dir)
        self._plan: dict[str, Any] | None = None
        self._phase_handlers: dict[str, PhaseHandler] = {}
        self._api_keys = api_keys or {}
        self._register_default_handlers()

    def set_api_keys(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def register_phase_handler(self, phase_name: str, handler: PhaseHandler) -> None:
        self._phase_handlers[phase_name] = handler

    def run(self, offer_pdf_path: str, cv_pdf_path: str) -> dict[str, Any]:
        session_id = self._orchestrator.get_session_id()

        self._state.set("offer_pdf_path", offer_pdf_path)
        self._state.set("cv_pdf_path", cv_pdf_path)
        self._state.set("session_id", session_id)

        self._plan = self._orchestrator.create_plan()

        results: dict[str, Any] = {"session_id": session_id, "phases": {}}
        pipeline = self._plan["pipeline"]

        for phase_def in pipeline["phases"]:
            phase_name = phase_def["name"]
            phase_id = phase_def["phase_id"]

            logger.info("Starting phase: %s (%s)", phase_name, phase_id)
            pipeline["current_phase"] = phase_def["order"]
            phase_def["status"] = "running"
            phase_def["started_at"] = datetime.now(timezone.utc).isoformat()

            try:
                handler = self._phase_handlers.get(phase_name)
                if handler:
                    phase_result = handler(phase_def, self)
                else:
                    phase_result = {"status": "skipped", "reason": f"No handler for phase '{phase_name}'"}

                phase_def["status"] = "completed"
                phase_def["completed_at"] = datetime.now(timezone.utc).isoformat()
                results["phases"][phase_name] = phase_result
                self._state.store_intermediate(phase_name, "result", phase_result)

            except Exception as e:
                logger.error("Phase %s failed: %s", phase_name, e, exc_info=True)
                phase_def["status"] = "failed"
                phase_def["error"] = str(e)
                results["phases"][phase_name] = {"status": "failed", "error": str(e)}
                break

        failed = any(p["status"] == "failed" for p in pipeline["phases"])
        pipeline["status"] = "failed" if failed else "completed"
        pipeline["completed_at"] = datetime.now(timezone.utc).isoformat()

        results["plan"] = self._plan
        results["trace"] = self._orchestrator.get_trace()

        self._state.store_output(f"result_{session_id}", results)
        self._state.store_log(session_id)

        return results

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def registry(self) -> SchemaRegistry:
        return self._registry

    @property
    def validator(self) -> SchemaValidator:
        return self._validator

    @property
    def api_keys(self) -> dict[str, str]:
        return self._api_keys

    def create_meta(self, schema_name: str, mcp_sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "session_id": self._orchestrator.get_session_id(),
            "object_id": str(uuid.uuid4()),
            "schema_version": self._registry.get_schema_version(schema_name),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mcp_sources": mcp_sources or [],
            "validation_status": "pending",
            "confidence": 0.0,
            "lineage": [],
        }

    def _register_default_handlers(self) -> None:
        self._phase_handlers["initialization"] = _handle_initialization
        self._phase_handlers["planning"] = _handle_planning
        self._phase_handlers["ingestion"] = _handle_ingestion
        self._phase_handlers["structuration"] = _handle_structuration
        self._phase_handlers["model_building"] = _handle_model_building
        self._phase_handlers["analysis"] = _handle_analysis
        self._phase_handlers["generation"] = _handle_generation


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_initialization(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 1: Initialize session, load schemas, validate environment."""
    schemas = engine.registry.list_schemas()
    has_claude = bool(engine.api_keys.get("ANTHROPIC_API_KEY"))
    return {
        "status": "completed",
        "schemas_loaded": schemas,
        "session_id": engine.state.get("session_id"),
        "claude_api_available": has_claude,
    }


def _handle_planning(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 2: Plan execution based on capabilities."""
    cap_map = engine._orchestrator.get_capability_map()
    coverage = cap_map.get_coverage() if cap_map else {}
    return {
        "status": "completed",
        "capability_coverage": coverage,
        "phase_count": len(phase_def.get("assigned_mcps", [])),
    }


def _handle_ingestion(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 3: Read PDFs and extract raw text."""
    offer_path = engine.state.get("offer_pdf_path", "")
    cv_path = engine.state.get("cv_pdf_path", "")
    now = datetime.now(timezone.utc).isoformat()

    offer_data = _read_pdf(offer_path)
    cv_data = _read_pdf(cv_path)

    input_obj = {
        "meta": engine.create_meta("input"),
        "inputs": {
            "offer_pdf": {
                "file_path": offer_path,
                "file_name": Path(offer_path).name if offer_path else "",
                "mime_type": "application/pdf",
                "loaded_at": now,
            },
            "cv_pdf": {
                "file_path": cv_path,
                "file_name": Path(cv_path).name if cv_path else "",
                "mime_type": "application/pdf",
                "loaded_at": now,
            },
        },
    }

    offer_extraction = {
        "meta": engine.create_meta("extraction"),
        "extraction": {
            "source_input_id": input_obj["meta"]["object_id"],
            "document_type": "offer",
            **offer_data,
        },
    }

    cv_extraction = {
        "meta": engine.create_meta("extraction"),
        "extraction": {
            "source_input_id": input_obj["meta"]["object_id"],
            "document_type": "cv",
            **cv_data,
        },
    }

    input_validation = engine.validator.validate(input_obj, "input")
    input_obj["meta"]["validation_status"] = "valid" if input_validation.valid else "partial"

    engine.state.set("input_object", input_obj)
    engine.state.set("offer_extraction", offer_extraction)
    engine.state.set("cv_extraction", cv_extraction)

    return {
        "status": "completed",
        "offer_pages": offer_data.get("total_pages", 0),
        "cv_pages": cv_data.get("total_pages", 0),
        "offer_chars": offer_data.get("total_chars", 0),
        "cv_chars": cv_data.get("total_chars", 0),
        "errors": [e for e in [offer_data.get("error"), cv_data.get("error")] if e],
    }


def _handle_structuration(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 4: Claude structures raw text into job and candidate models."""
    offer_extraction = engine.state.get("offer_extraction", {})
    cv_extraction = engine.state.get("cv_extraction", {})

    offer_text = offer_extraction.get("extraction", {}).get("raw_text", "")
    cv_text = cv_extraction.get("extraction", {}).get("raw_text", "")

    if not offer_text or not cv_text:
        return {"status": "failed", "error": "Texte PDF vide — verifiez les fichiers"}

    job_schema = engine.registry.get_schema("job")
    candidate_schema = engine.registry.get_schema("candidate")

    # Call Claude to structure both documents
    mcp_source = {
        "mcp_id": "anthropic_claude",
        "capability": "structuration",
        "invoked_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
    }

    logger.info("Structuring job offer with Claude (%d chars)...", len(offer_text))
    job_data = structure_job_offer(engine.api_keys, offer_text, job_schema)

    logger.info("Structuring CV with Claude (%d chars)...", len(cv_text))
    candidate_data = structure_candidate_cv(engine.api_keys, cv_text, candidate_schema)

    # Build schema-compliant model objects
    job_model = {
        "meta": engine.create_meta("job", [mcp_source]),
        "job": job_data,
    }

    candidate_model = {
        "meta": engine.create_meta("candidate", [mcp_source]),
        "candidate": candidate_data,
    }

    # Validate
    job_valid = engine.validator.validate(job_model, "job")
    candidate_valid = engine.validator.validate(candidate_model, "candidate")

    job_model["meta"]["validation_status"] = "valid" if job_valid.valid else "partial"
    job_model["meta"]["confidence"] = 0.85
    candidate_model["meta"]["validation_status"] = "valid" if candidate_valid.valid else "partial"
    candidate_model["meta"]["confidence"] = 0.85

    engine.state.set("job_model", job_model)
    engine.state.set("candidate_model", candidate_model)

    return {
        "status": "completed",
        "job_title": job_data.get("title", "?"),
        "candidate_name": candidate_data.get("identity", {}).get("name", "?"),
        "job_skills_count": len(job_data.get("requirements", {}).get("required_skills", [])),
        "candidate_skills_count": len(candidate_data.get("skills", [])),
        "job_model_valid": job_valid.valid,
        "candidate_model_valid": candidate_valid.valid,
        "powered_by": "claude",
    }


def _handle_model_building(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 5: Enrich models with web search (company info)."""
    job_model = engine.state.get("job_model", {})
    job_data = job_model.get("job", {})

    company_name = job_data.get("company", {}).get("name", "")
    job_title = job_data.get("title", "")

    # Web enrichment for company context
    company_info = {}
    if company_name:
        logger.info("Enriching company info: %s", company_name)
        company_info = search_company_info(company_name, job_title)
        engine.state.set("company_info", company_info)

        enrichment_summary = {
            "company_name": company_name,
            "results_found": company_info.get("result_count", 0),
            "source": company_info.get("source", "none"),
            "types": list(set(r.get("type", "") for r in company_info.get("results", []))),
        }
    else:
        enrichment_summary = {"company_name": "", "results_found": 0, "source": "none", "reason": "no_company_name_extracted"}
        engine.state.set("company_info", {})

    return {
        "status": "completed",
        "enrichment": enrichment_summary,
        "models_ready": ["job", "candidate"],
    }


def _handle_analysis(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 6: Claude analyzes candidate-job alignment."""
    job_model = engine.state.get("job_model", {})
    candidate_model = engine.state.get("candidate_model", {})
    company_info = engine.state.get("company_info", {})
    analysis_schema = engine.registry.get_schema("analysis")

    job_data = job_model.get("job", {})
    candidate_data = candidate_model.get("candidate", {})

    mcp_source = {
        "mcp_id": "anthropic_claude",
        "capability": "raisonnement",
        "invoked_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
    }

    logger.info("Analyzing candidacy with Claude...")
    analysis_data = analyze_candidacy(
        engine.api_keys, job_data, candidate_data, company_info, analysis_schema
    )

    # Ensure required fields
    analysis_data.setdefault("job_id", job_model.get("meta", {}).get("object_id", ""))
    analysis_data.setdefault("candidate_id", candidate_model.get("meta", {}).get("object_id", ""))
    analysis_data.setdefault("alignments", [])
    analysis_data.setdefault("gaps", [])
    analysis_data.setdefault("signals", [])
    analysis_data.setdefault("uncertainties", [])
    analysis_data.setdefault("overall_score", 0.0)
    analysis_data.setdefault("recommendation", "partial_match")

    analysis_obj = {
        "meta": engine.create_meta("analysis", [mcp_source]),
        "analysis": analysis_data,
    }

    analysis_valid = engine.validator.validate(analysis_obj, "analysis")
    analysis_obj["meta"]["validation_status"] = "valid" if analysis_valid.valid else "partial"
    analysis_obj["meta"]["confidence"] = 0.9

    engine.state.set("analysis_object", analysis_obj)

    return {
        "status": "completed",
        "overall_score": analysis_data.get("overall_score", 0),
        "recommendation": analysis_data.get("recommendation", "?"),
        "alignments_count": len(analysis_data.get("alignments", [])),
        "gaps_count": len(analysis_data.get("gaps", [])),
        "signals_count": len(analysis_data.get("signals", [])),
        "uncertainties_count": len(analysis_data.get("uncertainties", [])),
        "analysis_valid": analysis_valid.valid,
        "powered_by": "claude",
    }


def _handle_generation(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 7: Claude generates the final report."""
    analysis_obj = engine.state.get("analysis_object", {})
    job_model = engine.state.get("job_model", {})
    candidate_model = engine.state.get("candidate_model", {})
    company_info = engine.state.get("company_info", {})

    job_data = job_model.get("job", {})
    candidate_data = candidate_model.get("candidate", {})
    analysis_data = analysis_obj.get("analysis", {})

    mcp_source = {
        "mcp_id": "anthropic_claude",
        "capability": "generation",
        "invoked_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
    }

    # Generate markdown report via Claude
    logger.info("Generating report with Claude...")
    report_markdown = generate_report(
        engine.api_keys, job_data, candidate_data, analysis_data, company_info
    )

    now = datetime.now(timezone.utc).isoformat()

    # Build JSON summary artifact
    json_summary = json.dumps({
        "job": job_data,
        "candidate": candidate_data,
        "analysis": analysis_data,
        "company_info": {
            "name": company_info.get("company_name", ""),
            "results_count": company_info.get("result_count", 0),
        },
    }, ensure_ascii=False)

    generation_obj = {
        "meta": engine.create_meta("generation", [mcp_source]),
        "generation": {
            "analysis_id": analysis_obj.get("meta", {}).get("object_id", ""),
            "artifacts": [
                {
                    "artifact_id": str(uuid.uuid4()),
                    "type": "detailed_report",
                    "format": "markdown",
                    "title": "Rapport d'analyse de candidature",
                    "content": report_markdown if isinstance(report_markdown, str) else str(report_markdown),
                    "generated_at": now,
                },
                {
                    "artifact_id": str(uuid.uuid4()),
                    "type": "summary_report",
                    "format": "json",
                    "title": "Donnees structurees de l'analyse",
                    "content": json_summary,
                    "generated_at": now,
                },
            ],
            "summary": {
                "candidate_name": candidate_data.get("identity", {}).get("name", "Inconnu"),
                "job_title": job_data.get("title", "Inconnu"),
                "overall_score": analysis_data.get("overall_score", 0.0),
                "recommendation": analysis_data.get("recommendation", "partial_match"),
                "key_strengths": [
                    s.get("description", "")
                    for s in analysis_data.get("signals", [])
                    if s.get("type") == "strength"
                ][:5],
                "key_gaps": [
                    g.get("requirement", "")
                    for g in analysis_data.get("gaps", [])
                    if g.get("severity") in ("critical", "significant")
                ][:5],
                "key_uncertainties": [
                    u.get("area", "")
                    for u in analysis_data.get("uncertainties", [])
                ][:5],
                "next_steps": _derive_next_steps(analysis_data),
            },
        },
    }

    engine.state.set("generation_object", generation_obj)

    gen_valid = engine.validator.validate(generation_obj, "generation")
    generation_obj["meta"]["validation_status"] = "valid" if gen_valid.valid else "partial"
    generation_obj["meta"]["confidence"] = 0.9

    # Store final outputs
    engine.state.store_output(
        f"generation_{engine.state.get('session_id')}",
        generation_obj,
    )

    # Also store the markdown report separately for easy access
    report_path = engine._storage_dir / "outputs"
    report_path.mkdir(parents=True, exist_ok=True)
    report_file = report_path / f"report_{engine.state.get('session_id')}.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_markdown if isinstance(report_markdown, str) else str(report_markdown))

    return {
        "status": "completed",
        "artifacts_count": len(generation_obj["generation"]["artifacts"]),
        "generation_valid": gen_valid.valid,
        "recommendation": generation_obj["generation"]["summary"]["recommendation"],
        "overall_score": generation_obj["generation"]["summary"]["overall_score"],
        "candidate_name": generation_obj["generation"]["summary"]["candidate_name"],
        "job_title": generation_obj["generation"]["summary"]["job_title"],
        "report_file": str(report_file),
        "powered_by": "claude",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _read_pdf(file_path: str) -> dict[str, Any]:
    """Extract text from PDF using available methods."""
    path = Path(file_path)
    if not path.exists():
        return {"raw_text": "", "pages": [], "total_pages": 0, "error": f"File not found: {file_path}"}

    raw_text = ""
    pages = []

    try:
        import fitz
        doc = fitz.open(file_path)
        for i, page in enumerate(doc):
            page_text = page.get_text()
            pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
            raw_text += page_text + "\n"
        doc.close()
    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_text = page.extract_text() or ""
                    pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
                    raw_text += page_text + "\n"
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(file_path)
                for i, page in enumerate(reader.pages):
                    page_text = page.extract_text() or ""
                    pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
                    raw_text += page_text + "\n"
            except ImportError:
                return {
                    "raw_text": "", "pages": [], "total_pages": 0,
                    "error": "Aucune lib PDF disponible. Installez PyMuPDF, pdfplumber ou PyPDF2.",
                }

    with open(file_path, "rb") as f:
        checksum = hashlib.sha256(f.read()).hexdigest()

    return {
        "raw_text": raw_text.strip(),
        "pages": pages,
        "total_pages": len(pages),
        "total_chars": len(raw_text),
        "checksum_sha256": checksum,
    }


def _derive_next_steps(analysis_data: dict[str, Any]) -> list[str]:
    """Derive actionable next steps from the analysis."""
    rec = analysis_data.get("recommendation", "")
    steps = []

    if rec in ("strong_match", "good_match"):
        steps.append("Planifier un entretien avec le candidat")
        steps.append("Preparer les questions basees sur les incertitudes identifiees")
    elif rec == "partial_match":
        steps.append("Evaluer si les ecarts sont compensables")
        steps.append("Entretien exploratoire recommande")
    else:
        steps.append("Candidature peu alignee — archiver ou rediriger")

    critical_gaps = [g for g in analysis_data.get("gaps", []) if g.get("severity") == "critical"]
    if critical_gaps:
        steps.append(f"Verifier {len(critical_gaps)} ecart(s) critique(s) avant decision")

    uncertainties = analysis_data.get("uncertainties", [])
    if uncertainties:
        steps.append(f"Clarifier {len(uncertainties)} point(s) incertain(s)")

    return steps
