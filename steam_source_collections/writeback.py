"""EXPERIMENTAL: merge the plan into Steam's local collection storage file.

Steam stores collections in a local JSON file (historically
``userdata/<id3>/config/cloudstorage/cloud-storage-namespace-1.json``; newer
clients may use LevelDB), with the cloud revision counter in the sibling
``cloud-storage-namespaces.json``. Both must move together, otherwise the
client never uploads the new collections to the account and other devices
(Steam Deck) keep showing the old library. The exact schema is undocumented
and changes between client versions. This module therefore:

* refuses to touch the file unless its structure can be probed,
* only creates/overwrites collections whose name starts with ``[来源] ``,
* leaves every other key (including 收藏夹 / 已隐藏) untouched,
* makes two backups before an atomic replace,
* never writes without ``--apply --yes``.

If probing fails, use report.md and create the collections manually.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .cefbridge import LEGACY_PREFIX, PREFIX, is_managed_name
from .local import read_collections
from .plan import Plan

NAMESPACE_INDEX_NAME = "cloud-storage-namespaces.json"


class WritebackError(RuntimeError):
    pass


def _atomic_replace_json(path: Path, doc) -> None:
    """Replace a JSON file atomically, with the temp file in the same folder."""
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".ssc-", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def _bump_namespace_index(index_path: Path, version: int, stamp: str,
                          backup_roots: list[Path]) -> Path | None:
    """Raise the cloud revision counter for namespace 1.

    Steam keeps the last synced revision per namespace in
    ``cloud-storage-namespaces.json`` (``[[3,"0"],[1,"4440"],[4,"0"]]``).
    Collections added by editing ``namespace-1.json`` alone have versions
    above that counter, so the client never treats them as new and never
    uploads them to the account -- other devices keep showing the old library.
    The counter must be at least the highest version we just wrote.
    """
    if not index_path.is_file():
        return None
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, list):
        return None

    backups: list[Path] = []
    for folder in backup_roots:
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / f"{index_path.name}.{stamp}.bak"
        shutil.copy2(index_path, backup)
        backups.append(backup)

    found = False
    for item in doc:
        if not (isinstance(item, list) and len(item) >= 2 and str(item[0]) == "1"):
            continue
        try:
            current = int(str(item[1]))
        except (TypeError, ValueError):
            current = 0
        if current < version:
            item[1] = str(version)
        found = True
        break
    if not found:
        doc.append([1, str(version)])
    _atomic_replace_json(index_path, doc)
    return backups[0] if backups else None


def _looks_like_collection_map(value) -> bool:
    """A collection container holds entries with a name and a list field."""
    if not isinstance(value, dict):
        return False
    return any(
        isinstance(entry, dict)
        and any(key.casefold() == "name" for key in entry)
        and any(isinstance(field, list) for field in entry.values())
        for entry in value.values()
    )


def _find_container(node, path: str = "") -> tuple[dict, str] | None:
    if isinstance(node, dict):
        # Prefer the innermost map that actually looks like a collection map;
        # "collections" is often just a wrapper around "user-collections".
        for key in ("user-collections", "collections"):
            value = node.get(key)
            if isinstance(value, dict) and _looks_like_collection_map(value):
                return value, f"{path}/{key}"
        for key, value in node.items():
            found = _find_container(value, f"{path}/{key}")
            if found:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = _find_container(value, f"{path}[{index}]")
            if found:
                return found
    return None


def _probe_template(container: dict) -> tuple[str, str, bool]:
    """Return (appid-list field, name field, whether appids are integers)."""
    for entry in container.values():
        if not isinstance(entry, dict):
            continue
        list_keys = [key for key, value in entry.items() if isinstance(value, list)]
        name_keys = [key for key, value in entry.items() if key.casefold() == "name" and isinstance(value, str)]
        if list_keys and name_keys:
            sample = entry[list_keys[0]]
            return list_keys[0], name_keys[0], any(isinstance(item, int) for item in sample)
    raise WritebackError("无法从现有收藏集中探测 appid 列表字段，拒绝写入")


def hidden_console_kwargs() -> dict:
    """Subprocess options that never show a console window on Windows.

    The GUI runs under ``pythonw.exe`` (no console of its own).  Windows then
    creates a fresh console for every console-mode child, which shows up as a
    black window flashing on each status poll.  Returns ``{}`` off Windows.
    """
    flag = getattr(subprocess, "CREATE_NO_WINDOW", None)
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if flag is None or startupinfo_cls is None:
        return {}
    startupinfo = startupinfo_cls()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {"creationflags": flag, "startupinfo": startupinfo}


def _windows_process_exists(exe: str) -> bool | None:
    """Look for ``exe`` in the process list via Toolhelp32 (no child process)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        k32.Process32NextW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)

        snapshot = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        invalid = ctypes.c_void_p(-1).value
        if snapshot is None or snapshot in (invalid, 0):
            return None
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        target = exe.lower()
        found = False
        try:
            if k32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == target:
                        found = True
                        break
                    if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            k32.CloseHandle(snapshot)
        return found
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _steam_is_running() -> bool | None:
    if os.name == "nt":
        found = _windows_process_exists("steam.exe")
        if found is not None:
            return found
        # Last-resort fallback: keep the console hidden if Toolhelp is blocked.
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                capture_output=True, timeout=10, **hidden_console_kwargs(),
            ).stdout or b""
            return b"steam.exe" in out.lower()
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        out = subprocess.run(["pgrep", "-x", "steam"], capture_output=True, timeout=10)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def steam_is_running() -> bool | None:
    """Public wrapper (keeps tests able to patch ``_steam_is_running``)."""
    return _steam_is_running()


