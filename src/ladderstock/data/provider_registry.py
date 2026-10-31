"""生态 peer 工具链：主工具 → 同 peer 备工具 → 本地缓存回放。

对应 DEC-006 闸门 2（唯一取数入口）与 DEC-007 §2.4（运行期请求治理）。

职责只有三件事：

1. **组装工具链**：`config/sources.yaml` 的 peer+tool 与 `core/boundary.py:DATASET_CATALOG`
   的归属对齐，主备必须同 peer；跨 peer 的链路直接判配置错误（不是「降级」）。
2. **治理调用**（DEC-007 §2.4 四条，全部可关掉用于测试，不可被业务代码绕过）：
   - in-flight 合并：同一 `(dataset, 参数)` 的并发请求只打一次，其余等同一次结果；
   - 短窗缓存：成功结果按 `cold_hot.intraday_cache_ttl_sec` 内存缓存，命中标 `freshness=cached`；
   - 失败冷却：工具失败后按 `cooldown_sec` 冷却，冷却期内直接跳备源；
   - 每 peer 并发上限 + 最小间隔 + `retries` 有界重试。
3. **降级与溯源**：备源成功 / 缓存回放都写 `source_detail` 轨迹与 `source_status`，
   同时落一条 health 事件，供 `ldst sources health` 与看板标注。

**这里没有「换个地方抓」的分支**：链路终点永远是本地缓存，缓存也没有就抛
`LadderStockError(EXIT_SOURCE)`（DEC-007 §2.5）。
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from ladderstock import __version__
from ladderstock.cli.paths import config_dir, data_dir
from ladderstock.core.boundary import BoundaryViolation, owner_of
from ladderstock.core.models import LadderEnvelope
from ladderstock.core.privacy import mask_url
from ladderstock.data import archive
from ladderstock.data.adapters import (
    from_data_envelope,
    from_tool_response,
    from_wire_envelope,
)
from ladderstock.errors import EXIT_CONFIG, EXIT_SOURCE, LadderStockError

# 每 peer 的并发上限与最小调用间隔：配置里没有这两项（schema 冻结），
# 作为数据层常量写死并记录在 docs/SOURCES.md。目的是不打爆 peer 限流。
DEFAULT_PEER_CONCURRENCY: dict[str, int] = {"tickhub": 4, "wire": 2, "senate": 2}
DEFAULT_PEER_MIN_INTERVAL_SEC: dict[str, float] = {"tickhub": 0.05, "wire": 0.2, "senate": 0.2}

DEFAULT_TTL_SEC = 60.0
MAX_HEALTH_EVENTS = 500
INFLIGHT_WAIT_SEC = 180.0


class SourceUnavailable(LadderStockError):
    """源不可用（超时 / 不可达 / 上游拒收），可降级到备源或缓存。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_SOURCE)


# 这些异常本身就是「源不可用」的具体形态，向上原样抛出以保留超时/拒连的区分
_TRANSPORT_ERRORS = (
    TimeoutError,
    ConnectionError,
    OSError,
    httpx.HTTPError,
    SourceUnavailable,
)


# --------------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------------- #

