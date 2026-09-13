import json

from opengate.jsonl import run_jsonl


def test_jsonl_resumes_existing_indexes(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    source.write_text('{"value": 1}\n{"value": 2}\n', encoding="utf-8")
    output.write_text('{"index": 0, "result": 10}\n', encoding="utf-8")
    seen = []

    def process(record):
        seen.append(record["value"])
        return {"result": record["value"] * 10}

    assert run_jsonl(source, output, process) == 2
    assert seen == [2]
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"index": 0, "result": 10}, {"index": 1, "result": 20}]

