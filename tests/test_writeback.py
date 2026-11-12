"""Tests for the experimental, opt-in Steam writeback adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam_source_collections.plan import Plan
from steam_source_collections.writeback import PREFIX, WritebackError, apply, preview


def fake_plan() -> Plan:
    return Plan(
        groups=[
            {
                "id": "store",
                "name": "① Steam 商店购买",
                "kind": "source",
                "appids": [1001, 1002],
                "games": [],
            }
        ],
        assignments={},
        unmatched_licenses=[],
        unmatched_games=[],
        stats={},
    )


def write_fake_steam_file(path: Path) -> None:
    data = {
        "version": "1",
        "data": {
            "collections": {
                "user-collections": {
                    "sys-favorites": {"name": "收藏夹", "added": [1001], "removed": []},
                    "old-managed": {"name": PREFIX + "旧分组", "added": [9999], "removed": []},
                }
            }
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class WritebackTests(unittest.TestCase):
    def test_preview_probes_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cloud-storage-namespace-1.json"
            write_fake_steam_file(target)
            info = preview(target, fake_plan())
            self.assertEqual(info["container"], "/data/collections/user-collections")
            self.assertEqual(info["list_key"], "added")
            self.assertEqual(info["appid_type"], "int")
            self.assertEqual(info["existing_managed_collections"], [PREFIX + "旧分组"])

    def test_apply_keeps_system_collections_and_makes_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "cloud-storage-namespace-1.json"
            write_fake_steam_file(target)
            backups = apply(target, fake_plan(), root / "out")
            self.assertEqual(len(backups), 2)
            self.assertTrue(all(backup.is_file() for backup in backups))

            written = json.loads(target.read_text(encoding="utf-8"))
            collections = written["data"]["collections"]["user-collections"]
            self.assertEqual(written["version"], "1")
            self.assertEqual(collections["sys-favorites"]["name"], "收藏夹")
            self.assertNotIn("old-managed", collections)
            self.assertEqual(collections["src_store"]["name"], PREFIX + "① Steam 商店购买")
            self.assertEqual(collections["src_store"]["added"], [1001, 1002])

    def test_unknown_structure_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cloud-storage-namespace-1.json"
            target.write_text(json.dumps({"unrelated": True}), encoding="utf-8")
            with self.assertRaises(WritebackError):
                preview(target, fake_plan())


def write_kv_pairs_file(path: Path) -> list:
    """Mirror the modern Steam client's [key, meta] cloud store."""
    data = [
        ["GameReleased", {"key": "GameReleased", "timestamp": 1,
                          "value": "{}", "version": "10"}],
        ["user-collections.favorite", {
            "key": "user-collections.favorite", "timestamp": 2,
            "value": json.dumps({"id": "favorite", "name": "收藏夹",
                                 "added": [], "removed": []}, ensure_ascii=False),
            "version": "20",
        }],
        ["user-collections.hidden", {
            "key": "user-collections.hidden", "timestamp": 3,
            "value": json.dumps({"id": "hidden", "name": "已隐藏",
                                 "added": [1001], "removed": []}, ensure_ascii=False),
            "version": "30",
        }],
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


class KVPairsWritebackTests(unittest.TestCase):
    """The new-format store must be append-only, backed up and reversible."""

    def plan(self) -> Plan:
        return Plan(
            groups=[
                {"id": "store", "name": "① Steam 商店购买", "kind": "source",
                 "appids": [1001, 1002], "games": []},
                {"id": "free", "name": "③ 免费领取·喜加一", "kind": "source",
                 "appids": [2001], "games": []},
                {"id": "extra", "name": "③b 吃灰区", "kind": "cross",
                 "appids": [3001], "games": []},
            ],
            assignments={}, unmatched_licenses=[], unmatched_games=[], stats={},
        )

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_preview_reports_writable_and_only_source_groups(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cloud-storage-namespace-1.json"
            write_kv_pairs_file(target)
            info = preview(target, self.plan())
            self.assertTrue(info["writable"])
            self.assertTrue(info["requires_steam_closed"])
            self.assertNotIn("steam_running", info)
            _running.assert_not_called()  # file preflight never probes the process
            self.assertEqual(len(info["collections_to_write"]), 2)
            self.assertEqual(info["existing_collections"], 2)

    @patch("steam_source_collections.writeback._steam_is_running", return_value=False)
    def test_append_only_idempotent_with_backups_and_restore(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "cloud-storage-namespace-1.json"
            original = write_kv_pairs_file(target)
            outdir = root / "out"

            backups = apply(target, self.plan(), outdir)
            self.assertEqual(len(backups), 2)
            for backup in backups:
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

            written = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(written), len(original) + 2)
            self.assertEqual(written[: len(original)], original)
            new_entries = written[len(original):]
            self.assertTrue(all(e[0].startswith("user-collections.uc-") for e in new_entries))
            values = [json.loads(e[1]["value"]) for e in new_entries]
            self.assertTrue(all(v["name"].startswith(PREFIX) for v in values))
            self.assertEqual(values[0]["added"], [1001, 1002])
            self.assertTrue(all(int(e[1]["version"]) > 30 for e in new_entries))

            restore = outdir / "restore-collections.bat"
            self.assertTrue(restore.is_file())
            self.assertIn("copy /Y", restore.read_text(encoding="utf-8"))

            # Re-running replaces the tool's own keys instead of duplicating.
            apply(target, self.plan(), outdir)
            again = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(again), len(original) + 2)
            self.assertEqual(again[: len(original)], original)

    @patch("steam_source_collections.writeback._steam_is_running", return_value=True)
    def test_refuses_to_write_while_steam_is_running(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "cloud-storage-namespace-1.json"
            original = write_kv_pairs_file(target)
            with self.assertRaises(WritebackError):
                apply(target, self.plan(), root / "out")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)


class NoConsoleWindowTests(unittest.TestCase):
    """The GUI polls this every 12s; it must not spawn a console process."""

    def test_steam_detection_stays_in_process(self):
        from steam_source_collections import writeback

        with patch.object(writeback.os, "name", "nt"), \
             patch.object(writeback, "_windows_process_exists", return_value=True), \
             patch.object(writeback.subprocess, "run",
                          side_effect=AssertionError("spawned a console process")) as run:
            self.assertTrue(writeback._steam_is_running())
            run.assert_not_called()

    def test_hidden_console_kwargs_are_platform_safe(self):
        import subprocess

        from steam_source_collections import writeback

        kwargs = writeback.hidden_console_kwargs()
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)
        else:
            self.assertEqual(kwargs, {})


if __name__ == "__main__":
    unittest.main()
