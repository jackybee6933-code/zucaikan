from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from .cdp import CdpPage
from .cdp_profile import ensure_cdp_chrome, shutdown_cdp_chrome


LIVE_ISSUE_URL = "https://live.500.com/zucai.php?e={issue}"
SELECT_CANTONESE_JS = """() => {
  const radio = document.querySelector('#ck_y_2');
  if (!radio) return '';
  if (!radio.checked) radio.click();
  if (!radio.checked) return '';
  const table = document.querySelector('#table_match');
  return table ? table.outerHTML : '';
}"""


def parse_cantonese_matches(page_html: str) -> dict[str, tuple[str, str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.find("table", id="table_match")
    if table is None:
        return {}

    names: dict[str, tuple[str, str]] = {}
    for row in table.find_all("tr", id=re.compile(r"^a\d+$")):
        fixture_id = row.get("id", "")[1:]
        teams = [
            link.get_text(" ", strip=True)
            for link in row.select('a[href*="/team/"]')
            if link.get_text(" ", strip=True)
        ]
        if len(teams) >= 2:
            names[fixture_id] = (teams[0], teams[1])
    return names


def fetch_cantonese_matches(issue: str) -> dict[str, tuple[str, str]]:
    error = ensure_cdp_chrome()
    if error:
        raise RuntimeError(error)

    page = None
    try:
        page = CdpPage()
        page.goto(LIVE_ISSUE_URL.format(issue=issue))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            table_html = page.evaluate(SELECT_CANTONESE_JS)
            names = parse_cantonese_matches(table_html or "")
            if names:
                return names
            time.sleep(1)
        raise RuntimeError(f"500 粤语比分页未解析到球队：{issue}")
    finally:
        try:
            if page is not None:
                page.close()
        finally:
            shutdown_cdp_chrome()
