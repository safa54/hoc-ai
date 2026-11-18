"""Portable JSON schemas for structured output.

Providers disagree on which JSON-Schema features they accept. The common denominator is:
every object lists all its properties as required, forbids extras, and carries no numeric
or string constraints. Pydantic still validates the real constraints on our side.
"""

from __future__ import annotations

from typing import Any

_DROP = {
    "title",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "format",
}


def portable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a Pydantic JSON schema that strict structured-output modes accept."""

    def walk(node: Any, *, is_properties: bool = False) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if is_properties:  # keys are field names, not schema keywords
            return {name: walk(sub) for name, sub in node.items()}
        out = {
            key: walk(value, is_properties=key in ("properties", "$defs"))
            for key, value in node.items()
            if key not in _DROP
        }
        if out.get("type") == "object" or "properties" in out:
            out["required"] = list(out.get("properties", {}))
            out["additionalProperties"] = False
        return out

    result = walk(schema)
    assert isinstance(result, dict)
    return result
