import json

import pytest

from opengate.scaffold import init_project


def test_scaffold_creates_mcp_adapter_and_config(tmp_path):
    created = init_project(tmp_path, "Demo Project")
    assert {path.name for path in created} == {"project_mcp.py", ".env.opengate.example", "requirements.opengate.txt", "mcp-config.example.json"}
    config = json.loads((tmp_path / "mcp-config.example.json").read_text(encoding="utf-8"))
    assert config["mcpServers"]["demo-project"]["args"] == ["project_mcp.py"]
    adapter = (tmp_path / "project_mcp.py").read_text(encoding="utf-8")
    assert "create_mcp_server" in adapter
    assert '"parameters": parameters' in adapter
    assert "project_get_task_result" in adapter
    assert (tmp_path / "requirements.opengate.txt").read_text(encoding="utf-8") == "opengate-mcp[mcp]\n"


def test_scaffold_does_not_overwrite_by_default(tmp_path):
    (tmp_path / "project_mcp.py").write_text("keep", encoding="utf-8")
    (tmp_path / ".env.opengate.example").unlink(missing_ok=True)
    with pytest.raises(FileExistsError):
        init_project(tmp_path, "Demo Project")
    assert (tmp_path / "project_mcp.py").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ".env.opengate.example").exists()
