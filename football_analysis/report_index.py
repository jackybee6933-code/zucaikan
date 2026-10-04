from __future__ import annotations

import re
from pathlib import Path

_ISSUE_HTML_RE = re.compile(r"^issue_(\d+)_\d{8}_\d{6}\.html$")
_LATEST_HTML_RE = re.compile(r"^issue_(\d+)_(\d{8})_(\d{6})\.html$")


def build_issue_report_index(reports_dir: Path) -> dict[str, str]:
    """Map issue number to the newest browser report filename in reports_dir."""
    index: dict[str, str] = {}
    if not reports_dir.is_dir():
        return index
    for path in reports_dir.iterdir():
        match = _ISSUE_HTML_RE.match(path.name)
        if not match:
            continue
        ts = _LATEST_HTML_RE.match(path.name)
        key = match.group(1)
        stamp = ts.group(2) + ts.group(3) if ts else ""
        current = index.get(key)
        current_stamp = ""
        if current:
            ts = _LATEST_HTML_RE.match(current)
            if ts:
                current_stamp = ts.group(2) + ts.group(3)
        if stamp >= current_stamp:
            index[key] = path.name
    return index
