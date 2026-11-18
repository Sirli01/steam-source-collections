"""Read the export produced by the one-click bookmarklet.

The bookmarklet runs *inside* the logged-in Steam license page, so it can see
both the acquisition-method table and the account's owned appids. It writes a
single JSON file; this module turns that file into the same objects the CLI
uses for the manual (CSV / API) path.

Optional local enrichment
-------------------------
Steam's ``appcache/packageinfo.vdf`` maps package ids to exact appids. When
``--auto`` finds a Steam install, :func:`load_bundle` uses it to align license
rows without relying on game names. This rescues DLCs, delisted games and
playtests whose names the store API refuses to return. Without the local file
the module still works exactly as before.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .appinfo import (
    appinfo_path, first_cjk_fragment, read_app_names, read_trading_card_appids,
    resolve_localized_fragments, resolve_localized_names,
)
from .licenses import LicenseRecord, classify
from .local import LocalSteam, detect_idled_appids, discover
from .matching import CJK_RE, MatchResult, build_index, match_package, normalize, strip_promo_suffix
from .owned import Game
from .pkgexpand import expand_package_matches
from .vdfcache import single_app_packages, useful_packages

SCHEMA = "steam-license-export/1"
SUPPORTED_SCHEMAS = {
    "steam-license-export/1", "steam-license-export/2", "steam-license-export/3",
}


@dataclass
class Bundle:
    records: list[LicenseRecord]
    games: list[Game]
    matches: list[MatchResult]
    meta: dict


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_pid(value) -> int | None:
    try:
        pid = int(str(value).strip())
        return pid if pid > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_bundle_package_apps(raw_pkg_apps) -> tuple[dict[int, list[int]], dict[str, str]]:
    """Extract package -> appids and app names embedded in an export bundle."""
    pkg_map: dict[int, list[int]] = {}
    extra_names: dict[str, str] = {}
    if not isinstance(raw_pkg_apps, dict):
        return pkg_map, extra_names
    for raw_pid, raw_apps in raw_pkg_apps.items():
        pid = _as_pid(raw_pid)
        if pid is None or not isinstance(raw_apps, list):
            continue
        appids: list[int] = []
        for entry in raw_apps:
            if isinstance(entry, dict):
                aid = _as_int(entry.get("id") or entry.get("appid") or entry.get("app_id"))
                label = str(entry.get("name") or "").strip()
                if aid and aid > 0:
                    appids.append(aid)
                    if label and str(aid) not in extra_names:
                        extra_names[str(aid)] = label
            else:
                aid = _as_int(entry)
                if aid and aid > 0:
                    appids.append(aid)
        if appids:
            pkg_map[pid] = list(dict.fromkeys(appids))
    return pkg_map, extra_names


def _exact_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").casefold().strip())


def _guess_pid(
    name: str,
    name_to_pid: dict[str, int],
    exact_to_pid: dict[str, int] | None = None,
) -> int | None:
    """Resolve a license row's package id from its name, conservatively."""
    if exact_to_pid:
        exact = _exact_key(name)
        if exact and exact in exact_to_pid:
            return exact_to_pid[exact]
    key = normalize(name)
    if not key:
        return None
    if key in name_to_pid:
        return name_to_pid[key]
    if len(key) < 8:
        return None
    for candidate, pid in name_to_pid.items():
        if len(candidate) < 8:
            continue
        if key in candidate or candidate in key:
            overlap = min(len(key), len(candidate)) / max(len(key), len(candidate))
            if overlap >= 0.85:
                return pid
    return None


STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"


