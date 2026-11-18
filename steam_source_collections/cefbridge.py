"""Drive the *running* Steam client through its local CEF debugger (CDP).

Steam has no public API for library collections. Editing the cloud-storage
JSON files on disk is not enough: the client only uploads changes that it made
itself, so other devices never see collections written by an external program.
When CEF remote debugging is enabled, the client's SharedJSContext exposes the
internal collection store, and calling its save method makes Steam perform the
upload exactly as if the user had edited the collection by hand.

This module is localhost-only (127.0.0.1:8080), uses the Python standard
library only, and never enables debugging by itself. It is read-only unless a
caller explicitly evaluates a mutating expression.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import urllib.request
from urllib.parse import urlparse

CDP_HOST = "127.0.0.1"
CDP_PORT = 8080


class CEFError(RuntimeError):
    pass


def _http_json(path: str, timeout: float = 3.0):
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def debugger_available(timeout: float = 2.0) -> bool:
    """True when the running Steam client exposes its local CEF debugger."""
    try:
        info = _http_json("/json/version", timeout)
        # Port 8080 can belong to other software; require Steam's own UA.
        return "Valve Steam Client" in str(info.get("User-Agent", ""))
    except (OSError, ValueError, TimeoutError):
        return False


def shared_context(timeout: float = 3.0) -> str:
    """Find the SharedJSContext target (the JS context shared by Steam pages)."""
    try:
        targets = _http_json("/json/list", timeout)
    except (OSError, ValueError, TimeoutError) as exc:
        raise CEFError(f"Steam 调试接口不可用：{exc}") from exc
    for target in targets:
        if "SharedJSContext" in str(target.get("title", "")) and target.get(
            "webSocketDebuggerUrl"
        ):
            return target["webSocketDebuggerUrl"]
    raise CEFError("Steam 客户端里没有找到 SharedJSContext（客户端版本可能变了）")


class _WebSocket:
    """Minimal RFC 6455 client -- enough for one CDP request per connection."""

    def __init__(self, url: str, timeout: float = 12.0):
        parsed = urlparse(url)
        self.sock = socket.create_connection(
            (parsed.hostname or CDP_HOST, parsed.port or CDP_PORT), timeout=timeout
        )
        self.sock.settimeout(timeout)
        self._left = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CEFError("Steam 调试接口握手失败")
            header += chunk
        first, _, extra = header.partition(b"\r\n\r\n")
        if b"101" not in first.split(b"\r\n", 1)[0]:
            raise CEFError("Steam 拒绝了调试连接：" + first[:160].decode("ascii", "replace"))
        self._left = extra

    def _read(self, count: int) -> bytes:
        data = self._left[:count]
        self._left = self._left[count:]
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise CEFError("调试连接中断")
            data += chunk
        return data

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        size = len(payload)
        head = bytes([0x80 | opcode])
        if size < 126:
            head += bytes([0x80 | size])
        elif size < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", size)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def send(self, message: dict) -> None:
        self._frame(0x1, json.dumps(message, ensure_ascii=False).encode("utf-8"))

    def receive(self) -> str:
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(size)
            if mask:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if opcode == 0x9:  # ping -> pong
                self._frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise CEFError("Steam 关闭了调试连接")
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def evaluate(expression: str, timeout: float = 15.0, await_promise: bool = True):
    """Run JS inside Steam's SharedJSContext and return its value."""
    ws = _WebSocket(shared_context(), timeout)
    try:
        ws.send({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        })
        while True:
            message = json.loads(ws.receive())
            if message.get("id") != 1:
                continue  # CDP event notifications
            if "error" in message:
                raise CEFError(str(message["error"]))
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                detail = result["exceptionDetails"]
                raise CEFError(str(detail.get("exception", {}).get("description") or detail))
            return result.get("result", {}).get("value")
    finally:
        ws.close()


def probe() -> dict:
    """Read-only look at the collection-related globals in the Steam client."""
    return evaluate(r"""
(() => {
  const g = window;
  const cs = g.collectionStore;
  const sc = g.SteamClient || {};
  const names = Object.keys(g).filter(k => /collection|collectionstore/i.test(k));
  const own = cs ? Object.keys(cs) : [];
  const proto = cs ? Object.getOwnPropertyNames(Object.getPrototypeOf(cs) || {}) : [];
  return {
    globals: names.slice(0, 40),
    hasCollectionStore: !!cs,
    ownKeys: own.slice(0, 60),
    protoMethods: proto.slice(0, 80),
    steamClientKeys: Object.keys(sc).filter(k => /collect|cloud|storage/i.test(k)),
    hasSteamClient: !!g.SteamClient,
    href: location.href.slice(0, 120)
  };
})()
""")


PREFIX = "✥ "
LEGACY_PREFIX = "[来源] "
MANAGED_NUMS = tuple("①②③④⑤⑥⑦⑧⑨")
_LEGACY_NUM_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨][bc]?\s*")


