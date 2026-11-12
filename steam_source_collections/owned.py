"""Load the appid -> game-name list (what you actually own)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"


@dataclass(frozen=True)
class Game:
    appid: int
    name: str
    playtime_minutes: int = 0
    family_shared: bool = False

    def as_dict(self) -> dict:
        return {
            "appid": self.appid,
            "name": self.name,
            "playtime_minutes": self.playtime_minutes,
            "family_shared": self.family_shared,
        }


def _games_from_payload(data) -> list[Game]:
    if isinstance(data, dict):
        if "response" in data and isinstance(data["response"], dict):
            raw = data["response"].get("games", [])
        elif "games" in data:
            raw = data["games"]
        elif "appid" in data or "name" in data:
            raw = [data]
        else:
            # Also accept {"730": "Counter-Strike", ...} style exports.
            raw = []
            for key, value in data.items():
                if str(key).isdigit():
                    if isinstance(value, dict):
                        raw.append({"appid": int(key), **value})
                    else:
                        raw.append({"appid": int(key), "name": str(value)})
    elif isinstance(data, list):
        raw = data
    else:
        raw = []

    games: list[Game] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        appid = item.get("appid") or item.get("id") or item.get("app_id")
        name = item.get("name") or item.get("title")
        if appid is None or not name:
            continue
        try:
            appid = int(appid)
        except (TypeError, ValueError):
            continue
        playtime = item.get("playtime_forever", item.get("playtime", 0)) or 0
        try:
            playtime = int(playtime)
        except (TypeError, ValueError):
            playtime = 0
        games.append(
            Game(
                appid=appid,
                name=str(name),
                playtime_minutes=playtime,
                family_shared=bool(
                    item.get("family_shared")
                    or item.get("is_family_shared")
                    or item.get("owner") == "family"
                ),
            )
        )
    return games


def load_owned(path: str | Path) -> list[Game]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"游戏库文件不存在: {p}")
    games = _games_from_payload(json.loads(p.read_text(encoding="utf-8-sig")))
    if not games:
        raise ValueError("游戏库文件里没有解析到 appid + 名称")
    return games


def fetch_owned(api_key: str, steamid: str, timeout: int = 30) -> list[Game]:
    """Fetch GetOwnedGames. Requires the profile's game details to be public."""
    query = urllib.parse.urlencode(
        {
            "key": api_key,
            "steamid": steamid,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": "steam-source-collections/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    games = _games_from_payload(data)
    if not games:
        raise RuntimeError(
            "Steam 返回 0 款游戏。通常是「游戏详情」隐私未设为公开，"
            "或 SteamID 填错；也可以改用 --owned 导入自己导出的 JSON。"
        )
    return games


def load_family(path: str | Path | None) -> tuple[set[int], set[str]]:
    """Optional manual list of family-shared appids / names, one per line."""
    from .matching import normalize

    if not path:
        return set(), set()
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"家庭共享清单不存在: {p}")
    appids: set[int] = set()
    names: set[str] = set()
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value.isdigit():
            appids.add(int(value))
        else:
            names.add(normalize(value))
    return appids, names
