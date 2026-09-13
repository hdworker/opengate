from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelInfo:
    key: str
    name: str
    context: int
    capabilities: dict[str, Any]
    cost: dict[str, Any]
    status: str = "active"


@dataclass(frozen=True)
class Preset:
    name: str
    min_context: int
    reasoning: bool = False
    attachment: bool = False
    prefer_low_cost: bool = False


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
    "fast-extraction": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.8-flash", "opencode-go/deepseek-v4-flash"),
    "bulk-classification": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.8-flash", "opencode-go/qwen3.7-plus", "opencode-go/deepseek-v4-flash"),
    "long-document-classification": ("opencode-go/qwen3.7-plus", "opencode-go/qwen3.6-plus", "opencode-go/mimo-v2.5"),
    "deep-analysis": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.7-plus", "opencode-go/qwen3.8-flash", "opencode-go/minimax-m3"),
    "synthesis": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.7-plus", "opencode-go/qwen3.8-flash", "opencode-go/minimax-m3"),
    "million-synthesis": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.7-plus", "opencode-go/qwen3.6-plus", "opencode-go/minimax-m3"),
    "multimodal": ("opencode-go/mimo-v2.5", "opencode-go/qwen3.8-flash", "opencode/mimo-v2.5-free"),
}


def parse_catalog(payload: Any) -> list[ModelInfo]:
    providers = payload.get("providers", []) if isinstance(payload, dict) else []
    result: list[ModelInfo] = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            result.append(
                ModelInfo(
                    key=f"{provider.get('id')}/{model_id}",
                    name=str(model.get("name") or model_id),
                    context=int((model.get("limit") or {}).get("context") or 0),
                    capabilities=model.get("capabilities") or {},
                    cost=model.get("cost") or {},
                    status=str(model.get("status") or "active"),
                )
            )
    return [item for item in result if item.status == "active"]


def choose_models(catalog: Iterable[ModelInfo], task: str) -> list[str]:
    try:
        preset = PRESETS[task]
    except KeyError as exc:
        raise ValueError(f"Unknown OpenGate task preset: {task}") from exc
    preferred = PREFERRED_MODELS[task]
    eligible: list[tuple[int, float, int, str]] = []
    for model in catalog:
        if model.context < preset.min_context:
            continue
        if preset.reasoning and not model.capabilities.get("reasoning"):
            continue
        if preset.attachment and not (
            model.capabilities.get("attachment") or model.capabilities.get("input", {}).get("image")
        ):
            continue
        cost = float(model.cost.get("input") or 0) + float(model.cost.get("output") or 0)
        preference = preferred.index(model.key) if model.key in preferred else 99
        eligible.append((preference, cost if preset.prefer_low_cost else 0, -model.context, model.key))
    if not eligible:
        raise RuntimeError(f"No active model satisfies preset {task}")
    eligible.sort()
    return [item[3] for item in eligible]