def fetch_online_app_names(
    appids,
    timeout: float = 8.0,
    retries: int = 2,
    batch_size: int = 100,
) -> dict[int, str]:
    """Optional batch name lookup via Steam's IStoreBrowseService/GetItems.

    Silently degrades on network timeouts or errors and returns whatever names
    were fetched successfully.
    """
    ids = [int(a) for a in dict.fromkeys(appids) if _as_int(a)]
    if not ids:
        return {}
    found: dict[int, str] = {}
    for start in range(0, len(ids), batch_size):
        chunk = ids[start: start + batch_size]
        body = {
            "ids": [{"appid": a} for a in chunk],
            "context": {"language": "english", "country_code": "US", "steam_realm": 1},
            "data_request": {"include_basic_info": True},
        }
        url = STORE_ITEMS_URL + "?input_json=" + urllib.parse.quote(
            json.dumps(body, separators=(",", ":"))
        )
        req = urllib.request.Request(url, headers={"User-Agent": "steam-source-collections/1.0"})
        payload = None
        for _attempt in range(max(1, retries + 1)):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                continue
        if not isinstance(payload, dict):
            continue
        items = (payload.get("response") or {}).get("store_items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            aid = _as_int(item.get("appid") or item.get("id"))
            label = str(item.get("name") or "").strip()
            if aid and label:
                found[aid] = label
    return found


def align_license_packages(records: list[LicenseRecord], explicit_pids: list[int | None],
                           owned_packages: list, pkgmap: dict[int, list[int]],
                           infer_order: bool = True) -> dict[int, int]:
    """Infer package ids for license rows that do not print one.

    The license page lists rows newest-first, while ``rgOwnedPackages`` from
    ``dynamicstore/userdata`` is ordered oldest-first. Rows that *do* carry a
    packageid form anchors; on real exports those anchors are strictly
    anti-monotonic, and in ~90% of the gaps the number of rows equals the
    number of packages. Only such exactly-1:1 gaps are filled in, so a wrong
    guess cannot shift the rest of the alignment.

    Set ``infer_order=False`` to disable this and keep only direct evidence.
    """
    if not infer_order or not pkgmap:
        return {}
    pkgs = [pid for pid in (_as_pid(value) for value in owned_packages) if pid is not None]
    if len(pkgs) < 5:
        return {}
    position = {pid: index for index, pid in enumerate(pkgs)}
    anchors = sorted(
        (index, position[pid])
        for index, pid in enumerate(explicit_pids)
        if pid is not None and pid in position
    )
    if len(anchors) < 5:
        return {}
    descending = sum(1 for a, b in zip(anchors, anchors[1:]) if b[1] < a[1])
    if descending / (len(anchors) - 1) < 0.8:
        return {}  # ordering assumption does not hold for this account

    bounds = [(-1, len(pkgs))] + anchors + [(len(records), -1)]
    inferred: dict[int, int] = {}
    for (row0, pkg0), (row1, pkg1) in zip(bounds, bounds[1:]):
        rows_between = row1 - row0 - 1
        pkgs_between = pkg0 - pkg1 - 1
        if rows_between <= 0 or rows_between != pkgs_between:
            continue
        for step in range(rows_between):
            index = row0 + 1 + step
            package_index = pkg0 - 1 - step
            if 0 <= package_index < len(pkgs):
                pid = pkgs[package_index]
                if pid in pkgmap:
                    inferred[index] = pid
    return inferred


def load_bundle(path: str | Path, local: LocalSteam | None = None,
                threshold: float = 0.86, infer_order: bool = True,
                online_names: bool = False) -> Bundle:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"导出包不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("导出包格式不正确（顶层应为对象）")

    raw_licenses = data.get("licenses") or []
    if not isinstance(raw_licenses, list) or not raw_licenses:
        raise ValueError("导出包里没有许可记录，请确认是在许可页上点的导出按钮")

    bundle_sid = str(data.get("steamid64") or "").strip()
    if local and local.root and bundle_sid and bundle_sid != local.steamid64:
        switched = discover(local.root, preferred_steamid64=bundle_sid)
        local.steamid64 = switched.steamid64
        local.id3 = switched.id3
        local.cloud_path = switched.cloud_path
        local.cloud_format = switched.cloud_format
        local.collections = switched.collections
        local.playtimes = switched.playtimes
        local.last_played = switched.last_played
        local.unlocked_achievement_appids = switched.unlocked_achievement_appids
        local.defined_achievement_appids = switched.defined_achievement_appids
        local.saved_appids = switched.saved_appids
        local.package_apps = switched.package_apps
        local.notes = switched.notes

    records: list[LicenseRecord] = []
    explicit_appids: list[list[int]] = []
    explicit_pids: list[int | None] = []
    for row in raw_licenses:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("package_name") or "").strip()
        if not name:
            continue
        method = str(row.get("method") or row.get("method_raw") or "")
        records.append(LicenseRecord(
            date=str(row.get("date") or ""),
            package_name=name,
            method_raw=method,
            category=classify(method),
        ))
        ids = row.get("appids") or []
        explicit_appids.append([
            appid for appid in (_as_int(value) for value in ids) if appid is not None
        ] if isinstance(ids, list) else [])
        explicit_pids.append(_as_pid(row.get("packageid")))

    names = data.get("app_names") or {}
    names = {str(k): str(v) for k, v in names.items() if str(v).strip()} if isinstance(names, dict) else {}

    owned: list[int] = []
    for value in data.get("owned_appids") or []:
        appid = _as_int(value)
        if appid is not None:
            owned.append(appid)
    if not owned and local:
        owned = sorted(local.collection_appids)
    owned = list(dict.fromkeys(owned))
    if not owned:
        raise ValueError("导出包里没有拥有的游戏 appid 列表（owned_appids）")
    owned_set = set(owned)

    # Apps borrowed through Steam Families get their own Game rows flagged
    # family_shared, so build_plan puts them in group ⑤ without a fake license.
    family_names_raw = data.get("family_shared_names") or {}
    if isinstance(family_names_raw, dict):
        for key, value in family_names_raw.items():
            label = str(value).strip()
            if label and str(key) not in names:
                names[str(key)] = label
    family_shared: list[int] = []
    for value in data.get("family_shared_appids") or []:
        appid = _as_int(value)
        if appid is not None and appid not in owned_set:
            family_shared.append(appid)
    family_shared = list(dict.fromkeys(family_shared))
    family_diag = (data.get("diagnostics") or {}).get("family")
    if not isinstance(family_diag, dict):
        family_diag = {}

    playtimes: dict[int, int] = {}
    for key, value in (data.get("playtimes") or {}).items():
        appid = _as_int(key)
        minutes = _as_int(value)
        if appid is not None and minutes is not None:
            playtimes[appid] = minutes
    if local:
        for appid, minutes in local.playtimes.items():
            playtimes.setdefault(appid, minutes)

    auto_grant = {
        appid for appid in (_as_int(v) for v in (data.get("auto_grant_appids") or []))
        if appid is not None
    }

    # ---------- optional exact alignment from the local Steam cache ----------
    bundle_pkg_raw, bundle_pkg_names = _parse_bundle_package_apps(data.get("package_apps"))
    for key, label in bundle_pkg_names.items():
        names.setdefault(key, label)

    cache = appinfo_path(local.root) if local else None
    cache_bytes = b""
    if cache:
        try:
            cache_bytes = cache.read_bytes()
        except OSError:
            cache_bytes = b""

    # Recover missing app names from local appinfo.vdf *before* building matching
    # indices or borrowing package titles, filling gaps only (never overwriting).
    cache_recovered_names = 0
    if cache_bytes:
        all_ids = owned + family_shared
        gaps = {
            appid for appid in all_ids
            if not names.get(str(appid)) or str(names.get(str(appid), "")).startswith("App ")
        }
        if gaps:
            recovered = read_app_names(cache_bytes, gaps)
            for appid, label in recovered.items():
                key = str(appid)
                if not names.get(key) or str(names.get(key, "")).startswith("App "):
                    names[key] = label
                    cache_recovered_names += 1

    online_recovered_names = 0
    if online_names:
        all_ids = owned + family_shared
        gaps_list = [
            appid for appid in all_ids
            if not names.get(str(appid)) or str(names.get(str(appid), "")).startswith("App ")
        ]
        if gaps_list:
            fetched = fetch_online_app_names(gaps_list)
            for appid, label in fetched.items():
                key = str(appid)
                if not names.get(key) or str(names.get(key, "")).startswith("App "):
                    names[key] = label
                    online_recovered_names += 1

    local_pkgmap = useful_packages(local.package_apps, owned_set) if local else {}
    merged_raw = {**bundle_pkg_raw, **(local.package_apps if local else {})}
    pkgmap = useful_packages(merged_raw, owned_set) if merged_raw else {}
    name_to_pid: dict[str, int] = {}
    exact_to_pid: dict[str, int] = {}
    if pkgmap:
        # A package that grants exactly one owned app can be identified by
        # that app's name. This lets license rows without a packageid still
        # use the exact package -> appid mapping. Ambiguous normalized keys
        # shared by multiple distinct appids are excluded.
        norm_appid: dict[str, int] = {}
        ambiguous_norm: set[str] = set()
        exact_appid: dict[str, int] = {}
        ambiguous_exact: set[str] = set()
        for pid, apps in sorted(pkgmap.items()):
            if len(apps) != 1:
                continue
            aid = apps[0]
            label = names.get(str(aid), "")
            if not label or label.startswith("App "):
                continue
            ekey = _exact_key(label)
            if ekey:
                if ekey in exact_appid and exact_appid[ekey] != aid:
                    ambiguous_exact.add(ekey)
                else:
                    exact_appid[ekey] = aid
                    exact_to_pid.setdefault(ekey, pid)
            nkey = normalize(label)
            if nkey:
                if nkey in norm_appid and norm_appid[nkey] != aid:
                    ambiguous_norm.add(nkey)
                else:
                    norm_appid[nkey] = aid
                    name_to_pid.setdefault(nkey, pid)
        for ekey in ambiguous_exact:
            exact_to_pid.pop(ekey, None)
        for nkey in ambiguous_norm:
            name_to_pid.pop(nkey, None)

    order_pids = align_license_packages(
        records, explicit_pids, data.get("owned_packages") or [], pkgmap, infer_order
    )
    effective_pids: list[int | None] = []
    for index, (record, pid) in enumerate(zip(records, explicit_pids)):
        if pid is None or pid not in pkgmap:
            pid = order_pids.get(index) or _guess_pid(record.package_name, name_to_pid, exact_to_pid)
        effective_pids.append(pid)

    # An unnamed app that is the sole member of a package can borrow the
    # cleaned license package name for display only. It never enters the
    # matching index so package strings cannot leak into name matching.
    label_by_pid: dict[int, str] = {}
    for record, pid in zip(records, effective_pids):
        if pid is not None and pid not in label_by_pid:
            label_by_pid[pid] = record.package_name
    inferred_names = 0
    display_names = dict(names)
    if pkgmap:
        for appid, pid in single_app_packages(pkgmap).items():
            if (not display_names.get(str(appid)) or str(display_names.get(str(appid), "")).startswith("App ")) \
                    and pid in label_by_pid:
                label = strip_promo_suffix(label_by_pid[pid])
                if label:
                    display_names[str(appid)] = label[:120]
                    inferred_names += 1

    card_appids = read_trading_card_appids(cache_bytes, owned_set) if cache_bytes else set()
    idled_appids = (
        detect_idled_appids(
            playtimes,
            getattr(local, "unlocked_achievement_appids", None) or set(),
            getattr(local, "defined_achievement_appids", None) or set(),
            getattr(local, "saved_appids", None) or set(),
            getattr(local, "last_played", None) or {},
            card_appids=card_appids,
        )
        if local
        else set()
    )

    games = [
        Game(
            appid=appid,
            name=display_names.get(str(appid), f"App {appid}"),
            playtime_minutes=playtimes.get(appid, 0),
            idled=(appid in idled_appids and playtimes.get(appid, 0) > 0),
        )
        for appid in owned
    ] + [
        Game(
            appid=appid,
            name=display_names.get(str(appid), f"App {appid}"),
            playtime_minutes=playtimes.get(appid, 0),
            family_shared=True,
        )
        for appid in family_shared
    ]
    by_id = {game.appid: game for game in games}
    # Only personally-owned apps with real app names (not display-only borrowed
    # package labels) may be matched to license rows by name; a borrowed game
    # with the same title must not steal a license record.
    index = build_index([
        game for game in games[: len(owned)]
        if str(game.appid) in names and not names[str(game.appid)].startswith("App ")
    ])

    # The license page localizes package names, so rows like "米塔" cannot be
    # matched to "MiSide" by string similarity. Steam's own appinfo cache has
    # the localized names and their appids; use it as an exact lookup table.
    alias_map: dict[str, int] = {}
    if cache_bytes:
        wanted = [record.package_name for record in records
                  if CJK_RE.search(record.package_name)]
        alias_map = resolve_localized_names(cache_bytes, wanted, owned_set)

    matches: list[MatchResult] = []
    exact_rows = 0
    inferred_rows = 0
    localized_rows = 0
    for row_index, (record, ids, pid) in enumerate(zip(records, explicit_appids, effective_pids)):
        resolved_ids = list(ids)
        if pid is not None and pid in pkgmap:
            resolved_ids.extend(pkgmap[pid])
        resolved_ids = list(dict.fromkeys(resolved_ids))
        resolved = [by_id[appid] for appid in resolved_ids if appid in by_id]
        from_order = row_index in order_pids and explicit_pids[row_index] is None
        if resolved:
            exact_rows += 1
            if from_order:
                inferred_rows += 1
            note = ("由本机缓存顺序对齐推定的礼包包精确对应，建议抽查"
                    if from_order else
                    "由礼包包 → appid 精确对应" + ("（本机 Steam 缓存）" if local_pkgmap else ""))
            matches.append(MatchResult(
                games=resolved, via="order" if from_order else "package",
                score=0.65 if from_order else 1.0, note=note,
            ))
        else:
            alias_appid = alias_map.get(normalize(record.package_name))
            if alias_appid is not None and alias_appid in by_id:
                localized_rows += 1
                matches.append(MatchResult(
                    games=[by_id[alias_appid]], via="localized", score=0.92,
                    note="由 Steam 客户端本地化名称精确对齐",
                ))
            else:
                matches.append(match_package(record.package_name, index, threshold))

    # A row naming a collection ("Batman: Arkham Collection") can never match
    # one game. The local package cache tells us exactly which appids such a
    # package grants, so claim the whole bundle in one conservative step.
    expansion = {"expanded_rows": 0, "expanded_apps": 0}
    if pkgmap:
        localized_hits: dict[int, set[int]] = {}
        if cache_bytes:
            row_fragments: dict[int, str] = {}
            for row_index, (record, match) in enumerate(zip(records, matches)):
                if match.games:
                    continue
                fragment = first_cjk_fragment(record.package_name)
                if fragment:
                    row_fragments[row_index] = fragment
            if row_fragments:
                localized_hits = resolve_localized_fragments(cache_bytes, row_fragments, owned_set)
        expansion = expand_package_matches(
            records, matches,
            {pid for pid in effective_pids if pid is not None},
            pkgmap, names, by_id,
            localized_hits=localized_hits,
        )

    unnamed = sum(1 for game in games[: len(owned)] if game.name.startswith("App "))
    license_rows = len(records)
    owned_apps = len(owned)
    license_pages = _as_int(data.get("license_pages")) or 0
    warnings: list[str] = []
    # A single-page export is the classic "only first page" failure. A 12-page
    # export with 912 rows is not that failure, even if the page claims more.
    if owned_apps and license_pages <= 1 and license_rows < owned_apps * 0.5:
        warnings.append(
            f"只导出 {license_rows} 行许可，但库里有 {owned_apps} 款游戏；"
            "Steam 许可页是分页的，这份导出包很可能只抓了第一页，请用最新版书签重新导出。"
        )
    if owned_apps and unnamed > owned_apps * 0.25:
        tail = (
            f"已用本机 Steam 缓存把其中 {exact_rows} 行许可精确对齐到游戏，不需要重导。"
            if local_pkgmap else
            "可在试用页点「用本机 Steam 缓存补强」，或启动图形界面点「一键统计」自动读取本机缓存补齐。"
        )
        warnings.append(
            f"{unnamed} 款游戏没有名称（多为 DLC / 已下架 / 试玩项目，商店接口不返回名称）；{tail}"
        )
    account_notes = [
        note for note in (local.notes if local else [])
        if "导出包账号" in note
    ]
    meta = {
        "schema": data.get("schema"),
        "exported_at": data.get("exported_at"),
        "steamid64": data.get("steamid64") or (local.steamid64 if local else None),
        "license_rows": license_rows,
        "owned_apps": owned_apps,
        "family_apps": len(family_shared),
        "family_groupid": data.get("family_groupid") or "",
        "family_status": family_diag.get("status", ""),
        "named_apps": owned_apps - unnamed,
        "unnamed_apps": unnamed,
        "explicit_appid_rows": sum(1 for m in matches if m.via in ("package", "order")),
        "order_aligned_rows": inferred_rows,
        "localized_aliases": len(alias_map),
        "localized_matched_rows": localized_rows,
        "local_cache_packages": len(local_pkgmap),
        "bundle_cache_packages": len(bundle_pkg_raw),
        "bundle_expanded_rows": expansion["expanded_rows"],
        "bundle_expanded_apps": expansion["expanded_apps"],
        "cache_recovered_names": cache_recovered_names,
        "online_recovered_names": online_recovered_names,
        "inferred_names": inferred_names,
        "auto_grant_appids": len(auto_grant),
        "owned_packages": len(data.get("owned_packages") or []),
        "license_pages": license_pages,
        "license_total": _as_int(data.get("license_total")) or 0,
        "name_source": data.get("name_source") or "",
        "playtime_count": len(playtimes),
        "idled_count": sum(1 for game in games[: len(owned)] if game.idled),
        "account_notes": account_notes,
        "diagnostics": data.get("diagnostics") or {},
        "warnings": warnings,
        "source_file": str(p),
    }
    return Bundle(records, games, matches, meta)
