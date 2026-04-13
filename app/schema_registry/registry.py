"""Schema Registry: loads, stores, and serves all JSON schemas from the SSOT."""

import json
from pathlib import Path
from typing import Any


class SchemaRegistry:
    """Central schema registry that acts as the Single Source of Truth.

    All components must obtain their data contracts exclusively from this registry.
    """

    def __init__(self, schemas_dir: Path | None = None):
        self._schemas_dir = schemas_dir or Path(__file__).resolve().parent.parent.parent / "schemas"
        self._schemas: dict[str, dict[str, Any]] = {}
        self._registry_manifest: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all schemas from the registry manifest."""
        registry_path = self._schemas_dir / "registry.json"
        with open(registry_path) as f:
            self._registry_manifest = json.load(f)

        for name, entry in self._registry_manifest.get("schemas", {}).items():
            schema_path = self._schemas_dir / entry["path"]
            with open(schema_path) as f:
                self._schemas[name] = json.load(f)

        self._loaded = True

    def get_schema(self, name: str) -> dict[str, Any]:
        """Get a schema by its registry name."""
        self._ensure_loaded()
        if name not in self._schemas:
            raise KeyError(f"Schema '{name}' not found in registry. Available: {list(self._schemas.keys())}")
        return self._schemas[name]

    def get_meta_schema(self) -> dict[str, Any]:
        """Get the meta-schema that all objects must conform to."""
        return self.get_schema("meta")

    def get_pipeline_flow(self) -> list[dict[str, Any]]:
        """Get the pipeline phase flow definition from the registry."""
        self._ensure_loaded()
        return self._registry_manifest.get("pipeline_flow", [])

    def get_capability_mapping(self) -> dict[str, list[str]]:
        """Get the capability-to-phase mapping."""
        self._ensure_loaded()
        return self._registry_manifest.get("capability_mapping", {})

    def list_schemas(self) -> list[str]:
        """List all registered schema names."""
        self._ensure_loaded()
        return list(self._schemas.keys())

    def get_schema_version(self, name: str) -> str:
        """Get the version of a specific schema."""
        self._ensure_loaded()
        entry = self._registry_manifest.get("schemas", {}).get(name)
        if not entry:
            raise KeyError(f"Schema '{name}' not found in registry manifest")
        return entry["version"]

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
