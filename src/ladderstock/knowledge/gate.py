"""写入前的合规与隐私 gate：疑似密钥形态、越权内容一律拒绝。

归档正文会长期留在档案里、并会在后续召回中注入提示词——密钥一旦落进去，
每次召回都在重复泄露。所以写入前扫描，命中即拒绝并让调用方记事件，
绝不「过滤后继续写」：过滤永远会漏，拒绝才不漏。

隐私字段（journal / trades / positions 等）复用 `core/privacy.PRIVATE_KEYS`
的同一份集合，键命中即拒——档案是研究知识层，持仓与交易记录不该进去。
"""

from __future__ import annotations

import re
from typing import Any

from ladderstock.core.privacy import PRIVATE_KEYS

#: 疑似密钥形态（够宽，宁可误伤也不放进档案）：
#: - `sk-` / `sk-proj-` 开头的 OpenAI 形态
#: - `ghp_` / `gho_` / `github_pat_` 的 GitHub 形态
#: - `AKIA` 的 AWS 形态
#: - `Bearer ` 后跟长 token
#: - 32+ 位十六进制串（通用凭据熵）
#: - `xoxb-` / `xapp-` 的 Slack 形态
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)

_PRIVATE_SET = frozenset(key.lower() for key in PRIVATE_KEYS)


class GateViolation(ValueError):
    """gate 命中：正文或键里含疑似密钥 / 隐私字段，写入被拒绝。"""

    def __init__(self, reason: str, hits: list[str]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.hits = hits


def find_secret_hits(text: str) -> list[str]:
    """返回命中的密钥形态清单（只报形态名，不回显命中内容本身）。"""
    if not text:
        return []
    names: list[str] = []
    labels = (
        "openai-like (sk-)",
        "github-token",
        "github-pat",
        "aws-access-key",
        "slack-token",
        "bearer-token",
        "hex-blob (32+ hex chars)",
    )
    for pattern, label in zip(SECRET_PATTERNS, labels, strict=True):
        if pattern.search(text):
            names.append(label)
    return names


def find_private_keys(payload: Any) -> list[str]:
    """递归找出命中隐私字段的键名（复用 `core/privacy` 的集合）。"""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                name = str(key)
                if name.lower() in _PRIVATE_SET and name not in found:
                    found.append(name)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def gate_content(text: str) -> None:
    """正文 gate：疑似密钥命中即拒绝（`GateViolation`）。"""
    hits = find_secret_hits(text)
    if hits:
        raise GateViolation(
            "归档正文命中疑似密钥形态，已拒绝写入：" + "、".join(hits),
            hits,
        )


def gate_payload(payload: Any, text: str | None = None) -> None:
    """结构化载荷 + 可选渲染文本一起过 gate。"""
    keys = find_private_keys(payload)
    if keys:
        raise GateViolation(
            "归档载荷命中隐私字段，已拒绝写入：" + "、".join(keys),
            keys,
        )
    if text is not None:
        gate_content(text)


__all__ = [
    "GateViolation",
    "SECRET_PATTERNS",
    "find_private_keys",
    "find_secret_hits",
    "gate_content",
    "gate_payload",
]
