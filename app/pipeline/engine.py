"""Pipeline Engine: schema-driven execution engine that runs all 7 phases."""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..mcp_orchestrator import MCPOrchestrator
from ..schema_registry import SchemaRegistry, SchemaValidator
from .state import PipelineState

logger = logging.getLogger(__name__)

# Type for phase handler functions
PhaseHandler = Callable[[dict[str, Any], "PipelineEngine"], dict[str, Any]]


class PipelineEngine:
    """Schema-driven pipeline engine.

    Executes the 7 pipeline phases defined in the schema registry:
    1. Initialization
    2. Planning
    3. Ingestion
    4. Structuration
    5. Model Building
    6. Analysis
    7. Generation

    Each phase validates inputs/outputs against schemas and stores intermediates.
    """

    def __init__(self, orchestrator: MCPOrchestrator, storage_dir: Path | None = None):
        self._orchestrator = orchestrator
        self._registry = orchestrator.get_registry()
        self._validator = orchestrator.get_validator()
        self._storage_dir = storage_dir or Path(__file__).resolve().parent.parent / "storage"
        self._state = PipelineState(self._storage_dir)
        self._plan: dict[str, Any] | None = None
        self._phase_handlers: dict[str, PhaseHandler] = {}
        self._register_default_handlers()

    def register_phase_handler(self, phase_name: str, handler: PhaseHandler) -> None:
        """Register a custom handler for a pipeline phase."""
        self._phase_handlers[phase_name] = handler

    def run(self, offer_pdf_path: str, cv_pdf_path: str) -> dict[str, Any]:
        """Run the complete pipeline on the given PDF files."""
        session_id = self._orchestrator.get_session_id()

        # Store input paths
        self._state.set("offer_pdf_path", offer_pdf_path)
        self._state.set("cv_pdf_path", cv_pdf_path)
        self._state.set("session_id", session_id)

        # Get the plan
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

                # Store intermediate
                self._state.store_intermediate(phase_name, "result", phase_result)

            except Exception as e:
                logger.error("Phase %s failed: %s", phase_name, e)
                phase_def["status"] = "failed"
                phase_def["error"] = str(e)
                results["phases"][phase_name] = {"status": "failed", "error": str(e)}
                break

        # Determine final status
        failed = any(p["status"] == "failed" for p in pipeline["phases"])
        pipeline["status"] = "failed" if failed else "completed"
        pipeline["completed_at"] = datetime.now(timezone.utc).isoformat()

        results["plan"] = self._plan
        results["trace"] = self._orchestrator.get_trace()

        # Store final outputs
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

    def create_meta(self, schema_name: str, mcp_sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Create a metadata block conforming to the meta-schema."""
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
        """Register the default phase handlers."""
        self._phase_handlers["initialization"] = _handle_initialization
        self._phase_handlers["planning"] = _handle_planning
        self._phase_handlers["ingestion"] = _handle_ingestion
        self._phase_handlers["structuration"] = _handle_structuration
        self._phase_handlers["model_building"] = _handle_model_building
        self._phase_handlers["analysis"] = _handle_analysis
        self._phase_handlers["generation"] = _handle_generation


# ─── Default Phase Handlers ───────────────────────────────────────────────────

def _handle_initialization(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 1: Initialize session, load schemas, validate environment."""
    schemas = engine.registry.list_schemas()
    return {
        "status": "completed",
        "schemas_loaded": schemas,
        "session_id": engine.state.get("session_id"),
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

    def _read_pdf(file_path: str) -> dict[str, Any]:
        """Extract text from PDF using available methods."""
        path = Path(file_path)
        if not path.exists():
            return {"raw_text": "", "pages": [], "total_pages": 0, "error": f"File not found: {file_path}"}

        raw_text = ""
        pages = []

        # Try PyMuPDF (fitz) first
        try:
            import fitz
            doc = fitz.open(file_path)
            for i, page in enumerate(doc):
                page_text = page.get_text()
                pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
                raw_text += page_text + "\n"
            doc.close()
        except ImportError:
            # Fallback: try pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(file_path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        page_text = page.extract_text() or ""
                        pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
                        raw_text += page_text + "\n"
            except ImportError:
                # Fallback: try PyPDF2
                try:
                    from PyPDF2 import PdfReader
                    reader = PdfReader(file_path)
                    for i, page in enumerate(reader.pages):
                        page_text = page.extract_text() or ""
                        pages.append({"page_number": i + 1, "text": page_text, "char_count": len(page_text)})
                        raw_text += page_text + "\n"
                except ImportError:
                    return {
                        "raw_text": "",
                        "pages": [],
                        "total_pages": 0,
                        "error": "No PDF library available. Install PyMuPDF, pdfplumber, or PyPDF2.",
                    }

        # Compute checksum
        with open(file_path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        return {
            "raw_text": raw_text.strip(),
            "pages": pages,
            "total_pages": len(pages),
            "total_chars": len(raw_text),
            "checksum_sha256": checksum,
        }

    offer_data = _read_pdf(offer_path)
    cv_data = _read_pdf(cv_path)

    # Build input object
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

    # Build extraction objects
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

    # Validate
    input_validation = engine.validator.validate(input_obj, "input")
    input_obj["meta"]["validation_status"] = "valid" if input_validation.valid else "partial"

    # Store
    engine.state.set("input_object", input_obj)
    engine.state.set("offer_extraction", offer_extraction)
    engine.state.set("cv_extraction", cv_extraction)

    return {
        "status": "completed",
        "offer_pages": offer_data.get("total_pages", 0),
        "cv_pages": cv_data.get("total_pages", 0),
        "offer_chars": offer_data.get("total_chars", 0),
        "cv_chars": cv_data.get("total_chars", 0),
        "errors": [
            e for e in [offer_data.get("error"), cv_data.get("error")] if e
        ],
    }


def _handle_structuration(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 4: Transform raw extractions into structured data.

    This phase prepares structured prompts for LLM-based structuration.
    The actual LLM call is delegated to the orchestrator or external MCP.
    """
    offer_extraction = engine.state.get("offer_extraction", {})
    cv_extraction = engine.state.get("cv_extraction", {})

    # Get target schemas to guide structuration
    job_schema = engine.registry.get_schema("job")
    candidate_schema = engine.registry.get_schema("candidate")

    # Prepare structuration requests (to be processed by LLM/MCP)
    offer_request = {
        "task": "structure_job_offer",
        "source_text": offer_extraction.get("extraction", {}).get("raw_text", ""),
        "target_schema": job_schema,
        "instructions": "Extract and structure the job offer according to the provided schema.",
    }

    cv_request = {
        "task": "structure_candidate_cv",
        "source_text": cv_extraction.get("extraction", {}).get("raw_text", ""),
        "target_schema": candidate_schema,
        "instructions": "Extract and structure the candidate CV according to the provided schema.",
    }

    engine.state.set("structuration_offer_request", offer_request)
    engine.state.set("structuration_cv_request", cv_request)

    return {
        "status": "completed",
        "offer_text_length": len(offer_request["source_text"]),
        "cv_text_length": len(cv_request["source_text"]),
        "target_schemas": ["job", "candidate"],
    }


def _handle_model_building(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 5: Build structured models (job, candidate, signals, uncertainties).

    Creates schema-compliant model objects. In a full MCP flow, this is where
    the LLM/reasoning MCP produces the structured output.
    """
    # Create placeholder models conforming to schemas
    job_model = {
        "meta": engine.create_meta("job"),
        "job": {
            "title": "",
            "requirements": {
                "required_skills": [],
                "required_education": [],
                "required_languages": [],
            },
            "responsibilities": [],
            "raw_sections": {},
        },
    }

    candidate_model = {
        "meta": engine.create_meta("candidate"),
        "candidate": {
            "skills": [],
            "experience": [],
            "education": [],
            "languages": [],
        },
    }

    engine.state.set("job_model", job_model)
    engine.state.set("candidate_model", candidate_model)

    # Validate models
    job_valid = engine.validator.validate(job_model, "job")
    candidate_valid = engine.validator.validate(candidate_model, "candidate")

    return {
        "status": "completed",
        "job_model_valid": job_valid.valid,
        "candidate_model_valid": candidate_valid.valid,
        "models_built": ["job", "candidate"],
    }


def _handle_analysis(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 6: Analyze alignments, gaps, signals, uncertainties.

    Prepares the analysis request for the reasoning MCP/LLM.
    """
    job_model = engine.state.get("job_model", {})
    candidate_model = engine.state.get("candidate_model", {})
    analysis_schema = engine.registry.get_schema("analysis")

    analysis_request = {
        "task": "analyze_candidacy",
        "job_model": job_model,
        "candidate_model": candidate_model,
        "target_schema": analysis_schema,
        "instructions": (
            "Analyze the alignment between the candidate and the job offer. "
            "Identify: alignments, gaps, signals (strengths/risks/opportunities), "
            "uncertainties, and priorities. Produce a recommendation."
        ),
    }

    # Create analysis placeholder
    analysis_obj = {
        "meta": engine.create_meta("analysis"),
        "analysis": {
            "job_id": job_model.get("meta", {}).get("object_id", ""),
            "candidate_id": candidate_model.get("meta", {}).get("object_id", ""),
            "alignments": [],
            "gaps": [],
            "signals": [],
            "uncertainties": [],
            "overall_score": 0.0,
            "recommendation": "partial_match",
            "priorities": [],
        },
    }

    engine.state.set("analysis_request", analysis_request)
    engine.state.set("analysis_object", analysis_obj)

    analysis_valid = engine.validator.validate(analysis_obj, "analysis")

    return {
        "status": "completed",
        "analysis_valid": analysis_valid.valid,
        "analysis_request_prepared": True,
    }


def _handle_generation(phase_def: dict[str, Any], engine: PipelineEngine) -> dict[str, Any]:
    """Phase 7: Generate final output artifacts."""
    analysis_obj = engine.state.get("analysis_object", {})
    job_model = engine.state.get("job_model", {})
    candidate_model = engine.state.get("candidate_model", {})

    now = datetime.now(timezone.utc).isoformat()

    generation_obj = {
        "meta": engine.create_meta("generation"),
        "generation": {
            "analysis_id": analysis_obj.get("meta", {}).get("object_id", ""),
            "artifacts": [
                {
                    "artifact_id": str(uuid.uuid4()),
                    "type": "summary_report",
                    "format": "json",
                    "title": "Candidacy Analysis Summary",
                    "content": json.dumps({
                        "job": job_model.get("job", {}),
                        "candidate": candidate_model.get("candidate", {}),
                        "analysis": analysis_obj.get("analysis", {}),
                    }, ensure_ascii=False),
                    "generated_at": now,
                }
            ],
            "summary": {
                "candidate_name": candidate_model.get("candidate", {}).get("identity", {}).get("name", "Unknown"),
                "job_title": job_model.get("job", {}).get("title", "Unknown"),
                "overall_score": analysis_obj.get("analysis", {}).get("overall_score", 0.0),
                "recommendation": analysis_obj.get("analysis", {}).get("recommendation", "partial_match"),
                "key_strengths": [],
                "key_gaps": [],
                "key_uncertainties": [],
                "next_steps": ["Review detailed analysis", "Schedule interview if aligned"],
            },
        },
    }

    engine.state.set("generation_object", generation_obj)

    gen_valid = engine.validator.validate(generation_obj, "generation")
    generation_obj["meta"]["validation_status"] = "valid" if gen_valid.valid else "partial"

    # Store final output
    engine.state.store_output(
        f"generation_{engine.state.get('session_id')}",
        generation_obj,
    )

    return {
        "status": "completed",
        "artifacts_count": len(generation_obj["generation"]["artifacts"]),
        "generation_valid": gen_valid.valid,
        "recommendation": generation_obj["generation"]["summary"]["recommendation"],
    }


# Make json available in handler scope
import json
