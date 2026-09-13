from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


def run_jsonl(
    input_path: Path,
    output_path: Path,
    process: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    workers: int = 2,
) -> int:
    """Process JSONL records and resume by record index already in output."""
    records = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing: dict[int, dict[str, Any]] = {}
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                existing[item["index"]] = item
    pending = [(index, record) for index, record in enumerate(records) if index not in existing]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as pool:
            futures = {pool.submit(process, record): index for index, record in pending}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"ok": False, "error": {"kind": "invalid_input", "message": str(exc)}}
                existing[index] = {"index": index, **result}
                output_path.write_text("\n".join(json.dumps(existing[i], ensure_ascii=False) for i in sorted(existing)) + "\n", encoding="utf-8")
    return len(existing)
