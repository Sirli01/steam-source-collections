"""Resolve Chinese license package names via Steam's local appinfo cache.

``store.steampowered.com/account/licenses/`` localizes package names, so a row
may read ``米塔`` while the library and every public name API only know
``MiSide``. No amount of string similarity can bridge that. The Steam client,
however, caches ``appcache/appinfo.vdf`` with each app's localized names, and
it is a local read-only file: no network, no login, no API key.

Format notes
------------
Each app record starts with a 52-byte header (uint32 appid, uint32 section
size, uint32 state, uint64 timestamp, uint64 token, 20-byte hash, uint32
change number) followed by a binary KeyValues section. Rather than parse the
whole container -- Valve changes its layout regularly -- we only need to find
a localized string value and walk backwards to the nearest plausible header
that owns that offset. A hit is accepted only when the appid is in the user's
owned list and the string sits inside that record's declared section.

This module is strictly read-only and never writes to the Steam directory.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from pathlib import Path

from .matching import normalize

PACKAGE_WORDS = {
    "完全版", "重制版", "豪华版", "年度版", "试玩版", "标准版", "数字版",
    "典藏版", "终极版", "礼包", "合集", "捆绑包", "免费", "赠送", "试玩",
}


def first_cjk_fragment(name: str) -> str:
    """The leading Chinese word of a package name, without edition words."""
    text = unicodedata.normalize("NFKC", name or "")
    for fragment in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}", text):
        if fragment in PACKAGE_WORDS:
            continue
        return fragment[:6]
    return ""

HEADER_SIZE = 52
MIN_SECTION = 200
MAX_SECTION = 200_000
MIN_TIMESTAMP = 1_262_304_000  # 2010-01-01
MAX_TIMESTAMP = 1_893_456_000  # 2030-01-01
LOOKBACK = 200_000
MAX_HITS_PER_NAME = 8

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def appinfo_path(root: str | Path | None) -> Path | None:
    """Return the client's appinfo cache, or None when Steam is not installed."""
    if not root:
        return None
    path = Path(root) / "appcache" / "appinfo.vdf"
    return path if path.is_file() else None


def _header_end(data: bytes, off: int, owned: set[int]) -> tuple[int, int] | None:
    """(appid, end_offset) when off looks like a valid app record header."""
    if off < 0 or off + HEADER_SIZE > len(data):
        return None
    appid, size, _state = struct.unpack_from("<III", data, off)
    if appid not in owned or not (MIN_SECTION <= size <= MAX_SECTION):
        return None
    end = off + HEADER_SIZE + size
    if end > len(data):
        return None
    timestamp = struct.unpack_from("<Q", data, off + 12)[0]
    if not (MIN_TIMESTAMP <= timestamp <= MAX_TIMESTAMP):
        return None
    return appid, end


def _nearest_appid(data: bytes, pos: int, owned: set[int]) -> int | None:
    start = pos - 1
    # -1 (not 0) so a record header sitting at the very first byte is checked.
    floor = max(-1, pos - LOOKBACK)
    for off in range(start, floor, -1):
        header = _header_end(data, off, owned)
        if header and off + HEADER_SIZE <= pos < header[1]:
            return header[0]
    return None


def appids_for_localized_fragment(path: str | Path, fragment: str, owned) -> set[int]:
    """Owned appids whose cached localized name contains ``fragment``.

    Used for Chinese collection names such as ``《巫师 3：狂猎 - 完全版》``:
    the license page name differs from every English app title, but several
    apps in that bundle carry a localized name containing ``巫师``. Pointing
    at one unclaimed package with multiple hits is strong enough evidence.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return set()
    needle = str(fragment or "").encode("utf-8")
    if len(needle) < 4:
        return set()
    owned_ids = {int(value) for value in owned}
    hits: set[int] = set()
    pos = 0
    while len(hits) <= MAX_HITS_PER_NAME:
        index = data.find(needle, pos)
        if index < 0:
            break
        pos = index + 1
        boundary = data.rfind(b"\x00", max(0, index - 200), index)
        if boundary < 0:
            continue
        start = boundary + 1
        # Localized names are stored as 0x01 + 4-byte key + NUL + text + NUL.
        if start < 5 or data[start - 5] != 0x01 or b"\x00" in data[start:index]:
            continue
        appid = _nearest_appid(data, start, owned_ids)
        if appid is not None:
            hits.add(appid)
    return hits


def resolve_localized_names(path: str | Path, wanted, owned) -> dict[str, int]:
    """Map localized license package names to owned appids.

    Only names containing CJK characters are considered, and a mapping is
    accepted only when every occurrence points to the same owned appid.
    Anything ambiguous is left unmatched on purpose.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return {}
    owned_ids = {int(value) for value in owned}
    result: dict[str, int] = {}
    for raw_name in wanted:
        label = str(raw_name or "").strip()
        if not label or len(label) > 60 or not CJK_RE.search(label):
            continue
        needle = label.encode("utf-8")
        hits: set[int] = set()
        pos = 0
        while len(hits) <= MAX_HITS_PER_NAME:
            index = data.find(needle, pos)
            if index < 0:
                break
            pos = index + 1
            # A binary KeyValues string value: type marker + 4-byte key,
            # the text itself, and a NUL terminator.
            if index < 5 or data[index - 5] != 0x01:
                continue
            if data[index + len(needle): index + len(needle) + 1] != b"\x00":
                continue
            appid = _nearest_appid(data, index, owned_ids)
            if appid is not None:
                hits.add(appid)
        if len(hits) == 1:
            result[normalize(label)] = hits.pop()
    return result
