"""首次使用的引导必须显式、按真实点击顺序、且能解释失败原因。

These are static/structural checks: they never touch the user's real Steam
files and never connect to the local Steam debugger.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "steam_source_collections" / "gui_page.html"
README = ROOT / "README.md"


class FirstRunGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_guide_is_the_first_card_on_screen(self):
        self.assertIn('id="startBox"', self.html)
        self.assertIn('<span class="num">00</span>', self.html)
        self.assertLess(self.html.index('id="startBox"'),
                        self.html.index('id="analyse"'),
                        "导出引导必须排在一键统计之前")
        self.assertIn('id="startState"', self.html)

    def test_guide_shows_by_default(self):
        # 首屏默认展开：即使服务或 JS 出错，新用户也能看到步骤
        box = re.search(r'<div id="dataBox"[^>]*>', self.html)
        self.assertIsNotNone(box)
        self.assertNotIn("display:none", box.group(0))
        self.assertIn('id="startBox"', self.html)
        self.assertIn("needs-setup", self.html)

    def test_steps_follow_the_real_click_order(self):
        steps = self.html[self.html.index("<ol class=\"steps\">"):
                          self.html.index("</ol>")]
        self.assertIn("拖到浏览器书签栏", steps)
        self.assertIn("打开 Steam 许可页", steps)
        self.assertIn("别在这个工具页上点它", steps)
        self.assertIn("steam-licenses-*.json", steps)
        self.assertIn("直接点「一键统计 →」", steps)
        self.assertIn("不用你去翻目录", steps)
        # 顺序：先拖书签，再打开许可页，最后回来统计
        self.assertLess(steps.index("拖到浏览器书签栏"), steps.index("打开 Steam 许可页"))
        self.assertLess(steps.index("打开 Steam 许可页"), steps.index("一键统计"))

    def test_guide_explains_why_and_offers_fallbacks(self):
        self.assertIn("浏览器不允许本地工具去读登录状态", self.html)
        self.assertIn("只需做一次", self.html)
        for label in ("选择导出文件", "打开下载文件夹", "复制书签代码",
                      "下载脚本（给油猴插件用）"):
            self.assertIn(label, self.html)
        self.assertIn("拖进这个窗口", self.html)

    def test_state_drives_the_guide_and_surfaces_errors(self):
        js = self.html[self.html.index("<script>"):]
        for token in ("lastBundleKey", "guideAuto", "s.bundle_error",
                      "startState", "startHint", "导出包读不出来"):
            self.assertIn(token, js)
        self.assertIn("const need=!b||!!err;", js)
        self.assertIn('$("dataBox").style.display=need?"block":"none"', js)
        self.assertNotIn("oldBundleHint", js)

    def test_all_element_ids_used_by_js_exist(self):
        ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.html))
        refs = set(re.findall(re.escape("$") + r'\("([A-Za-z0-9_-]+)"\)', self.html))
        refs |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', self.html))
        self.assertTrue(refs)
        self.assertEqual(set(), refs - ids, f"脚本引用了不存在的元素：{sorted(refs - ids)}")

    def test_readme_puts_first_export_before_the_two_buttons(self):
        first = self.readme.index("### 第 1 次：先导出一份自己的数据")
        later = self.readme.index("### 以后每次：两个大按钮")
        self.assertLess(first, later)
        self.assertIn("工具不会自动拿到你的许可记录", self.readme)
        self.assertIn("直接点「一键统计 →」", self.readme)

    def test_old_button_name_is_gone(self):
        legacy = "数据不对？更新导出包"
        for rel in ("README.md", "steam_source_collections/gui_page.html",
                    "steam_source_collections/service.py",
                    "steam_source_collections/cli.py"):
            self.assertNotIn(legacy, (ROOT / rel).read_text(encoding="utf-8"), rel)


class BundleErrorTests(unittest.TestCase):
    def test_scan_reports_an_unreadable_bundle_instead_of_silently_hiding_it(self):
        from steam_source_collections import service

        fake = SimpleNamespace(root=None, steamid64="", cloud_path=None,
                              cloud_format="", collections=[], playtimes={},
                              package_apps={}, notes=[])
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "steam-licenses-broken.json"
            bad.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
            with patch.object(service, "find_latest_bundle", return_value=bad):
                state = service.scan(fake, outdir=tmp)
        self.assertIsNone(state["bundle"])
        self.assertIn("steam-licenses-*.json", state["bundle_error"])
        self.assertFalse(state["cloud"]["found"])


if __name__ == "__main__":
    unittest.main()
