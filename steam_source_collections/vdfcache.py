"""Read-only parser for Steam's local binary ``packageinfo.vdf`` cache.

Why this file matters
---------------------
``store.steampowered.com/account/licenses/`` is the only place that says
*how* a license was obtained, but it only prints package names. The store's
``appdetails`` endpoint refuses to return a name for many DLCs, delisted
titles and playtests, so name matching alone leaves hundreds of games in the
"unknown" bucket.

The Steam client keeps a local ``appcache/packageinfo.vdf`` that maps every
owned package id to the exact appids it grants. That mapping needs no network,
no login and no API key. Combining it with the license rows gives an exact
package -> appid alignment even for games whose name is unavailable.

Format notes
------------
The file is Valve's binary KeyValues format. Each package section contains
``\\x02packageid\\x00`` followed by a little-endian uint32, and an
``appids`` sub-map whose entries are ``\\x02"0"\\x00<uint32>``,
``\\x02"1"\\x00<uint32>`` and so on. We deliberately scan for those tokens
instead of parsing the whole container: Steam changes the section header
regularly, but the inner KeyValues encoding has been stable for years.

This module is strictly read-only and never writes to the Steam directory.
"""

from __future__ import annotations

import struct
from pathlib import Path

PACKAGEID_TOKEN = b"\x02packageid\x00"
APPIDS_TOKEN = b"appids\x00"

# Package 0 is the Steam client's own mega-package (hundreds of base apps).
# It is not a license the user bought and would create false assignments.
MAX_USEFUL_PACKAGE_APPS = 120


def _cstring_end(buf: bytes, start: int) -> int:
    end = buf.find(b"\x00", start)
    return -1 if end < 0 else end


def _read_appids(segment: bytes, start: int) -> list[int]:
    """Read ``\\x02"0"\\x00<uint32> ...`` entries from an appids sub-map."""
    out: list[int] = []
    pos = start
    size = len(segment)
    while pos + 1 < size:
        if segment[pos] != 0x02:  # 0x02 = int32; anything else ends the map
            break
        pos += 1
        end = _cstring_end(segment, pos)
        if end < 0 or end >= size:
            break
        key = segment[pos:end]
        if not key.isdigit():
            break
        pos = end + 1
        if pos + 4 > size:
            break
        appid = struct.unpack_from("<I", segment, pos)[0]
        pos += 4
        if appid:
            out.append(appid)
    return out


def read_package_apps(path: str | Path) -> dict[int, list[int]]:
    """Return ``{packageid: [appid, ...]}`` from a binary packageinfo.vdf.

    Missing, unreadable or unexpected files simply yield an empty mapping;
    the rest of the pipeline must keep working without this optional data.
    """
    file = Path(path)
    try:
        buf = file.read_bytes()
    except OSError:
        return {}
    if len(buf) < 32 or not buf.startswith(b"\x28\x55\x56\x06"):
        # Older clients used a slightly different magic; accept it too as
        # long as the inner tokens look right.
        if PACKAGEID_TOKEN not in buf[:4096]:
            return {}

    starts: list[int] = []
    offset = 0
    while True:
        found = buf.find(PACKAGEID_TOKEN, offset)
        if found < 0:
            break
        starts.append(found)
        offset = found + len(PACKAGEID_TOKEN)

    result: dict[int, list[int]] = {}
    for index, start in enumerate(starts):
        value_at = start + len(PACKAGEID_TOKEN)
        if value_at + 4 > len(buf):
            continue
        packageid = struct.unpack_from("<I", buf, value_at)[0]
        end = starts[index + 1] if index + 1 < len(starts) else len(buf)
        segment = buf[start:end]
        appids_at = segment.find(APPIDS_TOKEN)
        if appids_at < 0:
            continue
        apps = _read_appids(segment, appids_at + len(APPIDS_TOKEN))
        if apps:
            result[packageid] = list(dict.fromkeys(apps))
    return result


def package_cache_path(root: str | Path | None) -> Path | None:
    if not root:
        return None
    candidate = Path(root) / "appcache" / "packageinfo.vdf"
    return candidate if candidate.is_file() else None


def useful_packages(package_apps: dict[int, list[int]], owned: set[int]) -> dict[int, list[int]]:
    """Restrict the cache to plausible user licenses, keyed by owned appids."""
    out: dict[int, list[int]] = {}
    for packageid, appids in package_apps.items():
        if packageid == 0 or len(appids) > MAX_USEFUL_PACKAGE_APPS:
            continue
        hits = [appid for appid in appids if appid in owned]
        if hits:
            out[packageid] = hits
    return out


def single_app_packages(package_apps: dict[int, list[int]]) -> dict[int, int]:
    """Invert one-app packages into ``{appid: packageid}`` (first wins)."""
    inverse: dict[int, int] = {}
    for packageid, appids in sorted(package_apps.items()):
        if len(appids) == 1 and appids[0] not in inverse:
            inverse[appids[0]] = packageid
    return inverse
