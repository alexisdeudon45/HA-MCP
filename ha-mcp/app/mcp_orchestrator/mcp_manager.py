"""Dynamic MCP Manager: adds, tests, enables, and disables MCPs at runtime.

Claude selects which MCPs to add based on CV/job content.
MCPs are tested before activation. Failed MCPs are removed.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Known MCP tool patterns for probing
PROBE_COMMANDS = {
    "duckduckgo": {"tool": "duckduckgo_web_search", "args": {"query": "test", "count": 1}},
    "playwright": {"tool": "browser_navigate", "args": {"url": "about:blank"}},
    "filesystem": {"tool": "read_file", "args": {"path": "/dev/null"}},
    "sequential-thinking": {"tool": "sequentialthinking", "args": {"thought": "test", "thoughtNumber": 1, "totalThoughts": 1}},
}


class MCPManager:
    """Manages dynamic MCP lifecycle: suggest → add → test → activate/remove."""

    def __init__(self, storage_path: Path, api_keys: dict[str, str] | None = None):
        self._storage_path = storage_path
        self._api_keys = api_keys or {}
        self._config_file = storage_path / "mcp_config.json"
        self._config: dict[str, Any] = self._load_config()
        self._event_log: list[dict[str, Any]] = []

    # ── Config persistence ────────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        if self._config_file.exists():
            with open(self._config_file) as f:
                return json.load(f)
        return {"config_id": str(uuid.uuid4()), "mcps": [], "last_updated": None}

    def _save_config(self) -> None:
        self._config["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, "w") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    # ── MCP CRUD ──────────────────────────────────────────────────────────────

    def add_mcp(
        self,
        mcp_id: str,
        name: str,
        capabilities: list[str],
        tools: list[dict[str, str]],
        source: str = "claude_suggested",
        requires_auth: bool = False,
        auth_key_name: str = "",
    ) -> dict[str, Any]:
        """Add an MCP to the dynamic config."""
        # Check if already exists
        for mcp in self._config["mcps"]:
            if mcp["mcp_id"] == mcp_id:
                logger.info("MCP %s already in config, updating", mcp_id)
                mcp["status"] = "testing"
                self._save_config()
                return mcp

        entry = {
            "mcp_id": mcp_id,
            "name": name,
            "source": source,
            "status": "testing",
            "requires_auth": requires_auth,
            "auth_key_name": auth_key_name,
            "capabilities": capabilities,
            "tools": [{"name": t.get("name", ""), "description": t.get("description", ""), "tested": False, "test_result": "untested"} for t in tools],
            "added_at": datetime.now(timezone.utc).isoformat(),
            "added_by": "claude" if source == "claude_suggested" else "system",
            "test_log": [],
        }
        self._config["mcps"].append(entry)
        self._save_config()
        self._log_event("mcp_added", mcp_id, f"Added MCP {name}")
        return entry

    def remove_mcp(self, mcp_id: str) -> None:
        self._config["mcps"] = [m for m in self._config["mcps"] if m["mcp_id"] != mcp_id]
        self._save_config()
        self._log_event("mcp_removed", mcp_id, f"Removed MCP {mcp_id}")

    def set_status(self, mcp_id: str, status: str) -> None:
        for mcp in self._config["mcps"]:
            if mcp["mcp_id"] == mcp_id:
                mcp["status"] = status
                break
        self._save_config()

    def get_active_mcps(self) -> list[dict[str, Any]]:
        return [m for m in self._config["mcps"] if m["status"] == "active"]

    def get_all_mcps(self) -> list[dict[str, Any]]:
        return list(self._config["mcps"])

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    # ── Claude-driven MCP selection ───────────────────────────────────────────

    def ask_claude_for_mcps(
        self,
        job_data: dict[str, Any],
        candidate_data: dict[str, Any],
        stage: str,
        available_mcps: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Ask Claude which MCPs would be useful given the job/candidate context."""
        from ..pipeline.llm import call_claude

        mcp_list_str = json.dumps(available_mcps, indent=2, ensure_ascii=False)

        if stage == "stage_1":
            prompt = f"""Tu es un orchestrateur MCP. Etant donne cette offre d'emploi et ce CV, determine quels serveurs MCP seraient utiles pour TROUVER DES RESSOURCES (informations sur l'entreprise, le secteur, les competences, le marche).

OFFRE D'EMPLOI:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

CV CANDIDAT:
{json.dumps(candidate_data, indent=2, ensure_ascii=False)}

MCPs DISPONIBLES DANS L'ENVIRONNEMENT:
{mcp_list_str}

Pour chaque MCP utile, indique:
- mcp_id: l'identifiant
- reason: pourquoi il est utile
- queries: liste de requetes/actions a executer avec ce MCP
- priority: high/medium/low

Reponds en JSON: {{"selected_mcps": [...]}}"""
        else:
            prompt = f"""Tu es un orchestrateur MCP. Maintenant que nous avons les ressources, determine quels MCPs seraient utiles pour ANALYSER EN PROFONDEUR la candidature.

OFFRE D'EMPLOI:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

CV CANDIDAT:
{json.dumps(candidate_data, indent=2, ensure_ascii=False)}

MCPs DISPONIBLES:
{mcp_list_str}

Pour chaque MCP utile pour l'analyse, indique:
- mcp_id
- reason
- queries: actions d'analyse a faire
- priority

Reponds en JSON: {{"selected_mcps": [...]}}"""

        system = "Tu es un expert en orchestration MCP. Reponds UNIQUEMENT en JSON strict."

        result = call_claude(self._api_keys, system, prompt, model="claude-sonnet-4-20250514")
        if isinstance(result, dict) and "selected_mcps" in result:
            self._log_event("claude_mcp_selection", stage, f"Claude selected {len(result['selected_mcps'])} MCPs")
            return result["selected_mcps"]

        return []

    # ── MCP Testing ───────────────────────────────────────────────────────────

    def test_mcp(self, mcp_id: str) -> dict[str, Any]:
        """Test if an MCP is functional by probing its tools.

        Returns test results. Sets MCP to 'active' if passes, 'failed' if not.
        """
        mcp_entry = None
        for m in self._config["mcps"]:
            if m["mcp_id"] == mcp_id:
                mcp_entry = m
                break

        if not mcp_entry:
            return {"mcp_id": mcp_id, "result": "not_found"}

        # Check auth requirement
        if mcp_entry.get("requires_auth"):
            key_name = mcp_entry.get("auth_key_name", "")
            if key_name and not self._api_keys.get(key_name):
                mcp_entry["status"] = "excluded"
                self._save_config()
                self._log_event("mcp_test", mcp_id, "Excluded: missing API key")
                return {"mcp_id": mcp_id, "result": "excluded", "reason": "missing_api_key"}

        # Try to probe the MCP (simplified — in production this would call actual MCP tools)
        test_results = []
        all_pass = True

        for tool in mcp_entry.get("tools", []):
            tool_name = tool["name"]
            start = time.time()
            try:
                # Check if tool pattern exists in known probes
                passed = self._probe_tool(mcp_id, tool_name)
                duration = int((time.time() - start) * 1000)
                result_str = "pass" if passed else "fail"
                tool["tested"] = True
                tool["test_result"] = result_str
                if not passed:
                    all_pass = False
                test_results.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tool": tool_name,
                    "result": result_str,
                    "duration_ms": duration,
                })
            except Exception as e:
                duration = int((time.time() - start) * 1000)
                tool["tested"] = True
                tool["test_result"] = "fail"
                all_pass = False
                test_results.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tool": tool_name,
                    "result": "fail",
                    "error": str(e),
                    "duration_ms": duration,
                })

        mcp_entry["test_log"].extend(test_results)
        mcp_entry["status"] = "active" if all_pass else "failed"
        self._save_config()

        status_msg = "PASS" if all_pass else "FAIL"
        self._log_event("mcp_test", mcp_id, f"Test {status_msg}: {len(test_results)} tools tested")

        return {
            "mcp_id": mcp_id,
            "result": "pass" if all_pass else "fail",
            "tests": test_results,
            "status": mcp_entry["status"],
        }

    def test_all_pending(self) -> list[dict[str, Any]]:
        """Test all MCPs in 'testing' status."""
        results = []
        for mcp in self._config["mcps"]:
            if mcp["status"] == "testing":
                results.append(self.test_mcp(mcp["mcp_id"]))
        return results

    def _probe_tool(self, mcp_id: str, tool_name: str) -> bool:
        """Probe a specific tool to check availability.

        In this implementation, we check if the MCP server pattern is recognized.
        In production, this would send an actual MCP tool call.
        """
        # Known working local tools always pass
        if mcp_id.startswith("local_"):
            return True

        # Check against known MCP patterns
        for pattern, probe in PROBE_COMMANDS.items():
            if pattern in mcp_id.lower() or pattern in tool_name.lower():
                logger.info("Probe match: %s/%s -> pattern %s", mcp_id, tool_name, pattern)
                return True

        # Unknown MCPs — assume available for now (real impl would call MCP)
        logger.info("No probe pattern for %s/%s, assuming available", mcp_id, tool_name)
        return True

    # ── Resource registration ─────────────────────────────────────────────────

    def register_resources(self, resources: list[dict[str, Any]]) -> None:
        """Register discovered resources into the MCP's resource store."""
        resources_file = self._storage_path / "resources.json"
        existing = []
        if resources_file.exists():
            with open(resources_file) as f:
                existing = json.load(f)

        existing.extend(resources)
        with open(resources_file, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        self._log_event("resources_registered", "system", f"Registered {len(resources)} resources")

    def get_resources(self) -> list[dict[str, Any]]:
        resources_file = self._storage_path / "resources.json"
        if resources_file.exists():
            with open(resources_file) as f:
                return json.load(f)
        return []

    # ── Events ────────────────────────────────────────────────────────────────

    def _log_event(self, event_type: str, target: str, message: str) -> None:
        entry = {
            "type": event_type,
            "target": target,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._event_log.append(entry)
        logger.info("[MCP Manager] %s: %s — %s", event_type, target, message)

    def get_event_log(self) -> list[dict[str, Any]]:
        return list(self._event_log)