def is_managed_name(name: str, prefix: str = PREFIX) -> bool:
    """True when a collection name belongs to this tool (current or legacy)."""
    text = str(name or "")
    return (
        (bool(prefix) and text.startswith(prefix))
        or text.startswith(PREFIX)
        or text.startswith(LEGACY_PREFIX)
        or text.startswith(MANAGED_NUMS)
    )


def strip_managed_prefix(name: str) -> str:
    """Remove ``✥ ``, legacy ``[来源] `` prefix, and leading circled numbers."""
    text = str(name or "")
    for head in (PREFIX, LEGACY_PREFIX):
        if text.startswith(head):
            text = text[len(head):]
    return _LEGACY_NUM_RE.sub("", text)


def list_managed(prefix: str = PREFIX) -> list[dict]:
    """Read the tool's collections straight from the running client."""
    literal = json.dumps(prefix, ensure_ascii=False)
    result = evaluate("""
(() => {
  const cs = window.collectionStore;
  const map = cs.m_cloudStorageMap;
  const prefix = %s;
  return cs.userCollections
    .filter(c => {
      const n = String(c.m_strName || '');
      return (prefix && n.startsWith(prefix)) || n.startsWith("✥ ") || n.startsWith("[来源] ") || /^[①②③④⑤⑥⑦⑧⑨]/.test(n);
    })
    .map(c => {
      let obj = {};
      try { obj = map.GetObject(c.m_strId) || {}; } catch (e) {}
      return {
        id: c.m_strId,
        name: c.m_strName,
        added: (obj.added || []).map(Number),
        removed: (obj.removed || []).map(Number)
      };
    });
})()
""" % literal)
    # Belt and braces: never act on anything that is not one of our groups,
    # even if a client update changes the JS filter above.
    return [item for item in (result or [])
            if is_managed_name(item.get("name", ""), prefix)]


def save_many(items: list[dict], timeout: float = 40.0) -> list[dict]:
    """Ask the client itself to save collections -- this triggers Steam Cloud.

    Each item is ``{id, name, added, removed}``. Steam writes its own version
    numbers and uploads the change, so other devices receive it without any
    restart. Existing collections keep their id; never edit other collections.
    """
    payload = json.dumps(
        [{"id": str(item["id"]), "name": str(item["name"]),
          "added": [int(a) for a in item.get("added") or []],
          "removed": [int(a) for a in item.get("removed") or []]}
         for item in items],
        ensure_ascii=False,
    )
    expression = """
(async function() {
  const items = %s;
  const cs = window.collectionStore;
  const out = [];
  for (const item of items) {
    try {
      await cs.m_cloudStorageMap.StoreObject(item.id, item);
      const col = cs.GetCollection(item.id);
      if (!col) throw new Error('GetCollection returned null');
      await cs.SaveCollection(col);
      out.push({id: item.id, name: item.name, ok: true,
                count: item.added.length});
    } catch (e) {
      out.push({id: item.id, name: item.name, ok: false,
                error: String(e && e.message || e)});
    }
  }
  return out;
})()
""" % payload
    return evaluate(expression, timeout=timeout) or []


def delete_many(ids: list[str], prefix: str = PREFIX, timeout: float = 20.0) -> list[dict]:
    """Delete collections previously created by this tool inside the client."""
    clean_ids = [str(cid) for cid in ids if cid]
    if not clean_ids:
        return []
    expression = """
(async function() {
  const ids = %s;
  const prefix = %s;
  const cs = window.collectionStore;
  const out = [];
  for (const id of ids) {
    try {
      const col = cs.GetCollection(id);
      if (!col) { out.push({id, ok: true, skipped: true}); continue; }
      const n = String(col.m_strName || '');
      if (!((prefix && n.startsWith(prefix)) || n.startsWith("✥ ") || n.startsWith("[来源] ") || /^[①②③④⑤⑥⑦⑧⑨]/.test(n))) {
        out.push({id, ok: false, error: 'refused non-managed collection'});
        continue;
      }
      if (typeof cs.DeleteCollection === 'function') {
        await cs.DeleteCollection(col);
      } else if (typeof col.Delete === 'function') {
        await col.Delete();
      } else if (cs.m_cloudStorageMap && typeof cs.m_cloudStorageMap.RemoveObject === 'function') {
        await cs.m_cloudStorageMap.RemoveObject(id);
      } else {
        await cs.m_cloudStorageMap.DeleteObject(id);
      }
      out.push({id, ok: true});
    } catch (e) {
      out.push({id, ok: false, error: String(e && e.message || e)});
    }
  }
  return out;
})()
""" % (
        json.dumps(clean_ids, ensure_ascii=False),
        json.dumps(prefix, ensure_ascii=False),
    )
    return evaluate(expression, timeout=timeout) or []


def touch_managed(prefix: str = PREFIX) -> list[dict]:
    """Re-save existing collections unchanged, purely to trigger a cloud upload.

    Nothing is added or removed, so a manual edit the user just made (e.g.
    moving a game out of a group) is preserved.
    """
    return save_many(list_managed(prefix))
