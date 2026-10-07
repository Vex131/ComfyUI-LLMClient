import json
import os
from datetime import datetime, timezone

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")


def load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def update_cache(base_url: str, profile: str, models: list, context: int = None):
    cache = load_cache()
    cache[base_url] = {
        "profile": profile,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "context": context,
    }
    save_cache(cache)


def cached_entry(base_url: str) -> dict:
    return load_cache().get(base_url)


def cached_models(base_url: str) -> list:
    return load_cache().get(base_url, {}).get("models", [])


def all_cached_model_ids() -> list[str]:
    ids = []
    for entry in load_cache().values():
        for model in entry.get("models", []):
            model_id = model.get("id")
            if model_id and model_id not in ids:
                ids.append(model_id)
    return ids
