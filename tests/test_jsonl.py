import pytest

from opengate.jsonl import read_jsonl


def test_jsonl_reader_does_not_consult_existing_output(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text('{"value": 1}\n{"value": 2}\n', encoding="utf-8")
    assert list(read_jsonl(source)) == [{"value": 1}, {"value": 2}]


def test_jsonl_reader_rejects_non_object(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text('[1]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        list(read_jsonl(source))
