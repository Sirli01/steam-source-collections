"""Claim whole Steam packages for license rows that name a bundle, not a game.

The license page prints a *package* name. For a collection such as
``Batman: Arkham Collection`` there is no single game with that name, so
name matching returns nothing and every app the package grants (the three
games plus dozens of DLCs) lands in group ⑥. The Steam client's local
``appcache/packageinfo.vdf`` already lists exactly which appids each package
grants; the only missing link is which package a license row refers to.

This module closes that link conservatively: a license row with no match is
compared against the names of the apps inside each *unclaimed* package. Only
high-confidence, unambiguous hits are accepted, and only appids that no other
license row has claimed are assigned, so existing groups are never rewritten.

Read-only with respect to Steam; it only mutates the in-memory match list.
"""

from __future__ import annotations

import os

from .matching import MatchResult, normalize

# Calibrated on a 913-row / 1218-app real library: every correct hit scored
# >= 0.78, while unrelated package titles score 0.0. Keeping the bar high is
# more important than covering every row; the rest stays in ⑥ for review.
MIN_SCORE = 0.76
AMBIGUITY_GAP = 0.03


def package_title_score(package_name: str, app_name: str) -> float:
    """Similarity between a license package name and one app inside it."""
    package_key = normalize(package_name)
    app_key = normalize(app_name)
    if not package_key or not app_key:
        return 0.0
    if package_key == app_key:
        return 1.0
    short, long = ((package_key, app_key) if len(package_key) <= len(app_key)
                   else (app_key, package_key))
    if len(short) >= 5 and short in long:
        return 0.78 + 0.2 * len(short) / len(long)
    prefix = os.path.commonprefix([package_key, app_key])
    if len(prefix) >= 8 and len(prefix) >= 0.6 * min(len(package_key), len(app_key)):
        return 0.70 + 0.25 * len(prefix) / max(len(package_key), len(app_key))
    return 0.0


def expand_package_matches(
    records,
    matches: list[MatchResult],
    claimed_pids: set[int],
    package_apps: dict[int, list[int]],
    names: dict[str, str],
    by_id: dict[int, object],
    threshold: float = MIN_SCORE,
    localized_hits: dict[int, set[int]] | None = None,
) -> dict[str, int]:
    """Fill in unmatched license rows by expanding a whole package.

    ``package_apps`` is the local cache restricted to owned appids.
    ``localized_hits`` maps a row index to appids found via Chinese localized
    names in the client cache. Returns counters; ``matches`` is updated in place.
    """
    assigned = {game.appid for match in matches for game in match.games}  # type: ignore[attr-defined]
    candidates: list[tuple[int, list[int], list[str]]] = []
    for pid, apps in sorted(package_apps.items()):
        if pid in claimed_pids or not any(a not in assigned for a in apps):
            continue
        labels = [names.get(str(appid), "") for appid in apps]
        labels = [label for label in labels if label and not label.startswith("App ")]
        if labels:
            candidates.append((pid, apps, labels))
    if not candidates:
        return {"expanded_rows": 0, "expanded_apps": 0}

    proposals: list[tuple[float, int, int, list[int]]] = []
    for row_index, (record, match) in enumerate(zip(records, matches)):
        if match.games:
            continue
        scored: dict[int, tuple[float, list[int]]] = {}
        for pid, apps, labels in candidates:
            score = max(package_title_score(record.package_name, label)
                        for label in labels)
            if score >= threshold:
                scored[pid] = (score, apps)

        # A Chinese collection name has no overlap with English app titles.
        # Several localized names pointing into one unclaimed package are a
        # strong signal; a single hit is deliberately ignored.
        hits = (localized_hits or {}).get(row_index) or set()
        if hits:
            owners = [(pid, len(hits & set(apps)), apps)
                      for pid, apps, _labels in candidates
                      if hits & set(apps)]
            owners.sort(key=lambda item: (-item[1], item[0]))
            if owners and owners[0][1] >= 2 and (
                len(owners) == 1 or owners[1][1] == 0
            ):
                pid, _count, apps = owners[0]
                if pid not in scored or scored[pid][0] < 0.82:
                    scored[pid] = (0.82, apps)

        ranked = sorted(((score, pid, apps)
                         for pid, (score, apps) in scored.items()),
                        key=lambda item: (-item[0], item[1]))
        if not ranked:
            continue
        top = ranked[0]
        if len(ranked) > 1 and ranked[1][0] >= top[0] - AMBIGUITY_GAP:
            # Two equally good packages are only harmless when they grant the
            # same apps (regional variants of one bundle). Otherwise skip.
            if set(ranked[1][2]) != set(top[2]):
                continue
        proposals.append((top[0], row_index, top[1], top[2]))

    proposals.sort(key=lambda item: (-item[0], item[1]))
    used_pids: set[int] = set()
    used_rows: set[int] = set()
    expanded_rows = 0
    expanded_apps = 0
    for score, row_index, pid, apps in proposals:
        if pid in used_pids or row_index in used_rows:
            continue
        games = [by_id[appid] for appid in apps  # type: ignore[index]
                 if appid in by_id and appid not in assigned]
        if not games:
            continue
        matches[row_index] = MatchResult(
            games=games, via="bundle", score=round(score, 3),
            note="由本机礼包缓存按包内游戏名展开（整包同源）",
        )
        used_pids.add(pid)
        used_rows.add(row_index)
        assigned.update(game.appid for game in games)  # type: ignore[attr-defined]
        expanded_rows += 1
        expanded_apps += len(games)
    return {"expanded_rows": expanded_rows, "expanded_apps": expanded_apps}
