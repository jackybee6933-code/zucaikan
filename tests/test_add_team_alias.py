import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from football_analysis.store import list_team_name_aliases, upsert_team_name_aliases
from tools import add_team_alias


class AddTeamAliasTests(unittest.TestCase):
    def test_updates_original_and_alias_in_both_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            databases = (Path(tmp) / "data.sqlite", Path(tmp) / "dist.sqlite")
            for path in databases:
                upsert_team_name_aliases(path, {"史笃城": "斯托克城"})

            with patch.object(add_team_alias, "DATABASES", databases):
                self.assertEqual(add_team_alias.main(["斯托克城", "史笃城"]), 0)

            for path in databases:
                self.assertEqual(
                    list_team_name_aliases(path),
                    {"斯托克城": "史笃城", "史笃城": "史笃城"},
                )

    def test_requires_existing_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = (Path(tmp) / "missing.sqlite",)
            with patch.object(add_team_alias, "DATABASES", missing):
                with self.assertRaises(SystemExit):
                    add_team_alias.main(["原名", "别名"])


if __name__ == "__main__":
    unittest.main()
