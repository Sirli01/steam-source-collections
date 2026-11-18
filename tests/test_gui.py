"""Tests for the desktop GUI's service layer and its HTTP guard rails."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from steam_source_collections import cefbridge, gui, service
from steam_source_collections.local import LocalSteam
from steam_source_collections.plan import build_plan
from steam_source_collections.bundle import load_bundle

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "export_bundle_sample.json"


def tiny_bundle(path: Path) -> None:
    path.write_text(json.dumps({
        "schema": "steam-license-export/3",
        "licenses": [{"date": "2026-01-01", "name": "Sample Game",
                      "method": "Steam Store", "packageid": "36", "appids": [70]}],
        "owned_appids": [70, 400],
        "app_names": {"70": "Sample Game", "400": "Another Game"},
        "playtimes": {"70": 12}, "owned_packages": ["36"],
        "license_pages": 1, "license_total": 1,
    }, ensure_ascii=False), encoding="utf-8")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        # Unit tests must never reach the user's real, running Steam client.
        patcher = patch("steam_source_collections.cefbridge.debugger_available",
                        return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_locate_bundle_remembers_uploaded_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            (out / "uploaded").mkdir(parents=True)
            saved = out / "uploaded" / "steam-licenses-saved.json"
            tiny_bundle(saved)
            with patch.object(service, "find_latest_bundle", return_value=None):
                self.assertEqual(service.locate_bundle(out), saved)
            self.assertEqual(service.locate_bundle(out, str(saved)), saved)

    def test_analyse_matches_direct_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            bundle = Path(tmp) / "steam-licenses-test.json"
            tiny_bundle(bundle)
            fake_local = LocalSteam()
            with patch.object(service, "discover", return_value=fake_local), \
                 patch.object(service, "locate_bundle", return_value=bundle):
                analysis = service.analyse(outdir=out)
            direct = load_bundle(bundle, fake_local)
            expected = build_plan(direct.records, direct.games, direct.matches, set(), set())
            self.assertEqual(
                {g["id"]: sorted(g["appids"]) for g in analysis.plan.groups},
                {g["id"]: sorted(g["appids"]) for g in expected.groups},
            )
            self.assertTrue((out / "report.html").is_file())
            self.assertTrue((out / "mapping.csv").is_file())
            self.assertEqual(len(analysis.groups()), len(expected.groups))
            self.assertIn("家庭共享", " ".join(analysis.notes))

    def test_scan_never_probes_steam_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "steam-licenses-x.json"
            tiny_bundle(bundle)
            with patch.object(service, "discover", return_value=LocalSteam()), \
                 patch.object(service, "locate_bundle", return_value=bundle), \
                 patch.object(service, "steam_is_running",
                              side_effect=AssertionError("background probe")):
                state = service.scan(outdir=Path(tmp) / "out")
            self.assertEqual(state["bundle"]["license_rows"], 1)
            self.assertTrue(state["bundle"]["needs_update"] is False)
            self.assertNotIn("steam_running", state)
            self.assertEqual(state["license_page"],
                             "https://store.steampowered.com/account/licenses/")

    def test_apply_guards_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            bundle = Path(tmp) / "steam-licenses-test.json"
            tiny_bundle(bundle)
            cloud = Path(tmp) / "cloud-storage-namespace-1.json"
            original = json.dumps([
                ["user-collections.uc-old", {
                    "key": "user-collections.uc-old", "timestamp": 1, "version": "3",
                    "value": json.dumps({"id": "uc-old", "name": "Favourites",
                                         "added": [70], "removed": []}),
                }],
                ["other.key", {"key": "other.key", "value": "x", "version": "4"}],
            ], ensure_ascii=False)
            cloud.write_text(original, encoding="utf-8")
            local = LocalSteam(cloud_path=cloud, cloud_format="kv-pairs")
            with patch.object(service, "discover", return_value=local), \
                 patch.object(service, "locate_bundle", return_value=bundle), \
                 patch.object(service, "steam_is_running", return_value=False), \
                 patch("steam_source_collections.writeback._steam_is_running",
                       return_value=False):
                analysis = service.analyse(outdir=out)
            analysis.local = local
            with self.assertRaises(service.ServiceError):
                service.apply_plan(analysis, out, confirm=False)
            with patch.object(service, "steam_is_running", return_value=True), \
                 patch("steam_source_collections.writeback._steam_is_running",
                       return_value=True):
                with self.assertRaisesRegex(service.ServiceError, "还在运行"):
                    service.apply_plan(analysis, out, confirm=True)
            with patch.object(service, "steam_is_running", return_value=False), \
                 patch("steam_source_collections.writeback._steam_is_running",
                       return_value=False):
                result = service.apply_plan(analysis, out, confirm=True)
                self.assertTrue(result["ok"])
                self.assertEqual(len(result["backups"]), 2)
                written = json.loads(cloud.read_text(encoding="utf-8"))
                self.assertTrue(any(
                    isinstance(item, list) and isinstance(item[1], dict)
                    and cefbridge.PREFIX in str(item[1].get("value", ""))
                    for item in written))
                self.assertTrue(any(item[0] == "user-collections.uc-old" for item in written))
                restored = service.restore_latest(cloud, out)
                self.assertTrue(restored["ok"])
                self.assertEqual(cloud.read_text(encoding="utf-8"), original)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("steam_source_collections.cefbridge.debugger_available",
                        return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name) / "out"
        self.outdir.mkdir()
        self.state = gui.GuiState("unit-token", self.outdir)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
        self.server.daemon_threads = True
        self.server.state = self.state  # type: ignore[attr-defined]
        self.state.server = self.server
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, path, token="unit-token", host=None, post=None):
        data = json.dumps(post).encode("utf-8") if post is not None else None
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"X-SSC-Token": token, "Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def test_root_requires_token_and_injects_it(self):
        status, body = self.request("/", token="wrong")
        self.assertEqual(status, 403)
        status, body = self.request("/?token=unit-token", token="")
        self.assertEqual(status, 200)
        self.assertNotIn("__TOKEN__", body)
        self.assertIn("unit-token", body)
        self.assertIn("一键统计", body)

    def test_state_and_ping(self):
        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIn("steam", data["state"])
        self.assertEqual(self.request("/api/ping")[0], 200)

    def test_bad_host_or_origin_is_rejected(self):
        self.assertEqual(self.request("/api/state", host="evil.example")[0], 403)

    def test_upload_then_report_end_to_end(self):
        bundle = Path(self.tmp.name) / "steam-licenses-drop.json"
        tiny_bundle(bundle)
        status, body = self.request("/api/upload", post={
            "name": bundle.name, "text": bundle.read_text(encoding="utf-8")})
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["state"]["bundle"])
        with patch.object(service, "discover", return_value=LocalSteam()):
            status, body = self.request("/api/report", post={})
        self.assertEqual(status, 200, body[:300])
        result = json.loads(body)
        self.assertTrue(result["groups"])
        self.assertTrue((self.outdir / "report.html").is_file())
        status, html = self.request("/api/report-file?token=unit-token")
        self.assertEqual(status, 200)
        self.assertIn("Steam 收藏集", html)

    def test_steam_is_checked_only_when_the_user_tries_to_write(self):
        bundle = Path(self.tmp.name) / "steam-licenses-drop.json"
        tiny_bundle(bundle)
        with patch.object(service, "discover", return_value=LocalSteam()):
            self.request("/api/upload", post={
                "name": bundle.name, "text": bundle.read_text(encoding="utf-8")})
            status, body = self.request("/api/report", post={})
        self.assertEqual(status, 200, body[:200])

        with patch.object(service, "steam_is_running", return_value=True) as probe:
            status, body = self.request("/api/state")
            self.assertEqual(status, 200)
            probe.assert_not_called()  # no polling on a plain page load
            status, body = self.request("/api/preflight", post={})
            self.assertEqual(probe.call_count, 1)
        self.assertEqual(status, 200, body[:200])
        self.assertTrue(json.loads(body)["running"])

        with patch.object(service, "steam_is_running", return_value=True):
            status, body = self.request("/api/apply", post={"confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("还没有关闭", body)

    def test_apply_without_confirm_is_refused(self):
        with self.assertRaises(service.ServiceError):
            service.apply_plan(None, self.outdir, confirm=False)  # type: ignore[arg-type]

    def test_bookmark_source_is_identical_everywhere(self):
        standalone = (ROOT / "steam-export.user.js").read_text(encoding="utf-8").strip()
        packaged = (ROOT / "steam_source_collections" / "bookmark_src.js").read_text(
            encoding="utf-8").strip()
        page = (ROOT / "try-it.html").read_text(encoding="utf-8")
        tag = '<script id="bmSource" type="text/plain">'
        start = page.index(tag) + len(tag)
        embedded = page[start:page.index("</script>", start)].strip()
        self.assertEqual(standalone, packaged)
        self.assertEqual(standalone, embedded)
        self.assertIn("v4", standalone)

    def test_user_facing_copy_has_no_development_history(self):
        forbidden = ("600 款", "不再只补前 600", "版本历史", "v3 及更早", "上限")
        for name in ("README.md", "try-it.html", "steam-export.user.js",
                     "steam_source_collections/gui_page.html",
                     "steam_source_collections/cli.py",
                     "steam_source_collections/service.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{name} still explains {phrase}")

    def test_gui_assets_are_packaged(self):
        self.assertTrue((ROOT / "steam_source_collections" / "gui_page.html").is_file())
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("gui_page.html", pyproject)
        self.assertIn("bookmark_src.js", pyproject)
        self.assertIn("steam-collections-gui", pyproject)

    @patch("steam_source_collections.service.subprocess.run")
    def test_shortcut_command_does_not_touch_disk_in_tests(self, run):
        from types import SimpleNamespace
        run.return_value = SimpleNamespace(returncode=0, stdout=b"C:\\Desktop\\Steam.lnk\r\n",
                                          stderr=b"")
        with patch.object(service.os, "name", "nt"):
            label = service.desktop_shortcut()
        self.assertIn("Steam.lnk", label)
        command = run.call_args[0][0][-1]
        self.assertIn("-m steam_source_collections.gui", command)
        self.assertIn("WorkingDirectory", command)


if __name__ == "__main__":
    unittest.main()
