from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .bootstrap import ensure_server
from .catalog import PRESETS
from .errors import ExecutionError
from .execution import BatchItem, ExecutionRequest, ExecutionService
from .jsonl import read_jsonl, write_jsonl_item
from .scaffold import init_project


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return _json_value(asdict(value))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, ExecutionError):
        return {
            "kind": value.kind,
            "message": str(value),
            "diagnostic": _json_value(value.diagnostic),
            "attempts": _json_value(value.attempts),
            "session_id": value.session_id,
        }
    return value


def _service(url: str | None, *, concurrency: int = 2) -> ExecutionService:
    return ExecutionService.from_env(base_url=url, concurrency=concurrency)


def _same_file_path(left: Path, right: Path) -> bool:
    try:
        if left.samefile(right):
            return True
    except OSError:
        pass
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


async def _run_async(args: argparse.Namespace) -> Any:
    if args.command == "models":
        service = _service(args.url)
        try:
            snapshot = await service.catalog_snapshot()
            return [_json_value(item) for item in snapshot.models]
        finally:
            await service.close()
    if args.command == "execute":
        service = _service(args.url)
        try:
            request = ExecutionRequest(args.text, task=args.task or "", model=args.model, billing_mode=args.billing_mode, timeout=args.timeout)
            result = await service.execute(request)
            return _json_value(result)
        finally:
            await service.close()
    if args.command == "batch":
        input_path = Path(args.input)
        output_path = Path(args.output)
        if _same_file_path(input_path, output_path):
            raise ValueError("batch input and output must refer to different files")
        service = _service(args.url, concurrency=args.workers)
        try:
            request = ExecutionRequest("", task=args.task or "", model=args.model, billing_mode=args.billing_mode, timeout=args.timeout)
            items = (
                BatchItem(index, args.template.format_map({**record, "value": record.get(args.field, "")}), str(record.get("key", "")))
                for index, record in enumerate(read_jsonl(input_path))
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with output_path.open("w", encoding="utf-8") as handle:
                async for item in service.execute_batch(request, items):
                    value = {
                        "index": item.index,
                        "key": item.key,
                        "outcome": item.outcome,
                        "result": item.result,
                        "error": item.error,
                        "attempts": item.attempts,
                    }
                    write_jsonl_item(handle, _json_value(value))
                    count += 1
                    if count % 100 == 0:
                        handle.flush()
            return {"processed": count}
        finally:
            await service.close()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opengate")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("ensure", help="probe or start local OpenCode Serve")
    check.add_argument("--hostname", default="127.0.0.1")
    check.add_argument("--port", type=int, default=4096)
    check.add_argument("--timeout", type=float, default=20)
    check.add_argument("--no-start", action="store_true")
    sub.add_parser("presets", help="list task presets")
    models = sub.add_parser("models", help="read the live model catalog")
    models.add_argument("--url")
    execute = sub.add_parser("execute", help="execute one prompt through the model plan")
    execute.add_argument("text")
    execute.add_argument("--task", choices=sorted(PRESETS))
    execute.add_argument("--model", default="")
    execute.add_argument("--billing-mode", choices=["free-first", "free-only", "paid-only", "strict-model"], default="free-first")
    execute.add_argument("--url")
    execute.add_argument("--timeout", type=float, default=180)
    batch = sub.add_parser("batch", help="execute an async JSONL batch")
    batch.add_argument("input")
    batch.add_argument("output")
    batch.add_argument("--field", default="text")
    batch.add_argument("--template", default="{text}")
    batch.add_argument("--task", choices=sorted(PRESETS))
    batch.add_argument("--model", default="")
    batch.add_argument("--billing-mode", choices=["free-first", "free-only", "paid-only"], default="free-first")
    batch.add_argument("--url")
    batch.add_argument("--workers", type=int, default=2)
    batch.add_argument("--timeout", type=float, default=180)
    init = sub.add_parser("init", help="create a project MCP adapter scaffold")
    init.add_argument("target")
    init.add_argument("--name", required=True)
    init.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "ensure":
            value = ensure_server(args.hostname, args.port, args.timeout, no_start=args.no_start)
        elif args.command == "presets":
            value = {name: preset.__dict__ for name, preset in PRESETS.items()}
        elif args.command == "init":
            value = {"created": [str(path) for path in init_project(Path(args.target), args.name, force=args.force)]}
        else:
            value = asyncio.run(_run_async(args))
        print(json.dumps(_json_value(value), ensure_ascii=False, indent=2))
        status = value.get("status", "online") if isinstance(value, dict) else "online"
        return 0 if status not in {"offline", "failed"} else 1
    except (ExecutionError, RuntimeError, ValueError, TypeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": getattr(exc, "kind", "error"), "diagnostic": _json_value(getattr(exc, "diagnostic", None))}, ensure_ascii=False), file=sys.stderr)
        return 1
