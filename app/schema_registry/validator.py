"""Schema Validator: validates data objects against their schemas."""

import json
from typing import Any

from .registry import SchemaRegistry


class ValidationError:
    """Represents a single validation error."""

    def __init__(self, path: str, message: str, schema_name: str):
        self.path = path
        self.message = message
        self.schema_name = schema_name

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message, "schema": self.schema_name}

    def __repr__(self) -> str:
        return f"ValidationError({self.path}: {self.message})"


class ValidationResult:
    """Result of a schema validation."""

    def __init__(self, valid: bool, errors: list[ValidationError] | None = None):
        self.valid = valid
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error_count": len(self.errors),
            "errors": [e.to_dict() for e in self.errors],
        }


class SchemaValidator:
    """Validates data objects against schemas from the registry.

    Uses a lightweight recursive validation approach that doesn't
    require external dependencies like jsonschema.
    """

    def __init__(self, registry: SchemaRegistry):
        self._registry = registry

    def validate(self, data: dict[str, Any], schema_name: str) -> ValidationResult:
        """Validate a data object against a named schema."""
        schema = self._registry.get_schema(schema_name)
        errors = self._validate_object(data, schema, schema_name, path="$")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def validate_meta(self, data: dict[str, Any]) -> ValidationResult:
        """Validate that an object has valid metadata."""
        meta_schema = self._registry.get_meta_schema()
        meta_props = meta_schema.get("properties", {}).get("meta", {})

        if "meta" not in data:
            return ValidationResult(
                valid=False,
                errors=[ValidationError("$.meta", "Missing required 'meta' block", "meta")],
            )

        errors = []
        meta = data["meta"]
        required_fields = meta_props.get("properties", {}).keys()

        for field in meta_props.get("required", []):
            if field not in meta:
                errors.append(ValidationError(f"$.meta.{field}", f"Missing required field '{field}'", "meta"))

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _validate_object(
        self, data: Any, schema: dict[str, Any], schema_name: str, path: str
    ) -> list[ValidationError]:
        """Recursively validate an object against a schema."""
        errors: list[ValidationError] = []

        schema_type = schema.get("type")

        if schema_type == "object" and not isinstance(data, dict):
            errors.append(ValidationError(path, f"Expected object, got {type(data).__name__}", schema_name))
            return errors

        if schema_type == "array" and not isinstance(data, list):
            errors.append(ValidationError(path, f"Expected array, got {type(data).__name__}", schema_name))
            return errors

        if schema_type == "string" and not isinstance(data, str):
            errors.append(ValidationError(path, f"Expected string, got {type(data).__name__}", schema_name))
            return errors

        if schema_type == "number" and not isinstance(data, (int, float)):
            errors.append(ValidationError(path, f"Expected number, got {type(data).__name__}", schema_name))
            return errors

        if schema_type == "integer" and not isinstance(data, int):
            errors.append(ValidationError(path, f"Expected integer, got {type(data).__name__}", schema_name))
            return errors

        # Check required fields for objects
        if schema_type == "object" and isinstance(data, dict):
            for field in schema.get("required", []):
                if field not in data:
                    errors.append(ValidationError(f"{path}.{field}", f"Missing required field '{field}'", schema_name))

            # Validate properties
            for prop_name, prop_schema in schema.get("properties", {}).items():
                if prop_name in data:
                    errors.extend(
                        self._validate_object(data[prop_name], prop_schema, schema_name, f"{path}.{prop_name}")
                    )

        # Check enum constraints
        if "enum" in schema and data not in schema["enum"]:
            errors.append(ValidationError(path, f"Value '{data}' not in enum {schema['enum']}", schema_name))

        # Check numeric bounds
        if "minimum" in schema and isinstance(data, (int, float)) and data < schema["minimum"]:
            errors.append(ValidationError(path, f"Value {data} below minimum {schema['minimum']}", schema_name))
        if "maximum" in schema and isinstance(data, (int, float)) and data > schema["maximum"]:
            errors.append(ValidationError(path, f"Value {data} above maximum {schema['maximum']}", schema_name))

        # Validate array items
        if schema_type == "array" and isinstance(data, list) and "items" in schema:
            for i, item in enumerate(data):
                errors.extend(
                    self._validate_object(item, schema["items"], schema_name, f"{path}[{i}]")
                )

        return errors
