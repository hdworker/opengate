from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def read_jsonl(input_path: Path) -> Iterator[dict[str, Any]]:
    """Read immutable JSONL input; an existing output is never a checkpoint."""
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} must contain an object")
            yield value


def write_jsonl_item(handle: Any, value: dict[str, Any]) -> None:
    """Write one result item; persistence belongs to the batch consumer."""
    handle.write(json.dumps(value, ensure_ascii=False) + "\n")
