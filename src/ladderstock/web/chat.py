"""对话通道（QUOT-473）：首页研究入口 / 页级助手的一轮问答。

只做四件事：

1. **复用既有通道**：`ai.fallback.load_config` 选后端，CLI 走
   `cli_provider.CliProvider.acomplete`（可中止），HTTP 走
   `build_chat_client(config).complete`。不新起模型调用，不硬编码模型名 / 密钥。
2. **一轮一答**：请求带 `message` 与可选 `session_id`；服务端保留最近几轮
   对话（内存、TTL 30 分钟），只在本轮成功后落账。
3. **来源如实**：`sources[]` 只收录调用方随上下文交来的**真实取数记录**
   （`name` + `fetched_at` + 可选 `url`），服务端校验时间 / 链接后回传，
   并补 `used_at` 标明「本次调用在什么时候用到了它」。一条都没有 → 空数组，
   且在正文末尾如实说明；服务端不主动抓持仓、不抓论点正文。
4. **失败可分**：对齐 `ai/error_taxonomy.FailureKind`，另给 `reason`
   （`no_channel` / `timeout` / `network` / `quota` / `format` / `truncated` /
   `internal` / `empty` / `aborted`），每类带可读文案、动作与 `retryable`。

超时上限默认 60s，`LDST_AI_CHAT_TIMEOUT` 可覆盖（5–300s）；调用方只能往下压。
前端 abort 后服务端取消本轮，CLI 子进程连同其子进程一并终止。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ladderstock.ai import answer_blocks, cli_provider, fallback
from ladderstock.ai.cli_provider import build_chat_client
from ladderstock.ai.error_taxonomy import classify_error, sanitize
from ladderstock.ai.providers import UnconfiguredError, provider_for
from ladderstock.core.boundary import BoundaryViolation

#: 单轮上限：默认 60s；`LDST_AI_CHAT_TIMEOUT` 可覆盖，但本身也被硬上限夹住
DEFAULT_TIMEOUT_SEC = 60.0
MIN_TIMEOUT_SEC = 5.0
MAX_TIMEOUT_SEC = 300.0
TIMEOUT_ENV = "LDST_AI_CHAT_TIMEOUT"

#: 请求载荷上限（超了直接 400，不偷偷截断后当成完整数据回答）
MAX_MESSAGE_CHARS = 4000
MAX_CONTEXT_CHARS = 12000
MAX_CONTEXT_DEPTH = 6
MAX_SOURCES = 20
MAX_SOURCE_NAME_CHARS = 80
MAX_URL_CHARS = 500

#: 会话记忆：只留最近 6 轮（12 条消息），30 分钟不用即过期，最多 64 个会话
MAX_SESSION_TURNS = 6
SESSION_TTL_SEC = 1800.0
MAX_SESSIONS = 64
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

#: 断连轮询间隔：前端 abort 到这个间隔内被服务端发现
DISCONNECT_POLL_SEC = 0.2
#: 流式心跳：消费循环每 0.5s 醒一次；距上一帧 ≥2s（E5）就补一帧 heartbeat
STREAM_HEARTBEAT_POLL_SEC = 0.5
STREAM_HEARTBEAT_GAP_SEC = 2.0
#: 取数时间允许的未来偏差（时钟漂移），超出即判「编造」
_CLOCK_SKEW = timedelta(minutes=5)

SYSTEM_PROMPT = (
    "你是 LadderStock 的研究助手。只依据【页内数据】与【可用来源】回答，"
    "不要编造数据里没有的数字、日期、代码或链接。\n"
    "- 【页内数据】为空时，直说「这一项没有取到」，不要用一般知识补成行情结论。\n"
    "- 只有【可用来源】里列出的条目才可以在回答里提到；没有列出的链接一律不许出现。\n"
    "- 页内数据只是页面已展示内容的摘要，不能据此预测价格，也不要给出买卖建议。\n"
    "- 回答用简洁中文，纯文本与短横线分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），不要复述整段上下文。"
)

#: `pending_research` 的触发词：只有问题明确指向个股研究时才建议起后台任务
_RESEARCH_HINTS = (
    "研究",
    "深挖",
    "深度",
    "分析",
    "为什么",
    "原因",
    "财报",
    "估值",
    "公告",
    "事件",
    "基本面",
    "产业链",
    "对比",
    "预期",
)
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

_NO_CHANNEL_ACTION = (
    "到「设置 → 接入 AI」配置 HTTP 通道，或登录本机 Codex / Claude / WorkBuddy CLI 后重试"
)
_CLI_STATE_LABELS = {"missing": "未安装", "broken": "装上了但跑不起来", "no_login": "未登录"}


class ChatRequestContext(BaseModel):
    """页面已展示的只读上下文：字段由调用方裁剪，服务端不主动补数据。"""

    model_config = ConfigDict(extra="forbid")

    page: str | None = None
    data: dict[str, Any] | None = None
    sources: list[ChatSource] = Field(default_factory=list)


class ChatSource(BaseModel):
    """调用方的一次真实取数记录：来源名 + 取数时间（ISO 8601）+ 可选链接。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    fetched_at: str
    url: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    session_id: str | None = None
    context: ChatRequestContext | None = None
    #: 只能压低服务端上限，不能抬高
    timeout_sec: float | None = None


class ChatFailure(Exception):
    """一次问答的可读失败：`code` / `reason` / `kind` 供前端区分与配色。"""

    def __init__(
        self,
        status: int,
        *,
        code: str,
        reason: str,
        kind: str,
        message: str,
        action: str = "",
        retryable: bool = True,
        detail: str = "",
        channel_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.reason = reason
        self.kind = kind
        self.message = message
        self.action = action
        self.retryable = retryable
        self.detail = detail
        self.channel_state = channel_state

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "message": self.message,
            "action": self.action,
            "retryable": self.retryable,
            "detail": self.detail or self.message,
            "channel_state": self.channel_state,
        }


class ChatAborted(Exception):
    """前端 abort：本轮被取消，不写会话记忆、不回响应。"""


@dataclasses.dataclass(frozen=True)
class Channel:
    """本轮落在哪条既有通道上（只做描述，不另起调用）。"""

    kind: str
    label: str
    config: Any
    target: Any


# --------------------------------------------------------------------------- #
# 会话记忆（进程内）
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class _Session:
    turns: list[tuple[str, str]]
    touched: float


_SESSIONS: OrderedDict[str, _Session] = OrderedDict()
_SESSIONS_LOCK = threading.Lock()


