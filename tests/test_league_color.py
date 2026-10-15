import unittest

from football_analysis.jczq import parse_jczq_html
from football_analysis.sfc import parse_sfc_html

SFC_ROW = (
    '<table id="vsTable">'
    '<tr class="bet-tb-tr">'
    '<td class="td td-no">1</td>'
    '<td class="td td-evt"><a href="https://liansai.500.com/zuqiu-19906/" style="background:#FF1717;">英超</a></td>'
    '<td class="td td-endtime">09-19 03:00</td>'
    '<td class="td td-team"><i class="team-vs">VS</i></td>'
    '<td class="td td-data"><a href="https://odds.500.com/fenxi/shuju-12345.shtml">析</a></td>'
    "</tr>"
    "</table>"
)

JCZQ_ROW = (
    '<tr class="bet-tb-tr" data-fixtureid="67890" data-simpleleague="芬超"'
    ' data-homesxname="H" data-awaysxname="A">'
    '<td class="td td-evt"><a href="https://liansai.500.com/zuqiu-19506/"'
    ' style="background:#137AAC;" title="芬兰超级联赛">芬兰超级联赛</a></td>'
    '<td class="td td-team"><a class="team-l">H</a><i class="team-vs">VS</i><a class="team-r">A</a></td>'
    "</tr>"
)


class SfcLeagueColorTests(unittest.TestCase):
    def test_color_taken_from_td_evt_inline_style(self):
        rows = parse_sfc_html(SFC_ROW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["league"], "英超")
        self.assertEqual(rows[0]["league_color"], "#FF1717")

    def test_absent_style_yields_empty_color(self):
        rows = parse_sfc_html(SFC_ROW.replace('style="background:#FF1717;"', ""))
        self.assertEqual(rows[0]["league"], "英超")
        self.assertEqual(rows[0]["league_color"], "")


class JczqLeagueColorTests(unittest.TestCase):
    def test_color_taken_from_td_evt_inline_style(self):
        result = parse_jczq_html(JCZQ_ROW)
        self.assertIn("67890", result)
        self.assertEqual(result["67890"]["league"], "芬超")
        self.assertEqual(result["67890"]["league_color"], "#137AAC")

    def test_absent_style_yields_empty_color(self):
        result = parse_jczq_html(JCZQ_ROW.replace('style="background:#137AAC;"', ""))
        self.assertEqual(result["67890"]["league_color"], "")


if __name__ == "__main__":
    unittest.main()
