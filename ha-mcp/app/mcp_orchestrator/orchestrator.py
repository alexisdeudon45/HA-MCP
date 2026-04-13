"""MCP Orchestrator: central coordinator that discovers MCPs, plans execution, and orchestrates the pipeline."""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema_registry import SchemaRegistry, SchemaValidator
from .capability import CapabilityMap
from .discovery import MCPDiscovery
from .planner import ExecutionPlanner

logger = logging.getLogger(__name__)


class MCPOrchestrator:
    """Central MCP Orchestrator.

    Responsibilities:
    - Discover available MCPs
    - Analyze capabilities
    - Build execution plans
    - Orchestrate pipeline calls
    - Validate results via schemas
    - Manage traceability
    - Handle errors and degradation
    """

    def __init__(self, project_root: Path | None = None):
        self._project_root = project_root or Path(__file__).resolve().parent.parent.parent
        self._registry = SchemaRegistry(self._project_root / "schemas")
        self._validator = SchemaValidator(self._registry)
        self._discovery = MCPDiscovery(self._project_root / "config" / "mcp_discovery.json")
        self._capability_map: CapabilityMap | None = None
        self._planner: ExecutionPlanner | None = None
        self._session_id: str = ""
        self._trace: list[dict[str, Any]] = []

    def initialize(self, session_id: str | None = None) -> dict[str, Any]:
        """Initialize the orchestrator: load schemas, prepare session."""
        self._session_id = session_id or str(uuid.uuid4())
        self._registry.load()

        self._log_trace("initialize", "Orchestrator initialized", {
            "schemas_loaded": self._registry.list_schemas(),
            "session_id": self._session_id,
        })

        return {
            "session_id": self._session_id,
            "schemas_loaded": self._registry.list_schemas(),
            "status": "initialized",
        }

    def discover_mcps(self, mcp_tool_lists: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Discover and classify available MCPs.

        Args:
            mcp_tool_lists: mapping of MCP server IDs to their tool descriptors.
        """
        self._capability_map = self._discovery.discover(mcp_tool_lists)
        self._planner = ExecutionPlanner(self._registry, self._capability_map)

        result = {
            "available_mcps": [m.to_dict() for m in self._capability_map.list_available_mcps()],
            "excluded_mcps": [m.to_dict() for m in self._capability_map.list_excluded_mcps()],
            "coverage": self._capability_map.get_coverage(),
        }

        self._log_trace("discover_mcps", "MCP discovery complete", result)
        return result

    def create_plan(self) -> dict[str, Any]:
        """Create an execution plan based on discovered capabilities."""
        if not self._planner:
            raise RuntimeError("Must call discover_mcps() before create_plan()")

        plan = self._planner.create_plan(self._session_id)

        validation = self._validator.validate(plan, "pipeline")
        if not validation.valid:
            logger.warning("Plan validation issues: %s", validation.errors)
            plan["meta"]["validation_status"] = "partial"

        self._log_trace("create_plan", "Execution plan created", {
            "plan_id": plan["pipeline"]["plan_id"],
            "phase_count": len(plan["pipeline"]["phases"]),
            "validation": validation.to_dict(),
        })

        return plan

    def validate_data(self, data: dict[str, Any], schema_name: str) -> dict[str, Any]:
        """Validate a data object against a named schema."""
        result = self._validator.validate(data, schema_name)
        meta_result = self._validator.validate_meta(data)

        return {
            "schema_validation": result.to_dict(),
            "meta_validation": meta_result.to_dict(),
            "overall_valid": result.valid and meta_result.valid,
        }

    def get_registry(self) -> SchemaRegistry:
        return self._registry

    def get_validator(self) -> SchemaValidator:
        return self._validator

    def get_capability_map(self) -> CapabilityMap | None:
        return self._capability_map

    def get_session_id(self) -> str:
        return self._session_id

    def get_trace(self) -> list[dict[str, Any]]:
        return self._trace

    def _log_trace(self, step: str, message: str, data: dict[str, Any] | None = None) -> None:
        entry = {
            "step": step,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self._session_id,
        }
        if data:
            entry["data"] = data
        self._trace.append(entry)
        logger.info("[%s] %s", step, message)
