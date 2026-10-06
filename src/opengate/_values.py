from __future__ import annotations

from typing import Any


def dump_sdk_value(value: Any) -> Any:
    """Convert SDK models to their public dictionary representation."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return value
