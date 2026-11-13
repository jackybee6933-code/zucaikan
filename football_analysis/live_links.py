from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


HOME_URL = "http://huakangsy.cn/zuqiu/"
CRZB_URL = "https://www.crzb.com"
BIG_FIVE_LEAGUES = frozenset(("英超", "西甲", "德甲", "意甲", "法甲"))
_CACHE_SECONDS = 60 * 60
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
    ("斯洛文尼", "斯洛文尼亚"),
    ("哈萨克", "哈薩克", "哈萨克斯坦"),
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


def _collect_huakang_links(rows: list[dict]) -> dict[str, str]:
    try:
        home_html = _fetch(HOME_URL)
    except requests.RequestException:
        return {}
    links: dict[tuple[str, str], str] = {}
    for league_url in parse_league_links(home_html, HOME_URL):
        try:
            league_html = _fetch(league_url)
        except requests.RequestException:
            continue
        links.update(parse_match_links(league_html, league_url, rows))
        if len(links) == len(rows):
            break
    return {
        str(row.get("match_no") or ""):
        links.get((
            _normalize(str(row.get("home_team") or "")),
            _normalize(str(row.get("away_team") or "")),
        ), "")
        for row in rows
        if links.get((
            _normalize(str(row.get("home_team") or "")),
            _normalize(str(row.get("away_team") or "")),
        ))
    }


def _fetch_crzb_json(path: str) -> list | dict:
    response = requests.get(
        CRZB_URL + "/api" + path,
        timeout=8,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/142.0.0.0 Safari/537.36",
            "Referer": CRZB_URL + "/score/football/lives",
            "Accept": "application/json",
            "webclienttype": "web", "clientType": "web", "ad-channel": "crzb",
            "channel": "channel_crzb", "site": "crzb", "timezone": "UTC+8",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if (not isinstance(payload, dict) or payload.get("code") != 200
            or not isinstance(payload.get("data"), (dict, list))):
        raise ValueError("超人直播接口未返回有效数据")
    return payload.get("data")


def _same_team(team: str, *names: str) -> bool:
    normalized = _normalize(team)
    aliases = _TEAM_ALIASES.get(normalized, {normalized})
    return bool(normalized) and any(_normalize(name) in aliases for name in names)


def _collect_crzb_links(rows: list[dict]) -> dict[str, str]:
    try:
        matches = _fetch_crzb_json("/web/web_football_score_list")
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(matches, list):
        return {}

    links: dict[str, str] = {}
    for row in rows:
        for match in matches:
            if not isinstance(match, dict) or str(match.get("is_zb")) != "1":
                continue
            if not all(_same_team(
                str(row.get(f"{side}_team") or ""),
                str(match.get(f"{side}_name_zh") or ""),
                str(match.get(f"{side}_name_zht") or ""),
            ) for side in ("home", "away")):
                continue
            if row.get("match_time"):
                try:
                    kickoff = datetime.fromtimestamp(
                        int(match["match_time_int"]), timezone(timedelta(hours=8))
                    )
                except (KeyError, ValueError, TypeError, OSError, OverflowError):
                    continue
                if kickoff.strftime("%m-%d %H:%M") != row["match_time"]:
                    continue
            match_id = str(match.get("tournament_id") or "")
            if not match_id.isdigit():
                continue
            try:
                data = _fetch_crzb_json(
                    f"/web/get_public_anchor?tournament_id={match_id}&type=1&tournament_type=1"
                )
            except (requests.RequestException, ValueError):
                continue
            anchors = data.get("more", []) if isinstance(data, dict) else []
            if not isinstance(anchors, list):
                continue
            for anchor in anchors:
                if not isinstance(anchor, dict):
                    continue
                member_id = str(anchor.get("member_id") or "")
                if (str(anchor.get("tournament_id")) == match_id
                        and str(anchor.get("tournament_type")) == "1"
                        and member_id.isdigit()):
                    links[str(row.get("match_no") or "")] = (
                        f"{CRZB_URL}/detail/1/{match_id}/{member_id}"
                    )
                    break
            if str(row.get("match_no") or "") in links:
                break
    return links


def _collect_links(rows: list[dict], issue: str) -> dict[str, str]:
    big_five_rows = [
        row for row in rows if str(row.get("league") or "").strip() in BIG_FIVE_LEAGUES
    ]
    links: dict[str, str] = {}
    if big_five_rows:
        try:
            from .cantonese import fetch_mandarin_matches

            mandarin_matches = fetch_mandarin_matches(issue)
        except Exception:
            mandarin_matches = {}
        match_rows = [
            {**row, "home_team": names[0], "away_team": names[1]}
            if (names := mandarin_matches.get(str(row.get("match_no") or ""))) else row
            for row in big_five_rows
        ]
        links = _collect_huakang_links(match_rows)
    missing = [row for row in rows if str(row.get("match_no") or "") not in links]
    if missing:
        links.update(_collect_crzb_links(missing))
    return links


def load_cached_live_links(
    rows: list[dict], issue: str, cache_dir: str | Path
) -> tuple[dict[tuple[str, str], str], bool, bool]:
    """Load per-issue links; return links, expired, and cache-exists."""
    if not issue.isdigit():
        return {}, False, False

    path = Path(cache_dir) / f"live_links_{issue}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload["fetched_at"])
        found = payload["links"]
        if payload.get("issue") != issue or not isinstance(found, dict):
            return {}, False, False
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {}, False, False

    links = {
        (issue, str(row.get("match_no") or "")): found[str(row.get("match_no") or "")]
        for row in rows
        if str(row.get("match_no") or "") in found
        and isinstance(found[str(row.get("match_no") or "")], str)
    }
    expired = time.time() - fetched_at >= _CACHE_SECONDS
    return links, expired, True


def live_links(
    rows: list[dict],
    issue: str,
    *,
    force: bool = False,
    cache_dir: str | Path | None = None,
) -> dict[tuple[str, str], str]:
    """Return live-room URLs keyed by issue and match number."""
    global _cache_expires
    now = time.monotonic()
    with _cache_lock:
        if not force and cache_dir is not None:
            cached, expired, exists = load_cached_live_links(rows, issue, cache_dir)
            if exists and not expired:
                return cached
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
        if cache_dir is not None and issue.isdigit():
            path = Path(cache_dir) / f"live_links_{issue}.json"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(
                        {"issue": issue, "fetched_at": time.time(), "links": found},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                temporary.replace(path)
            except OSError:
                pass
        return {
            (issue, str(row.get("match_no") or "")): found[str(row.get("match_no") or "")]
            for row in rows
            if str(row.get("match_no") or "") in found
        }
