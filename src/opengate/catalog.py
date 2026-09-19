from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal


BillingMode = Literal["free-first", "free-only", "paid-only", "strict-model"]

FREE_MODEL_KEYS: tuple[str, ...] = (
    "opencode/big-pickle",
    "opencode/mimo-v2.5-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/muse-spark-1.3-contributor-free",
)

FREE_MODEL_NAMES: tuple[str, ...] = (
    "Big Pickle",
    "MiMo-V2.5 Free",
    "Ling 3.0 Flash Fin Free",
    "Nemotron 3 Ultra Free",
    "Nemotron 3.5 Lightning Free",
    "Muse Spark 1.3 Contributor Free",
)

_FREE_NAME_ALIASES = {
    "bigpickle": "opencode/big-pickle",
    "mimov25free": "opencode/mimo-v2.5-free",
    "ling30flashfinfree": "opencode/ling-3.0-flash-fin-free",
    "nemotron3ultrafree": "opencode/nemotron-3-ultra-free",
    "nemotron35lightningfree": "opencode/nemotron-3.5-lightning-free",
    "musespark13contributorfree": "opencode/muse-spark-1.3-contributor-free",
    "musespark13free": "opencode/muse-spark-1.3-contributor-free",
}


@dataclass(frozen=True)
class ModelInfo:
    key: str
    name: str
    context: int
    capabilities: dict[str, Any]
    cost: dict[str, Any]
    status: str = "active"

    @property
    def is_free(self) -> bool:
        return self.key in FREE_MODEL_KEYS or _normalise_name(self.name) in _FREE_NAME_ALIASES


@dataclass(frozen=True)
class Preset:
    name: str
    min_context: int
    reasoning: bool = False
    attachment: bool = False
    prefer_low_cost: bool = False


@dataclass(frozen=True)
class CatalogSnapshot:
    captured_at: datetime
    models: tuple[ModelInfo, ...]
    defaults: dict[str, str]

    def by_key(self) -> dict[str, ModelInfo]:
        return {model.key: model for model in self.models}


@dataclass(frozen=True)
class CandidatePlan:
    free: tuple[str, ...]
    paid: tuple[str, ...]

    @property
    def ordered(self) -> tuple[str, ...]:
        return self.free + self.paid


PRESETS: dict[str, Preset] = {
    "fast-extraction": Preset("fast-extraction", 128_000, prefer_low_cost=True),
    "bulk-classification": Preset("bulk-classification", 200_000, prefer_low_cost=True),
    "long-document-classification": Preset("long-document-classification", 256_000),
    "deep-analysis": Preset("deep-analysis", 200_000, reasoning=True),
    "synthesis": Preset("synthesis", 500_000, reasoning=True),
    "million-synthesis": Preset("million-synthesis", 900_000, reasoning=True),
    "multimodal": Preset("multimodal", 128_000, attachment=True, prefer_low_cost=True),
}

PREFERRED_MODELS: dict[str, tuple[str, ...]] = {
    "fast-extraction": ("opencode/big-pickle", "opencode/ling-3.0-flash-fin-free", "opencode/mimo-v2.5-free"),
    "bulk-classification": ("opencode/big-pickle", "opencode/ling-3.0-flash-fin-free", "opencode/nemotron-3.5-lightning-free", "opencode/mimo-v2.5-free"),
    "long-document-classification": ("opencode/nemotron-3.5-lightning-free", "opencode/ling-3.0-flash-fin-free", "opencode/mimo-v2.5-free"),
    "deep-analysis": ("opencode/nemotron-3-ultra-free", "opencode/mimo-v2.5-free", "opencode/muse-spark-1.3-contributor-free"),
    "synthesis": ("opencode/nemotron-3-ultra-free", "opencode/mimo-v2.5-free", "opencode/muse-spark-1.3-contributor-free"),
    "million-synthesis": ("opencode/nemotron-3-ultra-free", "opencode/muse-spark-1.3-contributor-free", "opencode/mimo-v2.5-free"),
    "multimodal": ("opencode/mimo-v2.5-free", "opencode/muse-spark-1.3-contributor-free"),
}


