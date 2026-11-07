import unittest

from football_analysis.cantonese import (
    LIVE_ISSUE_URL,
    parse_cantonese_matches,
    parse_mandarin_matches,
)


class CantoneseMatchParserTests(unittest.TestCase):
    def test_names_are_indexed_by_live_fixture_id(self):
        html = (
            '<table id="table_match"><tr id="a1479882">'
            '<td><a href="/team/1/">AC米蘭</a></td>'
            '<td><a href="/team/2/">費倫斯華路士</a></td>'
            '</tr><tr id="a1479889">'
            '<td><a href="/team/3/">安德列治</a></td>'
            '<td><a href="/team/4/">薩爾斯堡</a></td>'
            '</tr></table>'
        )

        self.assertEqual(
            parse_cantonese_matches(html),
            {
                "1479882": ("AC米蘭", "費倫斯華路士"),
                "1479889": ("安德列治", "薩爾斯堡"),
            },
        )

    def test_missing_match_table_returns_empty_mapping(self):
        self.assertEqual(parse_cantonese_matches("<html></html>"), {})

    def test_mandarin_names_follow_table_order_not_fixture_id(self):
        html = (
            '<table id="table_match"><tr id="a1479882">'
            '<td><a href="/team/1/">Mainz</a></td>'
            '<td><a href="/team/2/">Bayern Munich</a></td>'
            '</tr><tr id="a1479889">'
            '<td><a href="/team/3/">Chelsea</a></td>'
            '<td><a href="/team/4/">Arsenal</a></td>'
            '</tr></table>'
        )
        self.assertEqual(
            parse_mandarin_matches(html),
            {"1": ("Mainz", "Bayern Munich"), "2": ("Chelsea", "Arsenal")},
        )

    def test_issue_url_includes_requested_issue(self):
        self.assertEqual(
            LIVE_ISSUE_URL.format(issue="26161"),
            "https://live.500.com/zucai.php?e=26161",
        )


if __name__ == "__main__":
    unittest.main()