def target_groups(
    plan: Plan,
    include_cross: bool = False,
    selected_groups: list[str] | set[str] | None = None,
) -> list[dict]:
    """Return non-empty collections to write based on checkbox selection."""
    if selected_groups is not None:
        wanted = {str(x) for x in selected_groups}
        return [
            g for g in plan.groups
            if (g.get("id") in wanted or g.get("name") in wanted) and g.get("appids")
        ]
    allowed = ("source", "cross") if include_cross else ("source",)
    return [g for g in plan.groups if g.get("kind") in allowed and g.get("appids")]


def preview(
    path: Path,
    plan: Plan,
    include_cross: bool = False,
    selected_groups: list[str] | set[str] | None = None,
) -> dict:
    schema, entries = read_collections(path)
    common = {
        "collections_to_write": [
            PREFIX + group["name"]
            for group in target_groups(plan, include_cross, selected_groups)
        ],
    }
    if schema == "kv-pairs":
        # Newer Steam clients store collections as a [key, meta] array. We now
        # understand the shape well enough to ADD keys without touching any
        # existing entry: each collection is
        #   ["user-collections.uc-XXX",
        #    {"key": ..., "timestamp": ..., "version": "...", "value": "<json>"}]
        # where value is {"id","name","added":[...],"removed":[]}.
        # Still experimental: Steam cloud sync may overwrite the local file on
        # next launch, so we require Steam to be closed, keep two backups and
        # always emit a one-click restore script.
        return {
            **common,
            "format": "kv-pairs（新版键值格式 · 只追加本工具分组）",
            "writable": True,
            "strategy": "只创建或更新「✥ 」符号前缀分组（并自动清理旧版序号或 [来源] 分组），不修改任何其它收藏集；写入前双备份、原子替换",
            "requires_steam_closed": True,
            "existing_collections": len(entries),
            "existing_managed_collections": [e["name"] for e in entries if is_managed_name(e["name"])],
            "caveat": "写入会同时抬高 Steam 的云同步版本计数；重新启动 Steam 客户端后，"
                      "新分组才会同步到云端。"
                      "若出现云同步冲突提示，选择保留/上传本机版本。"
                      "若重启后没出现，在图形界面点「↩️ 一键还原」即可恢复（备用：out/restore-collections.bat）；"
                      "也可以完全不写入，直接用 report.html 里的「复制游戏名清单」在客户端手动建组。",
        }
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    found = _find_container(data)
    if not found:
        raise WritebackError("找不到收藏集容器（user-collections / collections），拒绝写入")
    container, location = found
    list_key, name_key, use_int = _probe_template(container)
    existing = [
        value.get(name_key, "")
        for value in container.values()
        if isinstance(value, dict) and is_managed_name(str(value.get(name_key, "")))
    ]
    return {
        **common,
        "format": "dict（旧版字典格式）",
        "writable": True,
        "container": location,
        "list_key": list_key,
        "name_key": name_key,
        "appid_type": "int" if use_int else "str",
        "existing_collections": len(container),
        "existing_managed_collections": existing,
    }


