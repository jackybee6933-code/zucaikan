"""盘口结算：四分之一盘拆成两半，返回赢/赢半/走/输半/输。"""
from __future__ import annotations


def split_line(line: float) -> tuple[float, ...]:
    """0.25、0.75 等四分之一盘拆成相邻两条半盘，其余盘口原样返回。"""
    value = float(line)
    if round(value * 4) % 2:
        return (value - 0.25, value + 0.25)
    return (value,)


def _half_result(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def settle_asian(
    home_goals: int,
    away_goals: int,
    line: float,
    picked_side: str,
) -> float | None:
    """按主队视角 line 结算亚盘，返回 -1 / -0.5 / 0 / 0.5 / 1。"""
    if picked_side not in ("home", "away"):
        return None
    goal_diff = home_goals - away_goals
    parts = []
    for half_line in split_line(line):
        value = goal_diff + half_line
        if picked_side == "away":
            value = -value
        parts.append(_half_result(value))
    return sum(parts) / len(parts)


def settle_total(total_goals: int, line: float, over: bool) -> float:
    """按大小球盘结算，返回 -1 / -0.5 / 0 / 0.5 / 1。"""
    parts = []
    for half_line in split_line(line):
        value = total_goals - half_line
        if not over:
            value = -value
        parts.append(_half_result(value))
    return sum(parts) / len(parts)


def profit_units(result: float, water: float) -> float:
    """把结算结果换成每注 1 单位的收益；水位缺失时只按结果计。"""
    if result > 0:
        return result * water if water > 0 else 0.0
    return result


def summarize(results: list[float]) -> dict:
    """汇总五类结果；hits 为赢+赢半，total 为不含走水的已决注数。"""
    values = [round(float(value), 2) for value in results]
    wins = sum(value == 1 for value in values)
    half_wins = sum(value == 0.5 for value in values)
    pushes = sum(value == 0 for value in values)
    half_losses = sum(value == -0.5 for value in values)
    losses = sum(value == -1 for value in values)
    return {
        "bets": len(values),
        "wins": wins,
        "half_wins": half_wins,
        "pushes": pushes,
        "half_losses": half_losses,
        "losses": losses,
        "hits": wins + half_wins,
        "total": wins + half_wins + half_losses + losses,
        "units": sum(values),
    }
