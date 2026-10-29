"""AI 失败四分类：网络 / 额度 / 格式 / 截断。

文档要求（M1）：AI 结构化输出失败时，用户必须看到**可行动的根因**，
而不是「AI 环节没跑通」的模糊占位；服务端日志保留完整异常栈，
前端只拿脱敏摘要。

本模块只做分类与脱敏，不发请求、不重试；M3 的编排状态机复用
`classify_error()` 决定是否 fallback / retry，直接使用 `AiFailure.retryable`。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# 四类面向用户的失败 + 一个兜底（真正的未预期异常走 internal，不伪装成四类之一）
FailureKind = Literal["network", "quota", "format", "truncated", "internal"]

# 供测试 / UI 枚举的稳定顺序
FAILURE_KINDS: tuple[str, ...] = ("network", "quota", "format", "truncated")

_ACTION: dict[str, str] = {
    "network": "检查网络或代理后重试；持续失败时切换到备用模型，或回放本地缓存结果",
    "quota": "额度或频率受限：换 fallback 模型或稍后重试（检查账户额度与并发上限）",
    "format": "重试一次；仍失败则切换到支持 JSON 模式 / tool_choice 的模型，并检查输出 Schema",
    "truncated": "提高 max_tokens 或缩小单段输入后重试；长文分段生成，避免一次性截断",
    "internal": "保留完整日志并检查模型密钥 / 权限配置（走环境变量，勿写入配置）",
}

_RETRYABLE: dict[str, bool] = {
    "network": True,
    "quota": True,
    "format": True,
    # 截断不是临时故障，原样重试只会再截断：参数调整后才值得重试
    "truncated": False,
    # 未预期异常（含鉴权 / 配置错误）不应自动重试
    "internal": False,
}

_FINISH_TRUNCATED = frozenset({"length", "max_tokens", "max_output_tokens", "truncated"})

_TRUNCATED_HINTS = (
    "finish_reason=length",
    "finish_reason: length",
    "finish_reason':'length",
    "finish_reason\":\"length",
    "stop_reason=max_tokens",
    "max_tokens",
    "output truncated",
    "truncated output",
    "输出截断",
    "内容截断",
)

_QUOTA_HINTS = (
    "429",
    "rate limit",
    "ratelimit",
    "too many requests",
    "quota",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "额度",
    "限流",
    "频率受限",
)

_NETWORK_HINTS = (
    "timeout",
    "timed out",
    "connecterror",
    "connection error",
    "connection reset",
    "connection refused",
    "network",
    "dns",
    "ssl",
    "eof",
    "peer closed",
    "超时",
    "连接失败",
    "网络",
)

_FORMAT_HINTS = (
    "json",
    "schema",
    "validationerror",
    "parse",
    "decode",
    "结构化输出",
    "解析失败",
    "格式",
)

_NETWORK_STATUS = frozenset({408, 425, 500, 502, 503, 504, 522, 524})
_AUTH_STATUS = frozenset({401, 403})


@dataclass(frozen=True)
class AiFailure:
    """一次 AI 失败的分类结果（可直接序列化给前端）。"""

    kind: FailureKind
    message: str
    action: str
    retryable: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "action": self.action,
            "retryable": self.retryable,
            "detail": self.detail,
        }


def classify_error(
    exc_or_response: Any,
    *,
    schema: Mapping[str, Any] | None = None,
    required_keys: Sequence[str] | None = None,
    max_detail: int = 240,
) -> AiFailure:
    """把异常或模型响应归入四类失败，并给出可行动提示与脱敏摘要。

    支持的输入：
    - 异常对象（httpx / socket / json.JSONDecodeError / pydantic.ValidationError …）；
    - 模型响应（dict 或对象），可带 `status_code`、`finish_reason`、
      `choices[0].finish_reason`、`content` / `text` / `choices[0].message.content`；
    - 已解析内容字符串（非法 JSON / schema 不符即判 `format`）。

    `schema` 用 JSON Schema 校验结构化输出；`required_keys` 为轻量版
    （M3 编排只需判断关键字段是否缺失时用它）。绝不回传 key、完整 prompt。
    """
    status = _status_code(exc_or_response)
    finish = _finish_reason(exc_or_response)
    raw_text = _text_of(exc_or_response)
    error_message = _error_message(exc_or_response)
    # 分类信号只看异常类型与错误描述：模型正文里的数字/词（如“429 家下跌”）
    # 不能影响分类，否则正常复盘文本会被误判成额度错误。
    signal = " ".join(part for part in (_type_name(exc_or_response), error_message) if part)
    lowered = signal.lower()

    if finish in _FINISH_TRUNCATED:
        return _failure("truncated", error_message or f"finish_reason={finish}", max_detail)
    if _is_truncated_error(exc_or_response, lowered):
        return _failure("truncated", error_message or "输出达到长度上限", max_detail)

    if status == 429 or _contains_any(lowered, _QUOTA_HINTS):
        return _failure("quota", error_message or f"HTTP {status or '429'}", max_detail)

    if _is_network_error(exc_or_response, status, lowered):
        return _failure("network", error_message or _network_reason(status, lowered), max_detail)

    # 字符串入参：可能是 `str(exc)`，也可能就是模型正文。先按「异常描述」的
    # 高精度提示词判断，命中即分类；都不命中才继续走 JSON / Schema 判定。
    if _is_plain_text(raw_text) and isinstance(exc_or_response, (str, bytes, bytearray)):
        hinted = _plain_text_kind(raw_text or "")
        if hinted is not None:
            return _failure(hinted, error_message or (raw_text or ""), max_detail)

    if _is_format_error(exc_or_response, raw_text, schema, required_keys):
        return _failure(
            "format", error_message or "模型输出不是合法 JSON / 不符合 Schema", max_detail
        )

    if status in _AUTH_STATUS:
        return _failure("internal", error_message or f"HTTP {status} 鉴权失败", max_detail)

    if isinstance(exc_or_response, (str, bytes, bytearray)) and raw_text:
        # 纯文本输入：确实无法解析成结构化输出
        return _failure("format", "模型输出不是合法 JSON", max_detail)

    return _failure("internal", error_message or signal or "未归类的 AI 失败", max_detail)


# --------------------------------------------------------------------------- #
# 分类信号
# --------------------------------------------------------------------------- #

def _failure(kind: FailureKind, detail: str, max_detail: int) -> AiFailure:
    clean = sanitize(detail, max_len=max_detail)
    label = {
        "network": "网络请求失败",
        "quota": "额度或频率受限",
        "format": "结构化输出解析失败",
        "truncated": "输出被截断",
        "internal": "AI 调用未归类失败",
    }[kind]
    return AiFailure(
        kind=kind,
        message=f"{label}：{clean}" if clean else label,
        action=_ACTION[kind],
        retryable=_RETRYABLE[kind],
        detail=clean,
    )


def _is_network_error(value: Any, status: int | None, lowered: str) -> bool:
    if status in _NETWORK_STATUS:
        return True
    if _import_isinstance(value, "httpx", "TimeoutException"):
        return True
    if _import_isinstance(value, "httpx", "TransportError"):
        return True
    if isinstance(value, (TimeoutError, ConnectionError)):
        return True
    if isinstance(value, OSError) and _contains_any(
        lowered, ("timed out", "connect", "超时", "连接")
    ):
        return True
    name = _type_name(value).lower()
    if "timeout" in name or "connect" in name or "transport" in name:
        return True
    return _contains_any(lowered, _NETWORK_HINTS) and not _contains_any(lowered, _QUOTA_HINTS)


def _is_truncated_error(value: Any, lowered: str) -> bool:
    name = _type_name(value).lower()
    if "truncat" in name or "length" in name:
        return True
    # 只有出现 max_tokens / finish_reason 语境才算截断，避免把普通参数报错误判
    if any(hint in lowered for hint in _TRUNCATED_HINTS if hint != "max_tokens"):
        return True
    return "max_tokens" in lowered and _contains_any(
        lowered, ("finish", "stop", "length", "limit", "截断")
    )


def _is_format_error(
    value: Any,
    raw_text: str | None,
    schema: Mapping[str, Any] | None,
    required_keys: Sequence[str] | None,
) -> bool:
    if isinstance(value, (json.JSONDecodeError, UnicodeDecodeError)):
        return True
    name = _type_name(value)
    if name in {"ValidationError", "StructuredOutputError", "FormatError"}:
        return True
    if _import_isinstance(value, "pydantic", "ValidationError"):
        return True

    if raw_text is not None and raw_text.strip():
        parsed: Any = _NOT_PARSED
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            # 模型响应带了正文却不是 JSON → 结构化输出失败；
            # 字符串入参只在"像结构化输出"时才这么判，其余交给提示词 / 末位兜底。
            if not isinstance(value, (str, bytes, bytearray)) or _looks_like_structured_attempt(
                raw_text
            ):
                return True
        if parsed is not _NOT_PARSED:
            if required_keys:
                if not isinstance(parsed, Mapping):
                    return True
                if any(key not in parsed for key in required_keys):
                    return True
            if schema is not None:
                try:
                    from jsonschema import Draft202012Validator

                    if not Draft202012Validator(schema).is_valid(parsed):
                        return True
                except Exception:  # noqa: BLE001 - schema 本身有问题不算模型失败
                    pass

    lowered = " ".join(
        part for part in (_type_name(value), str(value)) if part
    ).lower()
    return _contains_any(lowered, ("invalid json", "jsondecode", "schema 不符", "schema mismatch"))


def _looks_like_structured_attempt(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(("{", "[")) and stripped.endswith(("}", "]"))


def _is_plain_text(text: str | None) -> bool:
    """字符串入参不是合法 JSON 时，才有资格当作「异常描述」参与提示词判定。

    合法 JSON 说明它是模型的结构化输出，正文内容（如“429 家下跌”）不能反过来
    影响错误分类。
    """
    if not text or not text.strip():
        return False
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return True
    return False


# 纯文本入参用的高精度提示词：刻意不含 "网络" / "超时" / "json" 这类在 A 股叙述里
# 也会出现的词（如“网络游戏板块”），避免把模型正文误判成调用失败。
_PLAIN_TEXT_SIGNALS: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
    (
        "truncated",
        (
            "finish_reason=length",
            "finish_reason: length",
            "output truncated",
            "truncated output",
            "输出截断",
            "内容截断",
        ),
    ),
    (
        "quota",
        (
            "http 429",
            "status 429",
            "code 429",
            "429 too many",
            "too many requests",
            "rate limit",
            "ratelimit",
            "insufficient_quota",
            "exceeded your current quota",
            "额度",
            "限流",
        ),
    ),
    (
        "network",
        (
            "timed out",
            "timeout",
            "connecterror",
            "connection refused",
            "connection reset",
            "connection aborted",
            "connection error",
            "name or service not known",
            "temporary failure in name resolution",
            "unreachable",
            "peer closed",
            "请求超时",
            "连接失败",
            "网络请求",
            "eof occurred",
        ),
    ),
)


def _plain_text_kind(text: str) -> FailureKind | None:
    lowered = text.lower()
    for kind, hints in _PLAIN_TEXT_SIGNALS:
        if _contains_any(lowered, hints):
            return kind
    return None


def _network_reason(status: int | None, lowered: str) -> str:
    if status:
        return f"HTTP {status}（上游不可用）"
    if "timed out" in lowered or "timeout" in lowered or "超时" in lowered:
        return "请求超时"
    return "连接失败"


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #

_KEY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # sk-xxx / sk-proj-xxx 等常见厂商 Key
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "***"),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 ***"),
    # api_key=xxx / "api_key": "xxx" / access_token: xxx
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)"
            r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',}&]+)"
        ),
        r"\1\2***",
    ),
    # URL query 里的 key=... / token=...
    (
        re.compile(r"(?i)([?&](?:key|token|api_key|access_token)=)[^&\s]+"),
        r"\1***",
    ),
)

_URL_QUERY = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")
_SECRET_ENV = re.compile(r"(?i)(key|token|secret|password)")


def sanitize(text: str, *, max_len: int = 240) -> str:
    """压成一行、去掉凭据与 URL 查询串，并截断到 `max_len`。

    规则：
    - 环境变量里名字含 KEY / TOKEN / SECRET / PASSWORD 的值一律打码（不回显）；
    - 常见 Key 形态、Bearer、`api_key=`、URL 里的 `?key=` 打码；
    - URL 查询串整体丢弃（避免把 key 带进前端）；
    - 紧凑空白并截断，不保留换行（前端单行展示）。
    """
    if not text:
        return ""
    cleaned = str(text)
    for name, value in os.environ.items():
        if len(value) >= 8 and _SECRET_ENV.search(name):
            cleaned = cleaned.replace(value, "***")
    cleaned = _URL_QUERY.sub(r"\1?…", cleaned)
    for pattern, replacement in _KEY_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


# --------------------------------------------------------------------------- #
# 响应 / 异常的鸭子类型取值
# --------------------------------------------------------------------------- #

_NOT_PARSED: Any = object()


def _status_code(value: Any) -> int | None:
    candidates = [value]
    if isinstance(value, Mapping):
        candidates += [value.get("response")]
    else:
        candidates.append(getattr(value, "response", None))
    for item in candidates:
        if item is None:
            continue
        if isinstance(item, Mapping):
            raw = item.get("status_code")
        else:
            raw = getattr(item, "status_code", None)
        if isinstance(raw, int):
            return raw
    return None


def _finish_reason(value: Any) -> str | None:
    finish = _get(value, "finish_reason") or _get(value, "stop_reason")
    if finish:
        return str(finish).lower()
    choices = _get(value, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        first = choices[0]
        finish = _get(first, "finish_reason") or _get(first, "stop_reason")
        if finish:
            return str(finish).lower()
    return None


def _text_of(value: Any) -> str | None:
    """取模型输出正文（不取 prompt / 请求体）。"""
    if isinstance(value, (str, bytes, bytearray)):
        return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value

    direct = _get(value, "content") or _get(value, "text") or _get(value, "output_text")
    if direct is not None and not isinstance(direct, (dict, list, tuple)):
        return str(direct)

    choices = _get(value, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        first = choices[0]
        message = _get(first, "message")
        for candidate in (
            _get(message, "content") if message is not None else None,
            _get(first, "text"),
        ):
            if candidate is not None:
                if isinstance(candidate, (list, tuple)):
                    parts = [
                        str(_get(part, "text"))
                        for part in candidate
                        if _get(part, "text") is not None
                    ]
                    if parts:
                        return "".join(parts)
                elif not isinstance(candidate, (dict,)):
                    return str(candidate)
    return None


def _error_message(value: Any) -> str:
    """只取错误描述，不拼整个请求对象（避免把 prompt 带出来）。"""
    for candidate in (_get(value, "error"), value):
        if candidate is None:
            continue
        if isinstance(candidate, Mapping):
            message = candidate.get("message") or candidate.get("detail")
            if message:
                return str(message)
            continue
        message = getattr(candidate, "message", None)
        if isinstance(message, str) and message:
            return message
    if isinstance(value, BaseException):
        return str(value)
    return ""


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _type_name(value: Any) -> str:
    return type(value).__name__


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(needle in text for needle in needles)


def _import_isinstance(value: Any, module: str, name: str) -> bool:
    try:
        mod = __import__(module, fromlist=[name])
    except ImportError:  # pragma: no cover - 可选依赖缺失
        return False
    cls = getattr(mod, name, None)
    return isinstance(value, cls) if isinstance(cls, type) else False
