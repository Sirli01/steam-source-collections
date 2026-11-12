"""Regression tests for the local packageinfo.vdf enrichment (v0.4.0)."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from steam_source_collections.bundle import align_license_packages, load_bundle
from steam_source_collections.licenses import LicenseRecord
from steam_source_collections.plan import _should_replace
from steam_source_collections.vdfcache import (
    read_package_apps,
    single_app_packages,
    useful_packages,
)


def _segment(packageid: int, appids: list[int]) -> bytes:
    """Build the smallest byte string the parser understands."""
    out = b"\x02packageid\x00" + struct.pack("<I", packageid)
    out += b"\x08\x00appids\x00"
    for index, appid in enumerate(appids):
        out += b"\x02" + str(index).encode() + b"\x00" + struct.pack("<I", appid)
    out += b"\x08\x08\x08"  # ends the appids sub-map
    return out


class VdfParserTests(unittest.TestCase):
    def test_reads_package_to_appids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packageinfo.vdf"
            path.write_bytes(
                b"\x28\x55\x56\x06\x01\x00\x00\x00" +
                _segment(0, [7, 10, 20]) +
                _segment(218, [4000]) +
                _segment(515, [211, 215, 218, 400])
            )
            parsed = read_package_apps(path)
            self.assertEqual(parsed[218], [4000])
            self.assertEqual(parsed[515], [211, 215, 218, 400])

    def test_useful_packages_filters_client_megapackage(self):
        parsed = {0: [7, 10, 20], 218: [4000], 515: [211, 215, 218, 400]}
        owned = {4000, 211, 215, 218, 400}
        useful = useful_packages(parsed, owned)
        self.assertNotIn(0, useful)
        self.assertEqual(useful[218], [4000])
        self.assertEqual(single_app_packages(useful), {4000: 218})

    def test_unreadable_file_is_optional(self):
        self.assertEqual(read_package_apps(Path("does-not-exist.vdf")), {})


class OrderAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            LicenseRecord("2026-01-01", "Row %d" % i, "Steam 商店", "store")
            for i in range(8)
        ]
        # License rows are newest-first; owned_packages are oldest-first.
        self.packages = [10, 20, 30, 40, 50, 60, 70, 80]
        self.pkgmap = {pid: [pid * 10] for pid in self.packages}

    def test_fills_only_strict_one_to_one_gaps(self):
        explicit = [80, None, 60, 50, 40, 30, None, 10]
        inferred = align_license_packages(
            self.records, explicit, self.packages, self.pkgmap
        )
        self.assertEqual(inferred, {1: 70, 6: 20})

    def test_refuses_when_anchors_are_not_reverse_monotonic(self):
        explicit = [10, None, 20, 30, 40, 50, None, 80]
        self.assertEqual(
            align_license_packages(self.records, explicit, self.packages, self.pkgmap),
            {},
        )

    def test_can_be_disabled(self):
        explicit = [80, None, 60, 50, 40, 30, None, 10]
        self.assertEqual(
            align_license_packages(
                self.records, explicit, self.packages, self.pkgmap, infer_order=False
            ),
            {},
        )

    def test_inferred_evidence_never_overwrites_direct_evidence(self):
        old = {"group": "retail", "match_via": "package"}
        new = {"group": "store", "match_via": "order"}
        self.assertFalse(_should_replace(old, new, "priority"))
        self.assertTrue(_should_replace({"group": "unknown", "match_via": "none"}, new, "priority"))


class WarningWordingTests(unittest.TestCase):
    def test_multipage_export_is_not_called_a_pagination_failure(self):
        """The real 912-row / 12-page export must not say 're-export'."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps({
                "schema": "steam-license-export/1",
                "license_pages": 12,
                "license_total": 1163,
                "licenses": [
                    {"date": "2026-11-08", "name": "Game %d" % i, "method": "Steam 商店"}
                    for i in range(60)
                ],
                "owned_appids": list(range(1000, 1100)),
                "app_names": {"1000": "Only One"},
            }), encoding="utf-8")
            bundle = load_bundle(path)
            warnings = bundle.meta["warnings"]
            self.assertEqual(len(warnings), 1)
            self.assertNotIn("分页", warnings[0])
            self.assertIn("没有名称", warnings[0])
            self.assertNotIn("重新", warnings[0])
            self.assertEqual(bundle.meta["license_pages"], 12)

    def test_single_page_export_is_still_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps({
                "license_pages": 1,
                "licenses": [{"date": "2026-11-08", "name": "One", "method": "零售"}],
                "owned_appids": list(range(1000, 1100)),
                "app_names": {},
            }), encoding="utf-8")
            warnings = load_bundle(path).meta["warnings"]
            self.assertIn("分页", warnings[0])


if __name__ == "__main__":
    unittest.main()
