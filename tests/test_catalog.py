from opengate.catalog import FREE_MODEL_KEYS, ModelInfo, build_candidate_plan, parse_catalog, snapshot_catalog


def test_parse_sdk_shaped_catalog_and_free_first_plan():
    snapshot = snapshot_catalog({"default": {"opencode": "opencode/big-pickle"}, "providers": [{"id": "opencode", "models": {
        "big-pickle": {"name": "Big Pickle", "limit": {"context": 128000}, "cost": {"input": 0, "output": 0}},
        "paid": {"name": "Paid", "limit": {"context": 600000}, "cost": {"input": 1, "output": 1}},
    }}]})
    plan = build_candidate_plan(snapshot, task="fast-extraction")
    assert plan.free[0] == "opencode/big-pickle"
    assert "opencode/paid" in plan.paid
    assert set(FREE_MODEL_KEYS).issubset(set(plan.free))


def test_catalog_gap_keeps_known_identity_callable():
    plan = build_candidate_plan(snapshot_catalog({"providers": []}), task="deep-analysis")
    assert plan.free
    assert "opencode/nemotron-3-ultra-free" in plan.free


def test_incompatible_catalogued_model_is_not_reintroduced():
    catalog = parse_catalog({"providers": [{"id": "x", "models": {
        "no-image": {"name": "No image", "limit": {"context": 200000}},
        "image": {"name": "Image", "limit": {"context": 200000}, "attachment": True},
    }}]})
    plan = build_candidate_plan(snapshot_catalog({"providers": [{"id": "x", "models": {
        "no-image": {"name": "No image", "limit": {"context": 200000}},
        "image": {"name": "Image", "limit": {"context": 200000}, "attachment": True},
    }}]}), task="multimodal")
    assert "x/image" in plan.ordered
    assert "x/no-image" not in plan.ordered
    assert len(catalog) == 2

