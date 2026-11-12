"""A write is only useful if Steam uploads it to the account.

Steam keeps the last synced revision of each cloud namespace in
``cloud-storage-namespaces.json``. Editing ``cloud-storage-namespace-1.json``
without raising that counter leaves the new collections local-only, so a
Steam Deck signed into the same account keeps showing the old library.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam_source_collections import service
from steam_source_collections.plan import Plan
from steam_source_collections.writeback import apply

MAIN = "cloud-storage-namespace-1.json"
INDEX = "cloud-storage-namespaces.json"


def plan() -> Plan:
    return Plan(
        groups=[
            {"id": "store", "name": "① Steam 商店购买", "kind": "source",
             "appids": [1001, 1002], "games": []},
            {"id": "key", "name": "② CDK·Key 激活", "kind": "source",
             "appids": [2001], "games": []},
        ],
        assignments={}, unmatched_licenses=[], unmatched_games=[], stats={},
    )


def make_store(root: Path, index=None):
    main = root / MAIN
    original = [
        ["GameReleased", {"key": "GameReleased", "timestamp": 1,
                          "value": "{}", "version": "10"}],
        ["user-collections.favorite", {
            "key": "user-collections.favorite", "timestamp": 2,
            "value": json.dumps({"id": "favorite", "name": "收藏夹",
                                 "added": [], "removed": []}, ensure_ascii=False),
            "version": "30",
        }],
    ]
    main.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    if index is not None:
        (root / INDEX).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return main, original


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def counter(path: Path) -> int:
    for item in read_json(path):
        if isinstance(item, list) and len(item) >= 2 and str(item[0]) == "1":
            return int(str(item[1]))
    raise AssertionError("namespace 1 counter missing")


class CloudSyncTests(unittest.TestCase):
    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_counter_is_raised_and_both_files_are_backed_up(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, original = make_store(root, [[3, "0"], [1, "20"], [4, "0"]])
            outdir = root / "out"
            original_index = read_json(root / INDEX)

            backups = apply(main, plan(), outdir)
            self.assertEqual(len(backups), 2)
            for backup in backups:
                self.assertEqual(read_json(backup), original)

            written = read_json(main)
            versions = [int(m["version"]) for _, m in written
                        if isinstance(m, dict) and str(m.get("version", "")).isdigit()]
            self.assertGreater(counter(root / INDEX), 20)
            self.assertGreaterEqual(counter(root / INDEX), max(versions))
            # Other namespaces and ordering are untouched.
            now = read_json(root / INDEX)
            self.assertEqual(now[0], original_index[0])
            self.assertEqual(now[2], original_index[2])

            for folder in (root / "backups", outdir / "backups"):
                self.assertEqual(len(list(folder.glob(f"{MAIN}.*.bak"))), 1)
                self.assertEqual(len(list(folder.glob(f"{INDEX}.*.bak"))), 1)
                self.assertEqual(read_json(next(folder.glob(f"{INDEX}.*.bak"))),
                                 original_index)

            restore = (outdir / "restore-collections.bat").read_text(encoding="utf-8")
            self.assertIn(MAIN, restore)
            self.assertIn(INDEX, restore)
            self.assertEqual(restore.count("copy /Y"), 2)

            new_entries = written[len(original):]
            for _, meta in new_entries:
                self.assertEqual(meta["conflictResolutionMethod"], "custom")
                self.assertEqual(meta["strMethodId"], "union-collections")

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    @patch("steam_source_collections.service.steam_is_running", return_value=False)
    def test_restore_rolls_back_both_files(self, _svc_running, _wb_running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, original = make_store(root, [[3, "0"], [1, "20"], [4, "0"]])
            outdir = root / "out"
            original_index = read_json(root / INDEX)

            apply(main, plan(), outdir)
            self.assertNotEqual(read_json(main), original)
            self.assertNotEqual(read_json(root / INDEX), original_index)

            result = service.restore_latest(main, outdir)
            self.assertTrue(result["ok"])
            self.assertTrue(result["index_restored"])
            self.assertEqual(read_json(main), original)
            self.assertEqual(read_json(root / INDEX), original_index)

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_missing_namespace_entry_is_added(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, original = make_store(root, [[3, "0"], [4, "0"]])
            apply(main, plan(), root / "out")
            self.assertGreaterEqual(counter(root / INDEX), 31)

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_counter_is_never_lowered(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, _ = make_store(root, [[3, "0"], [1, "99999"], [4, "0"]])
            apply(main, plan(), root / "out")
            self.assertEqual(counter(root / INDEX), 99999)

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_missing_index_file_is_tolerated(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, original = make_store(root)  # no index file
            outdir = root / "out"
            apply(main, plan(), outdir)
            self.assertEqual(len(read_json(main)), len(original) + 2)
            restore = (outdir / "restore-collections.bat").read_text(encoding="utf-8")
            self.assertEqual(restore.count("copy /Y"), 1)


if __name__ == "__main__":
    unittest.main()
