from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Literal

from ._values import dump_sdk_value


BillingMode = Literal["free-first", "free-only", "paid-only", "strict-model"]
BILLING_MODES: tuple[str, ...] = ("free-first", "free-only", "paid-only", "strict-model")

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


class NoCandidatePlanError(RuntimeError):
    """The catalog contains no model allowed by the requested plan."""


class CatalogParseError(ValueError):
    """The provider catalog does not have the shape required for planning."""


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


def _catalog_mapping(value: Any, path: str, *, none_is_empty: bool = False) -> dict[str, Any]:
    data = dump_sdk_value(value)
    if data is None and none_is_empty:
        return {}
    if not isinstance(data, dict):
        raise CatalogParseError(f"{path} must be an object")
    return data


def parse_catalog(payload: Any) -> list[ModelInfo]:
    data = _catalog_mapping(payload, "catalog")
    providers = data.get("providers")
    if not isinstance(providers, list):
        raise CatalogParseError("catalog.providers must be an array")
    result: list[ModelInfo] = []
    for provider_index, raw_provider in enumerate(providers):
        provider_path = f"catalog.providers[{provider_index}]"
        provider = _catalog_mapping(raw_provider, provider_path)
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or not provider_id:
            raise CatalogParseError(f"{provider_path}.id must be a non-empty string")
        models = provider.get("models")
        if not isinstance(models, dict):
            raise CatalogParseError(f"{provider_path}.models must be an object")
        for model_id, raw_model in models.items():
            model_path = f"{provider_path}.models.{model_id}"
            if not isinstance(model_id, str) or not model_id:
                raise CatalogParseError(f"{provider_path}.models keys must be non-empty strings")
            model = _catalog_mapping(raw_model, model_path)
            nested_capabilities = _catalog_mapping(model.get("capabilities"), f"{model_path}.capabilities", none_is_empty=True)
            cost = _catalog_mapping(model.get("cost"), f"{model_path}.cost", none_is_empty=True)
            limit = _catalog_mapping(model.get("limit"), f"{model_path}.limit", none_is_empty=True)
            modalities = _catalog_mapping(model.get("modalities"), f"{model_path}.modalities", none_is_empty=True)
            raw_input_modalities = modalities.get("input")
            if raw_input_modalities is None:
                input_modalities = []
            elif isinstance(raw_input_modalities, (list, tuple)):
                input_modalities = raw_input_modalities
            else:
                raise CatalogParseError(f"{model_path}.modalities.input must be an array")

            raw_context = limit.get("context")
            if raw_context is None:
                context = 0
            elif isinstance(raw_context, bool) or not isinstance(raw_context, (int, float)):
                raise CatalogParseError(f"{model_path}.limit.context must be a non-negative integer")
            elif isinstance(raw_context, int):
                context = raw_context
            elif math.isfinite(raw_context) and raw_context.is_integer():
                context = int(raw_context)
            else:
                raise CatalogParseError(f"{model_path}.limit.context must be a non-negative integer")
            if context < 0:
                raise CatalogParseError(f"{model_path}.limit.context must be a non-negative integer")

            for price_field in ("input", "output"):
                if price_field not in cost:
                    continue
                price = cost[price_field]
                if isinstance(price, bool) or not isinstance(price, (int, float)):
                    raise CatalogParseError(f"{model_path}.cost.{price_field} must be a finite non-negative number")
                if (isinstance(price, float) and not math.isfinite(price)) or price < 0:
                    raise CatalogParseError(f"{model_path}.cost.{price_field} must be a finite non-negative number")
            capabilities = {
                "reasoning": bool(model.get("reasoning") or nested_capabilities.get("reasoning")),
                "attachment": bool(model.get("attachment") or nested_capabilities.get("attachment")),
                "input": {"image": "image" in input_modalities},
            }
            status = str(model.get("status") or "active")
            if status == "deprecated":
                continue
            result.append(ModelInfo(f"{provider_id}/{model_id}", str(model.get("name") or model_id), context, capabilities, cost, status))
    return [item for item in result if item.status == "active"]


def snapshot_catalog(payload: Any) -> CatalogSnapshot:
    data = _catalog_mapping(payload, "catalog")
    defaults = _catalog_mapping(data.get("default"), "catalog.default", none_is_empty=True)
    return CatalogSnapshot(datetime.now(timezone.utc), tuple(parse_catalog(data)), defaults)


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


def _explicit_model_is_free(snapshot: CatalogSnapshot, model: str) -> bool:
    model_info = snapshot.by_key().get(model)
    return model in FREE_MODEL_KEYS or (model_info is not None and model_info.is_free)


def explicit_model_satisfies_billing_mode(snapshot: CatalogSnapshot, model: str, billing_mode: BillingMode) -> bool:
    if billing_mode not in {"free-only", "paid-only"}:
        return True
    is_free = _explicit_model_is_free(snapshot, model)
    if billing_mode == "free-only":
        return is_free
    model_info = snapshot.by_key().get(model)
    return model_info is not None and not is_free


def build_candidate_plan(snapshot: CatalogSnapshot, *, task: str = "", model: str = "", billing_mode: BillingMode = "free-first") -> CandidatePlan:
    if billing_mode not in BILLING_MODES:
        raise ValueError(f"Unknown billing mode: {billing_mode}")
    if model:
        if not explicit_model_satisfies_billing_mode(snapshot, model, billing_mode):
            raise ValueError(f"Model {model} does not satisfy {billing_mode} billing mode")
        if _explicit_model_is_free(snapshot, model):
            return CandidatePlan((model,), ())
        return CandidatePlan((), (model,))
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
        raise NoCandidatePlanError(f"No active model satisfies preset {task}" if task else "No model candidates available")
    return CandidatePlan(tuple(free), tuple(paid))


def choose_models(catalog: Iterable[ModelInfo], task: str) -> list[str]:
    snapshot = CatalogSnapshot(datetime.now(timezone.utc), tuple(catalog), {})
    return list(build_candidate_plan(snapshot, task=task).ordered)
