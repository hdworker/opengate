"""Small, dependency-free structured-output seam for consumers.

The gateway remains domain-neutral.  Consumers provide a JSON Schema-shaped
contract and get a parsed value or an actionable validation diagnostic.
"""

from __future__ import annotations

import json
import re
from typing import Any


class StructuredOutputError(ValueError):
    def __init__(self, message: str, *, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


def parse_structured(text: str, schema: dict[str, Any]) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON: {exc.msg}") from exc
    validate_structured(value, schema)
    return value


def validate_structured(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    expected = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and not type_ok.get(expected, True):
        raise StructuredOutputError(f"expected {expected}, got {type(value).__name__}", path=path)
    if "enum" in schema and value not in schema["enum"]:
        raise StructuredOutputError(f"value is not one of {schema['enum']!r}", path=path)
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                raise StructuredOutputError(f"missing required property {name!r}", path=f"{path}.{name}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise StructuredOutputError(f"unexpected properties: {', '.join(unknown)}", path=path)
        for name, child_schema in properties.items():
            if name in value and isinstance(child_schema, dict):
                validate_structured(value[name], child_schema, path=f"{path}.{name}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_structured(item, schema["items"], path=f"{path}[{index}]")


def json_instruction(schema: dict[str, Any]) -> str:
    return (
        "Верни только валидный JSON без Markdown. Не добавляй поля, которых нет в схеме. "
        f"JSON Schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


__all__ = ["StructuredOutputError", "json_instruction", "parse_structured", "validate_structured"]
