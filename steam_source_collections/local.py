"""Read-only auto-discovery of a locally installed Steam client.

What a local install *does* give us:

* install directory, SteamID64 and the account's ``id3`` folder,
* the collection storage file (both the legacy dict schema and the newer
  ``[[key, meta], ...]`` key/value schema),
* per-app playtime from ``localconfig.vdf``,
* the set of appids that appear in existing collections.

What it deliberately NEVER reads: cookies, ``loginusers.vdf`` passwords or
``RememberPassword`` values, web helper caches, auth tokens, or the encrypted
``licensecache`` blob. The acquisition method (store / retail / gift / free)
is NOT stored in plaintext anywhere in the client; it only exists on the
logged-in web page ``store.steampowered.com/account/licenses/``.

Everything here is strictly read-only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .vdfcache import package_cache_path, read_package_apps

STEAMID64_BASE = 76561197960265728
BUNDLE_GLOB = "steam-licenses-*.json"


@dataclass
class LocalSteam:
    root: Path | None = None
    steamid64: str | None = None
    id3: str | None = None
    cloud_path: Path | None = None
    cloud_format: str = "missing"  # kv-pairs | dict | missing | unreadable
    collections: list[dict] = field(default_factory=list)
    playtimes: dict[int, int] = field(default_factory=dict)
    package_apps: dict[int, list[int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def collection_appids(self) -> set[int]:
        ids: set[int] = set()
        for entry in self.collections:
            for value in entry.get("added", []):
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    continue
        return ids

    def summary(self) -> dict:
        return {
            "steam_root": str(self.root) if self.root else None,
            "steamid64": self.steamid64,
            "id3": self.id3,
            "cloud_storage": str(self.cloud_path) if self.cloud_path else None,
            "cloud_format": self.cloud_format,
            "existing_collections": len(self.collections),
            "appids_in_collections": len(self.collection_appids),
            "local_playtime_records": len(self.playtimes),
            "local_package_records": len(self.package_apps),
            "notes": self.notes,
        }


def _candidate_roots(explicit: str | Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))

    if os.name == "nt":
        try:
            import winreg

            for hive, key in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for value_name in ("InstallPath", "SteamPath"):
                            try:
                                value, _ = winreg.QueryValueEx(handle, value_name)
                                if value:
                                    candidates.append(Path(str(value)))
                            except OSError:
                                continue
                except OSError:
                    continue
        except ImportError:
            pass

    env = os.environ.get("STEAM_PATH")
    if env:
        candidates.append(Path(env))

    home = Path.home()
    if os.name == "nt":
        for drive in ("C", "D", "E", "F"):
            candidates += [
                Path(f"{drive}:/Program Files (x86)/Steam"),
                Path(f"{drive}:/Program Files/Steam"),
                Path(f"{drive}:/Steam"),
                Path(f"{drive}:/steam"),
            ]
    elif os.uname().sysname == "Darwin":
        candidates.append(home / "Library/Application Support/Steam")
    else:
        candidates += [
            home / ".steam/steam",
            home / ".local/share/Steam",
            Path("/usr/share/steam"),
        ]
    return candidates


def discover_root(explicit: str | Path | None = None) -> Path | None:
    """Return the first directory that actually looks like a Steam install."""
    seen: set[str] = set()
    for candidate in _candidate_roots(explicit):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if (resolved / "userdata").is_dir() or (resolved / "steam.exe").is_file():
            return resolved
    return None


def read_steamid64(root: Path) -> str | None:
    """Pick the most-recently-used account. Never returns account names."""
    path = root / "config/loginusers.vdf"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    matches = list(re.finditer(r'"(\d{17})"\s*\{', text))
    if not matches:
        return None
    best: tuple[int, str] | None = None
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        recent = re.search(r'"MostRecent"\s*"(\d+)"', block)
        score = int(recent.group(1)) if recent else 0
        if best is None or score > best[0]:
            best = (score, match.group(1))
    return best[1] if best else matches[0].group(1)


def _id3_from_steamid64(steamid64: str) -> str | None:
    try:
        return str(int(steamid64) - STEAMID64_BASE)
    except ValueError:
        return None


def _parse_collection_value(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def read_collections(path: Path) -> tuple[str, list[dict]]:
    """Support both the legacy dict schema and the newer key/value schema.

    Newer clients store a JSON array of ``[key, meta]`` pairs where each
    collection lives under a key like ``user-collections.uc-XXXX`` and its
    ``meta.value`` is itself a JSON string with id/name/added/removed.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return "unreadable", []

    found: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not (isinstance(item, list) and len(item) >= 2):
                continue
            key, meta = item[0], item[1]
            if not isinstance(key, str) or not key.startswith("user-collections."):
                continue
            raw = meta.get("value") if isinstance(meta, dict) else None
            entry = _parse_collection_value(raw)
            if entry and isinstance(entry.get("name"), str):
                found.append({
                    "id": entry.get("id") or key.split(".", 1)[-1],
                    "name": entry["name"],
                    "added": entry.get("added") or [],
                    "removed": entry.get("removed") or [],
                })
        return ("kv-pairs" if found else "unreadable"), found

    if isinstance(data, dict):
        def walk(node) -> dict | None:
            if isinstance(node, dict):
                for key in ("user-collections", "collections"):
                    candidate = node.get(key)
                    if isinstance(candidate, dict) and any(
                        isinstance(v, dict) and "name" in v for v in candidate.values()
                    ):
                        return candidate
                for value in node.values():
                    hit = walk(value)
                    if hit:
                        return hit
            elif isinstance(node, list):
                for value in node:
                    hit = walk(value)
                    if hit:
                        return hit
            return None

        container = walk(data)
        if container:
            for key, entry in container.items():
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    for list_key in ("added", "apps", "appids"):
                        if isinstance(entry.get(list_key), list):
                            break
                    else:
                        list_key = "added"
                    found.append({
                        "id": key,
                        "name": entry["name"],
                        "added": entry.get(list_key) or [],
                        "removed": entry.get("removed") or [],
                    })
            return "dict", found
    return "unreadable", []