def load_sources_config(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml(Path(path) if path is not None else config_dir() / "sources.yaml")


def load_sync_config(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml(Path(path) if path is not None else config_dir() / "sync.yaml")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LadderStockError(f"配置文件不存在：{path}", EXIT_CONFIG)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LadderStockError(f"配置文件无法解析：{path}（{exc}）", EXIT_CONFIG) from exc
    if not isinstance(data, dict):
        raise LadderStockError(f"配置文件结构非法：{path}", EXIT_CONFIG)
    return data


# --------------------------------------------------------------------------- #
# 规格对象
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PeerEndpoint:
    key: str
    product: str
    mcp_name: str
    url: str
    timeout_sec: float


@dataclass(frozen=True)
class SourceSpec:
    id: str
    dataset: str
    via: str
    tool: str
    primary: bool
    fallback: tuple[str, ...] = ()
    timeout_sec: float = 10.0
    retries: int = 0
    cooldown_sec: float = 60.0
    weight: float = 1.0

    def trail_label(self) -> str:
        return f"{self.id}/{self.tool}"


def build_peers(sources_doc: dict[str, Any]) -> dict[str, PeerEndpoint]:
    peers: dict[str, PeerEndpoint] = {}
    for key, item in (sources_doc.get("peers") or {}).items():
        item = item or {}
        env_name = str(item.get("url_env") or "")
        url = os.environ.get(env_name, "") if env_name else ""
        peers[str(key)] = PeerEndpoint(
            key=str(key),
            product=str(item.get("product") or key),
            mcp_name=str(item.get("mcp_name") or key),
            url=url or str(item.get("default_url") or ""),
            timeout_sec=float(item.get("timeout_sec") or 10.0),
        )
    return peers


def build_specs(sources_doc: dict[str, Any]) -> dict[str, SourceSpec]:
    specs: dict[str, SourceSpec] = {}
    for item in sources_doc.get("sources") or []:
        sid = str(item.get("id"))
        specs[sid] = SourceSpec(
            id=sid,
            dataset=str(item.get("dataset")),
            via=str(item.get("via")),
            tool=str(item.get("tool")),
            primary=bool(item.get("primary")),
            fallback=tuple(str(x) for x in (item.get("fallback") or [])),
            timeout_sec=float(item.get("timeout_sec") or 10.0),
            retries=int(item.get("retries") or 0),
            cooldown_sec=float(item.get("cooldown_sec") or 0.0),
            weight=float(item.get("weight") or 1.0),
        )
    return specs


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #

class Transport(Protocol):
    """peer 工具调用接口；测试注入假 transport 即可完全离线。"""

    def call(
        self, peer: PeerEndpoint, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]: ...

    def probe(self, peer: PeerEndpoint, *, timeout: float) -> dict[str, Any]: ...


#: 握手时声明的协议版本；服务端可以协商成它支持的版本，以响应为准。
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SESSION_HEADER = "Mcp-Session-Id"
MCP_PROTOCOL_HEADER = "MCP-Protocol-Version"
MCP_ACCEPT = "application/json, text/event-stream"


@dataclass
class _McpSession:
    """一个 peer 的 MCP 会话：握手一次，后续请求复用同一 `Mcp-Session-Id`。"""

    session_id: str | None = None
    protocol_version: str = ""


class _SessionExpired(Exception):
    """服务端已不认这个会话（404）；重新握手后把这次请求重发一次。"""


class HttpMcpTransport:
    """MCP Streamable HTTP 客户端（2025-06-18）。只连 `sources.yaml` 里的 peer 地址。

    真实 peer 不是「裸 JSON-RPC POST」：必须先 `initialize` 拿 `Mcp-Session-Id`、
    回一条 `notifications/initialized`，后续请求带会话头；响应是 SSE
    （`text/event-stream` 的 `data:` 帧），中途还夹 `: ping` 保活注释。
    缺任何一步真实 TickHub-CN 都回 400 `Missing session ID`（QUOT-164 根因）。

    会话按 peer 缓存复用；服务端 404（会话过期）时重新握手并把这次请求重发一次。
    官方 `mcp` 包只有 async 客户端，而数据层是同步 + 线程池，这里自实现；
    只覆盖本层用到的 `initialize` / `tools/list` / `tools/call`。
    """

    def __init__(self, peers: dict[str, PeerEndpoint] | None = None) -> None:
        self.peers = peers or {}
        self._sessions: dict[str, _McpSession] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._ids = itertools.count(1)

    # ---------------------------------------------------------------- 公共接口

    def call(
        self, peer: PeerEndpoint, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        result = self._request(
            peer,
            "tools/call",
            {"name": tool, "arguments": params},
            timeout=timeout,
        )
        if not isinstance(result, dict):
            raise SourceUnavailable(f"{peer.mcp_name}/{tool} 返回缺少 result")
        if result.get("isError"):
            raise SourceUnavailable(
                f"{peer.mcp_name}/{tool} 报错：{_content_text(result) or '未知错误'}"
            )
        return _unwrap_mcp_result(result)

    def probe(self, peer: PeerEndpoint, *, timeout: float) -> dict[str, Any]:
        result = self._request(peer, "tools/list", {}, timeout=timeout)
        tools = result.get("tools") if isinstance(result, dict) else None
        names = [t.get("name") for t in (tools or []) if isinstance(t, dict)]
        return {"peer": peer.key, "tools": names}

    # ---------------------------------------------------------------- JSON-RPC

    def _request(
        self, peer: PeerEndpoint, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        """带会话的 JSON-RPC 调用；会话过期时重握手并重发一次。"""
        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        for attempt in range(2):
            session = self._ensure_session(peer, timeout=timeout)
            try:
                payload = self._post(peer, body, session=session, timeout=timeout)
            except _SessionExpired:
                if attempt:
                    raise SourceUnavailable(
                        f"{peer.product} 会话反复失效（{mask_url(peer.url)}）"
                    ) from None
                self._drop_session(peer)
                continue
            if payload.get("error"):
                error = payload["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise SourceUnavailable(f"{peer.product} 报错：{detail}")
            return payload.get("result")
        raise SourceUnavailable(  # pragma: no cover - 上面两轮必然 return 或 raise
            f"{peer.product} 会话不可用（{mask_url(peer.url)}）"
        )

    def _next_id(self) -> int:
        with self._guard:
            return next(self._ids)

    def _peer_lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    # ---------------------------------------------------------------- 会话

    def _ensure_session(self, peer: PeerEndpoint, *, timeout: float) -> _McpSession:
        with self._peer_lock(peer.key):
            session = self._sessions.get(peer.key)
            if session is None:
                session = self._handshake(peer, timeout=timeout)
                self._sessions[peer.key] = session
            return session

    def _drop_session(self, peer: PeerEndpoint) -> None:
        with self._peer_lock(peer.key):
            self._sessions.pop(peer.key, None)

    def _handshake(self, peer: PeerEndpoint, *, timeout: float) -> _McpSession:
        """`initialize` → 取 `Mcp-Session-Id` → `notifications/initialized`。"""
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ladderstock", "version": __version__},
            },
        }
        session = _McpSession()
        result = self._post(peer, body, session=session, timeout=timeout, capture=session)
        if not isinstance(result.get("result"), dict):
            raise SourceUnavailable(f"{peer.product} 未完成 MCP 握手（缺 initialize 结果）")
        session.protocol_version = str(result["result"].get("protocolVersion") or "")
        if not session.session_id:
            # 无状态实现（不回 Mcp-Session-Id）也允许：后续请求照常不带会话头。
            return session
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._post(peer, note, session=session, timeout=timeout, expect_response=False)
        return session

    # ---------------------------------------------------------------- 传输

    def _post(
        self,
        peer: PeerEndpoint,
        body: dict[str, Any],
        *,
        session: _McpSession,
        timeout: float,
        expect_response: bool = True,
        capture: _McpSession | None = None,
    ) -> dict[str, Any]:
        if not peer.url:
            raise SourceUnavailable(f"{peer.product} 未配置地址（{peer.key}）")
        headers = {"content-type": "application/json", "accept": MCP_ACCEPT}
        if session.session_id:
            headers[MCP_SESSION_HEADER] = session.session_id
        if session.protocol_version:
            headers[MCP_PROTOCOL_HEADER] = session.protocol_version
        session_id: str | None = None
        try:
            # peer 永远是本机回环地址；Windows 系统代理（WinINET）会被 httpx 的
            # trust_env 读进来，把 127.0.0.1 也转给代理，表现为莫名其妙的 503。
            with httpx.stream(
                "POST",
                peer.url,
                json=body,
                headers=headers,
                timeout=timeout,
                trust_env=False,
            ) as response:
                if response.status_code == 404 and session.session_id:
                    raise _SessionExpired()
                if response.status_code >= 400:
                    raise SourceUnavailable(
                        f"{peer.product} 拒收（HTTP {response.status_code}"
                        f"{_error_detail(response)}）"
                    )
                session_id = response.headers.get(MCP_SESSION_HEADER)
                if not expect_response:
                    return {}
                payload = self._read_payload(
                    peer, response, request_id=body.get("id"), timeout=timeout
                )
        except _SessionExpired:
            raise
        except httpx.HTTPError as exc:
            # 地址可能带查询串凭据（audit R-3）：报错只留 scheme://host/path
            raise SourceUnavailable(
                f"{peer.product} 不可达（{type(exc).__name__}，{mask_url(peer.url)}）"
            ) from exc
        if capture is not None:
            capture.session_id = session_id
        elif session_id and session_id != session.session_id:
            session.session_id = session_id
        return payload

    def _read_payload(
        self,
        peer: PeerEndpoint,
        response: httpx.Response,
        *,
        request_id: Any,
        timeout: float,
    ) -> dict[str, Any]:
        if "text/event-stream" in response.headers.get("content-type", "").lower():
            deadline = time.monotonic() + timeout
            for frame in _sse_frames(response.iter_lines(), deadline=deadline):
                try:
                    message = json.loads(frame)
                except json.JSONDecodeError:
                    continue  # 非 JSON 帧（服务端自定事件）不参与响应匹配
                for item in message if isinstance(message, list) else [message]:
                    if (
                        isinstance(item, dict)
                        and item.get("id") == request_id
                        and ("result" in item or "error" in item)
                    ):
                        return item
            raise SourceUnavailable(f"{peer.product} 的 SSE 流里没有对应的 JSON-RPC 响应")
        try:
            payload = json.loads(response.read())
        except ValueError as exc:
            raise SourceUnavailable(f"{peer.product} 返回非 JSON") from exc
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("id") == request_id:
                    return item
            raise SourceUnavailable(f"{peer.product} 返回里没有对应的 JSON-RPC 响应")
        return payload if isinstance(payload, dict) else {}


def _sse_frames(lines: Iterable[str], *, deadline: float) -> Iterator[str]:
    """按 SSE 规则切帧：空行分隔、多条 `data:` 用换行拼接、`:` 开头是注释。

    真实 peer 每 15s 发一条 `: ping - <ts>` 保活注释——它必须被忽略，
    但也会把连接一直保活，所以这里按**整体墙钟 deadline** 判超时，
    而不是依赖 httpx 的逐次读超时（后者会被 ping 无限续命）。
    """
    data: list[str] = []
    for line in lines:
        if time.monotonic() > deadline:
            raise httpx.ReadTimeout("peer 流式响应超时")
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator or field != "data":
            continue
        data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield "\n".join(data)


def _error_detail(response: httpx.Response) -> str:
    """HTTP 错误响应里的一句话（真实 peer 是 `{"error":{"message": ...}}`）。"""
    try:
        text = response.read().decode("utf-8", "replace").strip()
    except httpx.HTTPError:  # pragma: no cover - 读失败就不再纠结细节
        return ""
    if not text:
        return ""
    try:
        body = json.loads(text)
    except ValueError:
        return f"：{text[:160]}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return f"：{str(error['message'])[:160]}"
        if body.get("message"):
            return f"：{str(body['message'])[:160]}"
    return f"：{text[:160]}"


def _unwrap_mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    """MCP `tools/call` 结果 → 业务 JSON。

    优先 `structuredContent`（真实 TickHub-CN 按 outputSchema 把它包成
    `{"result": {...}}`，这里拆掉这层壳），其次把 `content[0].text` 当 JSON 解析；
    两者都没有时把 result 原样返回（adapter 会按 peer 形状校验）。
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        inner = structured.get("result")
        if len(structured) == 1 and isinstance(inner, dict):
            return inner
        return structured
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            try:
                parsed = json.loads(first["text"])
            except json.JSONDecodeError:
                raise SourceUnavailable("peer 返回的 text 不是 JSON") from None
            if isinstance(parsed, dict):
                return parsed
    return result


def _content_text(result: dict[str, Any]) -> str:
    """`tools/call` 失败时 `content[].text` 里的一句话（真实 peer 把校验错误放这里）。"""
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
                # 真实 peer 的校验错误是多行文本，压成一行再截断，别丢掉后半句
                return " ".join(item["text"].split())[:200]
    return ""


def build_transport(peers: dict[str, PeerEndpoint]) -> Transport:
    """默认传输；测试 monkeypatch 这个函数即可完全离线。"""
    return HttpMcpTransport(peers)


#: `ldst doctor` 的联调探针超时：体检不该为一个可选 peer 卡住十几秒，
#: 也绝不复用 `sources.yaml` 里的业务超时（那是取数用的，不是探活用的）。
PROVISIONAL_PROBE_TIMEOUT_SEC = 1.5


def provisional_tool_warnings(
    *,
    timeout: float = PROVISIONAL_PROBE_TIMEOUT_SEC,
    transport: Transport | None = None,
) -> list[str]:
    """对仍标「待联调」的数据集探测**一次** `tools/list`，给出准确措辞。

    三种结果三种说法，不再留「待联调」这种谁也判断不了的含糊话：

    - 连得上、工具在列表里 → **不产生** warning（联调已确认）；
    - 连得上、工具不在列表里 → 明确说工具不存在，并列出 peer 实际工具名；
    - 连不上 / 配置缺失 → 「未联调：peer 不可达（`mask_url` 之后的地址）」。

    这里只做诊断：失败不改 `ok`、不改退出码，也不写 health/冷却记录——
    peer 现在连不上不等于取数链路配置错了（DEC-008）。
    """
    from ladderstock.core.boundary import DATASET_CATALOG, PROVISIONAL_TOOLS

    pending = {
        dataset: binding
        for dataset, binding in DATASET_CATALOG.items()
        if binding.tool in PROVISIONAL_TOOLS
    }
    if not pending:
        return []
    try:
        peers = build_peers(load_sources_config())
    except LadderStockError:
        # 配置本身缺失/损坏由 doctor 的 config:* 检查项负责，这里不重复报
        return []
    probe = transport if transport is not None else build_transport(peers)

    warnings: list[str] = []
    for dataset, binding in pending.items():
        peer = peers.get(binding.peer)
        if peer is None:
            warnings.append(f"{dataset}: 未联调：sources.yaml 缺少 peer {binding.peer}")
            continue
        try:
            info = probe.probe(peer, timeout=timeout)
        except Exception:  # noqa: BLE001 - 探活失败只影响这条 warning 的措辞
            warnings.append(
                f"{dataset}: 未联调：peer 不可达（{mask_url(peer.url)}）"
            )
            continue
        names = [str(name) for name in (info.get("tools") or []) if name]
        if binding.tool in names:
            continue
        warnings.append(
            f"{dataset}: 工具 {binding.tool} 不在 {peer.product} 的 tools/list 中"
            f"（实际：{', '.join(names) if names else '无'}）"
        )
    return warnings


# --------------------------------------------------------------------------- #
# 健康与冷却日志
# --------------------------------------------------------------------------- #

def health_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / "state" / "health.json"


class HealthLog:
    """冷却窗口与降级事件：`<data_dir>/state/health.json`，进程重启后仍生效。"""

    def __init__(self, path: Path | None = None, *, root: Path | None = None) -> None:
        self.path = Path(path) if path is not None else health_path(root)
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "cooldowns": {}, "events": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {"version": 1, "cooldowns": {}, "events": []}
        if not isinstance(data, dict):
            return {"version": 1, "cooldowns": {}, "events": []}
        data.setdefault("cooldowns", {})
        data.setdefault("events", [])
        return data

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- 冷却

    def cooldown_until(self, source_id: str, *, now: datetime | None = None) -> datetime | None:
        item = (self._data.get("cooldowns") or {}).get(source_id)
        if not item:
            return None
        until = _parse_dt(item.get("until"))
        if until is None:
            return None
        return until if until > (now or _now()) else None

    def set_cooldown(
        self, source_id: str, seconds: float, *, reason: str, tool: str = ""
    ) -> None:
        if seconds <= 0:
            return
        until = _now() + timedelta(seconds=seconds)
        with self._lock:
            self._data.setdefault("cooldowns", {})[source_id] = {
                "until": until.isoformat(timespec="seconds"),
                "reason": reason,
                "tool": tool,
            }
            self._flush()

    def clear_cooldown(self, source_id: str) -> None:
        with self._lock:
            if (self._data.get("cooldowns") or {}).pop(source_id, None) is not None:
                self._flush()

    # ---------------------------------------------------------------- 事件

    def record(
        self,
        kind: str,
        dataset: str,
        *,
        source: str = "",
        tool: str = "",
        detail: str = "",
    ) -> None:
        event = {
            "at": _now().isoformat(timespec="seconds"),
            "kind": kind,
            "dataset": dataset,
            "source": source,
            "tool": tool,
            "detail": detail,
        }
        with self._lock:
            events = self._data.setdefault("events", [])
            events.append(event)
            if len(events) > MAX_HEALTH_EVENTS:
                del events[: len(events) - MAX_HEALTH_EVENTS]
            self._flush()

    def active_cooldowns(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = now or _now()
        rows: list[dict[str, Any]] = []
        for source_id, item in (self._data.get("cooldowns") or {}).items():
            until = _parse_dt(item.get("until"))
            if until is None or until <= moment:
                continue
            rows.append(
                {
                    "source": source_id,
                    "tool": item.get("tool") or "",
                    "until": item.get("until"),
                    "remaining_sec": int((until - moment).total_seconds()),
                    "reason": item.get("reason") or "",
                }
            )
        return sorted(rows, key=lambda row: str(row["until"]))

    def events(self, *, since: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self._data.get("events") or [])
        if since is not None:
            items = [item for item in items if (_parse_dt(item.get("at")) or _now()) >= since]
        if limit > 0:
            items = items[-limit:]
        return [dict(item) for item in items]

    def report(self, *, since: datetime | None = None, limit: int = 50) -> dict[str, Any]:
        return {
            "state_file": str(self.path),
            "active_cooldowns": self.active_cooldowns(),
            "events": self.events(since=since, limit=limit),
        }


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    envelope: LadderEnvelope | None = None
    error: Exception | None = None


@dataclass
class _PeerGate:
    semaphore: threading.Semaphore
    lock: threading.Lock
    last_call: float = 0.0
    interval: float = 0.0


@dataclass
class FetchOutcome:
    """一次取数的完整结果，供 sync 记录运行轨迹。"""

    envelope: LadderEnvelope
    source: str
    used_fallback: bool
    from_cache: bool
    trail: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.used_fallback or self.from_cache


class ProviderRegistry:
    """按 dataset 组装并调用生态工具链。"""

    def __init__(
        self,
        *,
        sources: dict[str, Any] | None = None,
        sync: dict[str, Any] | None = None,
        transport: Transport | None = None,
        health: HealthLog | None = None,
        cache_reader: Any = None,
        root: Path | None = None,
        ttl_sec: float | None = None,
        peer_concurrency: dict[str, int] | None = None,
        peer_min_interval: dict[str, float] | None = None,
    ) -> None:
        self.sources_doc = sources if sources is not None else load_sources_config()
        self.sync_doc = sync if sync is not None else load_sync_config()
        self.specs = build_specs(self.sources_doc)
        self.peers = build_peers(self.sources_doc)
        self.datasets: dict[str, Any] = (self.sync_doc.get("sync") or {}).get("datasets") or {}
        self.transport = transport if transport is not None else build_transport(self.peers)
        self.health = health if health is not None else HealthLog(root=root)
        self.root = root
        self.cache_reader = cache_reader or archive.snapshot
        cold_hot = (self.sync_doc.get("sync") or {}).get("cold_hot") or {}
        configured_ttl = cold_hot.get("intraday_cache_ttl_sec") or DEFAULT_TTL_SEC
        self.ttl_sec = float(ttl_sec) if ttl_sec is not None else float(configured_ttl)
        self._cache: dict[tuple[str, str, str], tuple[float, LadderEnvelope]] = {}
        self._inflight: dict[tuple[str, str, str], _Pending] = {}
        self._guard = threading.Lock()
        concurrency = {**DEFAULT_PEER_CONCURRENCY, **(peer_concurrency or {})}
        intervals = {**DEFAULT_PEER_MIN_INTERVAL_SEC, **(peer_min_interval or {})}
        self._gates: dict[str, _PeerGate] = {
            key: _PeerGate(
                semaphore=threading.Semaphore(max(1, int(concurrency.get(key, 2)))),
                lock=threading.Lock(),
                interval=float(intervals.get(key, 0.0)),
            )
            for key in self.peers
        }

    # ---------------------------------------------------------------- 链路

    def chain(self, dataset: str) -> list[SourceSpec]:
        """主工具 → 同 peer 备工具（顺序来自 sync.yaml，缺省用 sources.yaml 的 fallback）。"""
        declared = self.datasets.get(dataset)
        if declared is None:
            raise LadderStockError(f"{dataset} 未在 config/sync.yaml 声明", EXIT_CONFIG)
        primary_id = str(declared.get("primary") or "")
        if not primary_id:
            raise LadderStockError(f"{dataset} 在 sync.yaml 里缺 primary", EXIT_CONFIG)
        ids = [primary_id, *(str(x) for x in (declared.get("fallback") or []))]
        if len(ids) == 1:
            ids.extend(self.specs[primary_id].fallback if primary_id in self.specs else ())
        return self._validated_chain(dataset, ids)

    def source_chain(self, dataset: str) -> list[SourceSpec]:
        """只按 `sources.yaml` 的主备组装链路（用于**不进 sync 台账**的数据集）。

        盘中快照（`spot_quote`）这类数据集不在 `sync.yaml` 里——进了 sync.yaml 就会
        被 `ldst sync` 当作要落台账的数据集。链路来源不同，主备同 peer 的校验完全一样。
        """
        primary = next(
            (spec for spec in self.specs.values() if spec.dataset == dataset and spec.primary),
            None,
        )
        if primary is None:
            raise LadderStockError(
                f"{dataset} 在 sources.yaml 里没有 primary 记录", EXIT_CONFIG
            )
        return self._validated_chain(dataset, [primary.id, *primary.fallback])

    def _validated_chain(self, dataset: str, ids: list[str]) -> list[SourceSpec]:
        binding = owner_of(dataset)
        specs: list[SourceSpec] = []
        for sid in ids:
            spec = self.specs.get(sid)
            if spec is None:
                raise LadderStockError(f"sync.yaml 引用了不存在的 source：{sid}", EXIT_CONFIG)
            if spec.dataset != dataset:
                raise BoundaryViolation(f"{sid} 属于 {spec.dataset}，不能用于 {dataset}")
            if spec.via != binding.peer:
                # 跨 peer 备源 = 换供应商采集（DEC-006 闸门 3）
                raise BoundaryViolation(
                    f"{dataset} 归属 {binding.peer}，备源 {sid} 却走 {spec.via}"
                )
            if sid not in {s.id for s in specs}:
                specs.append(spec)
        return specs

    # ---------------------------------------------------------------- 取数

    def fetch(
        self,
        dataset: str,
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchOutcome:
        """按治理规则取数并返回带轨迹的结果。不可用且无缓存时抛 `SourceUnavailable`。"""
        return self._fetch(
            dataset,
            self.chain(dataset),
            trade_date=trade_date,
            params=params,
            allow_cache=allow_cache,
        )

    def fetch_live(
        self,
        dataset: str,
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchOutcome:
        """同 `fetch`，但链路按 `sources.yaml` 的登记组装（数据集不在 sync.yaml）。"""
        return self._fetch(
            dataset,
            self.source_chain(dataset),
            trade_date=trade_date,
            params=params,
            allow_cache=allow_cache,
        )

    def _fetch(
        self,
        dataset: str,
        chain: list[SourceSpec],
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchOutcome:
        owner_of(dataset)
        key = (dataset, trade_date.isoformat() if trade_date else "", _key(params or {}))
        owner = False
        with self._guard:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > time.monotonic():
                envelope = hit[1].model_copy(update={"freshness": "cached"})
                return FetchOutcome(
                    envelope=envelope,
                    source=_source_of(envelope),
                    used_fallback=False,
                    from_cache=False,
                    trail=["ttl_cache:hit"],
                )
            pending = self._inflight.get(key)
            if pending is None:
                pending = _Pending()
                self._inflight[key] = pending
                owner = True
        if not owner:
            pending.event.wait(INFLIGHT_WAIT_SEC)
            if pending.error is not None:
                raise pending.error
            if pending.envelope is None:  # pragma: no cover - 防御
                raise SourceUnavailable(f"{dataset} 取数等待超时")
            envelope = pending.envelope.model_copy()
            return FetchOutcome(
                envelope=envelope,
                source=_source_of(envelope),
                used_fallback=False,
                from_cache=False,
                trail=["inflight:merged"],
            )
        outcome: FetchOutcome | None = None
        error: Exception | None = None
        try:
            outcome = self._resolve(
                dataset, chain, trade_date, params or {}, allow_cache=allow_cache
            )
            with self._guard:
                self._cache[key] = (time.monotonic() + self.ttl_sec, outcome.envelope.model_copy())
            return outcome
        except Exception as exc:  # noqa: BLE001 - 原样交给调用方决定退出码
            error = exc
            raise
        finally:
            with self._guard:
                self._inflight.pop(key, None)
            pending.envelope = outcome.envelope if outcome else None
            pending.error = error
            pending.event.set()

    def _resolve(
        self,
        dataset: str,
        chain: list[SourceSpec],
        trade_date: date | None,
        params: dict[str, Any],
        *,
        allow_cache: bool,
    ) -> FetchOutcome:
        trail: list[str] = []
        failures: list[str] = []
        for index, spec in enumerate(chain):
            until = self.health.cooldown_until(spec.id)
            if until is not None:
                trail.append(f"{spec.trail_label()}:cooldown")
                self.health.record(
                    "skipped_cooldown",
                    dataset,
                    source=spec.id,
                    tool=spec.tool,
                    detail=f"冷却至 {until.isoformat(timespec='seconds')}",
                )
                continue
            try:
                envelope = self._call_source(spec, params, trade_date)
            except Exception as exc:
                reason = _reason(exc)
                trail.append(f"{spec.trail_label()}:{reason}")
                failures.append(f"{spec.trail_label()}={reason}")
                self.health.set_cooldown(
                    spec.id, spec.cooldown_sec, reason=reason, tool=spec.tool
                )
                self.health.record(
                    "failure",
                    dataset,
                    source=spec.id,
                    tool=spec.tool,
                    detail=f"{reason}: {exc}",
                )
                continue
            trail.append(f"{spec.trail_label()}:ok")
            used_fallback = index > 0
            envelope.source_detail = _source_detail(spec, used_fallback, trail)
            if used_fallback:
                envelope.source_status = "degraded"
                self.health.record(
                    "fallback",
                    dataset,
                    source=spec.id,
                    tool=spec.tool,
                    detail="主源不可用，已切同 peer 备源",
                )
            return FetchOutcome(
                envelope=envelope,
                source=spec.id,
                used_fallback=used_fallback,
                from_cache=False,
                trail=trail,
            )

        if allow_cache:
            outcome = self._replay(dataset, trade_date, trail, failures)
            if outcome is not None:
                return outcome
        # failures 为空说明链路里的工具都在冷却期，一次调用都没发出去——
        # 报「无可用工具」会误导排查方向，这里明说是冷却。
        reason = "；".join(failures) or "链路内工具均在冷却期，本轮未发起调用"
        raise SourceUnavailable(
            f"{dataset} 的生态链路全部不可用：{reason}"
            + ("，且本地无缓存可回放" if allow_cache else "（已禁用缓存回放）")
        )

    def _call_source(
        self, spec: SourceSpec, params: dict[str, Any], trade_date: date | None
    ) -> LadderEnvelope:
        peer = self.peers.get(spec.via)
        if peer is None:
            raise SourceUnavailable(f"sources.yaml 缺少 peer {spec.via}")
        last: Exception | None = None
        for _attempt in range(max(0, spec.retries) + 1):
            self._enter_peer(peer)
            try:
                response = self.transport.call(
                    peer, spec.tool, dict(params), timeout=spec.timeout_sec
                )
                return self._to_envelope(spec, response, trade_date)
            except Exception as exc:  # noqa: BLE001 - 换备源由上层决定
                last = exc
            finally:
                self._leave_peer(peer)
        assert last is not None
        # 传输层异常原样抛出：`_reason` 才能把 timeout / unreachable 区分进轨迹与冷却原因；
        # 包成 SourceUnavailable 会把所有失败压成同一句话，复盘时看不出是超时还是拒连。
        if isinstance(last, _TRANSPORT_ERRORS):
            raise last
        raise SourceUnavailable(str(last)) from last

    def _to_envelope(
        self, spec: SourceSpec, response: dict[str, Any], trade_date: date | None
    ) -> LadderEnvelope:
        if spec.via == "tickhub":
            return from_tool_response(
                spec.dataset,
                response,
                data_as_of=trade_date,
                topic=spec.dataset,
                tool=spec.tool,
            )
        body = dict(response)
        body.setdefault("data_as_of", (trade_date or _now().date()).isoformat())
        body.setdefault("fetched_at", _now().isoformat())
        if spec.via == "wire":
            return from_wire_envelope(spec.dataset, body, topic=spec.dataset)
        if spec.via == "senate":
            return from_data_envelope(spec.dataset, body, topic=spec.dataset)
        raise SourceUnavailable(f"未知 peer：{spec.via}")

    # ---------------------------------------------------------------- 缓存回放

    def _replay(
        self,
        dataset: str,
        trade_date: date | None,
        trail: list[str],
        failures: list[str],
    ) -> FetchOutcome | None:
        snapshot = self.cache_reader(dataset, trade_date, root=self.root)
        if getattr(snapshot, "empty", True) and trade_date is not None:
            latest = archive.latest_day(dataset, root=self.root)
            if latest is not None:
                snapshot = self.cache_reader(dataset, latest, root=self.root)
        if getattr(snapshot, "empty", True):
            return None
        as_of = snapshot.as_of or trade_date or _now().date()
        envelope = LadderEnvelope(
            domain="review",
            topic=dataset,
            data_as_of=as_of,
            payload={
                "dataset": dataset,
                "tool": "cache_replay",
                "records": list(snapshot.records),
            },
            source="local_cache",
            source_detail=_cache_detail(snapshot, trail),
            fetched_at=_now(),
            freshness="cached",
            confidence="low",
            source_status="degraded",
        )
        trail = [*trail, f"cache:{snapshot.backend}@{as_of.isoformat()}"]
        self.health.record(
            "cache_replay",
            dataset,
            source="local_cache",
            tool=str(getattr(snapshot, "backend", "none")),
            detail="peer 全部不可用，已回放本地缓存",
        )
        return FetchOutcome(
            envelope=envelope,
            source="local_cache",
            used_fallback=False,
            from_cache=True,
            trail=trail,
        )

    # ---------------------------------------------------------------- 限流

    def _enter_peer(self, peer: PeerEndpoint) -> None:
        gate = self._gates.get(peer.key)
        if gate is None:
            return
        gate.semaphore.acquire()
        with gate.lock:
            if gate.interval > 0 and gate.last_call > 0:
                wait = gate.last_call + gate.interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            gate.last_call = time.monotonic()

    def _leave_peer(self, peer: PeerEndpoint) -> None:
        gate = self._gates.get(peer.key)
        if gate is not None:
            gate.semaphore.release()

    # ---------------------------------------------------------------- 检视

    def describe(self) -> list[dict[str, Any]]:
        """`ldst sources list` 的行：peer / tool / primary / fallback / cooldown。"""
        rows: list[dict[str, Any]] = []
        for spec in self.specs.values():
            binding = owner_of(spec.dataset) if spec.dataset in _catalog() else None
            allowed = {binding.tool, *binding.fallback_tools} if binding else set()
            until = self.health.cooldown_until(spec.id)
            declared = self.datasets.get(spec.dataset) or {}
            rows.append(
                {
                    "dataset": spec.dataset,
                    "peer": spec.via,
                    "source": spec.id,
                    "tool": spec.tool,
                    "primary": spec.primary,
                    "fallback": ",".join(spec.fallback) or "-",
                    "cooldown_sec": spec.cooldown_sec,
                    "timeout_sec": spec.timeout_sec,
                    "retries": spec.retries,
                    "weight": spec.weight,
                    "in_chain": bool(declared),
                    "tool_registered": spec.tool in allowed,
                    "cooldown_until": until.isoformat(timespec="seconds") if until else "-",
                    "optional": bool(declared.get("optional", False)),
                }
            )
        return rows

    def peer_rows(self) -> list[dict[str, Any]]:
        cooldowns = {row["source"] for row in self.health.active_cooldowns()}
        rows = []
        for peer in self.peers.values():
            sources = [spec for spec in self.specs.values() if spec.via == peer.key]
            rows.append(
                {
                    "peer": peer.key,
                    "product": peer.product,
                    "cli": (self.sources_doc.get("peers") or {}).get(peer.key, {}).get("cli", ""),
                    # 检视面同样只给打码地址（audit R-3）：不含 userinfo / 查询串
                    "url": mask_url(peer.url),
                    "sources": len(sources),
                    "in_cooldown": sum(1 for spec in sources if spec.id in cooldowns),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

def _catalog() -> dict[str, Any]:
    from ladderstock.core.boundary import DATASET_CATALOG

    return DATASET_CATALOG


def _source_of(envelope: LadderEnvelope) -> str:
    return envelope.source


def _source_detail(spec: SourceSpec, used_fallback: bool, trail: list[str]) -> str:
    head = "备源" if used_fallback else "主源"
    return f"{head} {spec.trail_label()}；轨迹 {' → '.join(trail)}"


def _cache_detail(snapshot: Any, trail: list[str]) -> str:
    backend = getattr(snapshot, "backend", "none")
    as_of = getattr(snapshot, "as_of", None)
    return f"缓存回放（{backend}，{as_of}）；轨迹 {' → '.join(trail)}"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError, httpx.TransportError)):
        return "unreachable"
    if isinstance(exc, SourceUnavailable):
        return "unavailable"
    if isinstance(exc, LadderStockError):
        return "rejected"
    return "error"


def _key(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.astimezone()
    return None
