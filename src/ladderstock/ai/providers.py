"""最小 provider 适配层：只抹平协议差异（QUOT-236）。

两个协议：

- `openai`：`POST {base}/chat/completions`，`Authorization: Bearer`，
  `response_format: json_object` 开 JSON 模式，正文在 `choices[0].message.content`；
- `anthropic`：`POST {base}/v1/messages`，`x-api-key` + `anthropic-version`，
  `system` 在顶层、正文是 content blocks，没有 `response_format`
  （JSON 模式改为在 system 里追加一句要求）。

适配层**不**决定重试 / 换模型 / 降级——那些策略仍在 `orchestrator`。
这里只把上游响应翻译成正文，把 HTTP 状态 / 截断标记翻译成可分类的异常：
`ProviderError.status_code` 供 `error_taxonomy.classify_error` 归类，
消息里的 `finish_reason=length` / `stop_reason=max_tokens` 命中截断规则。
"""

from __future__ import annotations

from typing import Any

import httpx

from ladderstock.ai.fallback import ModelTarget

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
JSON_ONLY_INSTRUCTION = "只输出一个合法 JSON 对象，不要输出 Markdown 代码块或任何解释。"


class UnconfiguredError(RuntimeError):
    """缺少模型地址或密钥：属于本地配置问题，不是网络故障。"""


class ProviderError(RuntimeError):
    """上游返回了非 2xx 或截断响应；`status_code` 供失败分类读取。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _credentials(target: ModelTarget) -> tuple[str, str]:
    backend = target.backend
    if not backend.base_url or not backend.api_key:
        raise UnconfiguredError(
            f"未配置模型凭据（{backend.base_url_env} / {backend.api_key_env}）"
        )
    return backend.base_url, backend.api_key


def _post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout, transport=transport) as client:
        response = client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        # 错误正文只留前 240 字：足够定位，又不会把大段上游内容写进日志
        body = (response.text or "").strip().replace("\n", " ")[:240]
        raise ProviderError(
            f"HTTP {response.status_code}: {body}" if body else f"HTTP {response.status_code}",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError:
        raise ValueError("上游响应不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("上游响应不是 JSON 对象")
    return data


def _require_text(text: Any, location: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"模型响应缺少 {location} 或内容为空")
    return text


class OpenAiProvider:
    """OpenAI 兼容 `/chat/completions`。"""

    kind = "openai"

    def complete(
        self,
        target: ModelTarget,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> str:
        base_url, api_key = _credentials(target)
        payload: dict[str, Any] = {
            "model": target.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        data = _post(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            headers,
            timeout=timeout,
            transport=transport,
        )
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if isinstance(first, dict) and first.get("finish_reason") == "length":
            raise ProviderError("finish_reason=length（输出达到长度上限）")
        return _require_text(text, "choices[0].message.content")


class AnthropicProvider:
    """Anthropic Messages API（`/v1/messages`）。"""

    kind = "anthropic"

    def complete(
        self,
        target: ModelTarget,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> str:
        base_url, api_key = _credentials(target)
        base = base_url.rstrip("/")
        url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        instructions = f"{system}\n\n{JSON_ONLY_INSTRUCTION}" if json_mode else system
        payload: dict[str, Any] = {
            "model": target.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "system": instructions,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.2,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        data = _post(url, payload, headers, timeout=timeout, transport=transport)
        if data.get("stop_reason") == "max_tokens":
            raise ProviderError("stop_reason=max_tokens（输出达到长度上限）")
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ValueError("模型响应缺少 content blocks")
        parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return _require_text("\n".join(parts), "content blocks")


PROVIDERS: dict[str, type[OpenAiProvider] | type[AnthropicProvider]] = {
    "openai": OpenAiProvider,
    "anthropic": AnthropicProvider,
}


def provider_for(kind: str) -> OpenAiProvider | AnthropicProvider:
    try:
        return PROVIDERS[kind]()
    except KeyError:
        raise ValueError(f"不支持的 provider 类型：{kind!r}") from None


__all__ = [
    "ANTHROPIC_VERSION",
    "AnthropicProvider",
    "OpenAiProvider",
    "PROVIDERS",
    "ProviderError",
    "UnconfiguredError",
    "provider_for",
]
