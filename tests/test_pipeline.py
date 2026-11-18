"""Synthetic end-to-end tests. Run: python -m unittest discover -s tests -v"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from steam_source_collections.licenses import LicenseRecord, classify, load_licenses, normalize_date, parse_html
from steam_source_collections.matching import MatchResult, build_index, match_package
from steam_source_collections.owned import Game, load_family, load_owned
from steam_source_collections.plan import build_plan
from steam_source_collections.report import write_outputs

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def make_plan(**overrides):
    records = load_licenses(EXAMPLES / "licenses_sample.html")
    games = load_owned(EXAMPLES / "owned_games_sample.json")
    family_ids, family_names = load_family(EXAMPLES / "family_sample.txt")
    index = build_index(games)
    matches = [match_package(record.package_name, index) for record in records]
    kwargs = dict(
        conflict="priority",
        extra_groups=True,
    )
    kwargs.update(overrides)
    return build_plan(records, games, matches, family_ids, family_names, **kwargs)


class ClassificationTests(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(classify("Steam 商店"), "store")
        self.assertEqual(classify("Steam Store"), "store")
        self.assertEqual(classify("零售"), "retail")
        self.assertEqual(classify("Retail"), "retail")
        self.assertEqual(classify("免费赠送"), "free")
        self.assertEqual(classify("Complimentary"), "free")
        self.assertEqual(classify("礼物"), "gift")
        self.assertEqual(classify("Guest Pass"), "gift")

    def test_specific_label_wins(self):
        self.assertEqual(classify("Steam Store - Complimentary"), "free")
        self.assertEqual(classify(""), "unknown")

    def test_html_parsing_and_remove_link(self):
        records = load_licenses(EXAMPLES / "licenses_sample.html")
        self.assertEqual(len(records), 11)
        paper = next(r for r in records if r.package_name.startswith("Paper Kingdom"))
        self.assertEqual(paper.package_name, "Paper Kingdom")
        self.assertEqual(paper.category, "free")
        self.assertEqual(next(r for r in records if "Moss" in r.package_name).package_name, "Moss & Stone")

    def test_html_without_class_names(self):
        """Some browsers strip class attributes when saving the page."""
        rows = parse_html(
            "<table><tr><th>日期</th><th>名称</th><th>获取方式</th></tr>"
            "<tr><td>2024-08-12</td><td>Aurora Drift</td><td>Steam 商店</td></tr></table>"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].package_name, "Aurora Drift")
        self.assertEqual(rows[0].date, "2024-08-12")
        self.assertEqual(rows[0].category, "store")


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.games = load_owned(EXAMPLES / "owned_games_sample.json")
        self.index = build_index(self.games)

    def test_exact_and_normalization(self):
        self.assertEqual(match_package("Aurora Drift", self.index).via, "exact")
        # 括号后缀 + 大小写差异
        result = match_package("Hollow Lantern (Retail)", self.index)
        self.assertEqual([g.appid for g in result.games], [1004])

    def test_bundle_and_unmatched(self):
        bundle = match_package("Aurora Drift + Neon Foundry Bundle", self.index)
        self.assertEqual(bundle.via, "bundle")
        self.assertEqual(match_package("Unknown Deluxe Package", self.index).via, "none")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.by_id = {group["id"]: group["appids"] for group in self.plan.groups}

    def test_five_source_groups(self):
        self.assertEqual(self.by_id["store"], [1001, 1002])
        self.assertEqual(self.by_id["retail"], [1003, 1004])
        self.assertEqual(self.by_id["free"], [1005, 1006, 1009])
        self.assertEqual(self.by_id["gift"], [1007, 1008])
        self.assertEqual(self.by_id["family"], [1010])
        self.assertEqual(self.by_id["unknown"], [1011, 1012])

    def test_cross_groups(self):
        self.assertEqual(self.by_id["played"], [1001, 1003, 1006, 1008, 1011])
        self.assertEqual(self.by_id["idled"], [])
        self.assertEqual(self.by_id["unplayed"], [1002, 1004, 1005, 1007, 1009, 1012])

    def test_idled_games_go_to_idled_cross_group_only(self):
        games = [
            Game(1, "Played", playtime_minutes=500, idled=False),
            Game(2, "Idled", playtime_minutes=130, idled=True),
            Game(3, "Unplayed", playtime_minutes=0, idled=False),
            Game(4, "Shared", playtime_minutes=200, family_shared=True, idled=True),
        ]
        plan = build_plan([], games, [], set(), set(), extra_groups=True)
        by_id = {g["id"]: g["appids"] for g in plan.groups}
        self.assertEqual(by_id["played"], [1])
        self.assertEqual(by_id["idled"], [2])
        self.assertEqual(by_id["unplayed"], [3])

    def test_unmatched_reporting(self):
        self.assertIn(
            "Unknown Deluxe Package",
            [item["package_name"] for item in self.plan.unmatched_licenses],
        )
        self.assertIn(1012, [item["appid"] for item in self.plan.unmatched_games])

    def test_paid_wins_over_free(self):
        # The bundle row is free, but both games already have a store license.
        self.assertEqual(self.plan.assignments[1001]["group"], "store")
        self.assertEqual(self.plan.assignments[1002]["group"], "store")

    def test_normalize_date_formats(self):
        self.assertEqual(normalize_date("2024 年 2 月 15 日"), "2024-02-15")
        self.assertEqual(normalize_date("12 Aug, 2024"), "2024-08-12")
        self.assertEqual(normalize_date("Feb 1, 2025"), "2025-02-01")
        self.assertEqual(normalize_date("2024-12-1"), "2024-12-01")

    def test_latest_conflict_uses_normalized_localized_dates(self):
        games = [Game(appid=5001, name="Alpha"), Game(appid=5002, name="Beta")]
        records = [
            LicenseRecord(date="2024 年 2 月 15 日", package_name="Alpha",
                          method_raw="零售", category="retail"),
            LicenseRecord(date="2024 年 12 月 1 日", package_name="Alpha",
                          method_raw="免费赠送", category="free"),
            LicenseRecord(date="12 Aug, 2024", package_name="Beta",
                          method_raw="免费赠送", category="free"),
            LicenseRecord(date="1 Feb, 2025", package_name="Beta",
                          method_raw="Steam 商店", category="store"),
        ]
        matches = [
            MatchResult(games=[games[0]], via="exact", score=1.0),
            MatchResult(games=[games[0]], via="exact", score=1.0),
            MatchResult(games=[games[1]], via="exact", score=1.0),
            MatchResult(games=[games[1]], via="exact", score=1.0),
        ]
        latest_plan = build_plan(records, games, matches, set(), set(), conflict="latest")
        self.assertEqual(latest_plan.assignments[5001]["group"], "free")
        self.assertEqual(latest_plan.assignments[5002]["group"], "store")

    def test_write_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_outputs(self.plan, Path(tmp), "licenses_sample.html", "owned.json")
            self.assertTrue(all(path.is_file() for path in paths))
            payload = json.loads((Path(tmp) / "collections_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "steam-source-collections/1")
            self.assertEqual(len(payload["groups"]), 9)
            html = (Path(tmp) / "report.html").read_text(encoding="utf-8")
            self.assertIn("没有许可记录的游戏", html)
            self.assertIn("copyBox(", html)
            self.assertNotIn("{{", html)
            self.assertNotIn("}}", html)


if __name__ == "__main__":
    unittest.main()