def reset_sessions() -> None:
    """清空会话记忆（测试与「新开对话」用）。"""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()


def _evict(now: float) -> None:
    expired = [
        key
        for key, session in _SESSIONS.items()
        if now - session.touched > SESSION_TTL_SEC
    ]
    for key in expired:
        _SESSIONS.pop(key, None)
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.popitem(last=False)


def _history(session_id: str) -> list[tuple[str, str]]:
    now = time.monotonic()
    with _SESSIONS_LOCK:
        _evict(now)
        session = _SESSIONS.get(session_id)
        if session is None:
            return []
        _SESSIONS.move_to_end(session_id)
        return list(session.turns)


def _remember(session_id: str, question: str, answer: str) -> None:
    now = time.monotonic()
    with _SESSIONS_LOCK:
        _evict(now)
        session = _SESSIONS.setdefault(session_id, _Session(turns=[], touched=now))
        session.turns.append((question[:MAX_MESSAGE_CHARS], answer[:MAX_MESSAGE_CHARS]))
        session.turns = session.turns[-MAX_SESSION_TURNS:]
        session.touched = now
        _SESSIONS.move_to_end(session_id)


def _forget(session_id: str) -> None:
    """撤掉本轮刚记下的问答（流式路径块装配失败时用）。"""
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(session_id)
        if session is None or not session.turns:
            return
        session.turns.pop()
        session.touched = time.monotonic()
        if not session.turns:
            _SESSIONS.pop(session_id, None)


def resolve_session_id(raw: str | None) -> str:
    """`session_id` 缺省时新生成一个；给了就必须是 8–64 位安全字符。"""
    text = (raw or "").strip()
    if not text:
        return secrets.token_urlsafe(12)
    if not _SESSION_ID_RE.match(text):
        raise ChatFailure(
            400,
            code="invalid_session",
            reason="invalid_request",
            kind="internal",
            message="session_id 需要 8–64 位字母 / 数字 / 下划线 / 连字符",
            retryable=False,
        )
    return text


# --------------------------------------------------------------------------- #
# 时间 / 链接 / 上下文校验
# --------------------------------------------------------------------------- #

def _local_tz() -> Any:
    return datetime.now().astimezone().tzinfo


def _parse_moment(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_local_tz())
    return value


