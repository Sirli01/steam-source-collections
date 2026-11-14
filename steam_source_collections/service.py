"""High-level operations used by the desktop GUI.

The GUI never re-implements the pipeline: it calls :func:`scan`,
:func:`analyse`, :func:`apply_plan` and :func:`restore_latest`.  Keeping the
orchestration here means the CLI, the offline page and the GUI all agree on
the same numbers, and every destructive action has exactly one guard rail.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__, cefbridge
from .bundle import load_bundle
from .local import LocalSteam, discover, download_dirs, find_latest_bundle
from .plan import Plan, build_plan
from .report import write_outputs
from .writeback import (
    WritebackError,
    apply as writeback_apply,
    hidden_console_kwargs,
    preview as writeback_preview,
    steam_is_running,
)

LICENSE_PAGE = "https://store.steampowered.com/account/licenses/"


class ServiceError(RuntimeError):
    """An error that should be shown verbatim in the GUI."""


@dataclass
class Analysis:
    plan: Plan
    meta: dict
    notes: list[str]
    outputs: list[Path]
    bundle_path: Path
    local: LocalSteam
    include_cross: bool = False

    @property
    def report_path(self) -> Path | None:
        return next((p for p in self.outputs if p.suffix == ".html"), None)

    def groups(self) -> list[dict]:
        by_id = {game.appid: game.name for game in self._games}
        result = []
        for group in self.plan.groups:
            names = [by_id.get(int(a), f"App {a}") for a in group["appids"][:4]
                     if str(a).isdigit()]
            result.append({
                "id": group["id"],
                "name": group["name"],
                "kind": group.get("kind", "source"),
                "count": len(group["appids"]),
                "sample": names,
            })
        return result

    _games: list = field(default_factory=list)


def _read_bundle_head(path: Path) -> dict:
    """Lightweight peek at an export: enough for the status card, no parsing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError(f"导出包读不出来：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("licenses"), list):
        raise ServiceError("这不是书签导出的 steam-licenses-*.json")
    return {
        "path": str(path),
        "name": path.name,
        "schema": str(data.get("schema") or "未知"),
        "exported_at": data.get("exported_at") or "",
        "license_rows": len(data["licenses"]),
        "owned_apps": len(data.get("owned_appids") or []),
        "family_apps": len(data.get("family_shared_appids") or []),
        "license_pages": int(data.get("license_pages") or 0),
        "modified": path.stat().st_mtime,
        "needs_update": str(data.get("schema") or "") != "steam-license-export/3",
    }


def locate_bundle(outdir: str | Path = "out",
                  explicit: str | Path | None = None) -> Path | None:
    """Find the newest export, including files the user dropped into the GUI.

    Downloads get cleaned out; a bundle dropped into the window is copied to
    ``out/uploaded/`` so the next launch still works with zero navigation.
    """
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    path = find_latest_bundle()
    if path:
        return path
    folder = Path(outdir) / "uploaded"
    if folder.is_dir():
        saved = list(folder.glob("steam-licenses*.json"))
        if saved:
            return max(saved, key=lambda p: p.stat().st_mtime)
    return None


def scan(local: LocalSteam | None = None, bundle_path: str | Path | None = None,
         outdir: str | Path = "out") -> dict:
    """Read-only inventory of everything the GUI needs before pressing a button."""
    info = local or discover()
    path: Path | None = None
    if bundle_path:
        path = Path(bundle_path)
        if not path.is_file():
            raise ServiceError(f"找不到这个文件：{path}")
    else:
        path = locate_bundle(outdir)

    bundle = None
    bundle_error = ""
    if path:
        try:
            bundle = _read_bundle_head(path)
        except ServiceError as exc:
            bundle_error = str(exc)

    cloud = info.cloud_path
    return {
        "version": __version__,
        # Steam's process state is deliberately NOT probed here.  scan() runs on
        # page load, on every rescan and after each report; the user only cares
        # about it at the moment they try to write. See steam_status().
        "steam": {
            "found": info.root is not None,
            "root": str(info.root) if info.root else "",
            "steamid": bool(info.steamid64),
            "cloud_format": info.cloud_format,
            "collections": len(info.collections),
            "playtimes": len(info.playtimes),
            "cache_packages": len(info.package_apps),
            "notes": info.notes,
        },
        "cloud": {
            "found": bool(cloud and Path(cloud).is_file()),
            "path": str(cloud) if cloud else "",
        },
        "bundle": bundle,
        "bundle_error": bundle_error,
        "download_folders": [str(p) for p in download_dirs()],
        "has_report": (Path(outdir) / "report.html").is_file(),
        "license_page": LICENSE_PAGE,
    }


def build_notes(meta: dict) -> list[str]:
    notes: list[str] = list(meta.get("account_notes") or [])
    if meta.get("local_cache_packages"):
        notes.append(
            f"已只读解析本机 Steam 缓存：{meta['local_cache_packages']} 个礼包包、"
            f"{meta['explicit_appid_rows']} 行许可精确对齐到 appid，"
            f"并补回 {meta.get('inferred_names', 0)} 个显示名称。"
        )
    if meta.get("order_aligned_rows"):
        notes.append(
            f"其中 {meta['order_aligned_rows']} 行的礼包包由顺序对齐推定（间接证据），"
            "可在 mapping.csv 里筛 match_via=order 逐条复核。"
        )
    if meta.get("bundle_expanded_rows"):
        notes.append(
            f"另有 {meta['bundle_expanded_rows']} 行是合集包 / 促销包，"
            f"已按本机礼包缓存整包展开，把 {meta['bundle_expanded_apps']} 个 appid "
            "（含本体与 DLC）归入同一来源；可在 mapping.csv 里筛 match_via=bundle 核对。"
        )
    if meta.get("localized_matched_rows"):
        notes.append(
            f"另有 {meta['localized_matched_rows']} 行中文许可名由本机缓存本地化名称精确对齐。"
        )
    if meta.get("unnamed_apps"):
        notes.append(
            f"{meta['unnamed_apps']} 款游戏没有名称，多为 DLC / 已下架 / 试玩项目，"
            "商店接口本来就不返回；重新导出也补不回来，已尽量按礼包包归组。"
        )
    if meta.get("family_apps"):
        group = meta.get("family_groupid")
        notes.append(
            f"⑤ 家庭共享：读到 {meta['family_apps']} 款家人借给你的游戏"
            + (f"（家庭组 {group}）" if group else "")
            + "。这些游戏不出现在你的许可页里，属于正常情况。"
        )
    else:
        notes.append(
            "⑤ 家庭共享：这份导出包不含共享游戏清单。点界面最上面的「查看导出步骤 / 重新导出」"
            "重新导出一次，即可自动填好 ⑤ 组。"
        )
    return notes


def analyse(bundle_path: str | Path | None = None, outdir: str | Path = "out",
            infer_order: bool = True, threshold: float = 0.86,
            conflict: str = "priority", extra_groups: bool = True,
            include_cross: bool = False) -> Analysis:
    """One-click statistics: locate everything, build the plan, write reports."""
    local = discover()
    path = locate_bundle(outdir, bundle_path)
    if not path or not path.is_file():
        raise ServiceError(
            "还没有找到导出包。请按界面最上面 NO.00 的三步做一次：把「导出 Steam 数据」拖到书签栏 → "
            "打开 Steam 许可页并在那个页面上点一下书签 → 浏览器自动下载后回来点「一键统计」。"
            "也可以把下载好的 steam-licenses-*.json 直接拖进窗口。"
        )
    bundle = load_bundle(path, local, threshold, infer_order=infer_order)
    plan = build_plan(
        bundle.records, bundle.games, bundle.matches, set(), set(),
        conflict=conflict, extra_groups=extra_groups,
    )
    out = Path(outdir)
    notes = build_notes(bundle.meta)
    outputs = write_outputs(
        plan, out, path.name, f"一键导出包 {path.name}", notes=notes,
    )
    return Analysis(
        plan=plan, meta=bundle.meta, notes=notes,
        outputs=outputs, bundle_path=path, local=local,
        include_cross=include_cross, _games=bundle.games,
    )


def steam_status() -> bool | None:
    """One-shot Steam process check, called only when the user is about to write."""
    return steam_is_running()


BASELINE_NAME = "client_baseline.json"


def merge_plan_with_client(plan: Plan, existing: list[dict],
                           baseline: dict | None = None,
                           include_cross: bool = False) -> list[dict]:
    """Build client-side writes without undoing the user's manual edits.

    ``baseline`` maps a group name to the appids written last time. If it is
    missing, the current plan is used as the last write (true for anyone
    upgrading from the file-based writer). Games the user added since then are
    kept; games they removed stay removed.
    """
    prefix = cefbridge.PREFIX
    by_name = {
        str(item["name"])[len(prefix):]: item
        for item in existing
        if str(item.get("name", "")).startswith(prefix)
    }
    baseline = baseline or {}
    allowed = ("source", "cross") if include_cross else ("source",)
    items: list[dict] = []
    alphabet = string.ascii_letters + string.digits
    for group in plan.groups:
        if group.get("kind") not in allowed or not group.get("appids"):
            continue
        name = group["name"]
        want = {int(a) for a in group["appids"] if str(a).isdigit()}
        old = by_name.get(name)
        if old:
            base = {int(a) for a in baseline.get(name, group["appids"])}
            current = {int(a) for a in old.get("added") or []}
            user_added = current - base
            user_removed = base - current
            final = sorted((want | user_added) - user_removed)
            removed = sorted({int(a) for a in old.get("removed") or []})
            cid = str(old["id"])
        else:
            final = sorted(want)
            removed = []
            cid = "uc-" + "".join(secrets.choice(alphabet) for _ in range(12))
        items.append({"id": cid, "name": prefix + name,
                      "added": final, "removed": removed})
    return items


def client_write_plan(analysis: Analysis, outdir: str | Path,
                      include_cross: bool | None = None) -> dict:
    """Write through the *running* Steam client, which uploads to Steam Cloud."""
    cross = getattr(analysis, "include_cross", False) if include_cross is None else include_cross
    try:
        existing = cefbridge.list_managed()
    except (cefbridge.CEFError, OSError) as exc:
        raise ServiceError(f"读取 Steam 收藏集失败：{exc}") from exc

    out = Path(outdir)
    backup_dir = out / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_path = backup_dir / f"client-collections.{stamp}.bak.json"
    snapshot_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    path = out / BASELINE_NAME
    try:
        baseline = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        baseline = {}
    items = merge_plan_with_client(analysis.plan, existing, baseline, include_cross=cross)
    try:
        result = cefbridge.save_many(items)
    except (cefbridge.CEFError, OSError) as exc:
        raise ServiceError(f"Steam 客户端写入失败：{exc}") from exc
    failed = [row for row in result if not row.get("ok")]
    if failed:
        raise ServiceError("Steam 客户端未能保存：" + "；".join(
            f"{row.get('name', '')}：{row.get('error', '未知错误')}" for row in failed))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {item["name"][len(cefbridge.PREFIX):]: item["added"] for item in items},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "via": "client",
        "collections": [item["name"] for item in items],
        "saved": result,
        "backups": [str(snapshot_path)],
    }


