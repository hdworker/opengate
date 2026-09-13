from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from typing import Any


def ensure_server(hostname: str = "127.0.0.1", port: int = 4096, timeout: float = 20, *, no_start: bool = False) -> dict[str, Any]:
    """Probe or start OpenCode Serve without exposing it beyond loopback."""
    url = f"http://{hostname}:{port}"

    def probe() -> bool:
        try:
            request = urllib.request.Request(url + "/config/providers", headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                json.loads(response.read().decode("utf-8"))
                return True
        except Exception:
            return False

    started = False
    if not probe() and not no_start:
        executable = shutil.which("opencode")
        if not executable:
            return {"status": "offline", "url": url, "error": "opencode executable not found on PATH"}
        options: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        subprocess.Popen([executable, "serve", "--hostname", hostname, "--port", str(port)], **options)
        started = True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return {"status": "started" if started else "already_running", "url": url, "health": "ok"}
        time.sleep(0.4)
    return {"status": "offline", "url": url, "error": "OpenCode Serve did not become ready"}

