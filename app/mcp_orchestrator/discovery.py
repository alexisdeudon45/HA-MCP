"""MCP Discovery: scans environment for available MCP servers and classifies their capabilities."""

import json
import logging
from pathlib import Path
from typing import Any

from .capability import Capability, CapabilityCategory, CapabilityMap, MCPInfo

logger = logging.getLogger(__name__)

# Keywords that indicate an MCP tool requires authentication
AUTH_INDICATORS = [
    "authenticate", "auth", "login", "token", "api_key", "apikey",
    "credential", "oauth", "complete_authentication", "sign_in",
]


class MCPDiscovery:
    """Discovers MCP servers, probes capabilities, and applies authentication policy."""

    def __init__(self, config_path: Path | None = None):
        self._config: dict[str, Any] = {}
        self._capability_keywords: dict[str, list[str]] = {}
        if config_path and config_path.exists():
            with open(config_path) as f:
                full_config = json.load(f)
                self._config = full_config.get("discovery", {})
                self._capability_keywords = full_config.get("capability_keywords", {})

    def discover(self, mcp_tool_lists: dict[str, list[dict[str, Any]]]) -> CapabilityMap:
        """Discover and classify MCPs from a mapping of mcp_id -> tool list.

        Args:
            mcp_tool_lists: dict mapping MCP server IDs to their list of tool descriptors.
                Each tool descriptor has at minimum: {"name": str, "description": str}
        """
        cap_map = CapabilityMap()

        for mcp_id, tools in mcp_tool_lists.items():
            mcp_info = self._analyze_mcp(mcp_id, tools)
            cap_map.register_mcp(mcp_info)
            logger.info(
                "MCP '%s': status=%s, capabilities=%d",
                mcp_id,
                mcp_info.status,
                len(mcp_info.capabilities),
            )

        return cap_map

    def _analyze_mcp(self, mcp_id: str, tools: list[dict[str, Any]]) -> MCPInfo:
        """Analyze an MCP server's tools and classify capabilities."""
        requires_auth = self._check_requires_auth(tools)

        mcp = MCPInfo(
            mcp_id=mcp_id,
            name=mcp_id,
            tools=tools,
            requires_auth=requires_auth,
            exclusion_reason="requires_authentication" if requires_auth else None,
        )

        if not requires_auth:
            mcp.capabilities = self._classify_capabilities(mcp_id, tools)

        return mcp

    def _check_requires_auth(self, tools: list[dict[str, Any]]) -> bool:
        """Check if an MCP requires authentication based on its tool list."""
        for tool in tools:
            name = tool.get("name", "").lower()
            if any(indicator in name for indicator in AUTH_INDICATORS):
                return True
        return False

    def _classify_capabilities(self, mcp_id: str, tools: list[dict[str, Any]]) -> list[Capability]:
        """Classify each tool into capability categories."""
        capabilities = []

        for tool in tools:
            tool_name = tool.get("name", "")
            tool_desc = tool.get("description", "")
            combined = f"{tool_name} {tool_desc}".lower()

            for category_name, keywords in self._capability_keywords.items():
                if any(kw in combined for kw in keywords):
                    try:
                        category = CapabilityCategory(category_name)
                    except ValueError:
                        continue

                    capabilities.append(
                        Capability(
                            name=f"{mcp_id}:{tool_name}",
                            category=category,
                            mcp_id=mcp_id,
                            tool_name=tool_name,
                            description=tool_desc,
                            parameters=tool.get("parameters", {}),
                        )
                    )

        return capabilities
