import json

from opengate.client import GatewayResult, OpenGateClient


def test_client_batch_uses_resumable_jsonl(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    source.write_text('{"body": "one"}\n{"body": "two"}\n', encoding="utf-8")
    client = OpenGateClient()
    prompts = []

    def fake_run(prompt, **kwargs):
        prompts.append(prompt)
        return GatewayResult(prompt.upper(), "session", "model", [])

    client.run = fake_run  # type: ignore[method-assign]
    assert client.run_batch(source, output, field="body", template="Analyze: {body}") == 2
    assert prompts == ["Analyze: one", "Analyze: two"]
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert all(row["ok"] for row in rows)

