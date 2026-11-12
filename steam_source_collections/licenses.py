"""Parsers for Steam's "licenses and product key activations" page.

The page is the only consumer-facing place where Steam exposes the
acquisition method of a license. Its DOM (cross-checked against the
open-source "Steam 许可分类器" userscript) looks roughly like::

    <table class="account_table">
      <tr>
        <td class="license_date_col">2024-08-12</td>
        <td>Game or package name
            <a class="free_license_remove_link">移除</a></td>
        <td class="license_acquisition_col">Steam 商店</td>
      </tr>
    </table>

There is no appid on this page, only a package name. matching.py takes care
of aligning those names with the appids from the Web API.
"""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from pathlib import Path

# Ordered: more specific labels must win over generic ones. For example a
# "Steam Store - Complimentary" row is a free grant, not a purchase.
CLASSIFY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("retail", ("零售", "Retail", "Activation Code", "激活码", "Product Key", "CD-Key", "CD Key")),
    (
        "free",
        (
            "免费赠送", "贈品", "赠品", "免费获取", "免费领取", "免费周末",
            "Complimentary", "Promotional", "AutoGrant", "NoCost", "Free Weekend", "Free",
        ),
    ),
    ("gift", ("玩家通行证", "招待券", "禮物", "礼物", "Guest Pass", "GuestPass", "Gift", "订阅", "Subscription")),
    ("store", ("Steam 商店", "Steam Store", "商店购买", "商店")),
)

TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
TD_RE = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.I | re.S)
CLASS_RE = re.compile(r"class\s*=\s*[\"']([^\"']*)[\"']", re.I)
TAG_RE = re.compile(r"<[^>]+>")
REMOVE_LINK_RE = re.compile(r"<a\b[^>]*free_license_remove_link[^>]*>.*?</a>", re.I | re.S)


@dataclass(frozen=True)
class LicenseRecord:
    date: str
    package_name: str
    method_raw: str
    category: str  # store | retail | free | gift | unknown

    def as_dict(self) -> dict:
        return {
            "date": self.date,
            "package_name": self.package_name,
            "method_raw": self.method_raw,
            "category": self.category,
        }


def classify(method: str) -> str:
    """Map the acquisition-method column to one of our four source groups."""
    text = (method or "").strip().casefold()
    if not text:
        return "unknown"
    for category, keywords in CLASSIFY_RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            return category
    return "unknown"


def _text(fragment: str) -> str:
    fragment = REMOVE_LINK_RE.sub(" ", fragment)
    value = html.unescape(TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", value).strip()


def parse_html(text: str) -> list[LicenseRecord]:
    records: list[LicenseRecord] = []
    for row in TR_RE.finditer(text):
        inner = row.group(1)
        if re.search(r"<th\b", inner, re.I):
            continue
        cells = TD_RE.findall(inner)
        if len(cells) < 3:
            continue

        date = method = name = None
        other_cells: list[str] = []
        for attrs, body in cells:
            classes = " ".join(CLASS_RE.findall(attrs)).casefold()
            if "steamdb_license_id_col" in classes:
                continue
            value = _text(body)
            if "license_date_col" in classes:
                date = value
            elif "license_acquisition_col" in classes:
                method = value
            else:
                other_cells.append(value)

        if name is None and other_cells:
            name = other_cells[0]

        # Fallback for saved pages whose class names were stripped: Steam
        # renders the columns as date / name / acquisition method. When both
        # markers are missing, positional values must override the name too
        # (otherwise the date column would be mistaken for the game name).
        if (not date or not method) and len(cells) >= 3:
            values = [_text(body) for _, body in cells]
            if not date and not method:
                date, name, method = values[0], values[1], values[2]
            else:
                date = date or values[0]
                name = name or values[1]
                method = method or values[2]

        if not name:
            continue
        method = method or ""
        records.append(
            LicenseRecord(date=date or "", package_name=name, method_raw=method, category=classify(method))
        )
    return records


def parse_csv(path: Path) -> list[LicenseRecord]:
    """Read a ``date,name,method`` CSV (headers optional, UTF-8/UTF-8-BOM)."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [cell.strip().casefold() for cell in rows[0]]
    known = {"date", "日期", "name", "名称", "游戏名", "method", "获取方式", "acquisition", "type"}
    has_header = any(cell in known for cell in header)

    def column(aliases: tuple[str, ...], default: int) -> int:
        if has_header:
            for alias in aliases:
                if alias.casefold() in header:
                    return header.index(alias.casefold())
        return default

    di = column(("date", "日期", "time", "时间"), 0)
    ni = column(("name", "名称", "游戏名", "package", "包名"), 1)
    mi = column(("method", "获取方式", "acquisition", "type", "分类"), 2)
    start = 1 if has_header else 0

    records: list[LicenseRecord] = []
    for row in rows[start:]:
        if max(di, ni, mi) >= len(row):
            continue
        method = row[mi].strip()
        name = row[ni].strip()
        if not name:
            continue
        records.append(
            LicenseRecord(date=row[di].strip(), package_name=name, method_raw=method, category=classify(method))
        )
    return records


def parse_text(path: Path) -> list[LicenseRecord]:
    """Read lines separated by ``|`` or tabs: ``date | package name | method``."""
    records: list[LicenseRecord] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = [part.strip() for part in re.split(r"\s*[|\t]\s*", line.strip()) if part.strip()]
        if len(parts) < 3:
            continue
        date, name, method = parts[0], parts[1], parts[2]
        records.append(LicenseRecord(date=date, package_name=name, method_raw=method, category=classify(method)))
    return records


def load_licenses(path: str | Path) -> list[LicenseRecord]:
    """Dispatch by file extension. HTML is the page saved from Steam."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"许可文件不存在: {p}")
    suffix = p.suffix.casefold()
    if suffix in (".html", ".htm"):
        records = parse_html(p.read_text(encoding="utf-8-sig", errors="replace"))
    elif suffix == ".csv":
        records = parse_csv(p)
    elif suffix in (".txt", ".tsv"):
        records = parse_text(p)
    else:
        raise ValueError("许可文件需为 .html / .csv / .txt（用 CSV 最稳）")
    if not records:
        raise ValueError(
            "没有解析到任何许可行。请确认保存的是完整页面（含表格），"
            "或改用 CSV：date,name,method 三列。"
        )
    return records
