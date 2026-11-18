"""Turn license records + aligned appids into a collection plan."""

from __future__ import annotations

from dataclasses import dataclass

from .licenses import LicenseRecord, normalize_date
from .matching import MatchResult, normalize
from .owned import Game

GROUPS: tuple[tuple[str, str], ...] = (
    ("store", "Steam购买"),
    ("retail", "CDK激活"),
    ("free", "免费领取"),
    ("gift", "赠礼与订阅"),
    ("family", "家庭共享"),
    ("unknown", "待核对"),
)

# Paid licenses win when a game somehow shows up more than once: a purchased
# copy should never be silently re-labelled as a free giveaway.
PRIORITY: dict[str, int] = {
    "family": 100,
    "store": 5,
    "retail": 4,
    "gift": 3,
    "free": 2,
    "unknown": 0,
}


@dataclass
class Plan:
    groups: list[dict]
    assignments: dict[int, dict]
    unmatched_licenses: list[dict]
    unmatched_games: list[dict]
    stats: dict

    def as_dict(self) -> dict:
        return {
            "schema": "steam-source-collections/1",
            "groups": self.groups,
            "assignments": [self.assignments[key] for key in sorted(self.assignments)],
            "unmatched_licenses": self.unmatched_licenses,
            "unmatched_games": self.unmatched_games,
            "stats": self.stats,
        }


def _should_replace(old: dict, new: dict, conflict: str) -> bool:
    if old["group"] == "unknown":
        return True
    # A package id inferred from the local cache's row ordering is weaker
    # evidence than a real packageid or a matching game name. Never let it
    # overwrite a classification we already established from direct evidence.
    if new.get("match_via") == "order" and old.get("match_via") not in ("order", "none"):
        return False
    if conflict == "latest":
        return (normalize_date(new.get("date", "")), PRIORITY.get(new["group"], 0)) >= (
            normalize_date(old.get("date", "")),
            PRIORITY.get(old["group"], 0),
        )
    return PRIORITY.get(new["group"], 0) > PRIORITY.get(old["group"], 0)


def build_plan(
    records: list[LicenseRecord],
    games: list[Game],
    matches: list[MatchResult],
    family_appids: set[int],
    family_names: set[str],
    conflict: str = "priority",
    extra_groups: bool = True,
) -> Plan:
    game_by_id = {game.appid: game for game in games}
    assignments: dict[int, dict] = {
        game.appid: {
            "appid": game.appid,
            "name": game.name,
            "playtime_minutes": game.playtime_minutes,
            "idled": bool(getattr(game, "idled", False)),
            "group": "unknown",
            "method_raw": "",
            "license_package": "",
            "date": "",
            "match_via": "none",
            "match_score": 0.0,
            "note": (
                "没有许可行认领这个 appid（该 appid 在 Steam 数据库中已无记录或未缓存名称）"
                if game.name.startswith("App ")
                else "没有许可行认领这个 appid（礼包名与游戏名不同，或免费 / Demo 项目）"
            ),
        }
        for game in games
    }

    for game in games:
        if game.family_shared or game.appid in family_appids or normalize(game.name) in family_names:
            row = assignments[game.appid]
            row.update(group="family", method_raw="家庭共享清单", match_via="family-list", note="由 --family-file 或库数据中的 family_shared 标记")

    matched_game_ids: set[int] = set()
    unmatched_licenses: list[dict] = []

    for record, result in zip(records, matches):
        if not result.games:
            item = record.as_dict()
            item["note"] = result.note
            unmatched_licenses.append(item)
            continue
        for game in result.games:
            if game.appid not in assignments:
                continue
            matched_game_ids.add(game.appid)
            current = assignments[game.appid]
            if current["group"] == "family":
                continue
            note = result.note
            if game.name.startswith("App "):
                suffix = "该 appid 在 Steam 数据库中已无记录或未缓存名称"
                note = f"{note}；{suffix}" if note else suffix
            candidate = {
                **current,
                "group": record.category,
                "method_raw": record.method_raw,
                "license_package": record.package_name,
                "date": record.date,
                "match_via": result.via,
                "match_score": result.score,
                "note": note,
            }
            if _should_replace(current, candidate, conflict):
                assignments[game.appid] = candidate

    # Family-shared games never appear on your own license page, so they are
    # expected to have no license row and must not be reported as a gap.
    unmatched_games = [
        assignments[appid]
        for appid in sorted(set(assignments) - matched_game_ids)
        if assignments[appid]["group"] != "family"
    ]

    group_rows: dict[str, list[dict]] = {group_id: [] for group_id, _ in GROUPS}
    for appid in sorted(assignments):
        group_rows[assignments[appid]["group"]].append(assignments[appid])

    groups = [
        {
            "id": group_id,
            "name": name,
            "kind": "source",
            "appids": [row["appid"] for row in group_rows[group_id]],
            "games": group_rows[group_id],
        }
        for group_id, name in GROUPS
    ]

    if extra_groups:
        idled = [
            assignments[appid]
            for appid in sorted(assignments)
            if assignments[appid]["group"] != "family"
            and assignments[appid]["playtime_minutes"] > 0
            and assignments[appid].get("idled")
        ]
        groups.append(
            {
                "id": "idled",
                "name": "挂卡未玩（测试）",
                "kind": "cross",
                "appids": [row["appid"] for row in idled],
                "games": idled,
            }
        )

    stats = {
        "games_total": len(games),
        "license_rows": len(records),
        "license_unmatched": len(unmatched_licenses),
        "games_without_license": len(unmatched_games),
        "by_group": {group["id"]: len(group["appids"]) for group in groups},
        "conflict_strategy": conflict,
        "extra_groups": extra_groups,
    }
    return Plan(groups, assignments, unmatched_licenses, unmatched_games, stats)
