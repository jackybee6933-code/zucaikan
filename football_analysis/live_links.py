from __future__ import annotations

import re
import threading
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


HOME_URL = "http://huakangsy.cn/zuqiu/"
_CACHE_SECONDS = 300
_cache_lock = threading.Lock()
_cache_expires = 0.0
_cache_links: dict[str, dict[str, str]] = {}


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


_TEAM_ALIAS_GROUPS = (
    ("曼联", "曼彻斯特联"),
    ("维拉", "阿斯顿维拉"),
    ("国米", "国际米兰"),
    ("马竞", "马竞技", "马德里竞技"),
    ("巴萨", "巴塞罗那"),
    ("维拉利尔", "維拉利爾", "比利亚雷亚尔"),
    ("加泰", "赫塔菲", "赫塔费"),
    ("斯图加", "史特加", "斯图加特"),
    ("不来梅", "雲達不萊梅", "云达不莱梅"),
)
_TEAM_ALIASES = {
    _normalize(alias): frozenset(_normalize(name) for name in group)
    for group in _TEAM_ALIAS_GROUPS
    for alias in group
}


def _team_in_text(team: str, text: str) -> bool:
    team_norm = _normalize(team)
    text_norm = _normalize(text)
    if not team_norm:
        return False
    if team_norm in text_norm:
        return True
    return any(
        alias in text_norm for alias in _TEAM_ALIASES.get(team_norm, ())
    )


def _first_source_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(
        r"/zuqiu/(\d+)(?:_1)?\.html?$", parsed.path, flags=re.IGNORECASE
    )
    if not match:
        return url
    return parsed._replace(path=f"/play/{match.group(1)}_1.html").geturl()


def parse_match_links(
    page_html: str, base_url: str, rows: list[dict]
) -> dict[tuple[str, str], str]:
    soup = BeautifulSoup(page_html, "html.parser")
    links: dict[tuple[str, str], str] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, str(anchor.get("href") or ""))
        if urlparse(href).netloc != urlparse(HOME_URL).netloc:
            continue
        if not urlparse(href).path.lower().endswith((".html", ".htm")):
            continue

        title = str(anchor.get("title") or "")
        for row in rows:
            home = str(row.get("home_team") or "")
            away = str(row.get("away_team") or "")
            if _team_in_text(home, title) and _team_in_text(away, title):
                links[(_normalize(home), _normalize(away))] = _first_source_url(href)
    return links


def parse_league_links(page_html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    base_path = urlparse(HOME_URL).path.rstrip("/") + "/"
    league_urls = []
    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, str(anchor.get("href") or ""))
        parsed = urlparse(url)
        relative_path = parsed.path.removeprefix(base_path).strip("/")
        if (
            parsed.netloc == urlparse(HOME_URL).netloc
            and parsed.path.startswith(base_path)
            and relative_path
            and "/" not in relative_path
            and not relative_path.lower().endswith((".html", ".htm"))
        ):
            league_urls.append(url)
    return list(dict.fromkeys(league_urls))[:6]


def _fetch(url: str) -> str:
    response = requests.get(
        url,
        timeout=2,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return response.text


def _collect_links(rows: list[dict], issue: str) -> dict[str, str]:
    try:
        home_html = _fetch(HOME_URL)
    except requests.RequestException:
        return {}
    try:
        from .cantonese import fetch_mandarin_matches

        mandarin_matches = fetch_mandarin_matches(issue)
    except Exception:
        return {}

    mandarin_rows = [
        {
            **row,
            "home_team": mandarin_matches.get(str(row.get("match_no") or ""), ("", ""))[0],
            "away_team": mandarin_matches.get(str(row.get("match_no") or ""), ("", ""))[1],
        }
        for row in rows
        if str(row.get("match_no") or "") in mandarin_matches
    ]
    if not mandarin_rows:
        return {}

    links: dict[tuple[str, str], str] = {}
    for league_url in parse_league_links(home_html, HOME_URL):
        try:
            league_html = _fetch(league_url)
        except requests.RequestException:
            continue
        links.update(parse_match_links(league_html, league_url, mandarin_rows))
        if len(links) == len(mandarin_rows):
            break
    return {
        str(row.get("match_no") or ""):
        links.get((
            _normalize(str(row.get("home_team") or "")),
            _normalize(str(row.get("away_team") or "")),
        ), "")
        for row in mandarin_rows
        if links.get((
            _normalize(str(row.get("home_team") or "")),
            _normalize(str(row.get("away_team") or "")),
        ))
    }


def live_links(
    rows: list[dict], issue: str, *, force: bool = False
) -> dict[tuple[str, str], str]:
    """Return Huakang match-detail URLs keyed by normalized home/away names."""
    global _cache_expires
    now = time.monotonic()
    with _cache_lock:
        if not force and now < _cache_expires and issue in _cache_links:
            found = _cache_links[issue]
            return {
                (issue, str(row.get("match_no") or "")): found[str(row.get("match_no") or "")]
                for row in rows
                if str(row.get("match_no") or "") in found
            }
    found = _collect_links(rows, issue)
    with _cache_lock:
        _cache_links[issue] = found
        _cache_expires = time.monotonic() + _CACHE_SECONDS
        return {
            (issue, str(row.get("match_no") or "")): found[str(row.get("match_no") or "")]
            for row in rows
            if str(row.get("match_no") or "") in found
        }
