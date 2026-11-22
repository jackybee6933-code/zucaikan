from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .console import task_log

HOME_URL = "https://www.fmtg2f.com/score/football/lives"
HUAKANG_URL = "https://www.fmtg2f.com"
CRZB_URL = "https://crl4.crzbcx8.com"
CRZB_BACKUP_URL = "https://www.crzb.com"
CRZB_SECOND_BACKUP_URL = "https://www.crtv0.live"
_CACHE_SECONDS = 60 * 60
_cache_lock = threading.Lock()
_cache_expires = 0.0
LiveLink = str | list[dict[str, str]]
_cache_links: dict[str, dict[str, LiveLink]] = {}


def live_sources(value: LiveLink) -> list[dict[str, str]]:
    """兼容旧单链接缓存，只展示可打开的 HTTP(S) 入口。"""
    items = [{"name": "直播入口", "url": value}] if isinstance(value, str) else value
    sources = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        try:
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme not in ("http", "https") or not parsed.hostname:
                continue
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        source = {"name": str(item.get("name") or "直播入口"), "url": url}
        avatar = item.get("avatar")
        if isinstance(avatar, str) and avatar.startswith(("https://", "http://")):
            source["avatar"] = avatar
        sources.append(source)
    return sources


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


_TEAM_ALIAS_GROUPS = (
    ("曼联", "曼彻斯特联"),
    ("诺丁汉", "诺丁汉森林"),
    ("伯恩茅", "伯恩茅斯"),
    ("热刺", "托特纳姆热刺"),
    ("伊普斯", "伊普斯维奇"),
    ("纽卡斯", "纽卡斯尔", "纽卡斯尔联"),
    ("维拉", "阿斯顿维拉"),
    ("国米", "国际米兰"),
    ("马竞", "马竞技", "马德里竞技"),
    ("巴萨", "巴塞罗那"),
    ("维拉利尔", "維拉利爾", "比利亚雷", "比利亚雷亚尔"),
    ("弗洛西诺", "弗洛西诺尼", "費辛隆尼"),
    ("加泰", "赫塔菲", "赫塔费"),
    ("毕尔巴", "毕尔巴鄂", "毕尔巴鄂竞技", "畢爾包"),
    ("西班人", "西班牙人", "愛斯賓奴"),
    ("斯图加", "史特加", "斯图加特"),
    ("不来梅", "雲達不萊梅", "云达不莱梅"),
    ("埃沃斯", "埃沃斯堡", "埃尔沃斯贝格"),
    ("霍芬海", "霍芬海姆"),
    ("勒沃", "勒沃库森"),
    ("帕德博", "帕德博恩"),
    ("门兴", "门兴格拉德巴赫"),
    ("多特", "多特蒙德"),
    ("柏林联", "柏林联合"),
    ("莱红牛", "莱比锡红牛", "RB莱比锡"),
    ("沙尔克", "沙尔克04"),
    ("塞维利", "塞维利亚"),
    ("贝蒂斯", "皇家贝蒂斯"),
    ("拜仁", "拜仁慕尼黑"),
    ("比勒费", "比勒菲尔德", "比勒費爾德"),
    ("斯洛文尼", "斯洛文尼亚"),
    ("哈萨克", "哈薩克", "哈萨克斯坦"),
)
_TEAM_ALIASES = {
    _normalize(alias): frozenset(_normalize(name) for name in group)
    for group in _TEAM_ALIAS_GROUPS
    for alias in group
}


