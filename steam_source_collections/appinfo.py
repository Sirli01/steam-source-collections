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
    appid, size, state = struct.unpack_from("<III", data, off)
    if appid not in owned or state not in (1, 2) or not (MIN_SECTION <= size <= MAX_SECTION):
        return None
    end = off + HEADER_SIZE + size
    if end > len(data):
        return None
    timestamp = struct.unpack_from("<I", data, off + 12)[0]
    if not (MIN_TIMESTAMP <= timestamp <= MAX_TIMESTAMP):
        return None
    return appid, end


def _nearest_appid(data: bytes, pos: int, owned: set[int], lookback: int = LOOKBACK) -> int | None:
    start = pos - HEADER_SIZE
    # -1 (not 0) so a record header sitting at the very first byte is checked.
    floor = max(-1, pos - lookback)
    for off in range(start, floor, -1):
        header = _header_end(data, off, owned)
        if header and off + HEADER_SIZE <= pos < header[1]:
            return header[0]
    return None


def _load_bytes(path_or_data: str | Path | bytes | bytearray | memoryview) -> bytes:
    if isinstance(path_or_data, (bytes, bytearray, memoryview)):
        return bytes(path_or_data)
    path = Path(path_or_data)
    if path.is_dir():
        resolved = appinfo_path(path)
        if not resolved:
            return b""
        path = resolved
    try:
        return path.read_bytes()
    except OSError:
        return b""


NAME_KEY_MARK = b"\x01" + struct.pack("<I", 4)


def read_app_names(path_or_data: str | Path | bytes | bytearray | memoryview, wanted) -> dict[int, str]:
    """Read primary English app names from appinfo.vdf for ``wanted`` appids only."""
    wanted_ids = {int(value) for value in (wanted or ())}
    if not wanted_ids:
        return {}
    data = _load_bytes(path_or_data)
    if not data:
        return {}
    found: dict[int, str] = {}
    pos = 0
    while len(found) < len(wanted_ids):
        index = data.find(NAME_KEY_MARK, pos)
        if index < 0:
            break
        start = index + len(NAME_KEY_MARK)
        pos = start
        end = data.find(b"\x00", start)
        if end < 0 or end - start > 250:
            continue
        try:
            text = data[start:end].decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if not text or "\n" in text:
            continue
        appid = _nearest_appid(data, index, wanted_ids, lookback=1024)
        if appid is not None and appid not in found:
            found[appid] = text
    return found


def read_trading_card_appids(path_or_data: str | Path | bytes | bytearray | memoryview, wanted) -> set[int]:
    """Read owned appids tagged ``category_29 = 1`` (Steam Trading Cards) from v29 appinfo.vdf."""
    wanted_ids = {int(value) for value in (wanted or ())}
    if not wanted_ids:
        return set()
    data = _load_bytes(path_or_data)
    if len(data) < 16:
        return set()
    magic = struct.unpack_from("<I", data, 0)[0]
    if (magic & 0xFFFFFF00) != 0x07564400 or (magic & 0xFF) < 0x29:
        return set()
    st_off = struct.unpack_from("<Q", data, 8)[0]
    if not (0 < st_off <= len(data) - 4):
        return set()
    count = struct.unpack_from("<I", data, st_off)[0]
    pos = st_off + 4
    idx_29: int | None = None
    for i in range(count):
        if pos >= len(data):
            break
        end = data.find(0, pos)
        if end < 0:
            break
        if data[pos:end] == b"category_29":
            idx_29 = i
            break
        pos = end + 1
    if idx_29 is None:
        return set()
    pat = b"\x02" + struct.pack("<II", idx_29, 1)
    card_appids: set[int] = set()
    at = 0
    while True:
        hit = data.find(pat, at, st_off)
        if hit < 0:
            break
        appid = _nearest_appid(data, hit, wanted_ids, lookback=LOOKBACK)
        if appid is not None:
            card_appids.add(appid)
        at = hit + 9
    return card_appids


def _scan_localized_fragment(data: bytes, fragment: str, owned_ids: set[int]) -> set[int]:
    needle = str(fragment or "").encode("utf-8")
    if len(needle) < 4:
        return set()
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


def appids_for_localized_fragment(path: str | Path | bytes, fragment: str, owned) -> set[int]:
    """Owned appids whose cached localized name contains ``fragment``.

    Used for Chinese collection names such as ``《巫师 3：狂猎 - 完全版》``:
    the license page name differs from every English app title, but several
    apps in that bundle carry a localized name containing ``巫师``. Pointing
    at one unclaimed package with multiple hits is strong enough evidence.
    """
    data = _load_bytes(path)
    if not data:
        return set()
    owned_ids = {int(value) for value in owned}
    return _scan_localized_fragment(data, fragment, owned_ids)


def resolve_localized_fragments(
    path_or_data: str | Path | bytes,
    row_fragments: dict[int, str],
    owned,
) -> dict[int, set[int]]:
    """Batch-resolve localized name fragments using a single read of appinfo.vdf."""
    if not row_fragments:
        return {}
    data = _load_bytes(path_or_data)
    if not data:
        return {}
    owned_ids = {int(value) for value in owned}
    by_fragment: dict[str, list[int]] = {}
    for row_index, fragment in row_fragments.items():
        frag = str(fragment or "")
        if len(frag.encode("utf-8")) >= 4:
            by_fragment.setdefault(frag, []).append(row_index)
    out: dict[int, set[int]] = {}
    for frag, row_indices in by_fragment.items():
        found = _scan_localized_fragment(data, frag, owned_ids)
        if found:
            for row_index in row_indices:
                out[row_index] = set(found)
    return out


def resolve_localized_names(path: str | Path | bytes, wanted, owned) -> dict[str, int]:
    """Map localized license package names to owned appids.

    Only names containing CJK characters are considered, and a mapping is
    accepted only when every occurrence points to the same owned appid.
    Anything ambiguous is left unmatched on purpose.
    """
    data = _load_bytes(path)
    if not data:
        return {}
    owned_ids = {int(value) for value in owned}
    result: dict[str, int] = {}
    seen_labels: set[str] = set()
    for raw_name in wanted:
        label = str(raw_name or "").strip()
        if not label or label in seen_labels or len(label) > 60 or not CJK_RE.search(label):
            continue
        seen_labels.add(label)
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