def _safe_url(raw: str | None) -> str | None:
    """只留 http(s) 链接的 scheme / host / path：查询串可能带 token，整段丢掉。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, "", "")
    )[:MAX_URL_CHARS]


def _check_depth(value: Any, depth: int) -> None:
    if depth > MAX_CONTEXT_DEPTH:
        raise ChatFailure(
            400,
            code="context_too_deep",
            reason="invalid_request",
            kind="internal",
            message=f"context.data 嵌套超过 {MAX_CONTEXT_DEPTH} 层",
            retryable=False,
        )
    if isinstance(value, Mapping):
        for item in value.values():
            _check_depth(item, depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _check_depth(item, depth + 1)


def _record_sources(context: ChatRequestContext | None) -> list[dict[str, Any]]:
    """把调用方的取数记录校验成 `sources[]`：时间缺失 / 未来时间一律拒绝，不补不编。"""
    if context is None or not context.sources:
        return []
    now = datetime.now(UTC)
    recorded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(context.sources):
        name = item.name.strip()
        if not name:
            raise ChatFailure(
                400,
                code="invalid_source",
                reason="invalid_request",
                kind="internal",
                message=f"context.sources[{index}].name 不能为空",
                retryable=False,
            )
        fetched = _parse_moment(item.fetched_at)
        if fetched is None:
            raise ChatFailure(
                400,
                code="invalid_source",
                reason="invalid_request",
                kind="internal",
                message=(
                    f"context.sources[{index}].fetched_at 需要 ISO 8601 时间"
                    "（如 2026-11-07T09:20:00+08:00）"
                ),
                retryable=False,
            )
        if fetched - now > _CLOCK_SKEW:
            raise ChatFailure(
                400,
                code="invalid_source",
                reason="invalid_request",
                kind="internal",
                message=f"context.sources[{index}].fetched_at 晚于当前时间，拒绝当作已取到的数据",
                retryable=False,
            )
        name_value = name[:MAX_SOURCE_NAME_CHARS]
        fetched_value = fetched.isoformat()
        entry = {
            "name": name_value,
            "fetched_at": fetched_value,
            "url": _safe_url(item.url),
            "used_at": now.isoformat(timespec="seconds"),
            "kind": "page_context",
        }
        key = (name_value, fetched_value)
        if key in seen:
            continue
        seen.add(key)
        recorded.append(entry)
        if len(recorded) >= MAX_SOURCES:
            break
    return recorded


def _context_block(context: ChatRequestContext | None) -> tuple[str, bool]:
    """页内数据的 JSON 文本 + 是否有数据；超长 / 过深直接 400。"""
    if context is None or context.data is None:
        return "", False
    _check_depth(context.data, 0)
    dumped = json.dumps(context.data, ensure_ascii=False, sort_keys=True)
    if len(dumped) > MAX_CONTEXT_CHARS:
        raise ChatFailure(
            400,
            code="context_too_long",
            reason="invalid_request",
            kind="internal",
            message=(
                f"页内上下文过长（上限 {MAX_CONTEXT_CHARS} 字），请缩小页面数据后重试"
            ),
            retryable=False,
        )
    return dumped, True


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #

def _build_messages(
    *,
    message: str,
    context: ChatRequestContext | None,
    context_json: str,
    sources: Sequence[Mapping[str, Any]],
    history: Sequence[tuple[str, str]],
) -> tuple[str, str]:
    parts: list[str] = []
    if context is not None and context.page:
        parts.append(f"【页面】{context.page.strip()[:80]}")
    parts.append(
        "【页内数据（只读）】\n" + context_json
        if context_json
        else "【页内数据】本轮没有提供。"
    )
    if sources:
        lines = []
        for index, source in enumerate(sources, start=1):
            suffix = f" · {source['url']}" if source.get("url") else ""
            lines.append(f"{index}. {source['name']} · 取数 {source['fetched_at']}{suffix}")
        parts.append("【可用来源】\n" + "\n".join(lines))
    else:
        parts.append("【可用来源】无（不得编造来源或链接）")
    if history:
        rounds = [f"用户：{question}\n助手：{answer}" for question, answer in history]
        parts.append("【历史对话】\n" + "\n\n".join(rounds))
    parts.append(f"【本轮问题】\n{message}")
    return SYSTEM_PROMPT, "\n\n".join(parts)


def _source_note(*, has_data: bool, sources: Sequence[Mapping[str, Any]]) -> str:
    """没有来源时在正文末尾如实说明（服务端文案，不是模型编的）。"""
    if sources:
        return ""
    if has_data:
        return "（说明：本轮页内数据未附带取数记录，回答不列外部来源。）"
    return "（说明：本轮没有任何可核实的数据来源，以下内容不含具体行情数据。）"


# --------------------------------------------------------------------------- #
# 待确认的后台研究建议（只建议，不启动）
# --------------------------------------------------------------------------- #

def _pending_research(message: str) -> list[dict[str, Any]]:
    codes: list[str] = []
    for code in _CODE_RE.findall(message or ""):
        if code not in codes:
            codes.append(code)
    if not codes or not any(hint in message for hint in _RESEARCH_HINTS):
        return []
    return [
        {
            "kind": "stock_research",
            "code": code,
            "label": f"个股深挖 {code}",
            "reason": "问题涉及个股研究，确认后由前端调用既有研究接口启动",
            "confirm_required": True,
        }
        for code in codes[:3]
    ]


# --------------------------------------------------------------------------- #
# 通道选择与调用
# --------------------------------------------------------------------------- #

def timeout_limit() -> float:
    """服务端单轮上限：默认 60s，环境变量可覆盖（夹在 5–300s）。"""
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SEC
    except ValueError:
        value = DEFAULT_TIMEOUT_SEC
    return min(max(value, MIN_TIMEOUT_SEC), MAX_TIMEOUT_SEC)


def effective_timeout(requested: float | None) -> float:
    limit = timeout_limit()
    if requested is None:
        return limit
    try:
        value = float(requested)
    except (TypeError, ValueError):
        raise ChatFailure(
            400,
            code="invalid_timeout",
            reason="invalid_request",
            kind="internal",
            message="timeout_sec 需要数字（秒）",
            retryable=False,
        ) from None
    if value <= 0:
        raise ChatFailure(
            400,
            code="invalid_timeout",
            reason="invalid_request",
            kind="internal",
            message="timeout_sec 必须大于 0",
            retryable=False,
        )
    return min(max(value, MIN_TIMEOUT_SEC), limit)


def _no_channel(message: str, *, channel_state: str | None = None, action: str = "") -> ChatFailure:
    return ChatFailure(
        503,
        code="ai_no_channel",
        reason="no_channel",
        kind="internal",
        message=message,
        action=action or _NO_CHANNEL_ACTION,
        retryable=True,
        channel_state=channel_state,
    )


def load_channel(config_root: Any = None) -> Channel:
    """按既有配置选通道；不可用时抛 `ai_no_channel`（绝不降级成假回答）。"""
    try:
        config = fallback.load_config(config_root)
    except BoundaryViolation as exc:
        raise _no_channel(f"未配置 AI 通道：{sanitize(str(exc), max_len=200)}") from None
    try:
        target = config.resolve(config.default_model)
    except BoundaryViolation as exc:
        raise _no_channel(f"AI 配置无效：{sanitize(str(exc), max_len=200)}") from None

    if target.backend.kind == "cli":
        try:
            spec = cli_provider.cli_spec(target.backend.cli)
        except KeyError:
            raise _no_channel(
                f"订阅通道配置无效：未知的 CLI {target.backend.cli!r}（可选："
                + "、".join(item.id for item in cli_provider.CLI_SPECS)
                + "）"
            ) from None
        status = cli_provider.probe(spec)
        if status["state"] != "ok":
            label = _CLI_STATE_LABELS.get(str(status["state"]), str(status["state"]))
            raise _no_channel(
                f"{spec.label} 不可用（{label}）：{sanitize(str(status['detail']))}",
                channel_state=str(status["state"]),
                action=spec.login_hint or _NO_CHANNEL_ACTION,
            )
        return Channel(kind="cli", label=target.label, config=config, target=target)

    if not target.backend.configured:
        missing = "、".join(target.backend.missing_env()) or "环境变量"
        raise _no_channel(f"未配置 AI 通道：缺少 {missing}")
    return Channel(kind=target.backend.kind, label=target.label, config=config, target=target)


async def complete(channel: Channel, system: str, user: str, *, timeout: float) -> str:
    """走既有通道拿正文：CLI 可中止；HTTP 由 httpx 自己的超时兜住。"""
    if channel.kind == "cli":
        provider = cli_provider.CliProvider()
        return await provider.acomplete(
            channel.target, system, user, json_mode=False, timeout=timeout
        )
    client = build_chat_client(channel.config)
    client.timeout = timeout
    return await asyncio.to_thread(
        client.complete, channel.config.default_model, system, user, json_mode=False
    )


async def complete_iter(
    channel: Channel, system: str, user: str, *, timeout: float
) -> AsyncIterator[str]:
    """真流式增量（QUOT-581）：HTTP 通道逐段 yield 正文增量，任何失败从迭代器抛出。

    CLI 通道物理上是一次性子进程（`acomplete`），没有真增量可给——直接抛
    `NotImplementedError`，由调用方回落到一次性 `complete` 的降级路径
    （`stream_mode: "degraded"` 如实标注），不假装逐字流。
    """
    if channel.kind == "cli":
        raise NotImplementedError
    target = channel.config.resolve(channel.config.default_model)
    provider = provider_for(target.backend.kind)
    async for chunk in provider.aiter_text(target, system, user, timeout=timeout):
        yield chunk


async def _cancel(task: asyncio.Future[str]) -> None:
    if task.done():
        with contextlib.suppress(BaseException):
            task.exception()
        return
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


async def await_turn(
    work: Awaitable[str],
    *,
    timeout: float,
    channel_kind: str,
    is_disconnected: Callable[[], Awaitable[bool]] | None,
) -> str:
    """跑一轮补全，同时盯着时限与前端断连；两者都会取消底层调用。"""
    task = asyncio.ensure_future(work)
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            done, _ = await asyncio.wait({task}, timeout=min(DISCONNECT_POLL_SEC, remaining))
            if task in done:
                return task.result()
            if is_disconnected is not None and await is_disconnected():
                raise ChatAborted
    except TimeoutError:
        await _cancel(task)
        where = "本机 CLI" if channel_kind == "cli" else "上游模型"
        raise ChatFailure(
            504,
            code="ai_timeout",
            reason="timeout",
            kind="network",
            message=f"AI 响应超时（{int(timeout)}s）：{where}没有在时限内返回",
            action="稍后重试；持续超时可调小上下文，或在设置里换一条更快的通道",
            retryable=True,
        ) from None
    except ChatAborted:
        await _cancel(task)
        raise
    except asyncio.CancelledError:
        await _cancel(task)
        raise


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, httpx.TimeoutException)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


_UPSTREAM_STATUS_RE = re.compile(r"\bHTTP[ /]?(\d{3})\b")


def _upstream_5xx(detail: str) -> int | None:
    """上游真的回了 5xx：仍归 `network`（可重试），但要和「请求没送达」分开说。"""
    match = _UPSTREAM_STATUS_RE.search(detail or "")
    if match:
        status = int(match.group(1))
        if 500 <= status < 600:
            return status
    return None


def _classify_exception(exc: BaseException, channel: Channel) -> ChatFailure:
    """把异常翻译成「四类 FailureKind + 细分 reason」，绝不吞掉成假回答。"""
    if isinstance(exc, ChatFailure):
        return exc
    if isinstance(exc, (cli_provider.CliTimeout,)) or _is_timeout(exc):
        where = "本机 CLI" if channel.kind == "cli" else "上游模型"
        return ChatFailure(
            504,
            code="ai_timeout",
            reason="timeout",
            kind="network",
            message=f"AI 响应超时：{where}没有在时限内返回",
            action="稍后重试；持续超时可调小上下文，或在设置里换一条更快的通道",
            retryable=True,
        )
    if isinstance(exc, cli_provider.CliUnavailable):
        text = sanitize(str(exc), max_len=240)
        if "没有输出" in text:
            return ChatFailure(
                502,
                code="ai_empty",
                reason="empty",
                kind="internal",
                message="AI 返回了空回答（模型未产出内容），请重试。",
                action="重试一次；仍为空时检查本机 CLI 是否正常",
                retryable=True,
            )
        failure = classify_error(text)
        if failure.kind == "quota":
            return ChatFailure(
                429,
                code="ai_quota",
                reason="quota",
                kind="quota",
                message=failure.message,
                action=failure.action,
                retryable=True,
                detail=failure.detail,
            )
        if failure.kind == "network":
            status = _upstream_5xx(failure.detail)
            if status is not None:
                return ChatFailure(
                    502,
                    code="ai_upstream",
                    reason="upstream",
                    kind="network",
                    message=f"上游模型返回 {status}：{sanitize(failure.detail, max_len=200)}",
                    action=failure.action,
                    retryable=True,
                    detail=failure.detail,
                )
            return ChatFailure(
                502,
                code="ai_network",
                reason="network",
                kind="network",
                message=f"AI 请求未送达（网络/代理）：{failure.detail}",
                action=failure.action,
                retryable=True,
                detail=failure.detail,
            )
        return _no_channel(text)
    if isinstance(exc, (UnconfiguredError, BoundaryViolation)):
        return _no_channel(f"未配置 AI 通道：{sanitize(str(exc), max_len=200)}")

    failure = classify_error(exc)
    if failure.kind == "quota":
        return ChatFailure(
            429,
            code="ai_quota",
            reason="quota",
            kind="quota",
            message=failure.message,
            action=failure.action,
            retryable=True,
            detail=failure.detail,
        )
    if failure.kind == "network":
        status = _upstream_5xx(failure.detail)
        if status is not None:
            return ChatFailure(
                502,
                code="ai_upstream",
                reason="upstream",
                kind="network",
                message=f"上游模型返回 {status}：{sanitize(failure.detail, max_len=200)}",
                action=failure.action,
                retryable=True,
                detail=failure.detail,
            )
        return ChatFailure(
            502,
            code="ai_network",
            reason="network",
            kind="network",
            message=f"AI 请求未送达（网络/代理）：{failure.detail}",
            action=failure.action,
            retryable=True,
            detail=failure.detail,
        )
    if failure.kind == "truncated":
        return ChatFailure(
            502,
            code="ai_truncated",
            reason="truncated",
            kind="truncated",
            message=failure.message,
            action=failure.action,
            retryable=False,
            detail=failure.detail,
        )
    if failure.kind == "format":
        return ChatFailure(
            502,
            code="ai_format",
            reason="format",
            kind="format",
            message=failure.message,
            action=failure.action,
            retryable=True,
            detail=failure.detail,
        )
    return ChatFailure(
        502,
        code="ai_internal",
        reason="internal",
        kind="internal",
        message=failure.message or "AI 调用失败",
        action=failure.action,
        retryable=False,
        detail=failure.detail,
    )


# --------------------------------------------------------------------------- #
# 流式传输层（QUOT-581 / 任务书 6.3-6.4 / 9.1）
#
# 块协议以阶段 A 的 `ladderstock.ai.answer_blocks` 为唯一实现（PI 2026-11-12 拍板 A）：
# 块只由 `build_blocks(metrics, ai, sources, failure)` 装配，上限 `MAX_BLOCKS`（D2 = 6）。
#
# 6.4 第 5 条「整轮失败 = 只有一条 notice、reply 为空」**只约束非流式路径**（`handle`
# → `_failure_body`，逐字未改）。流式路径走「失败半流」：已发块保留、不追加 notice、
# `done` 带 `completed: false` + `reason`（PI 2026-11-12 拍板）。
#
# 生成与连接解耦（断开不停、跑完落库）不在本 PR 做，钩子见 `start_detached_generation`。
# --------------------------------------------------------------------------- #

#: 阶段 A 的块上限（D2 = 6）；`answer_blocks.MAX_BLOCKS` 是唯一来源
MAX_ANSWER_BLOCKS = answer_blocks.MAX_BLOCKS

#: `done.followups` 的顺序：本轮应发而未发的块按此顺序列出
_FOLLOWUP_ORDER: tuple[str, ...] = (
    answer_blocks.BLOCK_METRIC,
    answer_blocks.BLOCK_CHART,
    answer_blocks.BLOCK_TABLE,
    answer_blocks.BLOCK_ACTION,
)


def answer_block_payloads(
    *,
    ai: Mapping[str, Any] | None,
    sources: Sequence[Mapping[str, Any]] = (),
    metrics: Mapping[str, Any] | None = None,
    failure: ChatFailure | None = None,
    page_data: bool = True,
) -> list[dict[str, Any]]:
    """调阶段 A 的 `build_blocks()`；`ChatFailure.kind` 就是 `NOTICE_KINDS` 的取值。

    不在这里临时拼结构化 `ai`：它的形状由上游适配给出（QUOT-579，把 reply 聚成
    `narrative / highlights / risks / evidence`）。装配失败按 `format` 失败上抛，
    由流式路径收成 half-stream，不猜假结构顶上（红线 3）。

    `page_data` 透传给 `build_blocks`：来源为空时的说明措辞与非流式 `_source_note`
    同一套（带了页内数据 ≠ 完全没有数据），流式这里再多出一句就成了重复。
    """
    try:
        return answer_blocks.build_blocks(
            dict(metrics or {}),
            ai,
            list(sources),
            failure=failure,
            page_data=page_data,
        )
    except answer_blocks.AnswerBlockError as exc:
        raise ChatFailure(
            502,
            code="ai_format",
            reason="format",
            kind="format",
            message=f"答案块装配失败：{sanitize(str(exc), max_len=200)}",
            retryable=False,
        ) from None


def _followups(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """本轮没发的块按固定顺序如实列出，不编造。"""
    emitted = {str(block.get("type")) for block in blocks}
    return [kind for kind in _FOLLOWUP_ORDER if kind not in emitted]


def _without_source_note(turn: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> str:
    """`run_turn` 把来源说明拼在 reply 尾部（非流式契约）；流式已把它出成 notice 块。

    只在同一句确实作为块发出时才从 reply 尾部摘掉，其余情况原样返回——不猜。
    会话里落账的那份仍是完整文本，与非流式一致。
    """
    reply = str(turn["reply"])
    note = _source_note(has_data=bool(turn["has_page_data"]), sources=turn["sources"])
    if not note:
        return reply
    emitted = any(
        block.get("type") == answer_blocks.BLOCK_NOTICE
        and block["payload"].get("message") == note
        for block in blocks
    )
    suffix = f"\n\n{note}"
    if emitted and reply.endswith(suffix):
        return reply[: -len(suffix)]
    return reply


def sse_event(event: str, data: Mapping[str, Any]) -> str:
    """SSE 帧：`event: <name>` + 单行 `data:` JSON + 空行收尾（`curl -N` 可逐帧观察）。"""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


def _block_event(seq: int, block: Mapping[str, Any]) -> str:
    """`seq` 放在**事件层**（与 `type` / `payload` 平级），不进 payload（任务书 6.3）。"""
    return sse_event(
        "block", {"seq": seq, "type": block["type"], "payload": block["payload"]}
    )


async def start_detached_generation(*args: Any, **kwargs: Any) -> None:
    """「生成与连接解耦」钩子：断开不停、跑完落库。

    PI 2026-11-12：本 PR 只留接口，空实现。落地归 QUOT-583（先修 `await_turn`
    的断开信号——实测 7 次探针只有 1 次命中 `is_disconnected`）。
    """
    # TODO(QUOT-583): 连接断开后让生成继续跑完并落库；重连按 last_seq 续推。
    return None


#: 定稿前缀切分：与 assistant.py `_SENTENCE_RE` 同一套句界（快照装配同源）
_FINALIZED_SENTENCE_RE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?[ \t]*")


def _stream_status(
    action: str,
    item: str,
    state: str,
    *,
    item_id: str | None = None,
    elapsed_ms: int | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> str:
    """`event: status` 动作流水账帧（PI 2026-11-13 拍板）：动作级 running/done 对。"""
    data: dict[str, Any] = {"action": action, "item": item, "state": state}
    if item_id is not None:
        data["item_id"] = item_id
    if elapsed_ms is not None:
        data["elapsed_ms"] = elapsed_ms
    if actions is not None:
        data["actions"] = actions
    return sse_event("status", data)


def _heartbeat(seq_frame_ms: int, last_seq: int) -> str:
    return sse_event(
        "heartbeat",
        {
            "gap_ms": seq_frame_ms,
            "last_seq": last_seq,
            "note": "模型仍在生成，连接保持",
        },
    )


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _finalized_prefix(text: str) -> str:
    """已定稿文本前缀：只保留句末标点收尾的完整句（与 reply_to_structured 同界）。"""
    parts = _FINALIZED_SENTENCE_RE.findall(text)
    ends = ("。", "！", "？", "；", "!", "?", ";", "\n")
    return "".join(part for part in parts if part and part.endswith(ends)).rstrip()



class AnswerBlockDivergence(Exception):
    """定稿前缀的快照与已发块不一致：前缀稳定性假设被打破（内部错误）。"""


def _new_finalized_blocks(
    finalized: str,
    sources: list[dict[str, Any]],
    has_page_data: bool,
    metrics: Mapping[str, Any] | None,
    sent: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对定稿前缀做快照装配，diff 出**新增**的稳定块（同步版，消费循环用）。

    `build_blocks` 是唯一装配入口；流式序列必须是非流式序列的字节前缀
    （B2 断言），所以已发块如果在新快照里变了形——说明前缀假设被打破——
    `AnswerBlockDivergence` 上抛，收成 `model_error` 半流，绝不回改已发块。
    尾块（citation / 来源 notice）依赖最终 sources：hold-back 到收尾统一发。
    """
    from ladderstock.web.assistant import reply_to_structured

    ai = reply_to_structured(finalized)["build_input"]
    snapshot = answer_block_payloads(
        ai=ai, sources=sources, metrics=metrics, page_data=has_page_data
    )
    stable = snapshot[:-1] if snapshot else snapshot
    for index, block in enumerate(stable[: len(sent)]):
        if json.dumps(block, ensure_ascii=False, sort_keys=True) != json.dumps(
            sent[index], ensure_ascii=False, sort_keys=True
        ):
            raise AnswerBlockDivergence(index)
    return stable[len(sent):]


