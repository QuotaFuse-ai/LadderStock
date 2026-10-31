"""数字级引用：把出处渲染成角标 `[n]` / 〔未核实〕，并把「裸着的数字」判成审计失败。

三个纯函数，全部离线、不碰数据层（DEC-024：报告段 0 次取数，`--check` 也是）：

- `index_from_slot()`：`<stage>.json` 的 `provenance.sources[]` → 角标索引（`[n]` 的 n 就是序号）；
- `annotate()`：给叙述里的每个数字补角标（程序补，不劳模型——模型写引用等于让它自证清白）；
- `audit()`：读一份已经渲染好的 Markdown，逐个数值 token 判它有没有角标 / 〔未核实〕，
  以及角标指过去的来源记录与这个数字对不对得上（伪造引用的形态）。

豁免名单（`EXEMPTIONS`）是显式的一份，不是「看着不像数」的感觉：日期、时刻、封板时间
（`HHMMSS` 原样串）、证券代码、序号、百分位、版本号——`docs/specs/METRICS.md` §4.1
规定「原样字符串」的那几类都在这里，逐条写明理由。新增豁免要能说出它不是指标。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: 未核实标记：纯文本、可复制，与旧版看板同款语义
UNVERIFIED_MARK = "〔未核实〕"

#: 数值 token：一段连续数字（允许千分位与小数），前面不能再有数字
_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CITATION_RE = re.compile(r"^\[(\d+)\]")

#: 文末来源清单的小节标题（渲染与审计共用同一个字符串，避免两处漂移）
SOURCES_HEADING = "## 来源"

#: 豁免名单：命中即不要求角标。窗口整体覆盖 token 才算，`(?:...)` 里不放裸数字。
_EXEMPT_DATE = (
    r"\d{4}\s*[-/年]\s*\d{1,2}(?:\s*[-/月]\s*\d{1,2}\s*日?|年)?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日"
)
_EXEMPT_CODE = (
    r"\d{6}\s*(?:\.(?:SH|SZ|BJ)|开头的公司)"
    r"|(?:代码|标的)\s*[:：]?\s*\d{6}"
)
EXEMPTIONS: tuple[tuple[str, str], ...] = (
    ("角标本身", r"\[\d+\]"),
    ("日期", _EXEMPT_DATE),
    ("时刻", r"\d{1,2}:\d{2}(?::\d{2})?"),
    ("封板时间（HHMMSS 原样串，METRICS §4.1）", r"(?:封板|炸板|时间|盘口)\s*[:：]?\s*\d{6}"),
    ("证券代码（标识符不是指标）", _EXEMPT_CODE),
    ("序号（行首枚举与括注序号）", r"^\s*(?:[-*+]|\d+[.、)])\s|\(\d{1,3}\)|（\d{1,3}）"),
    ("序数与位次", r"第\s*\d{1,3}\s*(?:段|条|章|节|步|页|部分|项)|\b\d{1,2}(?:st|nd|rd|th)\b"),
    ("百分位记号", r"\d{1,3}\s*(?:个?\s*百分位|分位)|\b[Pp]\d{1,2}\b"),
    ("版本与决策号", r"[vV]\d+(?:\.\d+)*|\bR\d+\b|DEC-\d+|QUOT-\d+|§\d+(?:\.\d+)?"),
)
_COMPILED_EXEMPTIONS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.MULTILINE)) for name, pattern in EXEMPTIONS
)

#: 数值 token 允许的验证结果
_REASON_BARE = "无角标的数字"
_REASON_DANGLING = "角标指向不存在的来源"
_REASON_MISMATCH = "数字与角标记录的来源值不符"
_REASON_UNVERIFIED_CITATION = "角标指向未核实的来源"


@dataclass(frozen=True)
class Source:
    """`provenance.sources[]` 里的一条：`[n]` 的 n 就是它在列表里的序号（从 1 起）。"""

    index: int
    id: str
    dataset: str
    endpoint: str | None
    as_of: str
    url: str | None
    value: Any
    unit: str
    verified: bool
    metric: str
    stage: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "dataset": self.dataset,
            "endpoint": self.endpoint,
            "as_of": self.as_of,
            "url": self.url,
            "value": self.value,
            "unit": self.unit,
            "verified": self.verified,
            "metric": self.metric,
            "stage": self.stage,
        }

    def citation_line(self) -> str:
        """来源清单一行：`[n] 来源名 · endpoint · as_of · url`（未核实如实标出）。"""
        parts = [f"[{self.index}] {self.dataset}", self.endpoint or "-", self.as_of or "-",
                 self.url or "-"]
        line = " · ".join(parts)
        return line if self.verified else f"{line} · {UNVERIFIED_MARK}"


@dataclass(frozen=True)
class CitationIndex:
    """角标索引：值 → 来源（渲染时查），序号 → 来源（审计时查）。"""

    sources: tuple[Source, ...]

    def resolve(self, index: int) -> Source | None:
        return next((item for item in self.sources if item.index == index), None)

    def find(self, number: float) -> Source | None:
        """按数值找出处：同一个值有多条来源时取序号最小的一条（确定性，不按字典序猜）。"""
        candidates = [
            item
            for item in self.sources
            if _as_number(item.value) is not None and _as_number(item.value) == number
        ]
        return min(candidates, key=lambda item: item.index) if candidates else None

    def find_text(self, token: str) -> Source | None:
        number = _as_number(token)
        return self.find(number) if number is not None else None


def index_from_slot(slot: Mapping[str, Any] | None) -> CitationIndex:
    """`<stage>.json` 的 provenance 字段 → 角标索引；缺键按空索引处理。"""
    entries = list((slot or {}).get("sources") or [])
    sources: list[Source] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            continue
        params = entry.get("params") or {}
        sources.append(
            Source(
                index=int(entry.get("index") or position),
                id=str(entry.get("id") or f"S{position}"),
                dataset=str(entry.get("dataset") or entry.get("source") or ""),
                endpoint=entry.get("endpoint"),
                as_of=str(entry.get("as_of") or ""),
                url=entry.get("url"),
                value=entry.get("value"),
                unit=str(entry.get("unit") or ""),
                verified=bool(entry.get("verified")),
                metric=str(params.get("metric") or ""),
                stage=str(params.get("stage") or ""),
            )
        )
    return CitationIndex(tuple(sources))


# --------------------------------------------------------------------------- #
# token 扫描
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Token:
    """文本里的一段数字：`exempt` 非空表示它在豁免名单里，不要求角标。"""

    start: int
    end: int
    text: str
    exempt: str | None = None
    #: 紧邻右侧已有的标记（`[n]` 或 〔未核实〕），渲染时据此保持幂等
    marked: str | None = None

    @property
    def number(self) -> float | None:
        return _as_number(self.text)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%％").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _exempt_spans(line: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for name, pattern in _COMPILED_EXEMPTIONS:
        for match in pattern.finditer(line):
            if match.group(0):
                spans.append((match.start(), match.end(), name))
    return spans


#: 数字后面紧跟的量级/单位：角标要挂在单位之后（`37.2%[1]` 而不是 `37.2[1]%`）
_TRAILING_UNIT_RE = re.compile(r"^\s*(?:个百分点|%|％|[倍元亿万千天名个家点股手档位])")


def _mark_position(line: str, end: int) -> int:
    """标记的插入位置：跨过紧跟的单位，让角标落在「37.2%」整体之后。"""
    match = _TRAILING_UNIT_RE.match(line[end:])
    return end + match.end() if match else end


def _markers(line: str) -> dict[int, str]:
    """每个 token 结束位置后面紧跟的标记（`[n]` / 〔未核实〕），用于幂等渲染与审计。"""
    found: dict[int, str] = {}
    for match in _TOKEN_RE.finditer(line):
        stripped = line[_mark_position(line, match.end()):].lstrip(" ")
        citation = _CITATION_RE.match(stripped)
        if citation:
            found[match.end()] = f"[{citation.group(1)}]"
        elif stripped.startswith(UNVERIFIED_MARK):
            found[match.end()] = UNVERIFIED_MARK
    return found


def scan_line(line: str, identifiers: frozenset[str] = frozenset()) -> list[Token]:
    """一行之内的数值 token（含豁免判定与已有标记）。

    `identifiers` 是这份产物自己的标识符（证券代码等）：它们是名字不是指标，
    但形态上和 6 位数字指标无法区分，所以由调用方显式告知，不靠正则猜。
    """
    spans = _exempt_spans(line)
    markers = _markers(line)
    tokens: list[Token] = []
    for match in _TOKEN_RE.finditer(line):
        start, end = match.span()
        exempt = next(
            (name for lo, hi, name in spans if lo <= start and end <= hi),
            None,
        )
        if exempt is None and match.group(0) in identifiers:
            exempt = "标的标识符"
        tokens.append(
            Token(
                start=start,
                end=end,
                text=match.group(0),
                exempt=exempt,
                marked=markers.get(end),
            )
        )
    return tokens


def _identifier_keys(identifiers: Iterable[str]) -> frozenset[str]:
    """把 `600000.SH` 这类带后缀的标识符拆成 token 级形态（比对的是数字段）。"""
    keys: set[str] = set()
    for item in identifiers:
        text = str(item)
        keys.add(text)
        keys.update(match.group(0) for match in _TOKEN_RE.finditer(text))
    return frozenset(keys)


def _split_sections(text: str) -> tuple[list[str], set[int]]:
    """逐行文本 + 属于「## 来源」的小节行号集合（行号从 1 起）。

    来源清单是程序生成的元数据（as_of 与 URL 里全是数字），不参与「数字必须有角标」的
    判定；从 `## 来源` 到下一个 `## ` 为止。
    """
    lines = text.split("\n")
    source_lines: set[int] = set()
    in_sources = False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            in_sources = stripped == SOURCES_HEADING
        if in_sources:
            source_lines.add(number)
    return lines, source_lines


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def annotate_line(
    line: str, index: CitationIndex, identifiers: frozenset[str] = frozenset()
) -> str:
    """给一行里每个「无标记」的数字补角标：verified → `[n]`，其余 → 〔未核实〕。

    从右往左插，前面的位置不会被后面的插入挪动；已有标记的 token 一律不动，
    所以 `annotate(annotate(x)) == annotate(x)`（报告段会重渲染上游叙述，必须幂等）。
    """
    out = line
    for token in reversed(scan_line(out, identifiers)):
        if token.exempt or token.marked:
            continue
        source = index.find_text(token.text)
        mark = f"[{source.index}]" if source is not None and source.verified else UNVERIFIED_MARK
        position = _mark_position(out, token.end)
        out = f"{out[:position]}{mark}{out[position:]}"
    return out


def annotate(
    text: str, index: CitationIndex, *, identifiers: Iterable[str] = ()
) -> str:
    """整篇渲染：逐行补标记，`## 来源` 小节原样保留（那是程序生成的清单）。"""
    keys = _identifier_keys(identifiers)
    lines, source_lines = _split_sections(text)
    return "\n".join(
        line if number in source_lines else annotate_line(line, index, keys)
        for number, line in enumerate(lines, start=1)
    )


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Finding:
    """一条审计发现：定位到行、列、数字本身，再给出为什么不合格。"""

    line: int
    column: int
    number: str
    reason: str
    detail: str
    excerpt: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "number": self.number,
            "reason": self.reason,
            "detail": self.detail,
            "excerpt": self.excerpt,
        }

    def __str__(self) -> str:
        return f"第 {self.line} 行第 {self.column} 列 {self.number}：{self.detail}"


def audit_text(
    text: str, index: CitationIndex, *, identifiers: Iterable[str] = ()
) -> dict[str, Any]:
    """只审计文本：每个数字要么有角标，要么挂〔未核实〕，要么在豁免名单里。"""
    keys = _identifier_keys(identifiers)
    lines, source_lines = _split_sections(text)
    findings: list[Finding] = []
    counts = {"numbers": 0, "cited": 0, "unverified": 0, "exempt": 0}
    exempt_hits: dict[str, int] = {}
    cited_indexes: set[int] = set()

    for number, line in enumerate(lines, start=1):
        if number in source_lines:
            continue
        for token in scan_line(line, keys):
            if token.exempt:
                counts["exempt"] += 1
                exempt_hits[token.exempt] = exempt_hits.get(token.exempt, 0) + 1
                continue
            counts["numbers"] += 1
            if token.marked is None:
                findings.append(
                    Finding(
                        line=number,
                        column=token.start + 1,
                        number=token.text,
                        reason=_REASON_BARE,
                        detail=f"{_REASON_BARE}（既无角标 [n]，也未标{UNVERIFIED_MARK}）",
                        excerpt=line.strip()[:120],
                    )
                )
                continue
            if token.marked == UNVERIFIED_MARK:
                counts["unverified"] += 1
                continue
            cited = int(token.marked[1:-1])
            source = index.resolve(cited)
            if source is None:
                findings.append(
                    Finding(
                        line=number,
                        column=token.start + 1,
                        number=token.text,
                        reason=_REASON_DANGLING,
                        detail=f"角标 [{cited}] 在 `## 来源` 里没有对应条目",
                        excerpt=line.strip()[:120],
                    )
                )
                continue
            if not source.verified:
                findings.append(
                    Finding(
                        line=number,
                        column=token.start + 1,
                        number=token.text,
                        reason=_REASON_UNVERIFIED_CITATION,
                        detail=(
                            f"角标 [{cited}] 的来源（{source.dataset}）未核实，"
                            f"应标{UNVERIFIED_MARK}"
                        ),
                        excerpt=line.strip()[:120],
                    )
                )
                continue
            expected, printed = _as_number(source.value), token.number
            if expected is None or printed is None or expected != printed:
                findings.append(
                    Finding(
                        line=number,
                        column=token.start + 1,
                        number=token.text,
                        reason=_REASON_MISMATCH,
                        detail=(
                            f"数字 {token.text} 与角标 [{cited}] 记录的 "
                            f"{source.value!r} 不符（疑似伪造数字借用了真引用）"
                        ),
                        excerpt=line.strip()[:120],
                    )
                )
                continue
            counts["cited"] += 1
            cited_indexes.add(cited)

    unused = sorted(item.index for item in index.sources if item.index not in cited_indexes)
    return {
        "ok": not findings,
        "counts": {**counts, "sources": len(index.sources)},
        "exempt_hits": dict(sorted(exempt_hits.items())),
        "unused_sources": unused,
        "findings": [item.as_dict() for item in findings],
    }


__all__ = [
    "EXEMPTIONS",
    "SOURCES_HEADING",
    "UNVERIFIED_MARK",
    "CitationIndex",
    "Finding",
    "Source",
    "Token",
    "annotate",
    "annotate_line",
    "audit_text",
    "index_from_slot",
    "scan_line",
]