def preflight(analysis: Analysis, include_cross: bool | None = None) -> dict:
    """Read-only capability check; never probes Steam's process."""
    cross = getattr(analysis, "include_cross", False) if include_cross is None else include_cross
    allowed = ("source", "cross") if cross else ("source",)
    groups = [
        cefbridge.PREFIX + group["name"] for group in analysis.plan.groups
        if group.get("kind") in allowed and group.get("appids")
    ]
    cloud = analysis.local.cloud_path
    if cefbridge.debugger_available():
        # Steam is running with its local CEF debugger available: the client
        # can write and upload the collections itself. No file surgery, no
        # restart, no need to close Steam.
        return {
            "writable": True,
            "reason": "",
            "via": "client",
            "cef_available": True,
            "requires_steam_closed": False,
            "collections_to_write": groups,
            "strategy": "通过正在运行的 Steam 客户端写入，Steam 自动同步到账号与 Steam Deck",
            "cloud_path": str(cloud) if cloud else "",
        }
    if not cloud or not Path(cloud).is_file():
        return {"writable": False, "reason": "没有找到 Steam 收藏集文件",
                "via": "file", "cef_available": False,
                "collections_to_write": groups}
    try:
        info = writeback_preview(Path(cloud), analysis.plan, include_cross=cross)
    except (WritebackError, OSError, json.JSONDecodeError) as exc:
        return {"writable": False, "reason": str(exc), "via": "file",
                "cef_available": False, "collections_to_write": groups}
    info["reason"] = ""
    info["cloud_path"] = str(cloud)
    info["via"] = "file"
    info["cef_available"] = False
    return info


