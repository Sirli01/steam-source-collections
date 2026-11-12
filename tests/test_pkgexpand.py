"""Tests for claiming whole Steam packages behind collection names."""

from __future__ import annotations

import unittest

from steam_source_collections.appinfo import first_cjk_fragment
from steam_source_collections.licenses import LicenseRecord, classify
from steam_source_collections.matching import MatchResult, normalize
from steam_source_collections.owned import Game
from steam_source_collections.pkgexpand import (
    expand_package_matches, package_title_score,
)


def record(name: str, method: str = "Steam 商店") -> LicenseRecord:
    return LicenseRecord(date="2023-12-24", package_name=name,
                         method_raw=method, category=classify(method))


def fixture(package_name, app_labels, threshold=0.76):
    games = {appid: Game(appid=appid, name=label, playtime_minutes=0)
             for appid, label in app_labels.items()}
    return (record(package_name), [MatchResult()], games,
            {str(k): v for k, v in app_labels.items()}, threshold)


class NormalizationTests(unittest.TestCase):
    def test_trademark_symbols_do_not_become_letters(self):
        # NFKC folds "TM" into the letters "tm" when applied first, which used
        # to make a license name and its own game title compare unequal.
        self.assertEqual(normalize("Middle-earth™: Shadow of War™"),
                         normalize("Middle-earth: Shadow of War"))
        self.assertGreater(
            package_title_score("Middle-earth: Shadow of War Definitive Edition",
                                "Middle-earth™: Shadow of War™"), 0.76)

    def test_first_cjk_fragment_skips_edition_words(self):
        self.assertEqual(first_cjk_fragment("《巫师 3：狂猎 - 完全版》"), "巫师")
        self.assertEqual(first_cjk_fragment("人格解体"), "人格解体")
        self.assertEqual(first_cjk_fragment("Batman: Arkham Collection"), "")


class PackageExpandTests(unittest.TestCase):
    def test_collection_expands_every_app_in_the_package(self):
        rec, matches, games, names, _ = fixture(
            "Batman: Arkham Collection",
            {35140: "Batman: Arkham Asylum GOTY Edition",
             200260: "Batman: Arkham City GOTY",
             200269: "App 200269"},
        )
        result = expand_package_matches(
            [rec], matches, set(), {320795: list(games)}, names, games)
        self.assertEqual(result["expanded_rows"], 1)
        self.assertEqual(result["expanded_apps"], 3)
        self.assertEqual(matches[0].via, "bundle")
        self.assertEqual({g.appid for g in matches[0].games}, set(games))
        self.assertIn("整包同源", matches[0].note)

    def test_already_assigned_apps_are_never_reclassified(self):
        rec, matches, games, names, _ = fixture(
            "Batman: Arkham Collection",
            {35140: "Batman: Arkham Asylum GOTY Edition",
             200260: "Batman: Arkham City GOTY"},
        )
        matches[0] = MatchResult(games=[games[35140]], via="package", score=1.0)
        # Same row already matched, so nothing should be expanded.
        result = expand_package_matches(
            [rec], matches, set(), {320795: list(games)}, names, games)
        self.assertEqual(result["expanded_rows"], 0)
        self.assertEqual(matches[0].via, "package")

    def test_claimed_package_is_not_expanded_twice(self):
        rec, matches, games, names, _ = fixture(
            "Batman: Arkham Collection",
            {35140: "Batman: Arkham Asylum GOTY Edition",
             200260: "Batman: Arkham City GOTY"},
        )
        result = expand_package_matches(
            [rec], matches, {320795}, {320795: list(games)}, names, games)
        self.assertEqual(result["expanded_rows"], 0)

    def test_unrelated_names_stay_unmatched(self):
        rec, matches, games, names, _ = fixture(
            "Totally Different Product", {35140: "Batman: Arkham City GOTY"})
        result = expand_package_matches(
            [rec], matches, set(), {320795: list(games)}, names, games)
        self.assertEqual(result["expanded_rows"], 0)
        self.assertFalse(matches[0].games)

    def test_two_equally_good_packages_are_ambiguous(self):
        rec, matches, games, names, _ = fixture(
            "Batman: Arkham Collection",
            {35140: "Batman: Arkham City GOTY", 200260: "Batman: Arkham City GOTY"},
        )
        packages = {320795: [35140], 400092: [200260]}
        result = expand_package_matches(
            [rec], matches, set(), packages, names, games)
        self.assertEqual(result["expanded_rows"], 0)

    def test_localized_hits_rescue_a_chinese_collection(self):
        rec, matches, games, names, _ = fixture(
            "《巫师 3：狂猎 - 完全版》",
            {292030: "The Witcher 3: Wild Hunt — Remastered",
             378648: "The Witcher 3: Wild Hunt - Blood and Wine"},
        )
        # English titles share nothing with the Chinese license name; only the
        # localized-name hits can identify the package.
        result = expand_package_matches(
            [rec], matches, set(), {124923: list(games)}, names, games,
            localized_hits={0: {292030, 378648}},
        )
        self.assertEqual(result["expanded_rows"], 1)
        self.assertEqual(result["expanded_apps"], 2)

    def test_single_localized_hit_is_not_enough(self):
        rec, matches, games, names, _ = fixture(
            "《巫师 3：狂猎 - 完全版》",
            {292030: "The Witcher 3: Wild Hunt — Remastered"},
        )
        result = expand_package_matches(
            [rec], matches, set(), {124923: list(games)}, names, games,
            localized_hits={0: {292030}},
        )
        self.assertEqual(result["expanded_rows"], 0)


if __name__ == "__main__":
    unittest.main()
