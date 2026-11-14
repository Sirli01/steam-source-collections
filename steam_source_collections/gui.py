"""Desktop GUI for Steam 收藏集分组.

Run it with::

    python -m steam_source_collections.gui

It starts a tiny HTTP server bound to 127.0.0.1 on a random port, protected
by a one-shot token, and opens the browser.  Nothing listens on the network,
no data leaves the machine, and every API call checks both the token and the
Host header (DNS-rebinding guard).  The GUI is the product: the CLI is still
there for CI and power users, but nobody needs to see it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, cefbridge, service
from .service import ServiceError

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
PAGE = PKG / "gui_page.html"
IDLE_SECONDS = 30 * 60
MAX_BODY = 24 * 1024 * 1024
# A source checkout keeps the userscript at the repo root; an installed wheel
# only carries the copy inside the package.
SOURCE_TREE = (ROOT / "examples").is_dir()


def _userscript() -> Path:
    root_copy = ROOT / "steam-export.user.js"
    return root_copy if root_copy.is_file() else PKG / "bookmark_src.js"


class GuiState:
    def __init__(self, token: str, outdir: Path):
        self.token = token
        self.outdir = outdir
        self.local = None
        self.analysis: service.Analysis | None = None
        self.custom_bundle: Path | None = None
        self.last_request = time.monotonic()
        self.lock = threading.Lock()
        self.log_path = outdir / "gui.log"
        self.server: ThreadingHTTPServer | None = None

    def log(self, message: str) -> None:
        try:
            self.outdir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H:%M:%S")
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
        except OSError:
            pass

    def bundle_path(self) -> str | None:
        return str(self.custom_bundle) if self.custom_bundle else None


def _bookmarklet() -> str:
    source = _userscript().read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/", "", source)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    code = re.sub(r"\r?\n", " ", code)
    code = re.sub(r"\t", " ", code)
    code = re.sub(r"\s{2,}", " ", code).strip()
    return "javascript:" + code


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    return cleaned or "steam-licenses.json"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "SteamCollectionsGUI/1.0"

    @property
    def state(self) -> GuiState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        self.state.log("HTTP " + (fmt % args))

    # ---------- plumbing ----------

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _html(self, text: str, status: int = 200) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _authorized(self, query: dict) -> bool:
        self.state.last_request = time.monotonic()
        host = (self.headers.get("Host") or "").split(":", 1)[0].lower()
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin):
            return False
        supplied = self.headers.get("X-SSC-Token") or "".join(query.get("token") or [])
        return secrets.compare_digest(supplied, self.state.token)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ServiceError("文件太大（超过 24 MB）")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError("请求格式不对：" + str(exc)) from exc
        return data if isinstance(data, dict) else {}

    def _error(self, exc: Exception) -> None:
        self.state.log("ERROR " + str(exc))
        self._json({"ok": False, "error": str(exc)}, 400)

    # ---------- GET ----------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._html(
                "<meta charset='utf-8'><h2>请从桌面图标或项目里的启动器打开本工具</h2>"
                "<p>这个页面只能用启动时生成的临时地址访问，关闭后自动失效。</p>", 403)
            return
        try:
            if parsed.path in ("/", "/index.html"):
                page = PAGE.read_text(encoding="utf-8")
                self._html(page.replace("__TOKEN__", self.state.token)
                               .replace("__VERSION__", __version__))
            elif parsed.path == "/api/state":
                self._json({"ok": True, "state": self._state(False),
                            "has_plan": self.state.analysis is not None})
            elif parsed.path == "/api/scan":
                self._json({"ok": True, "state": self._state(True),
                            "has_plan": self.state.analysis is not None})
            elif parsed.path == "/api/bookmark-code":
                self._json({"ok": True, "code": _bookmarklet()})
            elif parsed.path == "/api/bookmark-file":
                body = _userscript().read_bytes()
                self._send(200, body, "text/javascript; charset=utf-8", {
                    "Content-Disposition": 'attachment; filename="steam-export.user.js"'})
            elif parsed.path == "/api/report-file":
                report = self.state.outdir / "report.html"
                if not report.is_file():
                    raise ServiceError("还没有报告，请先点「一键统计」。")
                self._html(report.read_text(encoding="utf-8"))
            elif parsed.path == "/api/ping":
                self._json({"ok": True})
            else:
                self._html("<meta charset='utf-8'><p>页面不存在</p>", 404)
        except (ServiceError, OSError, ValueError) as exc:
            self._html(f"<meta charset='utf-8'><pre>{exc}</pre>", 400)

    # ---------- POST ----------

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._json({"ok": False, "error": "未授权"}, 403)
            return
        with self.state.lock:
            try:
                body = self._body()
                result = self._dispatch(parsed.path, body)
                self._json({"ok": True, **result})
            except (ServiceError, OSError, ValueError, RuntimeError) as exc:
                self._error(exc)

    def _dispatch(self, path: str, body: dict) -> dict:
        state = self.state
        if path == "/api/report":
            state.log("一键统计开始")
            conflict = str(body.get("conflict") or "priority")
            if conflict not in ("priority", "latest"):
                conflict = "priority"
            include_cross = bool(body.get("include_cross", False))
            analysis = service.analyse(
                bundle_path=body.get("bundle") or state.bundle_path(),
                outdir=state.outdir,
                infer_order=bool(body.get("infer_order", True)),
                conflict=conflict,
                include_cross=include_cross,
            )
            state.analysis = analysis
            state.local = analysis.local
            state.log(f"统计完成：{analysis.plan.stats['games_total']} 款游戏")
            pre = service.preflight(analysis, include_cross=include_cross)
            return {
                "meta": {
                    "license_rows": analysis.meta["license_rows"],
                    "owned_apps": analysis.meta["owned_apps"],
                    "family_apps": analysis.meta["family_apps"],
                    "unnamed_apps": analysis.meta["unnamed_apps"],
                    "explicit_appid_rows": analysis.meta["explicit_appid_rows"],
                    "local_cache_packages": analysis.meta["local_cache_packages"],
                    "schema": analysis.meta["schema"],
                },
                "groups": analysis.groups(),
                "stats": analysis.plan.stats,
                "notes": analysis.notes,
                "outputs": [str(p) for p in analysis.outputs],
                "preflight": pre,
                "state": self._state(False),
            }
        if path == "/api/preflight":
            # The only time we look at Steam's process state: the user just
            # clicked the write button. No polling, no background work.
            if state.analysis is None:
                raise ServiceError("请先点「一键统计」，再写入 Steam。")
            include_cross = body.get("include_cross")
            if include_cross is not None:
                include_cross = bool(include_cross)
            return {"preflight": service.preflight(state.analysis, include_cross=include_cross),
                    "running": service.steam_status()}
        if path == "/api/apply":
            if state.analysis is None:
                raise ServiceError("请先点「一键统计」，再写入 Steam。")
            include_cross = body.get("include_cross")
            if include_cross is not None:
                include_cross = bool(include_cross)
            # The client channel writes through the running Steam client and
            # uploads itself; only the file fallback needs Steam closed.
            if service.preflight(state.analysis, include_cross=include_cross).get("via") != "client" \
                    and service.steam_status():
                raise ServiceError(
                    "Steam 客户端还在运行，而且没有开启本机调试接口——你还没有关闭 Steam。"
                    "请改用文件方式写入：在托盘图标右键 → 退出，再点一次写入。")
            state.log("写入 Steam 收藏集")
            result = service.apply_plan(state.analysis, state.outdir,
                                        confirm=bool(body.get("confirm")),
                                        include_cross=include_cross)
            state.log("写入完成：" + "、".join(result["collections"]))
            return {**result, "state": self._state(False)}
        if path == "/api/restore":
            cloud = service.discover().cloud_path
            if not cloud and not cefbridge.debugger_available():
                raise ServiceError("找不到 Steam 收藏集文件，无法还原。")
            state.log("还原收藏集")
            result = service.restore_latest(cloud, state.outdir)
            state.log("还原完成：" + result["backup"])
            return {**result, "state": self._state(False)}
        if path == "/api/open":
            label = service.open_path(str(body.get("target") or ""), state.outdir)
            return {"opened": label}
        if path == "/api/upload":
            text = str(body.get("text") or "")
            if not text.strip():
                raise ServiceError("文件内容是空的")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ServiceError("这不是有效的 JSON 导出包：" + str(exc)) from exc
            if not isinstance(data, dict) or not isinstance(data.get("licenses"), list):
                raise ServiceError("这不是书签导出的 steam-licenses-*.json")
            folder = state.outdir / "uploaded"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / _safe_filename(str(body.get("name") or "steam-licenses.json"))
            target.write_text(text, encoding="utf-8")
            state.custom_bundle = target
            state.log("收到手动拖入的导出包：" + target.name)
            return {"path": str(target), "name": target.name,
                    "state": self._state(True)}
        if path == "/api/shortcut":
            label = service.desktop_shortcut()
            state.log("创建桌面快捷方式：" + label)
            return {"path": label}
        if path == "/api/quit":
            state.log("用户退出")
            assert state.server is not None
            threading.Thread(target=state.server.shutdown, daemon=True).start()
            return {"quitting": True}
        raise ServiceError("未知操作：" + path)

    def _state(self, refresh: bool) -> dict:
        state = self.state
        if refresh or state.local is None:
            state.local = service.discover()
        return service.scan(state.local, state.bundle_path(), state.outdir)


def _idle_watchdog(state: GuiState) -> None:
    while True:
        time.sleep(30)
        if time.monotonic() - state.last_request > IDLE_SECONDS:
            state.log("空闲超时，自动退出")
            if state.server:
                state.server.shutdown()
            return


def main(argv: list[str] | None = None) -> int:
    default_out = (ROOT / "out") if SOURCE_TREE else (Path.cwd() / "out")
    outdir = Path(os.environ.get("SSC_OUTDIR") or default_out)
    outdir.mkdir(parents=True, exist_ok=True)
    # pythonw.exe has no console: make sure stray prints never crash the app.
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            try:
                setattr(sys, stream, (outdir / "gui-console.log").open("a", encoding="utf-8"))
            except OSError:
                pass
    token = secrets.token_urlsafe(24)
    state = GuiState(token, outdir)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]
    state.server = server
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={token}"
    (outdir / "gui.url").write_text(url + "\n", encoding="utf-8")
    state.log(f"启动 GUI v{__version__} on port {port}")
    threading.Thread(target=_idle_watchdog, args=(state,), daemon=True).start()
    if os.environ.get("SSC_NO_BROWSER") != "1":
        threading.Thread(target=lambda: (time.sleep(0.35), webbrowser.open(url)),
                         daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.log("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
