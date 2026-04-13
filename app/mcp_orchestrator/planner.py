"""Execution Planner: builds dynamic execution plans based on schema pipeline flow and available capabilities."""

import uuid
from datetime import datetime, timezone
from typing import Any

from ..schema_registry import SchemaRegistry
from .capability import CapabilityMap


class ExecutionPlanner:
    """Plans pipeline execution by mapping schema-defined phases to available MCP capabilities."""

    def __init__(self, registry: SchemaRegistry, capability_map: CapabilityMap):
        self._registry = registry
        self._capability_map = capability_map

    def create_plan(self, session_id: str) -> dict[str, Any]:
        """Create a full execution plan based on the registry pipeline flow and available capabilities."""
        pipeline_flow = self._registry.get_pipeline_flow()
        capability_mapping = self._registry.get_capability_mapping()
        coverage = self._capability_map.get_coverage()

        phases = []
        for phase_def in pipeline_flow:
            phase_name = phase_def["name"]
            phase_num = phase_def["phase"]

            # Determine required capabilities for this phase
            required_caps = []
            for cap_name, phase_refs in capability_mapping.items():
                if f"phase_{phase_num}" in phase_refs:
                    required_caps.append(cap_name)

            # Assign MCPs for each required capability
            assigned_mcps = []
            for cap_name in required_caps:
                best = self._capability_map.get_best_capability(cap_name)
                if best:
                    assigned_mcps.append({
                        "mcp_id": best.mcp_id,
                        "capability": cap_name,
                        "tool_name": best.tool_name,
                        "status": "assigned",
                    })
                else:
                    assigned_mcps.append({
                        "mcp_id": "local_fallback",
                        "capability": cap_name,
                        "tool_name": f"local_{cap_name}",
                        "status": "fallback",
                    })

            phases.append({
                "phase_id": f"phase_{phase_num}",
                "name": phase_name,
                "order": phase_num,
                "status": "pending",
                "required_capabilities": required_caps,
                "assigned_mcps": assigned_mcps,
                "input_schema_ref": _schema_ref_str(phase_def.get("input_schema")),
                "output_schema_ref": _schema_ref_str(phase_def.get("output_schema")),
            })

        now = datetime.now(timezone.utc).isoformat()
        plan = {
            "meta": {
                "session_id": session_id,
                "object_id": str(uuid.uuid4()),
                "schema_version": self._registry.get_schema_version("pipeline"),
                "timestamp": now,
                "mcp_sources": [],
                "validation_status": "valid",
                "confidence": 1.0,
                "lineage": [{"step": "plan_creation", "output_id": session_id, "timestamp": now}],
            },
            "pipeline": {
                "plan_id": str(uuid.uuid4()),
                "status": "planned",
                "phases": phases,
                "current_phase": 1,
                "started_at": now,
            },
        }

        return plan


def _schema_ref_str(ref: Any) -> str:
    """Convert a schema reference (str or list) to a string."""
    if ref is None:
        return ""
    if isinstance(ref, list):
        return ",".join(ref)
    return str(ref)
