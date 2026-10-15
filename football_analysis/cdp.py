"""极简 Chrome DevTools Protocol 客户端（纯 stdlib，无 playwright）。

tools/scrape_odds_pages.py 只用到 CDP 的一小片：建标签页、导航、执行 JS 取值、
读标题、置前、关页。playwright 的 node driver 有 100+MB，打不进 exe，这里用
stdlib socket 直接说 websocket，让 exe 里的「刷新盘口分歧」也能工作。

只处理小报文（每条 CDP 消息一个文本帧）；页面正文不整块回传，限流检测改成
在页面里算布尔值再取回，规避大分片。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import struct
import time
import urllib.request
from typing import Any


class CdpError(Exception):
    """CDP 连接/协议错误，对应 playwright 路径里的 PWError。"""


def _is_fn_expr(expr: str) -> bool:
    """`() => ...` / `function(){...}` 这类函数表达式，调用后才求值。"""
    stripped = expr.lstrip()
    if stripped.startswith("function"):
        return True
    return bool(re.match(r"\((?:[^()]*)\)\s*=>", stripped, re.S))


class _WS:
    """最小 websocket 文本收发（客户端掩码、服务端不掩码、自动回 pong）。"""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    @classmethod
    def connect(cls, url: str, timeout: float = 10.0) -> _WS:
        m = re.match(r"ws://([^/]+)(/.*)?", url)
        if not m:
            raise CdpError(f"非法的 ws 地址：{url}")
        host_port = m.group(1)
        path = m.group(2) or "/"
        host, _, port = host_port.partition(":")
        port = port or "80"
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise CdpError("websocket 握手被断开")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            sock.close()
            raise CdpError(f"websocket 握手失败：{resp.split(b'\r\n')[0][:80]!r}")
        return cls(sock)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise CdpError("连接已断开")
            buf += chunk
        return buf

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        n = len(payload)
        header = bytearray([0x81])
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        """读到一条完整文本消息；中间的 ping 自动回 pong，close 抛错。"""
        parts: list[str] = []
        while True:
            head = self._recv_exact(2)
            opcode = head[0] & 0x0F
            masked = head[1] & 0x80
            n = head[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(n) if n else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping -> 回 pong（客户端必须掩码）
                pm = os.urandom(4)
                masked_payload = bytes(b ^ pm[i % 4] for i, b in enumerate(payload))
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + pm + masked_payload)
                continue
            if opcode == 0x8:
                raise CdpError("websocket 对端关闭")
            if opcode in (0x1, 0x0):
                parts.append(payload.decode("utf-8", "replace"))
                if head[0] & 0x80:  # fin
                    return "".join(parts)
            # 其它 opcode 忽略

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _new_tab(endpoint: str, timeout: float = 10.0) -> dict:
    """在已有 Chrome 上开一个新标签页。新版 Chrome 要求 PUT。"""
    url = f"{endpoint.rstrip('/')}/json/new?about:blank"
    last_err: Exception | None = None
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise CdpError(f"无法开新标签页：{last_err}")


def close_tab(endpoint: str, target_id: str, timeout: float = 5.0) -> None:
    try:
        with urllib.request.urlopen(
            f"{endpoint.rstrip('/')}/json/close/{target_id}", timeout=timeout
        ) as resp:
            resp.read()
    except Exception:  # noqa: BLE001
        pass


def _browser_command(endpoint: str, method: str, params: dict, timeout: float = 5.0) -> dict:
    """在浏览器级 CDP 通道上跑一条命令（Browser.* 不属于某个页面目标）。"""
    with urllib.request.urlopen(f"{endpoint.rstrip('/')}/json/version", timeout=timeout) as resp:
        ws_url = json.loads(resp.read())["webSocketDebuggerUrl"]
    ws = _WS.connect(ws_url, timeout=timeout)
    try:
        ws.send_text(json.dumps({"id": 1, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            ws.sock.settimeout(max(deadline - time.time(), 0.001))
            try:
                msg = json.loads(ws.recv_text())
            except TimeoutError:
                break
            if msg.get("id") == 1:
                if "error" in msg:
                    raise CdpError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})
        raise CdpError(f"{method} 超时")
    finally:
        ws.close()


def reveal_window(
    endpoint: str,
    target_id: str,
    *,
    left: int = 80,
    top: int = 80,
    width: int = 1280,
    height: int = 900,
) -> bool:
    """把目标标签页所在窗口从屏幕外挪回可见区域；失败返回 False。"""
    try:
        window = _browser_command(endpoint, "Browser.getWindowForTarget", {"targetId": target_id})
        _browser_command(
            endpoint,
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "windowState": "normal",
                },
            },
        )
        return True
    except (CdpError, OSError, KeyError, ValueError):
        return False


class CdpPage:
    """playwright Page 的极简替代，只覆盖 scraper 用到的接口。"""

    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        info = _new_tab(endpoint)
        self.target_id = info.get("id", "")
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            close_tab(endpoint, self.target_id)
            raise CdpError("新标签页没有返回 ws 地址")
        self.endpoint = endpoint
        self.ws = _WS.connect(ws_url)
        self._id = 0
        self._cmd("Page.enable")

    def _cmd(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock_timeout(deadline - time.time())
            try:
                msg = json.loads(self.ws.recv_text())
            except TimeoutError:
                break
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})
            # 事件：这里不需要，丢弃
        raise CdpError(f"{method} 超时")

    def sock_timeout(self, seconds: float) -> None:
        self.ws.sock.settimeout(max(seconds, 0.001))

    def evaluate(self, expr: str, timeout: float = 30.0) -> Any:
        if _is_fn_expr(expr):
            expr = f"({expr})()"
        res = self._cmd(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            detail = res["exceptionDetails"].get("text", "js 异常")
            raise CdpError(f"执行 JS 失败：{detail}")
        return res.get("result", {}).get("value")

    def goto(
        self,
        url: str,
        timeout_ms: float = 25_000,
        timeout: float | None = None,
        wait_until: str | None = None,
    ) -> None:
        # timeout 沿用 playwright 的毫秒命名；timeout_ms 是本模块原生名。
        ms = timeout if timeout is not None else timeout_ms
        self._cmd("Page.navigate", {"url": url}, timeout=ms / 1000 + 5)

    def title(self) -> str:
        value = self.evaluate("() => document.title")
        return value if isinstance(value, str) else ""

    def bring_to_front(self) -> None:
        try:
            self._cmd("Page.bringToFront", timeout=10.0)
        except CdpError:
            pass

    def reveal_window(self) -> bool:
        """让窗口回到屏幕上（默认在屏幕外，只有验证页需要人工勾选时才露脸）。"""
        return reveal_window(self.endpoint, self.target_id)

    def close(self) -> None:
        self.ws.close()
        close_tab(self.endpoint, self.target_id)