async def stream_turn(
    *,
    message: str,
    session_id: str,
    context: ChatRequestContext | None = None,
    timeout_sec: float | None = None,
    config_root: Any = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ai: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> AsyncIterator[str]:
    """逐帧产出 SSE 文本；与非流式 `run_turn` 同一条计算链、同一份会话落账。

    块由 `answer_block_payloads()`（阶段 A `build_blocks`）装配：首块必为
    `conclusion`，总数 ≤ `MAX_ANSWER_BLOCKS`；`seq` 从 1 递增不跳号，`done`
    带 `last_seq` / `completed` / `elapsed_ms` / 累计 `actions` 摘要。

    真流式（PI 2026-11-13 拍板）：

    - status 是**动作流水账**：context / history / model / assemble / persist，
      每条动作 running→done 一对，done 帧带真实 `elapsed_ms`（计时器包在
      真实调用点两侧）；model 动作带真实模型名。
    - HTTP 通道走 `complete_iter`（stream:true）逐段取增量：按句界定稿前缀、
      快照 diff 出新增块逐个 `event: block` 发出，流式序列是非流式序列的
      **字节前缀**（B2 断言）；已发块若在新快照中变形 → `AnswerBlockDivergence`
      → `model_error` 半流，绝不回改已发块。来源尾块（citation / 来源 notice）
      依赖最终 sources，收尾统一发。
    - 任何 ≥2s 无帧补 `event: heartbeat`（消费循环内 0.5s 醒一次，无线程）。
    - CLI 通道物理上是一次性子进程：走 `complete` 降级，一次性装配后逐块发出，
      首块与 done 标 `stream_mode: "degraded"`。
    - 中途模型失败：已发块保留、不追加 notice，
      `done{completed:false, reason:"model_error"}`（reason 词表新增值，
      NOTICE_KINDS 不变）。

    失败半流（PI 2026-11-12）：已发块保留、不追加 notice，`done` 带
    `completed: false` + `reason`；6.4 第 5 条只约束非流式路径。
    """
    seq = 0
    stream_mode: str | None = None
    sent_blocks: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    model_label = ""

    def _done_frame(payload: dict[str, Any]) -> str:
        data: dict[str, Any] = {
            "session_id": session_id,
            "actions": list(actions),
            **payload,
        }
        if stream_mode is not None:
            data["stream_mode"] = stream_mode
        return sse_event("done", data)

    def _half_stream_done(
        reason: str, failure: ChatFailure | None = None
    ) -> list[str]:
        data: dict[str, Any] = {
            "ok": False,
            "completed": False,
            "reply": "",
            "reason": reason,
            "last_seq": seq,
            "followups": _followups(sent_blocks),
        }
        if failure is not None:
            data["code"] = failure.code
            data["failure"] = failure.as_dict()
            data["retryable"] = failure.retryable
        return [_done_frame(data)]

    try:
        started = _monotonic_ms()
        yield _stream_status("received", "已收到请求，正在准备", "running", item_id="turn")
        text = (message or "").strip()
        if not text:
            raise ChatFailure(
                400,
                code="empty_message",
                reason="invalid_request",
                kind="internal",
                message="message 不能为空",
                retryable=False,
            )
        if len(text) > MAX_MESSAGE_CHARS:
            raise ChatFailure(
                400,
                code="message_too_long",
                reason="invalid_request",
                kind="internal",
                message=f"message 过长（上限 {MAX_MESSAGE_CHARS} 字）",
                retryable=False,
            )

        # ---- context（真实调用点：_record_sources / _context_block）---------
        context_started = _monotonic_ms()
        yield _stream_status("context", "读取页内上下文与来源", "running", item_id="context")
        sources = _record_sources(context)
        context_json, has_data = _context_block(context)
        timeout = effective_timeout(timeout_sec)
        actions.append(
            {
                "action": "context",
                "state": "done",
                "item_id": "context",
                "elapsed_ms": _monotonic_ms() - context_started,
            }
        )
        yield _stream_status(
            "context",
            "页内上下文与来源就绪",
            "done",
            item_id="context",
            elapsed_ms=actions[-1]["elapsed_ms"],
        )
        yield _stream_status(
            "received",
            "请求准备完成",
            "done",
            item_id="turn",
            elapsed_ms=_monotonic_ms() - started,
        )

        # ---- history（真实调用点：_history）---------------------------------
        history_started = _monotonic_ms()
        yield _stream_status("history", "读取会话历史", "running", item_id="session_history")
        history = _history(session_id)
        actions.append(
            {
                "action": "history",
                "state": "done",
                "item_id": "session_history",
                "elapsed_ms": _monotonic_ms() - history_started,
            }
        )
        yield _stream_status(
            "history",
            "会话历史就绪",
            "done",
            item_id="session_history",
            elapsed_ms=actions[-1]["elapsed_ms"],
        )

        # ---- model（真实调用点：complete_iter / complete）-------------------
        yield _stream_status("model", "正在选择通道", "running", item_id="model_call")
        channel = await asyncio.to_thread(load_channel, config_root)
        model_label = channel.label
        system, user = _build_messages(
            message=text,
            context=context,
            context_json=context_json,
            sources=sources,
            history=history,
        )
        yield _stream_status(
            "model", f"正在调用 {model_label}", "running", item_id="model_call"
        )
        model_started = _monotonic_ms()
        last_frame_ms = _monotonic_ms()

        incremental = channel.kind != "cli" and ai is None
        reply_parts: list[str] = []
        tail = ""
        if incremental:
            # 真流式消费循环：0.5s 醒一次；≥2s 无帧补 heartbeat；
            # 句界定稿前缀 → 快照 diff → 新块逐个发出（已发块不可变）。
            aiter = complete_iter(channel, system, user, timeout=timeout).__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        aiter.__anext__(), timeout=STREAM_HEARTBEAT_POLL_SEC
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    if _monotonic_ms() - last_frame_ms >= STREAM_HEARTBEAT_GAP_SEC * 1000:
                        yield _heartbeat(_monotonic_ms() - last_frame_ms, seq)
                        last_frame_ms = _monotonic_ms()
                    continue
                if is_disconnected is not None and await is_disconnected():
                    stream_mode = "stream"
                    yield _done_frame(
                        {
                            "ok": False,
                            "completed": False,
                            "reply": "",
                            "reason": "cancelled",
                            "last_seq": seq,
                            "followups": _followups(sent_blocks),
                        }
                    )
                    return
                reply_parts.append(chunk)
                tail += chunk
                finalized = _finalized_prefix(tail)
                if finalized:
                    for block in _new_finalized_blocks(
                        finalized, sources, has_data, metrics, sent_blocks
                    ):
                        seq += 1
                        sent_blocks.append(block)
                        yield _block_event(seq, block)
                    last_frame_ms = _monotonic_ms()
                    tail = tail[len(finalized):]
            # 结尾残句（无句末标点）：补一个换行让它定稿进最后一块
            if tail.strip():
                finalized = _finalized_prefix(tail + "\n")
                if finalized:
                    for block in _new_finalized_blocks(
                        finalized, sources, has_data, metrics, sent_blocks
                    ):
                        seq += 1
                        sent_blocks.append(block)
                        yield _block_event(seq, block)
            reply_text = "".join(reply_parts).strip()
            if not reply_text:
                raise ChatFailure(
                    502,
                    code="ai_empty",
                    reason="empty",
                    kind="internal",
                    message="AI 返回了空回答（模型未产出内容），请重试。",
                    action="重试一次；仍为空时检查通道配置",
                    retryable=True,
                )
            actions.append(
                {
                    "action": "model",
                    "state": "done",
                    "item_id": "model_call",
                    "elapsed_ms": _monotonic_ms() - model_started,
                }
            )
            yield _stream_status(
                "model",
                f"{model_label} 返回完成",
                "done",
                item_id="model_call",
                elapsed_ms=actions[-1]["elapsed_ms"],
            )
            stream_mode = "stream"
        else:
            # 降级路径：CLI 一次性子进程 / 上游已传结构化 ai——正文一次性取回
            try:
                reply_text = await complete(channel, system, user, timeout=timeout)
            except ChatAborted:
                raise
            except ChatFailure:
                raise
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - 与 run_turn 同口径分类
                raise _classify_exception(exc, channel) from None
            if not (reply_text or "").strip():
                raise ChatFailure(
                    502,
                    code="ai_empty",
                    reason="empty",
                    kind="internal",
                    message="AI 返回了空回答（模型未产出内容），请重试。",
                    action="重试一次；仍为空时检查本机 CLI 是否正常",
                    retryable=True,
                )
            reply_text = reply_text.strip()
            actions.append(
                {
                    "action": "model",
                    "state": "done",
                    "item_id": "model_call",
                    "elapsed_ms": _monotonic_ms() - model_started,
                }
            )
            yield _stream_status(
                "model",
                f"{model_label} 返回完成",
                "done",
                item_id="model_call",
                elapsed_ms=actions[-1]["elapsed_ms"],
            )
            stream_mode = "degraded"

        # 非流式契约的来源说明拼在 reply 尾部（会话落账完整），流式单独出块
        note = _source_note(has_data=has_data, sources=sources)
        answer = reply_text if not note else f"{reply_text}\n\n{note}"

        # ---- persist（真实调用点：_remember）--------------------------------
        persist_started = _monotonic_ms()
        yield _stream_status("persist", "保存本轮问答", "running", item_id="persist")
        _remember(session_id, text, answer)
        actions.append(
            {
                "action": "persist",
                "state": "done",
                "item_id": "persist",
                "elapsed_ms": _monotonic_ms() - persist_started,
            }
        )
        yield _stream_status(
            "persist",
            "本轮问答已保存",
            "done",
            item_id="persist",
            elapsed_ms=actions[-1]["elapsed_ms"],
        )

        # ---- assemble（真实调用点：reply_to_structured → build_blocks）-------
        assemble_started = _monotonic_ms()
        yield _stream_status("assemble", "装配回答块", "running", item_id="assemble")
        from ladderstock.web.assistant import reply_to_structured

        final_ai = ai if ai is not None else reply_to_structured(answer)["build_input"]
        try:
            blocks = answer_block_payloads(
                ai=final_ai,
                sources=sources,
                metrics=metrics,
                page_data=has_data,
            )
        except ChatFailure as failure:
            # 块没装配出来，这一问不算数：撤掉刚落的账
            _forget(session_id)
            yield _half_stream_done(failure.reason, failure)[0]
            return
        for block in blocks[len(sent_blocks):]:
            if is_disconnected is not None and await is_disconnected():
                yield _done_frame(
                    {
                        "ok": False,
                        "completed": False,
                        "reply": "",
                        "reason": "cancelled",
                        "last_seq": seq,
                        "followups": _followups(sent_blocks),
                    }
                )
                return
            seq += 1
            sent_blocks.append(block)
            yield _block_event(seq, block)
        actions.append(
            {
                "action": "assemble",
                "state": "done",
                "item_id": "assemble",
                "elapsed_ms": _monotonic_ms() - assemble_started,
            }
        )
        yield _stream_status(
            "assemble",
            "回答块装配完成",
            "done",
            item_id="assemble",
            elapsed_ms=actions[-1]["elapsed_ms"],
        )

        yield _done_frame(
            {
                "ok": True,
                "completed": True,
                "reply": _without_source_note(
                    {"reply": answer, "has_page_data": has_data, "sources": sources},
                    blocks,
                ),
                "model": model_label,
                "channel": channel.kind,
                "sources": sources,
                "pending_research": _pending_research(text),
                "elapsed_ms": _monotonic_ms() - started,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "last_seq": seq,
                "followups": _followups(blocks),
            },
        )

    except ChatAborted:
        # 连接没了：不补发任何帧（落账发生在 Abort 之前才会留下）
        return
    except ChatFailure as failure:
        if seq == 0 and not sent_blocks:
            # 首块前失败：失败 notice 仍走块协议（与非流式 6.4 第 5 条同源）
            for block in answer_block_payloads(ai=None, sources=[], failure=failure):
                seq += 1
                yield _block_event(seq, block)
            yield _done_frame(
                {
                    "ok": False,
                    "completed": True,
                    "reply": "",
                    "code": failure.code,
                    "reason": failure.reason,
                    "failure": failure.as_dict(),
                    "retryable": failure.retryable,
                    "last_seq": seq,
                    "followups": [],
                },
            )
            return
        # 已发块后失败（模型中途挂 / 装配失败）：半流——已发块保留、不追加 notice
        reason = failure.reason
        if failure.code in ("ai_timeout", "ai_upstream", "ai_network", "ai_quota"):
            reason = "model_error"
        yield _half_stream_done(reason, failure)[0]
        return
    except AnswerBlockDivergence:
        # 定稿前缀快照与已发块不一致：前缀稳定性假设被打破（内部错误）
        yield _half_stream_done("model_error", None)[0]
        return
    except BaseException:  # noqa: BLE001 - 不可分类异常同收半流，不吞
        yield _half_stream_done("model_error", None)[0]
        return


# --------------------------------------------------------------------------- #
# 一轮问答
# --------------------------------------------------------------------------- #

async def run_turn(
    *,
    message: str,
    session_id: str,
    context: ChatRequestContext | None = None,
    timeout_sec: float | None = None,
    config_root: Any = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """跑一轮问答；成功返回响应体，失败抛 `ChatFailure`。"""
    text = (message or "").strip()
    if not text:
        raise ChatFailure(
            400,
            code="empty_message",
            reason="invalid_request",
            kind="internal",
            message="message 不能为空",
            retryable=False,
        )
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatFailure(
            400,
            code="message_too_long",
            reason="invalid_request",
            kind="internal",
            message=f"message 过长（上限 {MAX_MESSAGE_CHARS} 字）",
            retryable=False,
        )

    sources = _record_sources(context)
    context_json, has_data = _context_block(context)
    timeout = effective_timeout(timeout_sec)
    channel = await asyncio.to_thread(load_channel, config_root)

    system, user = _build_messages(
        message=text,
        context=context,
        context_json=context_json,
        sources=sources,
        history=_history(session_id),
    )
    started = time.monotonic()
    try:
        reply = await await_turn(
            complete(channel, system, user, timeout=timeout),
            timeout=timeout,
            channel_kind=channel.kind,
            is_disconnected=is_disconnected,
        )
    except ChatFailure:
        raise
    except ChatAborted:
        raise
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - 分类后统一处理
        raise _classify_exception(exc, channel) from None

    answer = (reply or "").strip()
    if not answer:
        raise ChatFailure(
            502,
            code="ai_empty",
            reason="empty",
            kind="internal",
            message="AI 返回了空回答（模型未产出内容），请重试。",
            action="重试一次；仍为空时检查本机 CLI 是否正常",
            retryable=True,
        )

    note = _source_note(has_data=has_data, sources=sources)
    if note:
        answer = f"{answer}\n\n{note}"
    _remember(session_id, text, answer)

    return {
        "ok": True,
        "session_id": session_id,
        "reply": answer,
        "model": channel.label,
        "channel": channel.kind,
        "sources": sources,
        "has_page_data": has_data,
        "pending_research": _pending_research(text),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _response(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )


def _failure_body(session_id: str, failure: ChatFailure) -> dict[str, Any]:
    return {
        "ok": False,
        "session_id": session_id,
        "reply": "",
        "sources": [],
        "pending_research": [],
        "code": failure.code,
        "detail": failure.message,
        "retryable": failure.retryable,
        "failure": failure.as_dict(),
    }


async def handle(
    body: ChatRequest,
    request: Request | None = None,
    *,
    config_root: Any = None,
) -> JSONResponse:
    """路由入口：统一把成功 / 失败都落成 JSON（失败带状态码与 `code`）。"""
    try:
        session_id = resolve_session_id(body.session_id)
    except ChatFailure as exc:
        return _response(_failure_body(body.session_id or "", exc), exc.status)

    try:
        payload = await run_turn(
            message=body.message,
            session_id=session_id,
            context=body.context,
            timeout_sec=body.timeout_sec,
            config_root=config_root,
            is_disconnected=request.is_disconnected if request is not None else None,
        )
    except ChatAborted:
        aborted = ChatFailure(
            499,
            code="ai_aborted",
            reason="aborted",
            kind="internal",
            message="本轮已被前端中止",
            retryable=False,
        )
        return _response(_failure_body(session_id, aborted), aborted.status)
    except ChatFailure as exc:
        return _response(_failure_body(session_id, exc), exc.status)
    return _response(payload)


async def handle_stream(
    body: ChatRequest,
    request: Request | None = None,
    *,
    config_root: Any = None,
) -> StreamingResponse:
    """流式入口（QUOT-581）：`text/event-stream`，帧见 `stream_turn`。

    与 `handle` 共用同一条 `run_turn` 链与同一份会话记忆；默认路径仍是
    `handle`（非流式），本函数只由显式开关（`Accept: text/event-stream`）进入。
    """
    try:
        session_id = resolve_session_id(body.session_id)
    except ChatFailure as exc:
        return StreamingResponse(
            _failure_stream(body.session_id or "", exc),
            status_code=exc.status,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def frames() -> AsyncIterator[str]:
        async for frame in stream_turn(
            message=body.message,
            session_id=session_id,
            context=body.context,
            timeout_sec=body.timeout_sec,
            config_root=config_root,
            is_disconnected=request.is_disconnected if request is not None else None,
        ):
            yield frame

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _failure_stream(session_id: str, failure: ChatFailure) -> AsyncIterator[str]:
    seq = 0
    for block in answer_block_payloads(ai=None, sources=[], failure=failure):
        seq += 1
        yield _block_event(seq, block)
    yield sse_event(
        "done",
        {
            "ok": False,
            "completed": True,
            "session_id": session_id,
            "reply": "",
            "code": failure.code,
            "reason": failure.reason,
            "failure": failure.as_dict(),
            "retryable": failure.retryable,
            "last_seq": seq,
            "followups": [],
        },
    )


def wants_stream(request: Request) -> bool:
    """显式开关：**只认** `Accept: text/event-stream`；缺省 / 其它取值一律非流式。"""
    values = request.headers.get("accept", "")
    return any(
        part.split(";", 1)[0].strip().lower() == "text/event-stream"
        for part in values.split(",")
    )


async def dispatch(
    body: ChatRequest,
    request: Request | None = None,
    *,
    config_root: Any = None,
) -> Response:
    """统一入口：`Accept: text/event-stream` 走 SSE，其余走既有 JSON（零改动）。"""
    if request is not None and wants_stream(request):
        return await handle_stream(body, request, config_root=config_root)
    return await handle(body, request, config_root=config_root)


__all__ = [
    "DEFAULT_TIMEOUT_SEC",
    "DISCONNECT_POLL_SEC",
    "MAX_ANSWER_BLOCKS",
    "MAX_CONTEXT_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_SESSIONS",
    "MAX_SESSION_TURNS",
    "MAX_SOURCES",
    "SESSION_TTL_SEC",
    "SYSTEM_PROMPT",
    "TIMEOUT_ENV",
    "Channel",
    "ChatAborted",
    "ChatFailure",
    "ChatRequest",
    "ChatRequestContext",
    "ChatSource",
    "answer_block_payloads",
    "await_turn",
    "complete",
    "dispatch",
    "effective_timeout",
    "handle",
    "handle_stream",
    "load_channel",
    "reset_sessions",
    "resolve_session_id",
    "run_turn",
    "sse_event",
    "start_detached_generation",
    "stream_turn",
    "timeout_limit",
    "wants_stream",
]