def apply_plan(analysis: Analysis, outdir: str | Path = "out",
               confirm: bool = False, include_cross: bool | None = None) -> dict:
    """Write to Steam: prefer the running client, fall back to the file store."""
    if not confirm:
        raise ServiceError("需要用户在界面上确认后才会写入。")
    cross = getattr(analysis, "include_cross", False) if include_cross is None else include_cross
    pre = preflight(analysis, include_cross=cross)
    if pre.get("via") == "client":
        return client_write_plan(analysis, outdir, include_cross=cross)
    if not pre.get("writable"):
        raise ServiceError("收藏集文件不可安全写入：" + (pre.get("reason") or "原因未知"))
    if steam_status():
        raise ServiceError(
            "Steam 客户端还在运行，而且没有开启本机调试接口——你还没有关闭 Steam。"
            "请改用文件方式写入：在托盘图标右键 → 退出，再点一次写入。"
        )
    try:
        backups = writeback_apply(Path(pre["cloud_path"]), analysis.plan,
                                  Path(outdir), include_cross=cross)
    except WritebackError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        "ok": True,
        "via": "file",
        "collections": pre.get("collections_to_write") or [],
        "backups": [str(p) for p in backups],
    }


def _backup_candidates(cloud: Path, outdir: Path) -> list[Path]:
    folders = [cloud.parent / "backups", outdir / "backups"]
    found: list[Path] = []
    for folder in folders:
        if folder.is_dir():
            found.extend(sorted(folder.glob("cloud-storage-namespace-1.json.*.bak")))
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)


