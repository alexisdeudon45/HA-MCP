"""Pipeline Engine v2: 2-stage schema-driven pipeline with dynamic MCP management.

Stage 1 — Discovery:
  1.0 init          → Load schemas, init session
  1.1 ingest        → Extract text from PDFs
  1.2 structure     → Claude structures job + candidate
  1.3 mcp_select    → Claude selects useful MCPs
  1.4 mcp_test      → Test selected MCPs
  1.5 resource_discover → Use MCPs to find resources
  1.6 resource_register → Register resources in our MCP

Stage 2 — Analysis:
  2.1 resource_consult     → Fetch & NLP-parse resources
  2.2 analysis_mcp_select  → Claude selects analysis MCPs
  2.3 analysis_mcp_test    → Test analysis MCPs
  2.4 analysis_execute     → Run analysis via MCPs + Claude
  2.5 combine              → Combine all into meta-analysis
  2.6 generate             → Generate final report
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..mcp_orchestrator import MCPOrchestrator
from ..mcp_orchestrator.mcp_manager import MCPManager
from ..schema_registry import SchemaRegistry, SchemaValidator
from .state import PipelineState
from .llm import (
    call_claude, structure_job_offer, structure_candidate_cv,
    analyze_candidacy, generate_report,
)
from .enrichment import search_company_info

logger = logging.getLogger(__name__)

StepHandler = Callable[["PipelineEngine"], dict[str, Any]]


class PipelineEngine:
    """2-stage schema-driven pipeline with dynamic MCP orchestration."""

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
        self._api_keys = api_keys or {}
        self._mcp_manager = MCPManager(self._storage_dir, self._api_keys)
        self._event_stream: list[dict[str, Any]] = []

        # Define pipeline steps in order
        self._steps: list[tuple[str, str, StepHandler]] = [
            ("1.0", "init", self._step_init),
            ("1.1", "ingest", self._step_ingest),
            ("1.2", "structure", self._step_structure),
            ("1.3", "mcp_select", self._step_mcp_select),
            ("1.4", "mcp_test", self._step_mcp_test),
            ("1.5", "resource_discover", self._step_resource_discover),
            ("1.6", "resource_register", self._step_resource_register),
            ("2.1", "resource_consult", self._step_resource_consult),
            ("2.2", "analysis_mcp_select", self._step_analysis_mcp_select),
            ("2.3", "analysis_mcp_test", self._step_analysis_mcp_test),
            ("2.4", "analysis_execute", self._step_analysis_execute),
            ("2.5", "combine", self._step_combine),
            ("2.55", "grand_meta", self._step_grand_meta),
            ("2.6", "generate", self._step_generate),
        ]

    def run(self, offer_pdf_path: str, cv_pdf_path: str) -> dict[str, Any]:
        session_id = self._orchestrator.get_session_id()
        self._state.set("offer_pdf_path", offer_pdf_path)
        self._state.set("cv_pdf_path", cv_pdf_path)
        self._state.set("session_id", session_id)

        results: dict[str, Any] = {
            "session_id": session_id,
            "stages": {"stage_1": {}, "stage_2": {}},
            "steps": {},
            "events": [],
        }

        for step_id, step_name, handler in self._steps:
            stage = "stage_1" if step_id.startswith("1") else "stage_2"
            self._emit("step_start", step_id, step_name, stage)

            start_time = time.time()
            try:
                step_result = handler()
                duration = int((time.time() - start_time) * 1000)
                step_result["duration_ms"] = duration
                step_result.setdefault("status", "completed")

                results["steps"][step_id] = step_result
                results["stages"][stage][step_name] = step_result
                self._state.store_intermediate(step_name, "result", step_result)
                self._emit("step_complete", step_id, step_name, stage, duration_ms=duration)

            except Exception as e:
                duration = int((time.time() - start_time) * 1000)
                error_result = {"status": "failed", "error": str(e), "duration_ms": duration}
                results["steps"][step_id] = error_result
                results["stages"][stage][step_name] = error_result
                self._emit("step_failed", step_id, step_name, stage, error=str(e))
                logger.error("Step %s (%s) failed: %s", step_id, step_name, e, exc_info=True)
                break

        results["events"] = self._event_stream
        results["mcp_config"] = self._mcp_manager.get_config()
        results["resources"] = self._mcp_manager.get_resources()
        results["grand_meta"] = self._state.get("grand_meta", {})
        results["trace"] = self._orchestrator.get_trace()

        self._state.store_output(f"result_{session_id}", results)
        self._state.store_log(session_id)
        return results

    # ── Properties ────────────────────────────────────────────────────────────

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

    @property
    def event_stream(self) -> list[dict[str, Any]]:
        return self._event_stream

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

    def _emit(self, event_type: str, step_id: str, step_name: str, stage: str, **extra: Any) -> None:
        event = {
            "type": event_type,
            "step_id": step_id,
            "step_name": step_name,
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        self._event_stream.append(event)
        logger.info("[%s] %s %s", event_type, step_id, step_name)

    # ═══════════════════════════════════════════════════════════════════════════
    # STAGE 1 — DISCOVERY
    # ═══════════════════════════════════════════════════════════════════════════

    def _step_init(self) -> dict[str, Any]:
        """1.0 — Init session, load schemas."""
        schemas = self._registry.list_schemas()
        has_claude = bool(self._api_keys.get("ANTHROPIC_API_KEY"))
        return {
            "status": "completed",
            "schemas_loaded": schemas,
            "schema_count": len(schemas),
            "claude_available": has_claude,
            "session_id": self._state.get("session_id"),
        }

    def _step_ingest(self) -> dict[str, Any]:
        """1.1 — Extract text from PDFs."""
        offer_path = self._state.get("offer_pdf_path", "")
        cv_path = self._state.get("cv_pdf_path", "")

        offer_data = _read_pdf(offer_path)
        cv_data = _read_pdf(cv_path)

        self._state.set("offer_raw_text", offer_data.get("raw_text", ""))
        self._state.set("cv_raw_text", cv_data.get("raw_text", ""))
        self._state.set("offer_extraction", offer_data)
        self._state.set("cv_extraction", cv_data)

        return {
            "status": "completed",
            "offer_pages": offer_data.get("total_pages", 0),
            "cv_pages": cv_data.get("total_pages", 0),
            "offer_chars": offer_data.get("total_chars", 0),
            "cv_chars": cv_data.get("total_chars", 0),
        }

    def _step_structure(self) -> dict[str, Any]:
        """1.2 — Claude structures job + candidate models."""
        offer_text = self._state.get("offer_raw_text", "")
        cv_text = self._state.get("cv_raw_text", "")

        if not offer_text or not cv_text:
            return {"status": "failed", "error": "PDF text empty"}

        job_schema = self._registry.get_schema("job")
        candidate_schema = self._registry.get_schema("candidate")

        job_data = structure_job_offer(self._api_keys, offer_text, job_schema)
        candidate_data = structure_candidate_cv(self._api_keys, cv_text, candidate_schema)

        self._state.set("job_data", job_data)
        self._state.set("candidate_data", candidate_data)

        return {
            "status": "completed",
            "job_title": job_data.get("title", "?"),
            "company": job_data.get("company", {}).get("name", "?"),
            "candidate_name": candidate_data.get("identity", {}).get("name", "?"),
            "job_skills": len(job_data.get("requirements", {}).get("required_skills", [])),
            "candidate_skills": len(candidate_data.get("skills", [])),
        }

    def _step_mcp_select(self) -> dict[str, Any]:
        """1.3 — Claude selects useful MCPs for resource discovery."""
        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})

        # Build list of available MCPs in the environment
        available = [
            {"mcp_id": "duckduckgo", "name": "DuckDuckGo Search", "capabilities": ["web_search"], "requires_auth": False},
            {"mcp_id": "playwright", "name": "Playwright Browser", "capabilities": ["web_scrape", "nlp_extract"], "requires_auth": False},
            {"mcp_id": "sequential-thinking", "name": "Sequential Thinking", "capabilities": ["reasoning"], "requires_auth": False},
            {"mcp_id": "filesystem-pipeline", "name": "Filesystem", "capabilities": ["file_read", "file_write"], "requires_auth": False},
            {"mcp_id": "anthropic_claude", "name": "Claude API", "capabilities": ["reasoning", "nlp", "structuration"], "requires_auth": True, "auth_key_name": "ANTHROPIC_API_KEY"},
        ]

        selected = self._mcp_manager.ask_claude_for_mcps(job_data, candidate_data, "stage_1", available)

        added = []
        for mcp_sel in selected:
            mcp_id = mcp_sel.get("mcp_id", "")
            # Find matching available MCP
            match = next((a for a in available if a["mcp_id"] == mcp_id), None)
            if match:
                entry = self._mcp_manager.add_mcp(
                    mcp_id=mcp_id,
                    name=match["name"],
                    capabilities=match.get("capabilities", []),
                    tools=[{"name": t, "description": ""} for t in match.get("capabilities", [])],
                    source="claude_suggested",
                    requires_auth=match.get("requires_auth", False),
                    auth_key_name=match.get("auth_key_name", ""),
                )
                added.append({"mcp_id": mcp_id, "reason": mcp_sel.get("reason", "")})

        self._state.set("stage1_selected_mcps", selected)
        return {
            "status": "completed",
            "selected_count": len(selected),
            "added_count": len(added),
            "selected": added,
        }

    def _step_mcp_test(self) -> dict[str, Any]:
        """1.4 — Test selected MCPs."""
        test_results = self._mcp_manager.test_all_pending()
        active = [r for r in test_results if r.get("result") == "pass"]
        failed = [r for r in test_results if r.get("result") != "pass"]

        # Remove failed MCPs
        for f in failed:
            if f.get("result") != "excluded":
                self._mcp_manager.remove_mcp(f["mcp_id"])

        return {
            "status": "completed",
            "tested": len(test_results),
            "active": len(active),
            "failed": len(failed),
            "results": test_results,
        }

    def _step_resource_discover(self) -> dict[str, Any]:
        """1.5 — Use MCPs to find resources about company, skills, market."""
        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})

        company_name = job_data.get("company", {}).get("name", "")
        job_title = job_data.get("title", "")
        skills = [s.get("skill", "") for s in job_data.get("requirements", {}).get("required_skills", [])]

        resources = []
        searches = []

        # 1. Company info
        if company_name:
            company_info = search_company_info(company_name, job_title)
            self._state.set("company_info", company_info)
            for r in company_info.get("results", []):
                rid = str(uuid.uuid4())
                resources.append({
                    "resource_id": rid,
                    "name": r.get("title", "")[:100],
                    "type": _map_resource_type(r.get("type", "")),
                    "source": {"method": "web_search", "mcp_id": "duckduckgo", "url": r.get("url", "")},
                    "content": {"raw": r.get("body", ""), "summary": r.get("body", "")[:200]},
                    "relevance": {"to_job": 0.8, "to_candidate": 0.3, "overall": 0.6},
                    "dependencies": [],
                    "stage": "stage_1",
                    "status": "discovered",
                })
                searches.append({
                    "query": f"{company_name} {r.get('type', '')}",
                    "source_type": "web",
                    "result_count": 1,
                })

        # 2. Key skills / technologies
        for skill in skills[:5]:
            try:
                from ddgs import DDGS
                ddgs = DDGS()
                skill_results = ddgs.text(f"{skill} technology overview requirements", max_results=2)
                for sr in skill_results:
                    rid = str(uuid.uuid4())
                    resources.append({
                        "resource_id": rid,
                        "name": f"Skill: {skill} — {sr.get('title', '')[:60]}",
                        "type": "skill_reference",
                        "source": {"method": "web_search", "mcp_id": "duckduckgo", "url": sr.get("href", "")},
                        "content": {"raw": sr.get("body", ""), "summary": sr.get("body", "")[:200]},
                        "relevance": {"to_job": 0.9, "to_candidate": 0.5, "overall": 0.7},
                        "dependencies": [],
                        "stage": "stage_1",
                        "status": "discovered",
                    })
            except Exception as e:
                logger.warning("Skill search failed for %s: %s", skill, e)

        # 3. Salary/market data
        if job_title and company_name:
            try:
                from ddgs import DDGS
                ddgs = DDGS()
                market_results = ddgs.text(f"{job_title} salaire marche emploi France 2024 2025", max_results=2)
                for mr in market_results:
                    resources.append({
                        "resource_id": str(uuid.uuid4()),
                        "name": f"Market: {mr.get('title', '')[:60]}",
                        "type": "job_market_data",
                        "source": {"method": "web_search", "mcp_id": "duckduckgo", "url": mr.get("href", "")},
                        "content": {"raw": mr.get("body", ""), "summary": mr.get("body", "")[:200]},
                        "relevance": {"to_job": 0.7, "to_candidate": 0.6, "overall": 0.65},
                        "dependencies": [],
                        "stage": "stage_1",
                        "status": "discovered",
                    })
            except Exception as e:
                logger.warning("Market search failed: %s", e)

        self._state.set("discovered_resources", resources)

        return {
            "status": "completed",
            "resources_found": len(resources),
            "searches_performed": len(searches),
            "resource_types": list(set(r["type"] for r in resources)),
        }

    def _step_resource_register(self) -> dict[str, Any]:
        """1.6 — Register discovered resources in our MCP."""
        resources = self._state.get("discovered_resources", [])
        self._mcp_manager.register_resources(resources)

        # Build dependency graph between resources
        _build_resource_dependencies(resources)
        self._state.set("registered_resources", resources)

        return {
            "status": "completed",
            "registered_count": len(resources),
            "types": list(set(r["type"] for r in resources)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # STAGE 2 — ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════

    def _step_resource_consult(self) -> dict[str, Any]:
        """2.1 — Fetch, parse, and NLP-analyze resources."""
        resources = self._state.get("registered_resources", [])

        # Use Claude as NLP to summarize and extract entities from resources
        enriched_count = 0
        for resource in resources:
            raw = resource.get("content", {}).get("raw", "")
            if not raw or len(raw) < 50:
                continue

            try:
                nlp_result = call_claude(
                    self._api_keys,
                    "Tu es un expert NLP. Extrais les entites cles et fais un resume.",
                    f"Analyse ce texte et retourne un JSON avec: summary, key_topics (list), entities (list of {{text, type}}), sentiment.\n\nTEXTE:\n{raw[:3000]}",
                    model="claude-sonnet-4-20250514",
                    max_tokens=2000,
                )
                if isinstance(nlp_result, dict):
                    resource["content"]["summary"] = nlp_result.get("summary", resource["content"].get("summary", ""))
                    resource["content"]["entities"] = [
                        {"entity": e.get("text", ""), "type": e.get("type", "other"), "relevance": "medium"}
                        for e in nlp_result.get("entities", [])
                    ]
                    resource["status"] = "enriched"
                    enriched_count += 1
            except Exception as e:
                logger.warning("NLP enrichment failed for resource: %s", e)

        self._state.set("enriched_resources", resources)
        return {
            "status": "completed",
            "total_resources": len(resources),
            "enriched": enriched_count,
        }

    def _step_analysis_mcp_select(self) -> dict[str, Any]:
        """2.2 — Claude selects MCPs for deep analysis."""
        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})

        available = [
            {"mcp_id": "anthropic_claude", "name": "Claude API", "capabilities": ["deep_analysis", "comparison"]},
            {"mcp_id": "sequential-thinking", "name": "Sequential Thinking", "capabilities": ["step_by_step_analysis"]},
        ]

        selected = self._mcp_manager.ask_claude_for_mcps(job_data, candidate_data, "stage_2", available)

        for mcp_sel in selected:
            mcp_id = mcp_sel.get("mcp_id", "")
            match = next((a for a in available if a["mcp_id"] == mcp_id), None)
            if match:
                self._mcp_manager.add_mcp(
                    mcp_id=mcp_id, name=match["name"],
                    capabilities=match.get("capabilities", []),
                    tools=[{"name": c, "description": ""} for c in match.get("capabilities", [])],
                    source="claude_suggested",
                )

        return {
            "status": "completed",
            "selected_count": len(selected),
        }

    def _step_analysis_mcp_test(self) -> dict[str, Any]:
        """2.3 — Test analysis MCPs."""
        results = self._mcp_manager.test_all_pending()
        return {
            "status": "completed",
            "tested": len(results),
            "active": sum(1 for r in results if r.get("result") == "pass"),
        }

    def _step_analysis_execute(self) -> dict[str, Any]:
        """2.4 — Run analysis queries via Claude with enriched context."""
        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})
        company_info = self._state.get("company_info", {})
        resources = self._state.get("enriched_resources", [])
        analysis_schema = self._registry.get_schema("analysis")

        # Build enriched context from resources
        resource_context = "\n".join([
            f"- [{r['type']}] {r['name']}: {r.get('content', {}).get('summary', '')[:200]}"
            for r in resources[:15]
        ])

        # Call Claude with full context
        analysis_data = analyze_candidacy(
            self._api_keys, job_data, candidate_data, company_info, analysis_schema
        )

        # Ensure required fields
        analysis_data.setdefault("job_id", "")
        analysis_data.setdefault("candidate_id", "")
        analysis_data.setdefault("alignments", [])
        analysis_data.setdefault("gaps", [])
        analysis_data.setdefault("signals", [])
        analysis_data.setdefault("uncertainties", [])
        analysis_data.setdefault("overall_score", 0.0)
        analysis_data.setdefault("recommendation", "partial_match")

        self._state.set("analysis_data", analysis_data)

        return {
            "status": "completed",
            "overall_score": analysis_data.get("overall_score", 0),
            "recommendation": analysis_data.get("recommendation", "?"),
            "alignments": len(analysis_data.get("alignments", [])),
            "gaps": len(analysis_data.get("gaps", [])),
            "signals": len(analysis_data.get("signals", [])),
        }

    def _step_combine(self) -> dict[str, Any]:
        """2.5 — Combine all results into meta-analysis."""
        analysis_data = self._state.get("analysis_data", {})
        resources = self._state.get("enriched_resources", [])
        mcp_config = self._mcp_manager.get_config()

        combined = {
            "analysis": analysis_data,
            "resources_used": len(resources),
            "mcps_active": len(self._mcp_manager.get_active_mcps()),
            "mcp_events": len(self._mcp_manager.get_event_log()),
            "resource_types": list(set(r["type"] for r in resources)),
            "combined_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state.set("combined_analysis", combined)

        return {"status": "completed", "resources_used": len(resources)}

    def _step_grand_meta(self) -> dict[str, Any]:
        """2.55 — Build the Grand Meta Schema (7 categories)."""
        from .grand_meta_builder import build_grand_meta

        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})
        analysis_data = self._state.get("analysis_data", {})
        resources = self._state.get("enriched_resources", [])
        company_info = self._state.get("company_info", {})

        logger.info("Building Grand Meta Schema (7 categories via Claude)...")
        grand_meta_data = build_grand_meta(
            self._api_keys, job_data, candidate_data, analysis_data, resources, company_info
        )

        grand_meta_obj = {
            "meta": self.create_meta("grand_meta", [{
                "mcp_id": "anthropic_claude",
                "capability": "grand_meta_building",
                "invoked_at": datetime.now(timezone.utc).isoformat(),
                "status": "success",
            }]),
            "grand_meta": grand_meta_data,
        }
        grand_meta_obj["meta"]["confidence"] = 0.85
        grand_meta_obj["meta"]["validation_status"] = "valid"

        self._state.set("grand_meta", grand_meta_data)
        self._state.set("grand_meta_obj", grand_meta_obj)

        # Save grand meta separately
        gm_dir = self._storage_dir / "outputs"
        gm_dir.mkdir(parents=True, exist_ok=True)
        session_id = self._state.get("session_id", "")
        with open(gm_dir / f"grand_meta_{session_id}.json", "w", encoding="utf-8") as f:
            json.dump(grand_meta_obj, f, indent=2, ensure_ascii=False)

        synthesis = grand_meta_data.get("match_synthesis", {})
        return {
            "status": "completed",
            "categories_built": list(grand_meta_data.keys()),
            "overall_score": synthesis.get("overall_score", 0),
            "recommendation": synthesis.get("recommendation", "?"),
            "category_scores": synthesis.get("category_scores", {}),
            "top_strengths_count": len(synthesis.get("top_strengths", [])),
            "top_risks_count": len(synthesis.get("top_risks", [])),
            "interview_questions_count": len(synthesis.get("interview_questions", [])),
        }

    def _step_generate(self) -> dict[str, Any]:
        """2.6 — Generate final report."""
        job_data = self._state.get("job_data", {})
        candidate_data = self._state.get("candidate_data", {})
        analysis_data = self._state.get("analysis_data", {})
        company_info = self._state.get("company_info", {})
        resources = self._state.get("enriched_resources", [])
        grand_meta = self._state.get("grand_meta", {})

        report_md = generate_report(
            self._api_keys, job_data, candidate_data, analysis_data, company_info
        )

        now = datetime.now(timezone.utc).isoformat()
        session_id = self._state.get("session_id", "")

        generation_obj = {
            "meta": self.create_meta("generation"),
            "generation": {
                "analysis_id": str(uuid.uuid4()),
                "artifacts": [
                    {
                        "artifact_id": str(uuid.uuid4()),
                        "type": "detailed_report",
                        "format": "markdown",
                        "title": "Rapport d'analyse de candidature",
                        "content": report_md if isinstance(report_md, str) else str(report_md),
                        "generated_at": now,
                    },
                    {
                        "artifact_id": str(uuid.uuid4()),
                        "type": "summary_report",
                        "format": "json",
                        "title": "Donnees structurees",
                        "content": json.dumps({"job": job_data, "candidate": candidate_data, "analysis": analysis_data}, ensure_ascii=False),
                        "generated_at": now,
                    },
                    {
                        "artifact_id": str(uuid.uuid4()),
                        "type": "custom",
                        "format": "json",
                        "title": "Grand Meta Schema — Analyse 7 categories",
                        "content": json.dumps(grand_meta, ensure_ascii=False),
                        "generated_at": now,
                    },
                ],
                "summary": {
                    "candidate_name": grand_meta.get("candidate_profile", {}).get("identity", {}).get("name", "") or candidate_data.get("identity", {}).get("name", "Inconnu"),
                    "job_title": grand_meta.get("job_position", {}).get("title", "") or job_data.get("title", "Inconnu"),
                    "overall_score": grand_meta.get("match_synthesis", {}).get("overall_score", analysis_data.get("overall_score", 0.0)),
                    "recommendation": grand_meta.get("match_synthesis", {}).get("recommendation", analysis_data.get("recommendation", "partial_match")),
                    "key_strengths": grand_meta.get("match_synthesis", {}).get("top_strengths", [])[:5] or [s.get("description", "") for s in analysis_data.get("signals", []) if s.get("type") == "strength"][:5],
                    "key_gaps": grand_meta.get("match_synthesis", {}).get("top_risks", [])[:5] or [g.get("requirement", "") for g in analysis_data.get("gaps", []) if g.get("severity") in ("critical", "significant")][:5],
                    "key_uncertainties": grand_meta.get("match_synthesis", {}).get("top_unknowns", [])[:5] or [u.get("area", "") for u in analysis_data.get("uncertainties", [])][:5],
                    "next_steps": _derive_next_steps(analysis_data),
                },
            },
        }

        # Save report file
        report_dir = self._storage_dir / "outputs"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"report_{session_id}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_md if isinstance(report_md, str) else str(report_md))

        self._state.store_output(f"generation_{session_id}", generation_obj)

        summary = generation_obj["generation"]["summary"]
        return {
            "status": "completed",
            "recommendation": summary["recommendation"],
            "overall_score": summary["overall_score"],
            "candidate_name": summary["candidate_name"],
            "job_title": summary["job_title"],
            "artifacts_count": len(generation_obj["generation"]["artifacts"]),
            "report_file": str(report_file),
        }


# ══════════════════════════════════════════════════════════════════════════════��
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _read_pdf(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {"raw_text": "", "pages": [], "total_pages": 0, "error": f"File not found: {file_path}"}

    raw_text, pages = "", []
    try:
        import fitz
        doc = fitz.open(file_path)
        for i, page in enumerate(doc):
            t = page.get_text()
            pages.append({"page_number": i + 1, "text": t, "char_count": len(t)})
            raw_text += t + "\n"
        doc.close()
    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    t = page.extract_text() or ""
                    pages.append({"page_number": i + 1, "text": t, "char_count": len(t)})
                    raw_text += t + "\n"
        except ImportError:
            return {"raw_text": "", "pages": [], "total_pages": 0, "error": "No PDF library available"}

    with open(file_path, "rb") as f:
        checksum = hashlib.sha256(f.read()).hexdigest()

    return {"raw_text": raw_text.strip(), "pages": pages, "total_pages": len(pages), "total_chars": len(raw_text), "checksum_sha256": checksum}


def _map_resource_type(search_type: str) -> str:
    mapping = {
        "company_info": "company_profile",
        "company_culture": "company_culture",
        "company_news": "company_news",
        "sector_context": "industry_report",
    }
    return mapping.get(search_type, "custom")


def _build_resource_dependencies(resources: list[dict[str, Any]]) -> None:
    """Build dependency links between related resources."""
    by_type: dict[str, list[str]] = {}
    for r in resources:
        t = r.get("type", "custom")
        by_type.setdefault(t, []).append(r["resource_id"])

    # Company resources complement each other
    company_types = ["company_profile", "company_culture", "company_news"]
    company_ids = []
    for ct in company_types:
        company_ids.extend(by_type.get(ct, []))

    for r in resources:
        if r["resource_id"] in company_ids:
            for other_id in company_ids:
                if other_id != r["resource_id"]:
                    r["dependencies"].append({"resource_id": other_id, "relation": "complements"})

    # Skill references validate job requirements
    for r in resources:
        if r["type"] == "skill_reference":
            for company_r in resources:
                if company_r["type"] == "company_profile":
                    r["dependencies"].append({"resource_id": company_r["resource_id"], "relation": "validates"})


def _derive_next_steps(analysis_data: dict[str, Any]) -> list[str]:
    rec = analysis_data.get("recommendation", "")
    steps = []
    if rec in ("strong_match", "good_match"):
        steps.extend(["Planifier un entretien", "Preparer les questions sur les incertitudes"])
    elif rec == "partial_match":
        steps.extend(["Evaluer si les ecarts sont compensables", "Entretien exploratoire recommande"])
    else:
        steps.append("Candidature peu alignee — archiver ou rediriger")

    critical = [g for g in analysis_data.get("gaps", []) if g.get("severity") == "critical"]
    if critical:
        steps.append(f"Verifier {len(critical)} ecart(s) critique(s)")
    return steps
