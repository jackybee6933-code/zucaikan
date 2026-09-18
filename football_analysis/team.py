from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup


TEAM_PAGE_URL = "https://liansai.500.com/team/{team_id}/"


@dataclass
class TeamRecord:
    team_id: str = ""
    team_name: str = ""
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    home_matches: int = 0
    home_wins: int = 0
    home_draws: int = 0
    home_losses: int = 0
    home_goals_for: int = 0
    home_goals_against: int = 0
    away_matches: int = 0
    away_wins: int = 0
    away_draws: int = 0
    away_losses: int = 0
    away_goals_for: int = 0
    away_goals_against: int = 0
    handicap_win: int = 0
    handicap_push: int = 0
    handicap_loss: int = 0
    over: int = 0
    under: int = 0
    rows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.pop("rows", None)
        payload["points_per_game"] = (
            (self.wins * 3 + self.draws) / self.matches if self.matches else 0.0
        )
        payload["goals_for_per_game"] = self.goals_for / self.matches if self.matches else 0.0
        payload["goals_against_per_game"] = (
            self.goals_against / self.matches if self.matches else 0.0
        )
        payload["home_points_per_game"] = (
            (self.home_wins * 3 + self.home_draws) / self.home_matches
            if self.home_matches
            else 0.0
        )
        payload["away_points_per_game"] = (
            (self.away_wins * 3 + self.away_draws) / self.away_matches
            if self.away_matches
            else 0.0
        )
        payload["handicap_win_rate"] = (
            self.handicap_win / (self.handicap_win + self.handicap_loss)
            if self.handicap_win + self.handicap_loss
            else 0.0
        )
        payload["over_rate"] = (
            self.over / (self.over + self.under) if self.over + self.under else 0.0
        )
        return payload


def _normalise(value: str) -> str:
    return str(value or "").replace("\xa0", " ").replace("\u3000", " ").strip()


def _clean_score(text: str) -> tuple[int, int]:
    matches = re.findall(r"(\d+)\s*[:：]\s*(\d+)", str(text or ""))
    if not matches:
        return 0, 0
    return int(matches[0][0]), int(matches[0][1])


def _row_team_ids(row) -> list[str]:
    ids: list[str] = []
    for anchor in row.find_all("a", href=True):
        match = re.search(r"/team/(\d+)/", anchor.get("href", ""))
        if match:
            ids.append(match.group(1))
    return ids


def parse_recent_summary(html: str) -> TeamRecord:
    soup = BeautifulSoup(html, "html.parser")
    summary_table = soup.find("table", class_="lwfull")
    if summary_table is None:
        raise RuntimeError("球队页面未找到近期统计表")

    text = summary_table.get_text(" ", strip=True)
    name_match = re.search(r"([^\s]+?)\s*近10场战绩", text)
    record_match = re.search(
        r"近\d+场战绩\s*(\d+)胜\s*(\d+)平\s*(\d+)负\s*进\s*(\d+)球\s*失\s*(\d+)球",
        text,
    )
    if not record_match:
        return TeamRecord(team_name=name_match.group(1) if name_match else "")
    wins, draws, losses, goals_for, goals_against = [int(x) for x in record_match.groups()]
    matches = wins + draws + losses
    record = TeamRecord(
        team_name=name_match.group(1) if name_match else "",
        matches=matches,
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=goals_for,
        goals_against=goals_against,
    )
    return record


def parse_team_recent_html(html: str, team_id: str, team_name: str = "") -> TeamRecord:
    """Parse the 10 completed games shown on liansai team page."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="lcur_race_list")
    if table is None:
        raise RuntimeError("球队页面未找到近期战绩表 lcur_race_list")
    header_cells = [
        cell.get_text(" ", strip=True) for cell in table.find_all("tr")[0].find_all(["th", "td"])
    ]
    expected = ["赛事", "比赛时间", "主队", "比分", "客队", "赛果", "盘路", "大小", "分析"]
    if len(header_cells) < 9 or header_cells[2] != "主队":
        # Table shape changed; fall back to structured summary only.
        return parse_recent_summary(html)

    record = TeamRecord(team_id=str(team_id), team_name=team_name)
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 8:
            continue
        league = cells[0].get_text(" ", strip=True)
        date = _normalise(cells[1].get_text(" ", strip=True))
        home_text = _normalise(cells[2].get_text(" ", strip=True))
        away_text = _normalise(cells[4].get_text(" ", strip=True))
        score_text = _normalise(cells[3].get_text(" ", strip=True))
        result = _normalise(cells[5].get_text(" ", strip=True))
        handicap_text = _normalise(cells[6].get_text(" ", strip=True))
        ou_text = _normalise(cells[7].get_text(" ", strip=True))
        if result not in {"胜", "平", "负"}:
            continue

        home_score, away_score = _clean_score(score_text)
        team_ids = _row_team_ids(row)
        is_home = bool(team_ids and str(team_id) == team_ids[0])
        is_away = bool(team_ids and len(team_ids) > 1 and str(team_id) == team_ids[1])
        if not team_ids:
            target = record.team_name or team_name
            is_home = bool(target) and target in home_text
            is_away = bool(target) and target in away_text
        if not is_home and not is_away:
            continue

        if is_home:
            gf, ga = home_score, away_score
        else:
            gf, ga = away_score, home_score
        record.matches += 1
        record.goals_for += gf
        record.goals_against += ga
        if is_home:
            record.home_matches += 1
            record.home_goals_for += gf
            record.home_goals_against += ga
        else:
            record.away_matches += 1
            record.away_goals_for += gf
            record.away_goals_against += ga

        if result == "胜":
            record.wins += 1
            if is_home:
                record.home_wins += 1
            else:
                record.away_wins += 1
        elif result == "平":
            record.draws += 1
            if is_home:
                record.home_draws += 1
            else:
                record.away_draws += 1
        else:
            record.losses += 1
            if is_home:
                record.home_losses += 1
            else:
                record.away_losses += 1

        if "赢" in handicap_text:
            record.handicap_win += 1
        elif "输" in handicap_text:
            record.handicap_loss += 1
        elif handicap_text in {"走", "走盘"}:
            record.handicap_push += 1
        if "大" in ou_text:
            record.over += 1
        elif "小" in ou_text:
            record.under += 1
        record.rows.append(
            {
                "league": league,
                "date": date,
                "home": home_text,
                "away": away_text,
                "score": score_text,
                "result": result,
                "home_side": is_home,
                "gf": gf,
                "ga": ga,
            }
        )

    if record.matches == 0:
        fallback = parse_recent_summary(html)
        fallback.team_id = str(team_id)
        fallback.team_name = fallback.team_name or team_name
        return fallback
    if not record.team_name:
        record.team_name = record.rows[0]["home"] if record.rows[0]["home_side"] else record.rows[0]["away"]
    return record
