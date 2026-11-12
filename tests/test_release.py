"""Release hygiene: what would end up public on GitHub must stay clean.

Pure static checks. They never read the user's real Steam files, never connect
to the local Steam debugger, and never require a remote repository.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Built in pieces on purpose: this test file must not itself contain the
# literals it searches for in files that are about to be published.
ACCOUNT_NICKNAME = "玄" + "陌" + "离"
STEAM_ID3 = "1126" + "824019"
REAL_EXPORT_STAMP = "steam-licenses-2026" + "1108"

SENSITIVE = [
    (ACCOUNT_NICKNAME, "账号昵称"),
    ("SIR" + "LI", "账号昵称变体"),
    (STEAM_ID3, "SteamID3"),
    ("D:" + "\\steam", "本机 Steam 安装路径"),
    ("C:" + "\\Users\\" + ACCOUNT_NICKNAME, "本机用户目录"),
    (REAL_EXPORT_STAMP, "真实导出包文件名"),
    ("2545046640" + "@qq.com", "私人邮箱"),
]


def files_that_would_be_committed() -> list[Path]:
    """Ask git, so ignore rules are tested as git actually applies them."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


class IgnoreRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    def test_personal_data_folders_are_ignored(self):
        for entry in ("out/", "demo/", "private/", "prototypes/preview/",
                      "steam-licenses-*.json", "*.bak", "backups/", "*.log"):
            self.assertIn(entry, self.ignore, f".gitignore 缺少 {entry}")

    def test_synthetic_examples_are_not_ignored(self):
        self.assertIn("!examples/*.json", self.ignore)
        self.assertTrue((ROOT / "examples" / "export_bundle_sample.json").is_file())

    def test_git_really_ignores_the_preview_and_output_folders(self):
        targets = ["out/report.html", "out/collections_plan.json",
                   "prototypes/preview/onboard-first-run.png",
                   "demo/report.md", "steam-licenses-demo.json"]
        for rel in targets:
            r = subprocess.run(
                ["git", "-C", str(ROOT), "check-ignore", "-q", rel],
                capture_output=True, timeout=15,
            )
            self.assertEqual(r.returncode, 0, f"{rel} 没被 git 忽略")


class PublicFileTests(unittest.TestCase):
    def test_license_and_metadata_are_present(self):
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "steam-source-collections"', pyproject)
        self.assertIn('requires-python = ">=3.9"', pyproject)

    def test_version_numbers_match(self):
        from steam_source_collections import __version__
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
        self.assertEqual(__version__, declared)

    def test_no_personal_identifiers_in_publishable_files(self):
        files = files_that_would_be_committed()
        self.assertTrue(files, "git 没有列出任何可提交文件，检查环境")
        hits = []
        for path in files:
            # This file deliberately holds the detection patterns themselves.
            if path.name == "test_release.py":
                continue
            if not path.is_file() or path.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pyc", ".woff"
            }:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for needle, label in SENSITIVE:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)} 含{label}")
        self.assertEqual([], hits, "待提交文件里有私人数据：" + "; ".join(hits))

    def test_readme_does_not_promise_a_write_channel_that_needs_no_setup(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("没有调试接口时", readme)
        self.assertIn("完全退出 Steam", readme)
        self.assertIn("二次发布", readme)
        self.assertIn("git status --ignored", readme)


if __name__ == "__main__":
    unittest.main()
