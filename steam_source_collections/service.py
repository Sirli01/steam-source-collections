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
    notes: list[str] = []
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
            infer_order: bool = True, threshold: float = 0.86) -> Analysis:
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
    plan = build_plan(bundle.records, bundle.games, bundle.matches, set(), set())
    out = Path(outdir)
    outputs = write_outputs(
        plan, out, path.name, f"一键导出包 {path.name}", notes=build_notes(bundle.meta),
    )
    return Analysis(
        plan=plan, meta=bundle.meta, notes=build_notes(bundle.meta),
        outputs=outputs, bundle_path=path, local=local,
        _games=bundle.games,
    )


def steam_status() -> bool | None:
    """One-shot Steam process check, called only when the user is about to write."""
    return steam_is_running()


BASELINE_NAME = "client_baseline.json"


def merge_plan_with_client(plan: Plan, existing: list[dict],
                           baseline: dict | None = None) -> list[dict]:
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
    items: list[dict] = []
    alphabet = string.ascii_letters + string.digits
    for group in plan.groups:
        if group.get("kind") != "source" or not group.get("appids"):
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


def client_write_plan(analysis: Analysis, outdir: str | Path) -> dict:
    """Write through the *running* Steam client, which uploads to Steam Cloud."""
    try:
        existing = cefbridge.list_managed()
    except (cefbridge.CEFError, OSError) as exc:
        raise ServiceError(f"读取 Steam 收藏集失败：{exc}") from exc
    path = Path(outdir) / BASELINE_NAME
    try:
        baseline = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        baseline = {}
    items = merge_plan_with_client(analysis.plan, existing, baseline)
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
        "backups": [],
    }


def preflight(analysis: Analysis) -> dict:
    """Read-only capability check; never probes Steam's process."""
    groups = [
        cefbridge.PREFIX + group["name"] for group in analysis.plan.groups
        if group.get("kind") == "source" and group.get("appids")
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
        info = writeback_preview(Path(cloud), analysis.plan)
    except (WritebackError, OSError, json.JSONDecodeError) as exc:
        return {"writable": False, "reason": str(exc), "via": "file",
                "cef_available": False, "collections_to_write": groups}
    info["reason"] = ""
    info["cloud_path"] = str(cloud)
    info["via"] = "file"
    info["cef_available"] = False
    return info


def apply_plan(analysis: Analysis, outdir: str | Path = "out",
               confirm: bool = False) -> dict:
    """Write to Steam: prefer the running client, fall back to the file store."""
    if not confirm:
        raise ServiceError("需要用户在界面上确认后才会写入。")
    pre = preflight(analysis)
    if pre.get("via") == "client":
        return client_write_plan(analysis, outdir)
    if not pre.get("writable"):
        raise ServiceError("收藏集文件不可安全写入：" + (pre.get("reason") or "原因未知"))
    if steam_status():
        raise ServiceError(
            "Steam 客户端还在运行，而且没有开启本机调试接口——你还没有关闭 Steam。"
            "请改用文件方式写入：在托盘图标右键 → 退出，再点一次写入。"
        )
    try:
        backups = writeback_apply(Path(pre["cloud_path"]), analysis.plan, Path(outdir))
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


def restore_latest(cloud: str | Path, outdir: str | Path = "out") -> dict:
    """Undo the last write from the newest backup this tool created."""
    target = Path(cloud)
    backups = _backup_candidates(target, Path(outdir))
    if not backups:
        raise ServiceError("没有找到备份文件，无法还原。")
    if steam_is_running():
        raise ServiceError("Steam 客户端还在运行。请先完全退出 Steam，再点还原。")
    latest = backups[0]
    shutil.copy2(latest, target)
    # Also put the cloud revision counter back to the pre-write value.
    marker = "cloud-storage-namespace-1.json."
    index_restored = False
    if latest.name.startswith(marker) and latest.name.endswith(".bak"):
        stamp = latest.name[len(marker):-len(".bak")]
        wanted = f"cloud-storage-namespaces.json.{stamp}.bak"
        for folder in (target.parent / "backups", Path(outdir) / "backups"):
            candidate = folder / wanted
            if candidate.is_file():
                shutil.copy2(candidate, target.parent / "cloud-storage-namespaces.json")
                index_restored = True
                break
    return {"ok": True, "backup": str(latest), "restored_to": str(target),
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
