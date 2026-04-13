"""Pipeline State: tracks execution state across all phases."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PipelineState:
    """Manages pipeline execution state and intermediate storage."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = storage_dir
        self._state: dict[str, Any] = {}
        self._intermediates: dict[str, Any] = {}
        self._log_entries: list[dict[str, Any]] = []

    def set(self, key: str, value: Any) -> None:
        """Store a value in pipeline state."""
        self._state[key] = value
        self._log("state_set", f"Set '{key}'")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from pipeline state."""
        return self._state.get(key, default)

    def store_intermediate(self, phase: str, name: str, data: Any) -> Path:
        """Store intermediate data for a pipeline phase."""
        key = f"{phase}/{name}"
        self._intermediates[key] = data

        # Persist to disk
        phase_dir = self._storage_dir / "intermediate" / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        file_path = phase_dir / f"{name}.json"
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._log("store_intermediate", f"Stored {key}")
        return file_path

    def get_intermediate(self, phase: str, name: str) -> Any | None:
        """Retrieve intermediate data."""
        key = f"{phase}/{name}"
        if key in self._intermediates:
            return self._intermediates[key]

        # Try loading from disk
        file_path = self._storage_dir / "intermediate" / phase / f"{name}.json"
        if file_path.exists():
            with open(file_path) as f:
                data = json.load(f)
            self._intermediates[key] = data
            return data

        return None

    def store_output(self, name: str, data: Any) -> Path:
        """Store final output data."""
        output_dir = self._storage_dir / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{name}.json"
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._log("store_output", f"Stored output: {name}")
        return file_path

    def store_log(self, session_id: str) -> Path:
        """Persist the execution log to disk."""
        log_dir = self._storage_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_dir / f"pipeline_{session_id}.json"
        with open(file_path, "w") as f:
            json.dump(self._log_entries, f, indent=2, ensure_ascii=False)
        return file_path

    def get_log(self) -> list[dict[str, Any]]:
        return list(self._log_entries)

    def _log(self, action: str, message: str) -> None:
        entry = {
            "action": action,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._log_entries.append(entry)
        logger.debug("[pipeline_state] %s: %s", action, message)
