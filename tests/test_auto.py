"""Tests for local auto-discovery, the bookmarklet export bundle and the
newer key/value collection format. All fixtures are synthetic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam_source_collections.bundle import load_bundle
from steam_source_collections.local import (
    STEAMID64_BASE, LocalSteam, detect_idled_appids, discover,
    read_collections, read_last_played, read_play_signals, read_playtimes, read_steamid64,
)
from steam_source_collections.plan import build_plan
from steam_source_collections.writeback import WritebackError, apply, preview

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

LEGACY = {
    "data": {"collections": {"user-collections": {
        "fav": {"name": "收藏夹", "added": [730, 570], "removed": []},
    }}}
}
NEW_FORMAT = [
    ["GameReleased", {"key": "GameReleased", "value": '{"apps":[]}', "version": "1"}],
    ["user-collections.favorite", {"key": "user-collections.favorite",
                                   "value": json.dumps({"id": "favorite", "name": "收藏夹",
                                                        "added": [730], "removed": []})}],
    ["user-collections.uc-AbC123", {"key": "user-collections.uc-AbC123",
                                    "value": json.dumps({"id": "uc-AbC123", "name": "想玩",
                                                         "added": [570, 440], "removed": []})}],
]


class LocalFormatTests(unittest.TestCase):
    def test_legacy_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ns1.json"
            p.write_text(json.dumps(LEGACY), encoding="utf-8")
            schema, entries = read_collections(p)
            self.assertEqual(schema, "dict")
            self.assertEqual(entries[0]["name"], "收藏夹")

    def test_new_kv_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ns1.json"
            p.write_text(json.dumps(NEW_FORMAT, ensure_ascii=False), encoding="utf-8")
            schema, entries = read_collections(p)
            self.assertEqual(schema, "kv-pairs")
            self.assertEqual(len(entries), 2)
            self.assertEqual({e["name"] for e in entries}, {"收藏夹", "想玩"})

    def test_local_steam_appids(self):
        info = LocalSteam(collections=[
            {"name": "A", "added": [730, 570], "removed": []},
            {"name": "B", "added": ["440"], "removed": []},
        ])
        self.assertEqual(info.collection_appids, {730, 570, 440})

    def test_playtimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "localconfig.vdf"
            p.write_text('"apps"\n{\n\t"730"\n\t{\n\t\t"LastPlayed"\t\t"1700000000"\n\t\t"Playtime"\t\t"1234"\n\t}\n'
                         '\t"570"\n\t{\n\t\t"Playtime"\t\t"0"\n\t}\n}', encoding="utf-8")
            self.assertEqual(read_playtimes(p), {730: 1234, 570: 0})
            self.assertEqual(read_last_played(p), {730: 1700000000})

    def test_read_play_signals_and_detect_idled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            id3 = "999"
            stats = root / "appcache" / "stats"
            stats.mkdir(parents=True)
            # 101: schema + unlocked achievements
            (stats / "UserGameStatsSchema_101.bin").write_bytes(b"\x00achievements\x00")
            (stats / f"UserGameStats_{id3}_101.bin").write_bytes(b"\x00AchievementTimes\x00" + b"x" * 40)
            # 102: schema + 38-byte empty stub (idled)
            (stats / "UserGameStatsSchema_102.bin").write_bytes(b"\x00achievements\x00")
            (stats / f"UserGameStats_{id3}_102.bin").write_bytes(b"\x00cache\x00" + b"\x00" * 31)
            # 103: no achievements, but has local save under userdata/999/103/
            save_dir = root / "userdata" / id3 / "103" / "remote"
            save_dir.mkdir(parents=True)
            (save_dir / "save.dat").write_bytes(b"ok")
            # 104: librarycache shows total>0 unlocked=0 (idled)
            lib = root / "userdata" / id3 / "config" / "librarycache"
            lib.mkdir(parents=True)
            (lib / "achievement_progress.json").write_text(
                json.dumps({"nVersion": 3, "mapCache": [[104, {"appid": 104, "unlocked": 0, "total": 12}]]}),
                encoding="utf-8",
            )
            # 105..108: within 60..460m, no achievements, same batch day & identical playtime cluster
            # 111: short run (<60m) with schema -> NOT idled (just tried briefly)
            # 112: long run (>460m) with schema -> NOT idled (played past card drop window)
            (stats / "UserGameStatsSchema_111.bin").write_bytes(b"\x00achievements\x00")
            (stats / "UserGameStatsSchema_112.bin").write_bytes(b"\x00achievements\x00")
            # 114: has schema, standalone day, NO trading cards -> excluded when card_appids known
            (stats / "UserGameStatsSchema_114.bin").write_bytes(b"\x00achievements\x00")
            # 113: identical-playtime cluster in 115..360m without achievements or batch day
            batch_day_ts = 20000 * 86400 + 3600
            playtimes = {
                101: 120, 102: 135, 103: 90, 104: 200,
                105: 250, 106: 120, 107: 120, 108: 120,
                109: 600, 110: 0, 111: 15, 112: 600, 113: 250, 114: 205,
            }
            last_played = {
                105: batch_day_ts, 106: batch_day_ts + 10,
                107: batch_day_ts + 20, 108: batch_day_ts + 30,
                109: batch_day_ts - 86400, 113: batch_day_ts - 172800,
                114: batch_day_ts - 259200,
            }

            unlocked, defined, saved = read_play_signals(root, id3)
            self.assertIn(101, unlocked)
            self.assertTrue({101, 102, 104, 111, 112, 114}.issubset(defined))
            self.assertIn(103, saved)

            idled = detect_idled_appids(
                playtimes, unlocked, defined, saved, last_played,
                card_appids={102, 104, 113},
            )
            self.assertEqual(idled, {102, 104, 105, 106, 107, 108, 113})
            self.assertNotIn(111, idled)
            self.assertNotIn(112, idled)
            self.assertNotIn(114, idled)
            # Empty signals fall back to not marking anything idled
            self.assertEqual(detect_idled_appids(playtimes, set(), set(), set(), last_played), set())

    def test_steamid64_picks_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            p = Path(tmp) / "config/loginusers.vdf"
            p.write_text('"users"\n{\n\t"76561198000000001"\n\t{\n\t\t"MostRecent"\t\t"0"\n\t}\n'
                         '\t"76561198000000002"\n\t{\n\t\t"MostRecent"\t\t"1"\n\t}\n}', encoding="utf-8")
            self.assertEqual(read_steamid64(Path(tmp)), "76561198000000002")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle(EXAMPLES / "export_bundle_sample.json")

    def test_exact_package_mapping(self):
        self.assertEqual(self.bundle.meta["explicit_appid_rows"], 9)
        self.assertEqual(self.bundle.meta["unnamed_apps"], 0)
        self.assertTrue(all(m.via == "package" for m in self.bundle.matches))

    def test_plan_matches_manual_path(self):
        plan = build_plan(self.bundle.records, self.bundle.games, self.bundle.matches,
                          {1010}, {"cloudninearena"})
        by_id = {g["id"]: g["appids"] for g in plan.groups}
        self.assertEqual(by_id["store"], [1001, 1002])
        self.assertEqual(by_id["retail"], [1003, 1004])
        self.assertEqual(by_id["free"], [1005, 1006, 1009])
        self.assertEqual(by_id["gift"], [1007, 1008])
        self.assertEqual(by_id["family"], [1010])
        self.assertEqual(by_id["unknown"], [1011, 1012])

    def test_missing_names_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bundle.json"
            p.write_text(json.dumps({
                "licenses": [{"date": "2024-01-01", "name": "Some Game", "method": "零售"}],
                "owned_appids": [123456],
                "app_names": {},
            }), encoding="utf-8")
            b = load_bundle(p)
            self.assertEqual(b.meta["unnamed_apps"], 1)
            self.assertEqual(b.games[0].name, "App 123456")

    def test_v2_metadata_is_read(self):
        m = self.bundle.meta
        self.assertEqual(m["license_pages"], 1)
        self.assertEqual(m["license_total"], 11)
        self.assertEqual(m["name_source"], "webapi")
        self.assertGreater(m["playtime_count"], 0)
        self.assertEqual(m["warnings"], [])

    def test_incomplete_export_is_flagged(self):
        """v1 bookmarklet on a large library: only page 1 + almost no names."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bundle.json"
            p.write_text(json.dumps({
                "licenses": [
                    {"date": "2026-11-08", "name": "Probably Stolen", "method": "Steam 商店"},
                    {"date": "2026-10-31", "name": "Promo Pack", "method": "免费赠送"},
                ],
                "owned_appids": list(range(1000, 1100)),
                "app_names": {"1000": "Only One Named Game"},
            }), encoding="utf-8")
            b = load_bundle(p)
            self.assertEqual(len(b.meta["warnings"]), 2)
            self.assertIn("分页", b.meta["warnings"][0])
            self.assertIn("没有名称", b.meta["warnings"][1])

    def test_discover_switches_account_by_preferred_steamid64(self):
        sid1 = "76561198000000001"
        sid2 = "76561198000000002"
        id3_1 = str(int(sid1) - STEAMID64_BASE)
        id3_2 = str(int(sid2) - STEAMID64_BASE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            (root / "config/loginusers.vdf").write_text(
                f'"users"\n{{\n\t"{sid1}"\n\t{{\n\t\t"MostRecent"\t\t"0"\n\t}}\n'
                f'\t"{sid2}"\n\t{{\n\t\t"MostRecent"\t\t"1"\n\t}}\n}}',
                encoding="utf-8",
            )
            (root / "userdata" / id3_1 / "config").mkdir(parents=True)
            (root / "userdata" / id3_2 / "config").mkdir(parents=True)
            info = discover(root, preferred_steamid64=sid1)
            self.assertEqual(info.steamid64, sid1)
            self.assertEqual(info.id3, id3_1)
            self.assertTrue(any("已自动匹配到导出包所属的 Steam 账号目录" in n for n in info.notes))

    def test_bundle_uses_embedded_package_apps_without_local_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bundle.json"
            p.write_text(json.dumps({
                "licenses": [
                    {"date": "2025-01-01", "name": "Some Package",
                     "method": "Steam 商店", "packageid": 77001},
                ],
                "owned_appids": [9001],
                "app_names": {},
                "package_apps": {"77001": [{"id": 9001, "name": "Embedded Hit"}]},
            }), encoding="utf-8")
            b = load_bundle(p, local=None)
            self.assertEqual(b.matches[0].via, "package")
            self.assertEqual([g.appid for g in b.matches[0].games], [9001])
            self.assertEqual(b.games[0].name, "Embedded Hit")
            self.assertEqual(b.meta["local_cache_packages"], 0)
            self.assertEqual(b.meta["bundle_cache_packages"], 1)


class NewFormatWritebackTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle(EXAMPLES / "export_bundle_sample.json")
        self.plan = build_plan(self.bundle.records, self.bundle.games,
                               self.bundle.matches, {1010}, {"cloudninearena"})

    @patch("steam_source_collections.writeback._steam_is_running", return_value=True)
    def test_preview_allows_append_only_new_format(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ns1.json"
            p.write_text(json.dumps(NEW_FORMAT, ensure_ascii=False), encoding="utf-8")
            info = preview(p, self.plan)
            self.assertTrue(info["writable"])
            self.assertTrue(info["requires_steam_closed"])
            self.assertIn("kv-pairs", info["format"])
            self.assertIn("追加", info["format"])
            self.assertEqual(info["existing_collections"], 2)
            # Still refuses the actual write while the Steam client is open.
            with self.assertRaises(WritebackError):
                apply(p, self.plan, Path(tmp) / "out")

    def test_legacy_still_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ns1.json"
            p.write_text(json.dumps(LEGACY), encoding="utf-8")
            info = preview(p, self.plan)
            self.assertTrue(info["writable"])
            self.assertEqual(info["format"].split("（")[0], "dict")


if __name__ == "__main__":
    unittest.main()