def _fetch_live_json(base_url: str, path: str, site: str) -> list | dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/142.0.0.0 Safari/537.36",
        "Referer": base_url + "/score/football/lives",
        "Accept": "application/json",
        "webclienttype": "web", "clientType": "web", "site": site,
        "ad-channel": "crzb" if site == "crzb" else "", "timezone": "UTC+8",
    }
    if site == "crzb":
        headers["channel"] = "channel_crzb"
    response = requests.get(
        base_url + "/api" + path,
        timeout=8,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    if (not isinstance(payload, dict) or payload.get("code") != 200
            or not isinstance(payload.get("data"), (dict, list))):
        raise ValueError("直播接口未返回有效数据")
    return payload["data"]


def _fetch_huakang_json(path: str) -> list | dict:
    return _fetch_live_json(HUAKANG_URL, path, "fmzb")


def _collect_huakang_links(rows: list[dict]) -> dict[str, LiveLink]:
    return _collect_api_links(rows, _fetch_huakang_json, HUAKANG_URL)


def _fetch_crzb_json(path: str) -> list | dict:
    last_error: requests.RequestException | ValueError = ValueError(
        "超人直播接口未返回有效数据"
    )
    for base_url in (CRZB_URL, CRZB_BACKUP_URL, CRZB_SECOND_BACKUP_URL):
        try:
            return _fetch_live_json(base_url, path, "crzb")
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            task_log("刷新直播", f"源站 {base_url} 请求失败，尝试备用：{exc}")
    raise last_error


def _same_team(team: str, *names: str) -> bool:
    normalized = _normalize(team)
    aliases = _TEAM_ALIASES.get(normalized, {normalized})
    return bool(normalized) and any(_normalize(name) in aliases for name in names)


def _collect_crzb_links(rows: list[dict]) -> dict[str, LiveLink]:
    return _collect_api_links(rows, _fetch_crzb_json, CRZB_URL)


def _collect_api_links(
    rows: list[dict], fetch_json: Callable[[str], list | dict], base_url: str
) -> dict[str, LiveLink]:
    try:
        matches = fetch_json("/web/web_football_score_list")
    except (requests.RequestException, ValueError) as exc:
        task_log("刷新直播", f"源站 {base_url} 赛程获取失败：{exc}")
        return {}
    if not isinstance(matches, list):
        return {}

    links: dict[str, LiveLink] = {}
    for index, row in enumerate(rows, 1):
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
                data = fetch_json(
                    f"/web/get_public_anchor?tournament_id={match_id}&type=1&tournament_type=1"
                )
            except (requests.RequestException, ValueError) as exc:
                task_log("刷新直播", f"第 {row.get('match_no')} 场主播获取失败：{exc}")
                continue
            if not isinstance(data, dict):
                continue
            more = data.get("more", [])
            anchors = list(more) if isinstance(more, list) else []
            default = data.get("data")
            if isinstance(default, dict):
                anchors.insert(0, default)
            sources = []
            seen = set()
            for anchor in anchors:
                if not isinstance(anchor, dict):
                    continue
                member_id = str(anchor.get("member_id") or "")
                if (str(anchor.get("tournament_id")) == match_id
                        and str(anchor.get("tournament_type", 1 if anchor is default else "")) == "1"
                        and member_id.isdigit() and member_id not in seen):
                    seen.add(member_id)
                    source = {
                        "name": str(anchor.get("nickname") or anchor.get("username") or f"主播 {member_id}"),
                        "url": f"{base_url}/detail/1/{match_id}/{member_id}",
                    }
                    avatar = anchor.get("avatar") or anchor.get("face")
                    if isinstance(avatar, str) and avatar.startswith(("https://", "http://")):
                        source["avatar"] = avatar
                    sources.append(source)
            if sources:
                links[str(row.get("match_no") or "")] = sources
            if str(row.get("match_no") or "") in links:
                break
        count = len(live_sources(links.get(str(row.get("match_no") or ""), "")))
        task_log("刷新直播", f"[{index}/{len(rows)}] 第 {row.get('match_no')} 场：{count} 个直播入口")
    return links


def _collect_links(rows: list[dict], issue: str) -> dict[str, LiveLink]:
    task_log("刷新直播", f"{issue} 飞马直播匹配 {len(rows)} 场，获取全部主播")
    links = _collect_huakang_links(rows)
    missing = [
        row for row in rows if str(row.get("match_no") or "") not in links
    ]
    if missing:
        task_log("刷新直播", f"{issue} 飞马缺失 {len(missing)} 场，超人直播兜底")
        links.update(_collect_crzb_links(missing))
    return links


def load_cached_live_links(
    rows: list[dict], issue: str, cache_dir: str | Path
) -> tuple[dict[tuple[str, str], LiveLink], bool, bool]:
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
        and isinstance(found[str(row.get("match_no") or "")], (str, list))
    }
    expired = time.time() - fetched_at >= _CACHE_SECONDS
    return links, expired, True


def live_links(
    rows: list[dict],
    issue: str,
    *,
    force: bool = False,
    cache_dir: str | Path | None = None,
) -> dict[tuple[str, str], LiveLink]:
    """Return live-room sources keyed by issue and match number."""
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
