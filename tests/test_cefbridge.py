"""Tests for writing collections through the running Steam client.

All CDP calls are mocked: these tests never touch a real Steam client.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from steam_source_collections import cefbridge, service
from steam_source_collections.plan import Plan

PREFIX = cefbridge.PREFIX


def plan(appids=(1001, 1002)) -> Plan:
    return Plan(
        groups=[
            {"id": "store", "name": "① Steam 商店购买", "kind": "source",
             "appids": list(appids), "games": []},
            {"id": "key", "name": "② CDK·Key 激活", "kind": "source",
             "appids": [2001], "games": []},
            {"id": "cross", "name": "③b 吃灰区", "kind": "cross",
             "appids": [3001], "games": []},
        ],
        assignments={}, unmatched_licenses=[], unmatched_games=[], stats={},
    )


def analysis(appids=(1001, 1002)):
    return SimpleNamespace(
        plan=plan(appids),
        local=SimpleNamespace(cloud_path=None),
    )


class MergePlanTests(unittest.TestCase):
    def test_creates_new_collections_without_touching_cross_groups(self):
        items = service.merge_plan_with_client(plan(), [])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], PREFIX + "① Steam 商店购买")
        self.assertEqual(items[0]["added"], [1001, 1002])
        self.assertTrue(items[0]["id"].startswith("uc-"))

    def test_include_cross_groups_when_enabled(self):
        items = service.merge_plan_with_client(plan(), [], include_cross=True)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[2]["name"], PREFIX + "③b 吃灰区")
        self.assertEqual(items[2]["added"], [3001])

    def test_manual_add_and_remove_are_preserved(self):
        existing = [
            {"id": "uc-A", "name": PREFIX + "① Steam 商店购买",
             "added": [1001, 1002, 9999], "removed": []},
            {"id": "uc-B", "name": PREFIX + "② CDK·Key 激活",
             "added": [], "removed": []},
        ]
        items = service.merge_plan_with_client(plan(), existing)
        self.assertEqual(items[0]["id"], "uc-A")
        self.assertEqual(items[0]["added"], [1001, 1002, 9999])
        self.assertEqual(items[1]["id"], "uc-B")
        self.assertEqual(items[1]["added"], [])  # user removed 2001

    def test_baseline_lets_new_games_in_but_keeps_manual_removals(self):
        existing = [
            {"id": "uc-A", "name": PREFIX + "① Steam 商店购买",
             "added": [1001, 1002], "removed": []},
            {"id": "uc-B", "name": PREFIX + "② CDK·Key 激活",
             "added": [], "removed": []},
        ]
        baseline = {"① Steam 商店购买": [1001, 1002],
                    "② CDK·Key 激活": [2001]}
        items = service.merge_plan_with_client(
            plan((1001, 1002, 1003)), existing, baseline
        )
        self.assertEqual(items[0]["added"], [1001, 1002, 1003])
        self.assertEqual(items[1]["added"], [])

    def test_renamed_source_group_reuses_id_and_preserves_edits(self):
        renamed_plan = Plan(
            groups=[
                {"id": "store", "name": "Steam购买", "kind": "source",
                 "appids": [1001, 1002, 1003], "games": []},
                {"id": "retail", "name": "CDK激活", "kind": "source",
                 "appids": [2001], "games": []},
            ],
            assignments={}, unmatched_licenses=[], unmatched_games=[], stats={},
        )
        existing = [
            {"id": "uc-old-store", "name": cefbridge.LEGACY_PREFIX + "① Steam 商店购买",
             "added": [1001, 9999], "removed": []},
            {"id": "uc-old-retail", "name": "② CDK激活",
             "added": [2001], "removed": []},
        ]
        baseline = {"① Steam 商店购买": [1001, 1002], "② CDK激活": [2001]}
        items = service.merge_plan_with_client(renamed_plan, existing, baseline)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "uc-old-store")
        self.assertEqual(items[0]["name"], PREFIX + "Steam购买")
        # 1002 manually removed stays removed; 9999 manually added stays; 1003 new joins
        self.assertEqual(items[0]["added"], [1001, 1003, 9999])
        self.assertEqual(items[1]["id"], "uc-old-retail")
        self.assertEqual(items[1]["name"], PREFIX + "CDK激活")

    def test_selected_groups_filters_written_collections(self):
        items = service.merge_plan_with_client(
            plan(), [], selected_groups=["store", "cross"]
        )
        self.assertEqual([item["name"] for item in items], [
            PREFIX + "① Steam 商店购买", PREFIX + "③b 吃灰区",
        ])


class CEFBridgeTests(unittest.TestCase):
    def test_debugger_requires_steam_user_agent(self):
        with patch.object(cefbridge, "_http_json",
                          return_value={"User-Agent": "Syncthing", "Browser": "Chrome/1"}):
            self.assertFalse(cefbridge.debugger_available())
        with patch.object(cefbridge, "_http_json", return_value={
                "User-Agent": "Mozilla/5.0 ... Valve Steam Client Safari/537.36"}):
            self.assertTrue(cefbridge.debugger_available())

    def test_save_many_builds_client_side_expression(self):
        with patch.object(cefbridge, "evaluate",
                          return_value=[{"ok": True}]) as mocked:
            cefbridge.save_many([
                {"id": "uc-X", "name": PREFIX + "X", "added": [1, 2], "removed": []}
            ])
        expression = mocked.call_args[0][0]
        compact = expression.replace(" ", "")
        self.assertIn("StoreObject", expression)
        self.assertIn("SaveCollection", expression)
        self.assertIn('"added":[1,2]', compact)
        self.assertIn(PREFIX, expression)

    def test_list_managed_filters_by_prefix(self):
        payload = [
            {"id": "uc-A", "name": PREFIX + "①", "added": [1], "removed": []},
            {"id": "fav", "name": "收藏夹", "added": [], "removed": []},
        ]
        with patch.object(cefbridge, "evaluate", return_value=payload) as mocked:
            result = cefbridge.list_managed()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "uc-A")
        self.assertIn("startsWith", mocked.call_args[0][0])


class ClientWriteFlowTests(unittest.TestCase):
    def test_preflight_chooses_client_channel(self):
        with patch.object(cefbridge, "debugger_available", return_value=True):
            pre = service.preflight(analysis())
        self.assertEqual(pre["via"], "client")
        self.assertTrue(pre["writable"])
        self.assertFalse(pre["requires_steam_closed"])
        self.assertEqual(len(pre["collections_to_write"]), 2)

    def test_preflight_falls_back_to_file_channel(self):
        with patch.object(cefbridge, "debugger_available", return_value=False):
            pre = service.preflight(analysis())
        self.assertEqual(pre["via"], "file")
        self.assertFalse(pre["writable"])

    def test_apply_uses_client_without_requiring_steam_to_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = []
            with patch.object(cefbridge, "debugger_available", return_value=True), \
                 patch.object(cefbridge, "list_managed", return_value=[]), \
                 patch.object(cefbridge, "save_many",
                              side_effect=lambda items: (
                                  saved.extend(items) or
                                  [{"id": item["id"], "name": item["name"], "ok": True}
                                   for item in items])), \
                 patch.object(service, "steam_status", return_value=True) as status:
                result = service.apply_plan(analysis(), tmp, confirm=True)
            self.assertEqual(result["via"], "client")
            self.assertEqual(len(result["collections"]), 2)
            status.assert_not_called()
            self.assertEqual(len(saved), 2)
            self.assertTrue((Path(tmp) / service.BASELINE_NAME).is_file())

    def test_client_write_creates_snapshot_and_restore_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing_before = [
                {"id": "uc-old", "name": PREFIX + "① Steam 商店购买",
                 "added": [999], "removed": []},
            ]
            with patch.object(cefbridge, "list_managed", return_value=existing_before), \
                 patch.object(cefbridge, "save_many",
                              side_effect=lambda items: [
                                  {"id": item["id"], "name": item["name"], "ok": True}
                                  for item in items]):
                write_res = service.client_write_plan(analysis(), tmp)
            self.assertEqual(len(write_res["backups"]), 1)
            bak_path = Path(write_res["backups"][0])
            self.assertTrue(bak_path.is_file())
            self.assertIn("client-collections.", bak_path.name)

            existing_after = [
                {"id": "uc-old", "name": PREFIX + "① Steam 商店购买",
                 "added": [999, 1001, 1002], "removed": []},
                {"id": "uc-new", "name": PREFIX + "② CDK·Key 激活",
                 "added": [2001], "removed": []},
            ]
            restored_saved = []
            deleted_ids = []
            with patch.object(cefbridge, "debugger_available", return_value=True), \
                 patch.object(cefbridge, "list_managed", return_value=existing_after), \
                 patch.object(cefbridge, "save_many",
                              side_effect=lambda items: (
                                  restored_saved.extend(items) or
                                  [{"id": item["id"], "name": item["name"], "ok": True}
                                   for item in items])), \
                 patch.object(cefbridge, "delete_many",
                              side_effect=lambda ids: (
                                  deleted_ids.extend(ids) or
                                  [{"id": cid, "ok": True} for cid in ids])):
                restore_res = service.restore_latest(None, tmp)
            self.assertTrue(restore_res["ok"])
            self.assertEqual(restore_res["via"], "client")
            self.assertEqual(len(restored_saved), 1)
            self.assertEqual(restored_saved[0]["id"], "uc-old")
            self.assertEqual(restored_saved[0]["added"], [999])
            self.assertEqual(deleted_ids, ["uc-new"])

    def test_client_write_cleans_up_stale_managed_collections(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing_before = [
                {"id": "uc-store", "name": PREFIX + "① Steam 商店购买", "added": [1001], "removed": []},
                {"id": "uc-stale", "name": PREFIX + "③b 免费领取·从未启动（吃灰区）", "added": [5001], "removed": []},
            ]
            deleted = []
            with patch.object(cefbridge, "list_managed", return_value=existing_before), \
                 patch.object(cefbridge, "save_many",
                              side_effect=lambda items: [
                                  {"id": item["id"], "name": item["name"], "ok": True}
                                  for item in items]), \
                 patch.object(cefbridge, "delete_many",
                              side_effect=lambda ids: (
                                  deleted.extend(ids) or [{"id": cid, "ok": True} for cid in ids])):
                service.client_write_plan(analysis(), tmp, include_cross=False)
            self.assertEqual(deleted, ["uc-stale"])


if __name__ == "__main__":
    unittest.main()