def _write_kv_pairs(
    path: Path,
    plan: Plan,
    outdir: Path,
    include_cross: bool = False,
    selected_groups: list[str] | set[str] | None = None,
) -> list[Path]:
    """Append-only write for the modern ``[key, meta]`` collection store.

    Existing entries are never edited. Only ``[来源] `` keys previously
    created by this tool are removed (so re-running is idempotent), then new
    keys are appended with a version above the current maximum.
    """
    if _steam_is_running():
        raise WritebackError(
            "Steam 客户端还在运行——你还没有关闭它。请在托盘图标右键 → 退出，再点一次写入；"
            "否则客户端会把本地文件覆盖回去。"
        )
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise WritebackError("云存储不是 [key, meta] 数组，拒绝写入")
    for item in raw:
        if not (isinstance(item, list) and len(item) >= 2
                and isinstance(item[0], str) and isinstance(item[1], dict)):
            raise WritebackError("云存储里有无法识别的条目，拒绝写入")

    max_version = 0
    managed: set[str] = set()
    for key, meta in raw:
        try:
            max_version = max(max_version, int(meta.get("version") or 0))
        except (TypeError, ValueError):
            pass
        value = meta.get("value")
        if not isinstance(value, str):
            continue
        try:
            entry = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and is_managed_name(str(entry.get("name", ""))):
            managed.add(key)
    data = [item for item in raw if item[0] not in managed]

    chosen = target_groups(plan, include_cross, selected_groups)
    if not chosen:
        raise WritebackError("计划里没有可写入的分组，拒绝写入")
    alphabet = string.ascii_letters + string.digits
    now = int(datetime.now().timestamp())
    created: list[str] = []
    version = max_version
    for group in chosen:
        appids = sorted({int(a) for a in group["appids"] if str(a).isdigit()})
        if not appids:
            continue
        version += 1
        cid = "uc-" + "".join(secrets.choice(alphabet) for _ in range(12))
        key = "user-collections." + cid
        value = json.dumps(
            {"id": cid, "name": PREFIX + group["name"],
             "added": appids, "removed": []},
            ensure_ascii=False, separators=(",", ":"),
        )
        data.append([key, {
            "key": key,
            "timestamp": now,
            "value": value,
            "version": str(version),
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
        }])
        created.append(PREFIX + group["name"])
    if not created:
        raise WritebackError("所有可写入分组都是空的，拒绝写入")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_roots = [path.parent / "backups", outdir / "backups"]
    backups = [folder / f"{path.name}.{stamp}.bak" for folder in backup_roots]
    for backup in backups:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)

    _atomic_replace_json(path, data)
    index_backup = _bump_namespace_index(
        path.parent / NAMESPACE_INDEX_NAME, version, stamp, backup_roots
    )

    outdir.mkdir(parents=True, exist_ok=True)
    restore_lines = [
        "@echo off",
        "chcp 65001 >nul",
        "rem Restore the Steam collection files from the pre-write backups.",
        f'copy /Y "{backups[0]}" "{path}"',
    ]
    if index_backup:
        restore_lines.append(
            f'copy /Y "{index_backup}" "{path.parent / NAMESPACE_INDEX_NAME}"'
        )
    restore_lines += [
        "if errorlevel 1 (echo Restore failed. & pause & exit /b 1)",
        "echo Restored. Restart Steam to confirm.",
        "pause",
    ]
    restore = outdir / "restore-collections.bat"
    restore.write_text("\n".join(restore_lines) + "\n", encoding="utf-8")
    print(f"[写回] 已新增 {len(created)} 个收藏集：" + "、".join(created))
    print(f"[写回] 备份：{backups[0]} / {backups[1]}")
    if index_backup:
        print(f"[写回] 云同步版本计数已抬到 {version}：{path.parent / NAMESPACE_INDEX_NAME}")
    print("[写回] 重新启动 Steam 后，新分组才会同步到云端。")
    print(f"[写回] 若重启 Steam 后没出现，在图形界面点「一键还原」即可恢复；备用脚本：{restore}")
    return backups


def apply(
    path: Path,
    plan: Plan,
    outdir: Path,
    include_cross: bool = False,
    selected_groups: list[str] | set[str] | None = None,
) -> list[Path]:
    schema, _ = read_collections(path)
    if schema == "kv-pairs":
        return _write_kv_pairs(
            path, plan, outdir,
            include_cross=include_cross,
            selected_groups=selected_groups,
        )
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    found = _find_container(data)
    if not found:
        raise WritebackError("找不到收藏集容器，拒绝写入；请按 report.md 手动建组")
    container, _ = found
    list_key, name_key, use_int = _probe_template(container)

    # Remove only collections previously created by this tool.
    for key in [
        key for key, value in container.items()
        if isinstance(value, dict) and is_managed_name(str(value.get(name_key, "")))
    ]:
        del container[key]

    chosen = target_groups(plan, include_cross, selected_groups)
    if not chosen:
        raise WritebackError("所有可写入分组都是空的，拒绝写入")
    for group in chosen:
        appids = [appid if use_int else str(appid) for appid in group["appids"]]
        container[f"src_{group['id']}"] = {
            name_key: PREFIX + group["name"],
            list_key: appids,
        }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = [
        path.parent / "backups" / f"{path.name}.{stamp}.bak",
        outdir / "backups" / f"{path.name}.{stamp}.bak",
    ]
    for backup in backups:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)

    _atomic_replace_json(path, data)
    return backups
