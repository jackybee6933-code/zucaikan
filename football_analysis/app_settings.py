"""Per-app tunables persisted next to the SQLite database."""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_SCRAPE_DELAY = 5.0


def app_settings_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path.cwd()
    return root / "data" / "app_settings.json"


def load_scrape_delay(path: str | Path | None = None) -> float:
    """Read scrape page interval in seconds; fall back to 5.0."""
    target = Path(path) if path else app_settings_path()
    if not target.exists():
        try:
            save_scrape_delay(DEFAULT_SCRAPE_DELAY, target)
        except OSError:
            pass
        return DEFAULT_SCRAPE_DELAY
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SCRAPE_DELAY
    if not isinstance(raw, dict):
        return DEFAULT_SCRAPE_DELAY
    try:
        value = float(raw.get("scrape_delay_seconds", DEFAULT_SCRAPE_DELAY))
    except (TypeError, ValueError):
        return DEFAULT_SCRAPE_DELAY
    return max(0.0, value)


def save_scrape_delay(value: float, path: str | Path | None = None) -> float:
    """Persist scrape page interval in seconds; clamp to >= 0."""
    try:
        delay = max(0.0, float(value))
    except (TypeError, ValueError):
        delay = DEFAULT_SCRAPE_DELAY
    target = Path(path) if path else app_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, object] = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, json.JSONDecodeError):
        pass
    raw["scrape_delay_seconds"] = delay
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return delay
