import os
from datetime import datetime

import folder_paths

CATEGORY_MAP = {
    "checkpoints": ["checkpoints"],
    "diffusion_models": ["diffusion_models", "unet"],
    "loras": ["loras"],
    "embeddings": ["embeddings"],
    "clip": ["clip"],
    "text_encoders": ["text_encoders"],
    "vae": ["vae"],
    "controlnet": ["controlnet"],
    "upscale_models": ["upscale_models"],
}

SIDECAR_EXTS = (".txt", ".md", ".json")
SIDECAR_CHARS = 4000
METADATA_VALUE_CHARS = 700
LIST_LIMIT = 250


def _all_source_categories() -> list:
    sources = []
    for cats in CATEGORY_MAP.values():
        for cat in cats:
            if cat not in sources:
                sources.append(cat)
    return sources


def list_assets(category: str = "all") -> str:
    if category in (None, "", "all"):
        cats = _all_source_categories()
    elif category in CATEGORY_MAP:
        cats = CATEGORY_MAP[category]
    else:
        return "Unknown category '{}'. Use 'all' or one of: {}.".format(
            category, ", ".join(sorted(CATEGORY_MAP))
        )
    rows = []
    total = 0
    seen = set()
    for cat in cats:
        try:
            names = folder_paths.get_filename_list(cat)
        except KeyError:
            continue
        for name in names:
            path = folder_paths.get_full_path(cat, name)
            if not path or path.lower() in seen:
                continue
            seen.add(path.lower())
            try:
                stat = os.stat(path)
            except OSError:
                continue
            total += 1
            rows.append("{} | {} | {:.1f} MB | modified {}".format(
                cat, name, stat.st_size / 1_000_000,
                datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
            ))
    if not total:
        return "No model files found in scope: {}.".format(category)
    if len(rows) > LIST_LIMIT:
        rows = rows[:LIST_LIMIT] + ["... {} more not shown; narrow the category.".format(total - LIST_LIMIT)]
    return "{} assets:\n".format(total) + "\n".join(rows)


def _find_asset(category: str, name: str):
    if category and category != "all" and category not in CATEGORY_MAP:
        return None, None
    cats = CATEGORY_MAP.get(category, _all_source_categories()) if category else _all_source_categories()
    for cat in cats:
        try:
            path = folder_paths.get_full_path(cat, name)
        except KeyError:
            path = None
        if path:
            return cat, path
    return None, None


def get_asset_details(category: str, name: str) -> str:
    cat, path = _find_asset(category or "all", name)
    if not path:
        return "Not found: '{}' (category '{}'). Call list_local_models first and use exact names.".format(
            name, category or "all"
        )
    stat = os.stat(path)
    parts = [
        "# {} (category: {})".format(name, cat),
        "path: {}".format(path),
        "size: {:.1f} MB | modified {}".format(
            stat.st_size / 1_000_000, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
        ),
    ]
    if path.lower().endswith(".safetensors"):
        try:
            from safetensors import safe_open
            with safe_open(path, framework="pt") as handle:
                metadata = handle.metadata() or {}
        except Exception as exc:
            metadata = {}
            parts.append("(safetensors metadata unavailable: {})".format(exc))
        triggers = {k: v for k, v in metadata.items() if "trigger" in k.lower()}
        if triggers:
            parts.append("trigger words:")
            for key, value in sorted(triggers.items()):
                parts.append("  {} = {}".format(key, str(value)[:METADATA_VALUE_CHARS]))
        for key in sorted(metadata):
            if key in triggers:
                continue
            value = str(metadata[key])
            if len(value) > METADATA_VALUE_CHARS:
                value = value[:METADATA_VALUE_CHARS] + "...[truncated]"
            parts.append("{}: {}".format(key, value))
        if not metadata:
            parts.append("(no embedded safetensors metadata)")
    else:
        parts.append("(metadata is only readable from .safetensors files)")
    stem = path.rsplit(".", 1)[0]
    for ext in SIDECAR_EXTS:
        sidecar = stem + ext
        if not os.path.isfile(sidecar):
            continue
        try:
            with open(sidecar, "r", encoding="utf-8", errors="replace") as handle:
                body = handle.read(SIDECAR_CHARS)
        except OSError as exc:
            parts.append("sidecar {} unreadable: {}".format(os.path.basename(sidecar), exc))
            continue
        parts.append("--- sidecar {} ---".format(os.path.basename(sidecar)))
        parts.append(body)
    return "\n".join(parts)
