"""Regression tests for the GUI layout and system-following light/dark theme."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from steam_source_collections import service
from steam_source_collections.local import LocalSteam
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
GUI_PAGE = ROOT / "steam_source_collections" / "gui_page.html"


def _css(text: str) -> str:
    return text[text.index("<style>"):text.index("</style>")]


class ThemeTests(unittest.TestCase):
    def test_result_grid_cannot_be_pushed_out_of_the_card(self):
        css = _css(GUI_PAGE.read_text(encoding="utf-8"))
        # 1fr alone keeps a min-content floor, so a long unbreakable game name
        # widens the column past the card. minmax(0,1fr) lets columns shrink.
        for selector in (".groups", ".status-grid"):
            rule = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
            self.assertIsNotNone(rule, selector)
            self.assertIn("minmax(0,1fr)", rule.group(1))  # type: ignore[union-attr]
        self.assertNotIn("grid-template-columns:repeat(3,1fr)", css)
        self.assertIn("min-width:0;overflow:hidden", css)
        self.assertIn("overflow-wrap:anywhere", css)

    def test_gui_follows_system_color_scheme(self):
        text = GUI_PAGE.read_text(encoding="utf-8")
        css = _css(text)
        self.assertIn('<meta name="color-scheme" content="light dark">', text)
        self.assertIn("@media (prefers-color-scheme:dark)", css)
        dark = css[css.index("@media (prefers-color-scheme:dark)"):]
        for var in ("--bg:", "--card:", "--ink:", "--line:", "--blue:", "--surface:"):
            self.assertIn(var, dark)
        # Hard-coded light surfaces would flash white in dark mode.
        for hardcoded in ("background:#fff", "background:#fbfcff", "background:#fafbfe"):
            self.assertNotIn(hardcoded, css)

    def test_generated_report_is_dark_aware_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "steam-licenses-theme.json"
            bundle.write_text(
                '{"schema":"steam-license-export/3","licenses":['
                '{"date":"2026-01-01","name":"Sample Game","method":"Steam Store",'
                '"packageid":"36","appids":[70]}],"owned_appids":[70],'
                '"app_names":{"70":"Sample Game"},"playtimes":{},'
                '"owned_packages":["36"],"license_pages":1,"license_total":1}',
                encoding="utf-8")
            with patch.object(service, "discover", return_value=LocalSteam()), \
                 patch.object(service, "locate_bundle", return_value=bundle):
                service.analyse(outdir=root / "out")
            html = (root / "out" / "report.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="color-scheme" content="light dark">', html)
        self.assertIn("@media (prefers-color-scheme:dark)", html)
        self.assertIn("background:var(--bg)", html)
        self.assertIn("minmax(min(310px,100%),1fr)", html)
        self.assertNotIn("{{", html)  # no stray f-string braces
        self.assertNotIn("}}", html)


if __name__ == "__main__":
    unittest.main()
