"""Regression tests for license package names that differ from game names."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from steam_source_collections.appinfo import appinfo_path, resolve_localized_names
from steam_source_collections.matching import alias_candidates, build_index, match_package, normalize
from steam_source_collections.owned import Game


def _appinfo_record(appid: int, values: list[str], timestamp: int = 1_790_000_000) -> bytes:
    """Build a minimal binary appinfo record with localized string values."""
    section = b""
    for value in values:
        section += b"\x01" + struct.pack("<I", 0x1C6) + value.encode("utf-8") + b"\x00"
    section += b"\x08" * 240  # real records are far larger than a few names
    header = (
        struct.pack("<IIIQ", appid, len(section), 2, timestamp)
        + b"\x00" * 8
        + b"\x11" * 20
        + struct.pack("<I", 1)
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


class AppInfoTests(unittest.TestCase):
    def test_resolves_localized_names_to_owned_appids(self):
        data = (
            _appinfo_record(2527500, ["米塔 MiSide", "米塔"])
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