def _client_backup_candidates(outdir: Path) -> list[Path]:
    folder = outdir / "backups"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("client-collections.*.bak.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def restore_latest(cloud: str | Path | None, outdir: str | Path = "out") -> dict:
    """Undo the last write from the newest backup this tool created."""
    out = Path(outdir)
    client_backups = _client_backup_candidates(out)
    file_backups = _backup_candidates(Path(cloud), out) if cloud else []

    if cefbridge.debugger_available() and client_backups and (
        not file_backups or client_backups[0].stat().st_mtime >= file_backups[0].stat().st_mtime
    ):
        latest_client = client_backups[0]
        try:
            snapshot = json.loads(latest_client.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServiceError(f"客户端备份文件读不出来：{exc}") from exc
        if not isinstance(snapshot, list):
            raise ServiceError("客户端备份格式不正确，无法还原。")
        try:
            current_managed = cefbridge.list_managed()
        except (cefbridge.CEFError, OSError) as exc:
            raise ServiceError(f"读取 Steam 收藏集失败：{exc}") from exc

        snapshot_items = [
            item for item in snapshot
            if isinstance(item, dict) and str(item.get("name", "")).startswith(cefbridge.PREFIX)
        ]
        snapshot_ids = {str(item["id"]) for item in snapshot_items if item.get("id")}
        to_delete = [
            str(item["id"]) for item in current_managed
            if item.get("id") and str(item["id"]) not in snapshot_ids
        ]
        try:
            if snapshot_items:
                res = cefbridge.save_many(snapshot_items)
                failed = [row for row in res if not row.get("ok")]
                if failed:
                    raise ServiceError("Steam 客户端未能还原：" + "；".join(
                        f"{row.get('name', '')}：{row.get('error', '未知错误')}" for row in failed))
            if to_delete:
                del_res = cefbridge.delete_many(to_delete)
                failed_del = [row for row in del_res if not row.get("ok")]
                if failed_del:
                    raise ServiceError("Steam 客户端未能删除新增分组：" + "；".join(
                        f"{row.get('id', '')}：{row.get('error', '未知错误')}" for row in failed_del))
        except (cefbridge.CEFError, OSError) as exc:
            raise ServiceError(f"Steam 客户端还原失败：{exc}") from exc

        baseline_path = out / BASELINE_NAME
        if snapshot_items:
            baseline_path.write_text(json.dumps(
                {item["name"][len(cefbridge.PREFIX):]: item.get("added") or [] for item in snapshot_items},
                ensure_ascii=False, indent=2), encoding="utf-8")
        elif baseline_path.is_file():
            baseline_path.unlink()
        return {"ok": True, "via": "client", "backup": str(latest_client),
                "restored_to": "Steam 客户端", "index_restored": False}

    if not cloud:
        raise ServiceError("找不到 Steam 收藏集文件，无法还原。")
    target = Path(cloud)
    if not file_backups:
        raise ServiceError("没有找到备份文件，无法还原。")
    if steam_is_running():
        raise ServiceError("Steam 客户端还在运行。请先完全退出 Steam，再点还原。")
    latest = file_backups[0]
    shutil.copy2(latest, target)
    # Also put the cloud revision counter back to the pre-write value.
    marker = "cloud-storage-namespace-1.json."
    index_restored = False
    if latest.name.startswith(marker) and latest.name.endswith(".bak"):
        stamp = latest.name[len(marker):-len(".bak")]
        wanted = f"cloud-storage-namespaces.json.{stamp}.bak"
        for folder in (target.parent / "backups", out / "backups"):
            candidate = folder / wanted
            if candidate.is_file():
                shutil.copy2(candidate, target.parent / "cloud-storage-namespaces.json")
                index_restored = True
                break
    return {"ok": True, "via": "file", "backup": str(latest), "restored_to": str(target),
            "index_restored": index_restored}


def open_path(target: str, outdir: str | Path = "out") -> str:
    """Open a folder/file/URL with the OS shell. Returns a readable label."""
    if target == "license":
        webbrowser.open(LICENSE_PAGE)
        return LICENSE_PAGE
    out = Path(outdir)
    choices = {
        "report": out / "report.html",
        "out": out,
        "downloads": next((p for p in download_dirs() if p.name.lower() in
                           ("downloads", "下载")), download_dirs()[0] if download_dirs() else out),
    }
    if target not in choices:
        raise ServiceError("未知的位置：" + target)
    path = choices[target]
    if target == "report" and not path.is_file():
        raise ServiceError("还没有报告，请先点「一键统计」。")
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except (OSError, AttributeError) as exc:
        raise ServiceError(f"打不开：{exc}") from exc
    return str(path)


def desktop_shortcut() -> str:
    """Create a desktop icon so the user never has to open the project folder."""
    if os.name != "nt":
        raise ServiceError("创建桌面快捷方式目前只支持 Windows；其它系统请直接运行 python -m steam_source_collections.gui")
    root = Path(__file__).resolve().parent.parent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.is_file() else Path(sys.executable)
    quote = lambda value: str(value).replace("'", "''")
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=New-Object -ComObject WScript.Shell;"
        "$l=$s.CreateShortcut((Join-Path $d 'Steam 收藏集分组.lnk'));"
        f"$l.TargetPath='{quote(target)}';"
        "$l.Arguments='-m steam_source_collections.gui';"
        f"$l.WorkingDirectory='{quote(root)}';"
        f"$l.IconLocation='{quote(target)},0';"
        "$l.Save();"
        "Write-Output (Join-Path $d 'Steam 收藏集分组.lnk')"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=30, **hidden_console_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceError(f"创建快捷方式失败：{exc}") from exc
    if done.returncode != 0:
        raise ServiceError("创建快捷方式失败：" +
                           (done.stderr or b"").decode("utf-8", "replace")[:200])
    return (done.stdout or b"").decode("utf-8", "replace").strip() or "桌面"
