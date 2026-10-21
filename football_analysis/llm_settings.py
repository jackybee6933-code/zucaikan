"""Local LLM endpoint settings used by the query page."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FIELDS = ("api_key", "base_url", "model")
DEFAULTS = {
    "primary": {"api_key": "", "base_url": "", "model": ""},
    "fallback": {"api_key": "", "base_url": "", "model": ""},
}


def default_settings_path() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    return root / "data" / "llm_settings.json"


def _clean_endpoint(value: object) -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {field: str(data.get(field) or "").strip() for field in FIELDS}


def load_settings(path: str | Path | None = None) -> dict[str, dict[str, str]]:
    target = Path(path) if path else default_settings_path()
    result = {name: dict(values) for name, values in DEFAULTS.items()}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    for name in result:
        result[name].update(_clean_endpoint(raw.get(name)))
    return result


def save_settings(
    settings: dict[str, dict[str, object]],
    path: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    target = Path(path) if path else default_settings_path()
    current = load_settings(target)
    result = {name: dict(values) for name, values in current.items()}
    for name in result:
        incoming = settings.get(name) if isinstance(settings, dict) else {}
        if not isinstance(incoming, dict):
            continue
        for field in FIELDS:
            if field in incoming:
                result[name][field] = str(incoming.get(field) or "").strip()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def public_settings(path: str | Path | None = None) -> dict:
    """Return editable settings without exposing stored API keys."""
    saved = load_settings(path)
    output: dict[str, dict[str, object]] = {}
    for name, env_prefix in (("primary", "LLM"), ("fallback", "LLM_FALLBACK")):
        item = saved[name]
        key = item["api_key"] or os.environ.get(f"{env_prefix}_API_KEY", "")
        output[name] = {
            "base_url": item["base_url"] or os.environ.get(f"{env_prefix}_BASE_URL", ""),
            "model": item["model"] or os.environ.get(f"{env_prefix}_MODEL", ""),
            "api_key_configured": bool(key),
            "api_key_hint": f"••••{key[-4:]}" if key else "",
        }
    return output
