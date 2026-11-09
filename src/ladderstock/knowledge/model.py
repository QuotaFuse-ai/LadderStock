"""标的档案的数据模型：frontmatter、状态判定、有效期归一。

契约照参考实现（vibe-astock `knowledge/`）：

- `manifest.json`：key = `market:symbol`（market ∈ SH | SZ | BJ），
  value = `{dir, as_of, status}`；
- 档案模板 frontmatter：`schema_version / symbol / market / name / as_of /
  status(fresh|stale|refuted) / valid_days（默认 90）/ sources`。

状态判定的唯一口径在这里：`refuted` 只能由显式反证写入（不自动推断）；
`stale` 可以是档案自带的（数据过期的运行归档），也可以由
`as_of + valid_days` 与当日（Asia/Shanghai）比较得出；其余为 `fresh`。

⚠️ frontmatter 来自用户数据区的文件 = 不可信输入：`valid_days` 可能是
`1e100 / -1 / "abc"`。不夹紧的话日期加法会抛 `OverflowError`，所以
`normalize_valid_days` 是必经之路（同参考实现 knowledge.ts 的教训）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

SCHEMA_VERSION = 1

#: 档案级默认有效期（天）；条目级可在表格「有效期(天)」列覆盖。
DEFAULT_VALID_DAYS = 90

#: `valid_days` 的合法区间：超出即回落到默认值（不夹紧，防止 1e100 这类值）。
MIN_VALID_DAYS = 1
MAX_VALID_DAYS = 3650

STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_REFUTED = "refuted"
STATUSES: tuple[str, ...] = (STATUS_FRESH, STATUS_STALE, STATUS_REFUTED)

#: 市场枚举：与 manifest / 模板一致（SH | SZ | BJ）。
MARKETS: tuple[str, ...] = ("SH", "SZ", "BJ")

#: symbol 合法形态：6 位数字（A 股代码）。
SYMBOL_PATTERN = re.compile(r"^\d{6}$")

_FRONTMATTER = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?")


class KnowledgeError(ValueError):
    """档案层的输入错误：market/symbol 非法、frontmatter 缺关键字段等。"""


def sh_today(now: datetime | None = None) -> date:
    """Asia/Shanghai 的当日日期（状态判定与归档的统一时钟）。"""
    moment = now or datetime.now(UTC)
    return (moment + timedelta(hours=8)).date()


def normalize_market(raw: Any) -> str:
    """market 只接受 SH | SZ | BJ（大小写宽容），其余一律拒绝。"""
    text = str(raw or "").strip().upper()
    if text not in MARKETS:
        raise KnowledgeError(
            f"market 非法：{raw!r}（只接受 {' | '.join(MARKETS)}）"
        )
    return text


def normalize_symbol(raw: Any) -> str:
    """symbol 只接受 6 位数字；带交易所后缀（`600000.SH`）先剥离。"""
    text = str(raw or "").strip().upper()
    if text.endswith((".SH", ".SZ", ".BJ")):
        text = text[:-3]
    if not SYMBOL_PATTERN.fullmatch(text):
        raise KnowledgeError(f"symbol 非法：{raw!r}（只接受 6 位数字代码）")
    return text


def normalize_valid_days(raw: Any, fallback: int = DEFAULT_VALID_DAYS) -> int:
    """`valid_days` 归一：非法 / 越界（1–3650 之外）回落到 fallback。"""
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return fallback
    if MIN_VALID_DAYS <= value <= MAX_VALID_DAYS:
        return value
    return fallback


def normalize_status(raw: Any, *, strict: bool = True) -> str:
    """status 只接受 fresh | stale | refuted；strict=False 时非法值回落 fresh。"""
    text = str(raw or "").strip().lower()
    if text in STATUSES:
        return text
    if strict:
        raise KnowledgeError(f"status 非法：{raw!r}（只接受 {' | '.join(STATUSES)}）")
    return STATUS_FRESH


def parse_frontmatter(text: str) -> dict[str, str]:
    """极简 frontmatter 解析（同参考实现）：`---` 块内的 `key: value` 行。

    只取字符串值，去掉行内注释与首尾引号；嵌套结构不用——档案的
    `sources` 清单由归档器渲染成 YAML 行内列表（flow style）。
    """
    match = _FRONTMATTER.match(text or "")
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        idx = line.find(":")
        if idx <= 0:
            continue
        key = line[:idx].strip()
        value = line[idx + 1 :].strip()
        value = re.sub(r"\s+#.*$", "", value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def strip_frontmatter(text: str) -> str:
    """去掉 frontmatter，只留正文（召回文本不含元数据块）。"""
    return _FRONTMATTER.sub("", text or "", count=1)


@dataclass
class Dossier:
    """一份标的档案：frontmatter 的关键字段 + 正文。"""

    symbol: str
    market: str
    name: str = ""
    as_of: str = "1970-01-01"
    status: str = STATUS_FRESH
    valid_days: int = DEFAULT_VALID_DAYS
    sources: list[dict[str, Any]] = field(default_factory=list)
    body: str = ""

    def computed_status(self, today: date | None = None) -> str:
        """加载时判定的最终状态：refuted 不改，stale 保持，fresh 按日期复核。

        `refuted` 只能由显式反证写入，这里永不产生；`stale` 可能是档案自带，
        也可能由 `as_of + valid_days` 过期得出。
        """
        if self.status == STATUS_REFUTED:
            return STATUS_REFUTED
        if self.status == STATUS_STALE:
            return STATUS_STALE
        today = today or sh_today()
        try:
            as_of = date.fromisoformat(self.as_of)
        except ValueError:
            return STATUS_STALE
        expiry = as_of + timedelta(days=self.valid_days)
        return STATUS_STALE if today > expiry else STATUS_FRESH

    def is_recalled(self) -> bool:
        """召回资格：refuted 档案不出现在任何召回里。"""
        return self.status != STATUS_REFUTED

    def render_frontmatter(self) -> str:
        lines = [
            "---",
            f"schema_version: {SCHEMA_VERSION}",
            f'symbol: "{self.symbol}"',
            f"market: {self.market}",
            f"name: {self.name or self.symbol}",
            f"as_of: {self.as_of}",
            f"status: {self.status}",
            f"valid_days: {self.valid_days}",
            "sources: " + _flow_list(self.sources),
            "---",
        ]
        return "\n".join(lines)

    def render(self) -> str:
        """整份档案文本：frontmatter + 正文（`sources` 为空时渲染 `[]`）。"""
        return self.render_frontmatter() + "\n" + (self.body or "") + (
            "" if (self.body or "").endswith("\n") else "\n"
        )


def _flow_list(items: list[dict[str, Any]]) -> str:
    """把来源清单渲染成 YAML flow style（frontmatter 解析器只认单行值）。"""
    if not items:
        return "[]"
    parts = []
    for item in items:
        inner = ", ".join(
            f'{key}: "{value}"' for key, value in sorted(item.items())
        )
        parts.append("{" + inner + "}")
    return "[" + ", ".join(parts) + "]"


def dossier_from_text(text: str) -> Dossier:
    """从档案文本构建 Dossier；frontmatter 缺 symbol/market 即拒绝。"""
    fm = parse_frontmatter(text)
    if not fm:
        raise KnowledgeError("档案缺少 frontmatter")
    symbol = fm.get("symbol")
    market = fm.get("market")
    if not symbol or not market:
        raise KnowledgeError("frontmatter 缺 symbol / market")
    return Dossier(
        symbol=normalize_symbol(symbol),
        market=normalize_market(market),
        name=fm.get("name", ""),
        as_of=fm.get("as_of") or "1970-01-01",
        status=normalize_status(fm.get("status"), strict=False),
        valid_days=normalize_valid_days(fm.get("valid_days")),
        sources=[],
        body=strip_frontmatter(text),
    )


__all__ = [
    "DEFAULT_VALID_DAYS",
    "Dossier",
    "KnowledgeError",
    "MARKETS",
    "MAX_VALID_DAYS",
    "SCHEMA_VERSION",
    "STATUSES",
    "STATUS_FRESH",
    "STATUS_REFUTED",
    "STATUS_STALE",
    "SYMBOL_PATTERN",
    "dossier_from_text",
    "normalize_market",
    "normalize_status",
    "normalize_symbol",
    "normalize_valid_days",
    "parse_frontmatter",
    "sh_today",
    "strip_frontmatter",
]
