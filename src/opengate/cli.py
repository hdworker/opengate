from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bootstrap import ensure_server
from .client import OpenGateClient
from .catalog import PRESETS
from .errors import GatewayError
from .scaffold import init_project


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
    models.add_argument("--url", default="http://127.0.0.1:4096")
    prompt = sub.add_parser("prompt", help="run one prompt")
    prompt.add_argument("text")
    prompt.add_argument("--task", choices=sorted(PRESETS))
    prompt.add_argument("--model", default="")
    prompt.add_argument("--url", default="http://127.0.0.1:4096")
    prompt.add_argument("--timeout", type=float, default=180)
    prompt.add_argument("--keep-session", action="store_true")
    batch = sub.add_parser("batch", help="run a resumable JSONL batch")
    batch.add_argument("input")
    batch.add_argument("output")
    batch.add_argument("--field", default="text")
    batch.add_argument("--template", default="{text}")
    batch.add_argument("--task", choices=sorted(PRESETS))
    batch.add_argument("--model", default="")
    batch.add_argument("--url", default="http://127.0.0.1:4096")
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
        elif args.command == "models":
            value = [item.__dict__ for item in OpenGateClient(args.url).catalog()]
        elif args.command == "init":
            value = {"created": [str(path) for path in init_project(Path(args.target), args.name, force=args.force)]}
        elif args.command == "batch":
            value = {"processed": OpenGateClient(args.url).run_batch(Path(args.input), Path(args.output), field=args.field, template=args.template, task=args.task or "", model=args.model, workers=args.workers, timeout=args.timeout)}
        else:
            value = OpenGateClient(args.url).run(args.text, task=args.task or "", model=args.model, timeout=args.timeout, keep_session=args.keep_session).__dict__
        print(json.dumps(value, ensure_ascii=False, indent=2))
        status = value.get("status", "online") if isinstance(value, dict) else "online"
        return 0 if status not in {"offline", "failed"} else 1
    except (GatewayError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": getattr(exc, "kind", "error")}, ensure_ascii=False), file=sys.stderr)
        return 1
