import unittest

from football_analysis.cantonese import parse_cantonese_matches


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


if __name__ == "__main__":
    unittest.main()
