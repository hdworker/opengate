from opengate.catalog import ModelInfo, choose_models, parse_catalog


def test_parse_catalog_and_choose_live_models():
    catalog = parse_catalog({"providers": [{"id": "opencode-go", "models": {
        "small": {"name": "Small", "limit": {"context": 128000}, "cost": {"input": 1, "output": 1}},
        "large": {"name": "Large", "limit": {"context": 600000}, "capabilities": {"reasoning": True}},
    }}]})
    assert [item.key for item in catalog] == ["opencode-go/small", "opencode-go/large"]
    assert choose_models(catalog, "synthesis") == ["opencode-go/large"]


def test_attachment_capability_is_required():
    catalog = [ModelInfo("x/no-image", "No image", 200000, {}, {}), ModelInfo("x/image", "Image", 200000, {"attachment": True}, {})]
    assert choose_models(catalog, "multimodal") == ["x/image"]