def read_playtimes(path: Path) -> dict[int, int]:
    """Extract ``appid -> playtime`` from localconfig.vdf (text VDF)."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    result: dict[int, int] = {}
    for match in re.finditer(r'"(\d{2,8})"\s*\{', text):
        window = text[match.end(): match.end() + 1500]
        playtime = re.search(r'"Playtime"\s*"(\d+)"', window)
        if playtime:
            result[int(match.group(1))] = int(playtime.group(1))
    return result


def discover(explicit: str | Path | None = None) -> LocalSteam:
    info = LocalSteam()
    info.root = discover_root(explicit)
    if not info.root:
        info.notes.append("未找到本机 Steam 安装目录，可用 --steam-root 指定")
        return info

    info.steamid64 = read_steamid64(info.root)
    if info.steamid64:
        info.id3 = _id3_from_steamid64(info.steamid64)
    userdata = info.root / "userdata"
    if not info.id3 and userdata.is_dir():
        folders = [d.name for d in userdata.iterdir() if d.is_dir() and d.name.isdigit()]
        if len(folders) == 1:
            info.id3 = folders[0]
    if not info.id3:
        info.notes.append("没能确定当前账号的 userdata 目录")
        return info

    config = userdata / info.id3 / "config"
    info.cloud_path = config / "cloudstorage/cloud-storage-namespace-1.json"
    if info.cloud_path.is_file():
        info.cloud_format, info.collections = read_collections(info.cloud_path)
        if info.cloud_format == "unreadable":
            info.notes.append("收藏集文件结构无法识别（客户端可能又改了格式）")
        elif info.cloud_format == "kv-pairs":
            info.notes.append("检测到新版键值格式收藏集，写入时只追加新收藏集、不修改现有分组")
    else:
        info.notes.append("没有找到 cloud-storage-namespace-1.json（新版客户端可能用 LevelDB）")

    info.playtimes = read_playtimes(config / "localconfig.vdf")
    if not info.playtimes:
        info.notes.append("localconfig.vdf 中没有读到游玩时长")

    cache = package_cache_path(info.root)
    if cache:
        raw = read_package_apps(cache)
        if raw:
            # usable packages are filtered against owned appids in bundle.py,
            # once the export's owned list is known; keep the full map here.
            info.package_apps = raw
        else:
            info.notes.append("packageinfo.vdf 里没解析出礼包包映射（客户端格式可能又变了）")
    else:
        info.notes.append("没有找到 appcache/packageinfo.vdf，无法用本机缓存做精确对齐")
    return info


def download_dirs() -> list[Path]:
    home = Path.home()
    candidates = [home / "Downloads", home / "下载", home / "Desktop", home / "桌面"]
    return [p for p in candidates if p.is_dir()]


def find_latest_bundle(extra: str | Path | None = None) -> Path | None:
    """Find the newest export produced by the one-click bookmarklet."""
    if extra:
        path = Path(extra)
        return path if path.is_file() else None
    matches: list[Path] = []
    for folder in download_dirs():
        matches.extend(folder.glob(BUNDLE_GLOB))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)
