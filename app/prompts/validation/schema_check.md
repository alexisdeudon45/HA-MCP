# Prompt: Schema Validation

## Purpose

Validate all data objects against their schemas at each pipeline phase.

## Validation Rules

1. **Structural validation**: Object matches the expected JSON schema structure
2. **Type validation**: All fields have the correct type
3. **Required fields**: All required fields are present
4. **Enum constraints**: Values match allowed enumerations
5. **Metadata compliance**: Every object has a valid `meta` block with all required fields
6. **Referential integrity**: Object IDs referenced in lineage and relations exist
7. **Confidence bounds**: Scores are within [0.0, 1.0]

## On Validation Failure

- Mark the object's `meta.validation_status` as "invalid" or "partial"
- Log the specific validation errors
- If critical: halt the pipeline phase
- If non-critical: continue with degraded confidence
