from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _find_chrome() -> str | None:
    path_env = shutil.which("chrome")
    if path_env:
        return path_env
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def open_in_chrome(url_or_path: str | Path) -> None:
    """Open a local file path or URL in Chrome when available, else fall back to the system browser."""
    chrome = _find_chrome()
    target = str(url_or_path)
    if not target.startswith("http") and not Path(target).exists():
        target = Path(target).as_uri()
    if chrome:
        subprocess.Popen([chrome, target], shell=False)
        return
    webbrowser.open(target)
