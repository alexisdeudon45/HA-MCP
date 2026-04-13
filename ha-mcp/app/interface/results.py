"""Results Formatter: formats pipeline results for display."""

import json
from typing import Any


class ResultsFormatter:
    """Formats pipeline results into human-readable output."""

    @staticmethod
    def format_summary(results: dict[str, Any]) -> str:
        """Format a concise text summary of pipeline results."""
        lines = []
        lines.append("=" * 60)
        lines.append("  MCP-POSTE - Candidacy Analysis Results")
        lines.append("=" * 60)
        lines.append(f"Session: {results.get('session_id', 'N/A')}")
        lines.append("")

        # Phase results
        phases = results.get("phases", {})
        lines.append("Pipeline Phases:")
        for phase_name, phase_result in phases.items():
            status = phase_result.get("status", "unknown")
            icon = "OK" if status == "completed" else "FAIL" if status == "failed" else "SKIP"
            lines.append(f"  [{icon}] {phase_name}")
            if status == "failed" and "error" in phase_result:
                lines.append(f"        Error: {phase_result['error']}")

        lines.append("")

        # Generation summary if available
        gen = phases.get("generation", {})
        if gen.get("status") == "completed":
            lines.append(f"Recommendation: {gen.get('recommendation', 'N/A')}")
            lines.append(f"Artifacts generated: {gen.get('artifacts_count', 0)}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def format_json(results: dict[str, Any], indent: int = 2) -> str:
        """Format results as pretty-printed JSON."""
        return json.dumps(results, indent=indent, ensure_ascii=False, default=str)

    @staticmethod
    def format_trace(trace: list[dict[str, Any]]) -> str:
        """Format the execution trace."""
        lines = ["Execution Trace:", "-" * 40]
        for entry in trace:
            ts = entry.get("timestamp", "")
            step = entry.get("step", "")
            msg = entry.get("message", "")
            lines.append(f"  [{ts}] {step}: {msg}")
        return "\n".join(lines)