def _normalise_name(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _as_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    return value


def parse_catalog(payload: Any) -> list[ModelInfo]:
    data = _as_dict(payload)
    providers = data.get("providers", []) if isinstance(data, dict) else []
    result: list[ModelInfo] = []
    for provider in providers:
        provider = _as_dict(provider)
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        models = provider.get("models") or {}
        for model_id, raw_model in models.items() if isinstance(models, dict) else []:
            model = _as_dict(raw_model)
            if not isinstance(model, dict):
                continue
            nested_capabilities = _as_dict(model.get("capabilities") or {})
            cost = _as_dict(model.get("cost") or {})
            limit = _as_dict(model.get("limit") or {})
            modalities = _as_dict(model.get("modalities") or {})
            capabilities = {
                "reasoning": bool(model.get("reasoning") or nested_capabilities.get("reasoning")),
                "attachment": bool(model.get("attachment") or nested_capabilities.get("attachment")),
                "input": {"image": "image" in ((_as_dict(modalities).get("input") or []))},
            }
            status = str(model.get("status") or "active")
            if status == "deprecated":
                continue
            result.append(ModelInfo(f"{provider_id}/{model_id}", str(model.get("name") or model_id), int(limit.get("context") or 0), capabilities, cost if isinstance(cost, dict) else {}, status))
    return [item for item in result if item.status == "active"]


def snapshot_catalog(payload: Any) -> CatalogSnapshot:
    data = _as_dict(payload)
    defaults = data.get("default") if isinstance(data, dict) else {}
    return CatalogSnapshot(datetime.now(timezone.utc), tuple(parse_catalog(data)), dict(defaults or {}))


def _eligible(model: ModelInfo, preset: Preset | None) -> bool:
    if preset is None or model.context == 0:
        return True
    if model.context < preset.min_context:
        return False
    if preset.reasoning and not model.capabilities.get("reasoning"):
        return False
    if preset.attachment and not (model.capabilities.get("attachment") or model.capabilities.get("input", {}).get("image")):
        return False
    return True


def _sort_key(model: ModelInfo, preset: Preset | None, preferred: tuple[str, ...]) -> tuple[int, float, int, str]:
    preference = preferred.index(model.key) if model.key in preferred else 99
    cost = float(model.cost.get("input") or 0) + float(model.cost.get("output") or 0)
    return preference, cost if preset and preset.prefer_low_cost else 0, -model.context, model.key


def build_candidate_plan(snapshot: CatalogSnapshot, *, task: str = "", model: str = "", billing_mode: BillingMode = "free-first") -> CandidatePlan:
    if model:
        if billing_mode == "paid-only":
            return CandidatePlan((), (model,))
        return CandidatePlan((model,), ())
    preset = PRESETS.get(task) if task else None
    if task and preset is None:
        raise ValueError(f"Unknown OpenGate task preset: {task}")
    preferred = PREFERRED_MODELS.get(task, ())
    models = [item for item in snapshot.models if _eligible(item, preset)]
    present_keys = {item.key for item in snapshot.models}
    known = {item.key: item for item in models}
    for key in FREE_MODEL_KEYS:
        # A catalog gap is advisory. An explicitly catalogued but incompatible
        # model remains excluded by the preset.
        if key not in present_keys:
            known.setdefault(key, ModelInfo(key, key.rsplit("/", 1)[-1], 0, {}, {}))
    models = list(known.values())
    models.sort(key=lambda item: _sort_key(item, preset, preferred))
    free = [item.key for item in models if item.is_free]
    paid = [item.key for item in models if not item.is_free]
    if billing_mode == "free-only":
        paid = []
    elif billing_mode == "paid-only":
        free = []
    if not free and not paid:
        raise RuntimeError(f"No active model satisfies preset {task}" if task else "No model candidates available")
    return CandidatePlan(tuple(free), tuple(paid))


def choose_models(catalog: Iterable[ModelInfo], task: str) -> list[str]:
    snapshot = CatalogSnapshot(datetime.now(timezone.utc), tuple(catalog), {})
    return list(build_candidate_plan(snapshot, task=task).ordered)
