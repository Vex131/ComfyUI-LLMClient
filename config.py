import json
import logging
import os
from datetime import datetime, timezone

import folder_paths

CACHE_OVERRIDE = os.environ.get("COMFYUI_LLMCLIENT_CACHE_PATH")
CACHE_PATH = os.path.join(
    CACHE_OVERRIDE or os.path.join(folder_paths.get_user_directory(), "llmclient"),
    "providers.json",
)
LEGACY_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")


def migrate_legacy_cache(cache: dict):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(CACHE_PATH)), exist_ok=True)
        with open(CACHE_PATH, "x", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except FileExistsError:
        pass
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not migrate LLMClient provider cache to %s: %s", CACHE_PATH, exc)


def load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except FileNotFoundError:
        if not CACHE_OVERRIDE:
            try:
                with open(LEGACY_CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if isinstance(cache, dict):
                    migrate_legacy_cache(cache)
                    return cache
                return {}
            except (OSError, ValueError):
                pass
        return {}
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict):
    os.makedirs(os.path.dirname(os.path.abspath(CACHE_PATH)), exist_ok=True)
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
