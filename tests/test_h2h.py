import unittest

from football_analysis.h2h import (
    build_sfc_match_map,
    parse_desktop_h2h_html,
    parse_mobile_h2h_html,
)
from football_analysis.model import build_model_input, predict_match


class DesktopH2HTests(unittest.TestCase):
    def test_desktop_h2h_orients_by_team_ids(self):
        html = """
        <script>
        var h2h_home = 1;
        var h2h_away = 2;
        var v_data = [
          ["26-01-10", 8, "League", "", 1, "Home", 2, "Away", 3, 1, "2-1", "", 1, -2, 1, 1001, "3", "2", "", 1],
          ["25-12-01", 8, "League", "", 2, "Away", 1, "Home", 0, 2, "0-1", "", 1, -2, 1, 1002, "2", "4", "", 0]
        ];
        </script>
        """
        parsed = parse_desktop_h2h_html(html)
        self.assertEqual(parsed["source"], "titan_desktop")
        self.assertEqual(parsed["total"], 2)
        self.assertEqual(parsed["home_wins"], 2)
        self.assertEqual(parsed["draws"], 0)
        self.assertEqual(parsed["away_wins"], 0)
        self.assertEqual(parsed["goals_for"], 5)
        self.assertEqual(parsed["goals_against"], 1)
        self.assertEqual(parsed["rows"][0]["score"], "3-1")
        self.assertEqual(parsed["rows"][1]["score"], "0-2")
        self.assertEqual(parsed["rows"][1]["current_home_score"], 2)


class MobileH2HTests(unittest.TestCase):
    def test_mobile_h2h_orients_by_ishome(self):
        html = """
        <div id="vsMatches"><table>
          <tr><th>Date</th></tr>
          <tr data-scheid="9001" data-ishome="1"
              data-homescore="2" data-awayscore="1"
              data-halfhome="1" data-halfaway="0">
            <td><span class="leagueName">League</span><div class="teamdate">26-01-10</div></td>
            <td class="homeTeam">Home</td><td>2-1</td><td class="awayTeam">Away</td>
          </tr>
          <tr data-scheid="9002" data-ishome="0"
              data-homescore="0" data-awayscore="1"
              data-halfhome="0" data-halfaway="1">
            <td><span class="leagueName">League</span><div class="teamdate">25-12-01</div></td>
            <td class="homeTeam">Away</td><td>0-1</td><td class="awayTeam">Home</td>
          </tr>
        </table></div>
        """
        parsed = parse_mobile_h2h_html(html)
        self.assertEqual(parsed["source"], "nowscore_mobile")
        self.assertEqual(parsed["total"], 2)
        self.assertEqual(parsed["home_wins"], 2)
        self.assertEqual(parsed["rows"][1]["score"], "0-1")
        self.assertEqual(parsed["rows"][1]["current_home_score"], 1)


class ScheduleH2HTests(unittest.TestCase):
    def test_sfc_match_map_uses_issue_suffix(self):
        html = """
        <script>
        var zuCaiSchedule = "3073215,3073214,3073213|120-01,120-02,120-03";
        var jinZuSchedule = "2929718|Tue001";
        </script>
        """
        mapped = build_sfc_match_map(html, "26120")
        self.assertEqual(mapped["1"], "3073215")
        self.assertEqual(mapped["2"], "3073214")
        self.assertEqual(mapped["3"], "3073213")


class ModelH2HTests(unittest.TestCase):
    def test_h2h_high_home_record_only_nudges_model(self):
        base = build_model_input(europe_home=2.2, europe_draw=3.4, europe_away=3.2)
        strong = build_model_input(
            europe_home=2.2,
            europe_draw=3.4,
            europe_away=3.2,
            h2h_total=6,
            h2h_home_wins=5,
            h2h_draws=0,
            h2h_away_wins=1,
        )
        base_pred = predict_match(base)
        strong_pred = predict_match(strong)
        self.assertGreater(
            strong_pred["final_probabilities"]["home"],
            base_pred["final_probabilities"]["home"],
        )
        self.assertLess(
            strong_pred["final_probabilities"]["away"],
            base_pred["final_probabilities"]["away"],
        )


if __name__ == "__main__":
    unittest.main()
