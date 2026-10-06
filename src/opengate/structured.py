"""Small, dependency-free structured-output seam for consumers.

The gateway remains domain-neutral.  Consumers provide a JSON Schema-shaped
contract and get a parsed value or an actionable validation diagnostic.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


class StructuredOutputError(ValueError):
    def __init__(self, message: str, *, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


class StructuredSchemaError(ValueError):
    """The consumer supplied an invalid or unsupported schema."""


_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_SCHEMA_KEYWORDS = {
    "type",
    "enum",
    "required",
    "properties",
    "items",
    "additionalProperties",
}


def validate_schema(schema: Any, *, path: str = "$", ancestors: frozenset[int] = frozenset()) -> None:
    """Validate the supported JSON Schema subset and reject unknown keywords.

    Object schemas support ``type``, ``enum``, ``required``, ``properties``,
    ``items``, and ``additionalProperties``. Boolean schemas are also accepted.
    """
    # Boolean schemas are valid JSON Schema subschemas: true accepts any value,
    # while false rejects every value.
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise StructuredSchemaError(f"{path}: schema must be an object or boolean")
    if id(schema) in ancestors:
        raise StructuredSchemaError(f"{path}: circular schema reference")
    ancestors = ancestors | {id(schema)}

    unsupported = set(schema) - _SCHEMA_KEYWORDS
    if unsupported:
        names = ", ".join(sorted(str(name) for name in unsupported))
        raise StructuredSchemaError(f"{path}: unsupported schema keyword(s): {names}")

    if "type" in schema:
        expected = schema["type"]
        allowed = expected if isinstance(expected, list) else [expected]
        if not allowed or any(not isinstance(name, str) or name not in _SCHEMA_TYPES for name in allowed):
            raise StructuredSchemaError(f"{path}: unsupported schema type {expected!r}")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise StructuredSchemaError(f"{path}: enum must be an array")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
            raise StructuredSchemaError(f"{path}: required must be an array of property names")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict) or any(not isinstance(name, str) for name in properties):
            raise StructuredSchemaError(f"{path}: properties must be an object")
        for name, child in properties.items():
            validate_schema(child, path=f"{path}.{name}", ancestors=ancestors)
    if "items" in schema:
        validate_schema(schema["items"], path=f"{path}[]", ancestors=ancestors)
    if "additionalProperties" in schema:
        validate_schema(schema["additionalProperties"], path=f"{path}.*", ancestors=ancestors)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number {value}")
    return parsed


def parse_structured(text: str, schema: Any) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S).strip()
    try:
        value = json.loads(cleaned, parse_constant=_reject_constant, parse_float=_parse_float)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise StructuredOutputError(f"invalid JSON: {exc}") from exc
    validate_structured(value, schema)
    return value


def _validate_value(value: Any, schema: Any, *, path: str) -> None:
    if schema is True:
        return
    if schema is False:
        raise StructuredOutputError("value is rejected by the false schema", path=path)

    expected = schema.get("type")

    def matches_type(type_name: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }[type_name]

    if "type" in schema:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(matches_type(type_name) for type_name in allowed):
            raise StructuredOutputError(f"expected one of {allowed!r}, got {type(value).__name__}", path=path)
    if "enum" in schema and not any(_json_equal(value, candidate) for candidate in schema["enum"]):
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
        additional_schema = schema.get("additionalProperties")
        if isinstance(additional_schema, dict):
            for name, child_value in value.items():
                if name not in properties:
                    _validate_value(child_value, additional_schema, path=f"{path}.{name}")
        for name, child_schema in properties.items():
            if name in value:
                _validate_value(value[name], child_schema, path=f"{path}.{name}")
    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        if item_schema is False and value:
            raise StructuredOutputError("array items are not allowed", path=path)
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item, item_schema, path=f"{path}[{index}]")


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int equality overlap."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None or right is None or isinstance(left, str) or isinstance(right, str):
        return type(left) is type(right) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        if any(not isinstance(key, str) for key in left) or any(not isinstance(key, str) for key in right):
            return False
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return False


def validate_structured(value: Any, schema: Any, *, path: str = "$") -> None:
    validate_schema(schema, path=path)
    _validate_value(value, schema, path=path)


def json_instruction(schema: Any) -> str:
    validate_schema(schema)
    return (
        "Верни только валидный JSON без Markdown. Не добавляй поля, которых нет в схеме. "
        f"JSON Schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'), allow_nan=False)}"
    )


__all__ = ["StructuredOutputError", "StructuredSchemaError", "json_instruction", "parse_structured", "validate_schema", "validate_structured"]
