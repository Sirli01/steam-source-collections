"""Regression tests for license package names that differ from game names."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from steam_source_collections.appinfo import appinfo_path, read_app_names, resolve_localized_names
from steam_source_collections.bundle import fetch_online_app_names, load_bundle
from steam_source_collections.local import LocalSteam
from steam_source_collections.matching import (
    alias_candidates, build_index, match_package, normalize, strip_promo_suffix,
)
from steam_source_collections.owned import Game


def _appinfo_record(appid: int, values: list[str], timestamp: int = 1_790_000_000,
                    primary_name: str | None = None, pics_token: int = 0) -> bytes:
    """Build a minimal binary appinfo record with localized and primary names."""
    section = b""
    if primary_name is not None:
        section += b"\x01" + struct.pack("<I", 4) + primary_name.encode("utf-8") + b"\x00"
    for value in values:
        section += b"\x01" + struct.pack("<I", 0x1C6) + value.encode("utf-8") + b"\x00"
    section += b"\x08" * 240  # real records are far larger than a few names
    header = (
        struct.pack("<IIIIQ", appid, len(section), 2, timestamp, pics_token)
        + b"\x11" * 20
        + struct.pack("<II", 1, 0)
    )
    assert len(header) == 52
    return header + section


class PackageNameTests(unittest.TestCase):
    def setUp(self):
        self.games = [
            Game(668580, "Atomic Heart"),
            Game(1237970, "Titanfall® 2"),
            Game(1382330, "Persona 5 Strikers"),
            Game(1623730, "Palworld"),
            Game(2527500, "MiSide"),
            Game(730, "Counter-Strike: Global Offensive"),
        ]
        self.index = build_index(self.games)

    def test_edition_and_region_suffixes_are_stripped(self):
        cases = {
            "Atomic Heart - Standard Edition (CN)": 668580,
            "Titanfall® 2: Ultimate Edition": 1237970,
            "Persona 5 Strikers - Retail - [Digital] (CTHM)": 1382330,
            "Remnant II® - Standard Edition (China)": None,
        }
        for package, expected in cases.items():
            if expected is None:
                continue
            with self.subTest(package=package):
                result = match_package(package, self.index)
                self.assertEqual([game.appid for game in result.games], [expected])
                self.assertEqual(result.via, "normalized")

    def test_bilingual_package_name(self):
        result = match_package("Palworld / 幻兽帕鲁", self.index)
        self.assertEqual([game.appid for game in result.games], [1623730])
        self.assertEqual(result.via, "alias")

    def test_chinese_only_name_is_not_guessed(self):
        # "米塔" and "MiSide" share no characters; only the local appinfo
        # cache can bridge them, so plain string matching must stay silent.
        self.assertEqual(match_package("米塔", self.index).games, [])

    def test_real_subtitle_is_never_shortened(self):
        index = build_index([Game(10, "Counter-Strike")])
        self.assertEqual(alias_candidates("Counter-Strike: Global Offensive"), [])
        self.assertEqual(match_package("Counter-Strike: Global Offensive", index).games, [])

    def test_exact_match_still_wins(self):
        result = match_package("Counter-Strike: Global Offensive", self.index)
        self.assertEqual(result.via, "exact")
        self.assertEqual([game.appid for game in result.games], [730])

    def test_promotional_and_free_weekend_suffixes_are_stripped(self):
        self.assertEqual(
            strip_promo_suffix("KEO Limited Free Promotional Package - Nov 2021"),
            "KEO",
        )
        self.assertEqual(
            strip_promo_suffix("Path of Exile 2 Free Weekend - May 2026"),
            "Path of Exile 2",
        )
        games = [
            Game(879850, "Box: The Game"),
            Game(1424910, "KEO"),
            Game(2694490, "Path of Exile 2"),
        ]
        index = build_index(games)
        box = match_package("Box: The Game Limited Free Promotional Package - Dec 2021", index)
        self.assertEqual([g.appid for g in box.games], [879850])
        self.assertEqual(box.via, "normalized")
        keo = match_package("KEO Limited Free Promotional Package - Nov 2021", index)
        self.assertEqual([g.appid for g in keo.games], [1424910])
        poe = match_package("Path of Exile 2 Free Weekend - May 2026", index)
        self.assertEqual([g.appid for g in poe.games], [2694490])

    def test_date_fragments_never_trigger_false_bundle_matches(self):
        # Even if a dirty name existed in an index, a "Dec 2021" fragment must
        # never cause "Box: The Game ... - Dec 2021" to match another Dec 2021 row.
        index = build_index([
            Game(1448030, "Press Any Button Limited Free Promotional Package - Dec 2021"),
        ])
        result = match_package("Box: The Game Limited Free Promotional Package - Dec 2021", index)
        self.assertEqual(result.games, [])
        self.assertEqual(result.via, "none")


class AppInfoTests(unittest.TestCase):
    def test_resolves_localized_names_to_owned_appids(self):
        data = (
            _appinfo_record(2527500, ["米塔 MiSide", "米塔"], pics_token=0x123456789ABCDEF0)
            + _appinfo_record(668580, ["原子之心"])
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            got = resolve_localized_names(
                path, ["米塔", "原子之心", "完全不存在的名字"], {2527500, 668580}
            )
        self.assertEqual(got.get(normalize("米塔")), 2527500)
        self.assertEqual(got.get(normalize("原子之心")), 668580)
        self.assertNotIn(normalize("完全不存在的名字"), got)

    def test_read_app_names_handles_pics_tokens_and_filters_wanted(self):
        data = (
            _appinfo_record(879850, [], primary_name="Box: The Game", pics_token=5528108090346780381)
            + _appinfo_record(1424910, [], primary_name="KEO")
            + _appinfo_record(999999, [], primary_name="Not Owned")
        )
        got = read_app_names(data, {879850, 1424910})
        self.assertEqual(got, {879850: "Box: The Game", 1424910: "KEO"})

    def test_load_bundle_recovers_gaps_without_overwriting_and_cleans_borrowed_names(self):
        vdf = (
            _appinfo_record(879850, [], primary_name="Box: The Game", pics_token=987654321012345)
            + _appinfo_record(1001, [], primary_name="Cache Title Should Not Overwrite")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "appcache").mkdir()
            (root / "appcache" / "appinfo.vdf").write_bytes(vdf)
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps({
                "licenses": [
                    {
                        "date": "2021 年 12 月 5 日",
                        "name": "Box: The Game Limited Free Promotional Package - Dec 2021",
                        "method": "免费赠送",
                    },
                    {
                        "date": "2021 年 12 月 5 日",
                        "name": "Press Any Button Limited Free Promotional Package - Dec 2021",
                        "method": "免费赠送",
                        "packageid": "660001",
                    },
                ],
                "owned_appids": [879850, 1448030, 1001],
                "app_names": {"1001": "Trusted WebAPI Title"},
            }), encoding="utf-8")
            local = LocalSteam(root=root, package_apps={660001: [1448030]})
            bundle = load_bundle(bundle_path, local)
            by_id = {g.appid: g for g in bundle.games}
            self.assertEqual(by_id[879850].name, "Box: The Game")
            self.assertEqual(by_id[1001].name, "Trusted WebAPI Title")
            self.assertEqual(by_id[1448030].name, "Press Any Button")
            self.assertEqual(bundle.meta["cache_recovered_names"], 1)
            self.assertEqual([g.appid for g in bundle.matches[0].games], [879850])
            self.assertEqual(bundle.matches[0].via, "normalized")

    def test_fetch_online_app_names_degrades_gracefully_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            self.assertEqual(fetch_online_app_names([879850], retries=1), {})

    def test_ambiguous_name_is_not_guessed(self):
        data = _appinfo_record(111, ["同名"]) + _appinfo_record(222, ["同名"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            got = resolve_localized_names(path, ["同名"], {111, 222})
        self.assertEqual(got, {})

    def test_appids_outside_the_library_are_ignored(self):
        data = _appinfo_record(999, ["别人家的游戏"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            got = resolve_localized_names(path, ["别人家的游戏"], {111})
        self.assertEqual(got, {})

    def test_missing_file_is_harmless(self):
        self.assertEqual(resolve_localized_names("no-such-file.vdf", ["米塔"], {1}), {})
        self.assertIsNone(appinfo_path(None))


if __name__ == "__main__":
    unittest.main()
