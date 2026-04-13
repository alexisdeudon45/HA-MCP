"""Capability model: defines MCP capabilities and their mapping."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapabilityCategory(str, Enum):
    INGESTION = "ingestion"
    STRUCTURATION = "structuration"
    ENRICHISSEMENT = "enrichissement"
    RAISONNEMENT = "raisonnement"
    VALIDATION = "validation"
    GENERATION = "generation"


@dataclass
class Capability:
    """A single capability offered by an MCP."""

    name: str
    category: CapabilityCategory
    mcp_id: str
    tool_name: str
    description: str = ""
    requires_auth: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return not self.requires_auth


@dataclass
class MCPInfo:
    """Information about a discovered MCP server."""

    mcp_id: str
    name: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    requires_auth: bool = False
    exclusion_reason: str | None = None
    capabilities: list[Capability] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.requires_auth:
            return "excluded"
        return "available"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mcp_id": self.mcp_id,
            "name": self.name,
            "status": self.status,
            "tool_count": len(self.tools),
            "capability_count": len(self.capabilities),
        }
        if self.exclusion_reason:
            result["exclusion_reason"] = self.exclusion_reason
        return result


class CapabilityMap:
    """Maps capabilities to MCPs and manages capability resolution."""

    def __init__(self):
        self._capabilities: dict[str, list[Capability]] = {}
        self._mcps: dict[str, MCPInfo] = {}

    def register_mcp(self, mcp: MCPInfo) -> None:
        """Register an MCP and its capabilities."""
        self._mcps[mcp.mcp_id] = mcp
        for cap in mcp.capabilities:
            category = cap.category.value
            if category not in self._capabilities:
                self._capabilities[category] = []
            self._capabilities[category].append(cap)

    def get_capabilities(self, category: str) -> list[Capability]:
        """Get all available capabilities for a category."""
        return [c for c in self._capabilities.get(category, []) if c.is_available]

    def get_best_capability(self, category: str) -> Capability | None:
        """Get the best available capability for a category."""
        available = self.get_capabilities(category)
        return available[0] if available else None

    def get_mcp(self, mcp_id: str) -> MCPInfo | None:
        return self._mcps.get(mcp_id)

    def list_available_mcps(self) -> list[MCPInfo]:
        return [m for m in self._mcps.values() if m.status == "available"]

    def list_excluded_mcps(self) -> list[MCPInfo]:
        return [m for m in self._mcps.values() if m.status == "excluded"]

    def get_coverage(self) -> dict[str, bool]:
        """Check which capability categories are covered."""
        return {cat.value: len(self.get_capabilities(cat.value)) > 0 for cat in CapabilityCategory}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mcps": {k: v.to_dict() for k, v in self._mcps.items()},
            "coverage": self.get_coverage(),
            "capabilities": {k: len(v) for k, v in self._capabilities.items()},
        }
