"""
Governance — Identity & Expected Outcome extension.

Closes a gap flagged in Agency-OS-Master-Plan.md §2: UCIP's public spec
treats identity as a bare `actor` string, and this codebase's own
AgentIdentity (governance/ucip.py) had no delegation lineage and no way to
declare "what does success look like" for an intent/goal before execution
starts. This module adds the missing piece — ExpectedOutcome — while
AgentIdentity itself gets `delegation_chain` added directly (see
governance/ucip.py).

Deliberately NOT using the `jsonschema` package — requirements-lite.txt
stays dependency-light for the Acer Aspire One target, and the actual need
here (required fields present, basic type checks, one level of nesting) is
small enough to hand-roll rather than pull in a general-purpose validator.
If a future stage needs full JSON Schema (oneOf/anyOf/pattern/etc.), that's
the point to add the real dependency — this is intentionally a subset.
"""
from dataclasses import dataclass, field
from typing import Optional

_TYPE_MAP = {
    "string": str, "str": str,
    "number": (int, float), "float": float,
    "integer": int, "int": int,
    "boolean": bool, "bool": bool,
    "array": list, "list": list,
    "object": dict, "dict": dict,
}


def validate_against_schema(data: dict, schema: dict, _path: str = "") -> tuple[bool, list[str]]:
    """Minimal structural validator. schema format:
        {"required": ["field1", "field2"],
         "properties": {"field1": {"type": "string"},
                         "field2": {"type": "object", "properties": {...}, "required": [...]}}}
    Returns (is_valid, list_of_error_strings)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return False, [f"{_path or 'root'}: expected an object, got {type(data).__name__}"]

    for req_field in schema.get("required", []):
        if req_field not in data:
            errors.append(f"{_path}{req_field}: required field missing")

    props = schema.get("properties", {})
    for field_name, field_schema in props.items():
        if field_name not in data:
            continue
        value = data[field_name]
        expected_type = field_schema.get("type")
        if expected_type:
            py_type = _TYPE_MAP.get(expected_type)
            if py_type and not isinstance(value, py_type):
                errors.append(
                    f"{_path}{field_name}: expected type '{expected_type}', got {type(value).__name__}"
                )
                continue
        if expected_type == "object" and isinstance(value, dict) and "properties" in field_schema:
            ok, sub_errors = validate_against_schema(value, field_schema, _path=f"{_path}{field_name}.")
            errors.extend(sub_errors)

    return (len(errors) == 0), errors


@dataclass
class ExpectedOutcome:
    """Attached to an intent/goal before execution starts, per the Intent→
    Identity→Expected Outcome→Capabilities→Workers→Validation→Execution→
    Reflection model from the master plan. `schema` is optional — a goal
    without one just gets no structural check, and completion is accepted
    on the Brain's word, same as before this existed."""
    description: str = ""
    schema: Optional[dict] = None   # None = no structural validation performed
    max_correction_attempts: int = 2  # bounded retries before giving up, not infinite

    def validate(self, output: str) -> tuple[bool, list[str]]:
        """output is expected to be a JSON string when a schema is set.
        Returns (valid, errors). If no schema is set, always valid."""
        if not self.schema:
            return True, []
        import json
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return False, ["output is not valid JSON, but an expected_outcome_schema was declared"]
        return validate_against_schema(parsed, self.schema)

    def to_dict(self) -> dict:
        return {"description": self.description, "schema": self.schema,
                "max_correction_attempts": self.max_correction_attempts}
