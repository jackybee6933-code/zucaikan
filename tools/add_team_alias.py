import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from football_analysis.store import upsert_team_name_aliases


DATABASES = (
    ROOT / "data" / "football_analysis.sqlite",
    ROOT / "dist" / "data" / "football_analysis.sqlite",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="将比赛原名映射为查询页显示名，并同步到源码与 dist 数据库。"
    )
    parser.add_argument("original_name", help="比赛数据中的原名")
    parser.add_argument("alias", help="希望显示的别名")
    args = parser.parse_args(argv)
    original_name = args.original_name.strip()
    alias = args.alias.strip()
    if not original_name or not alias:
        parser.error("原名和别名不能为空")

    missing = [str(path) for path in DATABASES if not path.is_file()]
    if missing:
        parser.error("找不到别名数据库：" + ", ".join(missing))

    mappings = {original_name: alias, alias: alias}
    for path in DATABASES:
        upsert_team_name_aliases(path, mappings)
        print(f"已更新：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
