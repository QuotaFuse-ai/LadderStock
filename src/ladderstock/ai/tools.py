"""研究段的受控工具集（R2 / QUOT-140）。

vibe-astock 那份报告之所以可审计，前提是「每段只给少数受控工具，模型不能自由
取数、不能自由写文件」。这里把那条前提搬过来：注册表是**代码内常量**，每段能用
哪几个工具由 `research/stages.py:ALLOWED_TOOLS` 声明，运行时不接受任何覆盖参数
（与 DEC-021 §4「研究侧用固定表、不进 config/ai.yaml」同一口径）。

三条硬规则：

1. **越权即失败**：请求的工具不在本段白名单（模型自己编造的工具名也算）→ 抛
   `UnauthorizedToolCall`，该段记 `failed`、整条管线退出码 9，审计日志留痕，
   **不重试、不换模型**（ROADMAP R2 验收）。
2. **只碰本机落盘产物**：每个工具在注册表里声明允许的输入根目录（本任务只有
   `run`，即 `data/research/<code>/<date>/`，由调用方解析后交进来）。实现里
   先把入参当**单段文件名**校验，再把解析后的真实路径与根目录比对，`..`、
   绝对路径、软链逃逸一律拒。工具不直连外网：注册表里只有受控检索工具标了
   `network=True`，而它在 `LDST_WEB_SEARCH=1` 之前一律被拒（DEC-018）。
3. **AI 写的不是 canonical**：程序落盘的 `<stage>.json` / `<stage>.md` 才是阶段
   产物（15 键契约 + 边界闸门，DEC-021 §3 / DEC-006 闸门 5）；模型经
   `write_stage` / `write_report` 写的文件带 `__` 标记（`company__draft.json`、
   `report__model.md`），`list_artifacts` / `read_artifact` 都看得见、审计都留痕，
   但页面与下游只认 canonical。

一次调用的完整轨迹都追加到 `<run>/tool_calls.jsonl`（段、工具、入参摘要、
出参摘要、耗时、是否拒绝）。没有工具调用时**不创建这个文件**，管线行为与
R2 之前完全一致。
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.core.boundary import AI_WRITABLE, BoundaryViolation, assert_narrative_only

#: 工具唯一允许读写文件的根目录名：本次研究的落盘目录
RUN_ROOT = "run"

AUDIT_FILENAME = "tool_calls.jsonl"

LIST_ARTIFACTS = "list_artifacts"
READ_ARTIFACT = "read_artifact"
COMPUTE = "compute"
WRITE_STAGE = "write_stage"
WRITE_REPORT = "write_report"
SEARCH_WEB = "search_web"

#: 白名单里可能出现的工具名（顺序即文档顺序）
TOOL_NAMES: tuple[str, ...] = (
    LIST_ARTIFACTS,
    READ_ARTIFACT,
    COMPUTE,
    WRITE_STAGE,
    WRITE_REPORT,
    SEARCH_WEB,
)

#: 单段一次生成最多调用几次工具；越界判拒绝，避免模型在工具里打转烧额度
MAX_CALLS_PER_STAGE = 8
#: 阶段产物与模型草稿允许的可读上限（超限判拒绝，不回传内容）
MAX_ARTIFACT_BYTES = 256 * 1024
#: 回传给模型 / 写进审计日志的单条文本上限
MAX_OUTPUT_CHARS = 4000
MAX_SUMMARY_CHARS = 200
#: 模型可写正文的上限
MAX_PAYLOAD_CHARS = 8000

#: 受控联网检索（DEC-018）：默认关闭，段内调用次数上限就是这一层的限速
WEB_SEARCH_ENV = "LDST_WEB_SEARCH"
WEB_SEARCH_HOSTS_ENV = "LDST_WEB_SEARCH_HOSTS"
WEB_SEARCH_CALL_BUDGET = 3

#: `write_report` 固定落盘名：模型的最终报告草稿，canonical 仍由程序写
REPORT_DRAFT_NAME = "model"

#: 单段文件名：小写字母开头，允许下划线与连字符，扩展名只有 json / md
_SEGMENT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_EXTENSIONS: tuple[str, ...] = ("json", "md")
#: 模型写的草稿用 `__` 与 canonical 区分（`company__draft.json`）
DRAFT_SEPARATOR = "__"

COMPUTE_OPS: tuple[str, ...] = ("add", "sub", "mul", "div", "pct_change")
_MAX_DECIMAL_DIGITS = 18
_RESULT_QUANT = Decimal("0.000001")


class ToolRefusal(Exception):
    """工具本身被授权，但这次调用被拒（参数非法、越界写、超预算……）。

    拒因回喂给模型，它可以选择不再调用工具直接成文；段不因此判 `failed`。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class UnauthorizedToolCall(Exception):
    """模型请求了本段白名单之外的工具（含编造的名字）——越权，即该段失败。"""

    def __init__(self, tool: str, *, stage: str, allowed: Sequence[str]) -> None:
        self.tool = tool
        self.stage = stage
        self.allowed = tuple(allowed)
        super().__init__(
            f"{stage} 段调用了未授权工具 {tool}（本段允许：{'、'.join(self.allowed) or '无'}）"
        )


@dataclass(frozen=True)
class ToolSpec:
    """注册表里的一项：名字 + JSON Schema + 纯函数实现 + 允许输入根目录。"""

    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[[ToolContext, Mapping[str, Any]], str]
    roots: tuple[str, ...] = (RUN_ROOT,)
    #: 只有标了 network 的工具可能触网，且必须先过 `LDST_WEB_SEARCH` 闸门
    network: bool = False
    note: str = ""


@dataclass
class ToolContext:
    """一次段内生成的工具视野：段、可落盘根目录、本段白名单、调用轨迹。"""

    stage: str
    roots: Mapping[str, Path]
    allowed: frozenset[str]
    model: str | None = None
    #: 受控检索后端（R4 提供）；没有它，`search_web` 一律拒绝
    search_backend: Callable[[str, frozenset[str]], Sequence[Mapping[str, Any]]] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: 本段已用的受控检索次数（DEC-018 的限速就落在这一层）
    search_calls: int = 0

    def run_dir(self) -> Path:
        return self.run_root(RUN_ROOT)

    def run_root(self, name: str) -> Path:
        try:
            return Path(self.roots[name])
        except KeyError:
            raise ToolRefusal("root_unavailable", f"调用方未提供根目录 {name}") from None

    def add_call(self, entry: dict[str, Any]) -> None:
        self.calls.append(entry)

    def require_roots(self, names: Sequence[str]) -> None:
        """注册表声明的输入根目录必须由调用方交出，否则这个工具在本段没有可达路径。"""
        missing = [name for name in names if name not in self.roots]
        if missing:
            raise ToolRefusal("root_unavailable", f"调用方未提供根目录：{'、'.join(missing)}")

    @property
    def call_count(self) -> int:
        return len(self.calls)


# --------------------------------------------------------------------------- #
# 实现：全是纯函数（同样的入参 + 同一个只读目录 → 同样的返回）
# --------------------------------------------------------------------------- #


def _list_artifacts(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """列本任务本次已落盘的阶段产物（canonical + 模型草稿）。"""
    stage = _safe_segment(str(args.get("stage") or ""), field="stage")
    run_dir = _require_dir(ctx)
    names = _artifact_names(run_dir)
    rows = [
        {"name": name, "stage": _stage_of(name), "bytes": size}
        for name, size in _existing_sizes(run_dir, names)
        if _stage_of(name) == stage
    ]
    return json.dumps(
        {"stage": stage, "count": len(rows), "artifacts": rows},
        ensure_ascii=False,
    )


def _read_artifact(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """读指定产物的正文；路径与软链都拦，超限不回传内容。"""
    stage = _safe_segment(str(args.get("stage") or ""), field="stage")
    name = _safe_artifact_name(str(args.get("name") or ""), stage=stage)
    run_dir = _require_dir(ctx)
    path = _resolve_inside(run_dir, name)
    text = _read_bounded(path)
    return json.dumps(
        {"stage": stage, "name": name, "bytes": len(text.encode("utf-8")), "content": text},
        ensure_ascii=False,
    )


def _compute(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """确定性算术：数字一律由这里算，模型不许心算（DEC-006 闸门 5）。"""
    op = str(args.get("op") or "")
    if op not in COMPUTE_OPS:
        raise ToolRefusal(
            "unknown_op", f"compute 只支持 {'、'.join(COMPUTE_OPS)}，收到 {op!r}"
        )
    left = _decimal(args.get("a"), field="a")
    right = _decimal(args.get("b"), field="b")
    if op == "add":
        raw = left + right
    elif op == "sub":
        raw = left - right
    elif op == "mul":
        raw = left * right
    elif op == "div":
        if right == 0:
            raise ToolRefusal("division_by_zero", "compute 除数为 0")
        raw = left / right
    else:  # pct_change：(b - a) 相对 a 的百分比
        if left == 0:
            raise ToolRefusal("division_by_zero", "compute 基准值 a 为 0，无法算变化率")
        raw = (right - left) / left * Decimal(100)
    try:
        result = raw.quantize(_RESULT_QUANT, rounding="ROUND_HALF_UP")
    except InvalidOperation:
        raise ToolRefusal("result_out_of_range", "结果超出可表示范围，请缩小数值") from None
    return json.dumps(
        {"op": op, "a": str(left), "b": str(right), "result": str(result)},
        ensure_ascii=False,
    )


def _write_stage(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """写本阶段的产物草稿：只能写本段、只能写叙述字段。"""
    stage = _safe_segment(str(args.get("stage") or ""), field="stage")
    if stage != ctx.stage:
        raise ToolRefusal(
            "cross_stage_write", f"{ctx.stage} 段不能写 {stage} 段产物（每段只写自己那段）"
        )
    name = _safe_segment(str(args.get("name") or ""), field="name")
    payload = _narrative_payload(args.get("payload"))
    run_dir = _require_dir(ctx)
    file_name = f"{stage}{DRAFT_SEPARATOR}{name}.json"
    path = _resolve_inside(run_dir, file_name, allow_new=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return json.dumps({"written": file_name, "keys": sorted(payload)}, ensure_ascii=False)


def _write_report(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """写最终报告草稿（Markdown）；canonical `report.md` 仍由程序落盘。"""
    if ctx.stage != "report":
        raise ToolRefusal("not_report_stage", f"{ctx.stage} 段不能写报告，只有报告段可以")
    md = args.get("md")
    if not isinstance(md, str) or not md.strip():
        raise ToolRefusal("empty_report", "write_report 需要非空 Markdown 正文")
    if len(md) > MAX_PAYLOAD_CHARS:
        raise ToolRefusal(
            "report_too_large", f"报告正文超过 {MAX_PAYLOAD_CHARS} 字上限"
        )
    run_dir = _require_dir(ctx)
    file_name = f"report{DRAFT_SEPARATOR}{REPORT_DRAFT_NAME}.md"
    path = _resolve_inside(run_dir, file_name, allow_new=True)
    atomic_write(path, md.strip() + "\n")
    return json.dumps({"written": file_name, "chars": len(md.strip())}, ensure_ascii=False)


def _search_web(ctx: ToolContext, args: Mapping[str, Any]) -> str:
    """受控联网检索（DEC-018）：默认关闭、域名白名单、限速、结果一律未核实。

    取数实现属 R4；这里只保证机制——触网闸门由 `execute` 统一拦（见 `ToolSpec.network`），
    这里再管域名白名单与来源标签：后端没接、域名不在白名单、缺来源标签，
    任何一种都直接拒绝，不做「先试一下」。
    """
    if ctx.search_calls >= WEB_SEARCH_CALL_BUDGET:
        raise ToolRefusal(
            "web_search_rate_limited",
            f"本段受控检索最多 {WEB_SEARCH_CALL_BUDGET} 次，已达上限",
        )
    hosts = allowlisted_hosts()
    if not hosts:
        raise ToolRefusal(
            "web_search_hosts_empty", f"域名白名单为空（{WEB_SEARCH_HOSTS_ENV}），按拒绝处理"
        )
    if ctx.search_backend is None:
        raise ToolRefusal(
            "web_search_backend_missing", "受控检索后端属 R4，尚未接入，本工具不做任何外呼"
        )
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolRefusal("empty_query", "search_web 需要非空 query")
    ctx.search_calls += 1
    rows = [dict(row) for row in ctx.search_backend(query, hosts)]
    labelled = [_require_source(row, hosts) for row in rows]
    for row in labelled:
        # 硬门槛：外采内容未经核实，R3 的「无来源不得引用」以此为输入
        row["verified"] = False
    return json.dumps(
        {"query": query, "count": len(labelled), "results": labelled}, ensure_ascii=False
    )


def _require_source(row: Mapping[str, Any], hosts: frozenset[str]) -> dict[str, Any]:
    """来源标签是硬门槛：URL + 抓取时间 + 标题，缺一即拒；域名必须在白名单内。"""
    url = str(row.get("url") or "").strip()
    title = str(row.get("title") or "").strip()
    fetched_at = str(row.get("fetched_at") or "").strip()
    if not url or not title or not fetched_at:
        raise ToolRefusal(
            "source_label_missing", "检索结果缺来源标签（url / title / fetched_at 必须齐全）"
        )
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host not in hosts:
        raise ToolRefusal("host_not_allowed", f"来源域名 {host} 不在白名单内")
    return {**row, "url": url, "title": title, "fetched_at": fetched_at, "source_host": host}


def env_enabled(name: str) -> bool:
    """环境变量开关：只认 `1/true/yes/on`，其余（含未设）一律关闭。"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def allowlisted_hosts() -> frozenset[str]:
    """域名白名单只从环境变量读，默认空集（空集 = 拒绝一切，不是放行一切）。"""
    raw = os.environ.get(WEB_SEARCH_HOSTS_ENV, "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


# --------------------------------------------------------------------------- #
# 入参校验：文件名单段 + 解析后仍在根目录内
# --------------------------------------------------------------------------- #


def _require_dir(ctx: ToolContext) -> Path:
    run_dir = ctx.run_dir()
    if not run_dir.is_dir():
        raise ToolRefusal("run_dir_missing", f"落盘目录尚未建立：{run_dir.name}")
    return run_dir


def _safe_segment(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if not _SEGMENT.fullmatch(cleaned):
        raise ToolRefusal(
            "bad_argument",
            f"{field} 只能是 1-40 位小写字母/数字/下划线/连字符且以字母开头，收到 {value!r}",
        )
    return cleaned


def _safe_artifact_name(value: str, *, stage: str) -> str:
    """`name` 必须是本段的一个产物文件名，不是一条路径。"""
    cleaned = value.strip()
    stem, _, extension = cleaned.rpartition(".")
    if extension not in _EXTENSIONS or not stem:
        raise ToolRefusal(
            "bad_artifact_name", f"name 需要以 {'/'.join(_EXTENSIONS)} 结尾，收到 {value!r}"
        )
    _safe_segment(stem, field="name")
    expected_prefix = f"{stage}{DRAFT_SEPARATOR}"
    if cleaned not in (f"{stage}.json", f"{stage}.md") and not stem.startswith(expected_prefix):
        raise ToolRefusal(
            "foreign_artifact",
            f"name 只能是 {stage} 段自己的产物（{stage}.json / {stage}.md / {stage}__*.json|md）",
        )
    return cleaned


def _resolve_inside(run_dir: Path, file_name: str, *, allow_new: bool = False) -> Path:
    """把文件名校验成根目录内的一个真实条目：拒绝分隔符、逃逸与软链。"""
    candidate = run_dir / file_name
    # 目录必须存在才能 resolve 出真实路径；写场景由 atomic_write 建目录
    if not allow_new and not candidate.is_file():
        raise ToolRefusal("artifact_missing", f"产物不存在：{file_name}")
    if candidate.is_symlink():
        raise ToolRefusal("symlink_rejected", f"不读软链产物：{file_name}")
    base = run_dir.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base) or resolved.parent != base:
        raise ToolRefusal("path_escape", f"路径越界：{file_name}")
    if resolved.name != file_name:
        raise ToolRefusal("path_escape", f"解析后的文件名不一致：{file_name}")
    return resolved


def _read_bounded(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        raise ToolRefusal("artifact_missing", "产物读取失败") from None
    if size > MAX_ARTIFACT_BYTES:
        raise ToolRefusal("artifact_too_large", f"产物超过 {MAX_ARTIFACT_BYTES} 字节上限")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ToolRefusal("artifact_unreadable", "产物不是可读的 UTF-8 文本") from None
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n…（已截断）"
    return text


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolRefusal("bad_number", f"{field} 需要是数字，收到 {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolRefusal("bad_number", f"{field} 不是有限数字")
    try:
        number = Decimal(repr(value) if isinstance(value, float) else str(value))
    except (InvalidOperation, ValueError):
        raise ToolRefusal("bad_number", f"{field} 无法转成十进制数") from None
    if not number.is_finite():
        raise ToolRefusal("bad_number", f"{field} 不是有限数字")
    if abs(number.adjusted()) > _MAX_DECIMAL_DIGITS:
        raise ToolRefusal(
            "bad_number", f"{field} 的数量级或有效位超过 {_MAX_DECIMAL_DIGITS}，请用快照里的数字"
        )
    return number


def _narrative_payload(value: Any) -> dict[str, Any]:
    """payload 只允许叙述键，且必须带非空 narrative（DEC-006 闸门 5）。"""
    if not isinstance(value, Mapping):
        raise ToolRefusal("bad_payload", "payload 需要是 JSON 对象")
    try:
        assert_narrative_only(value, {})
    except BoundaryViolation as exc:
        raise ToolRefusal("narrative_only", str(exc)) from None
    if "narrative" not in value:
        raise ToolRefusal("narrative_only", "payload 必须包含 narrative")
    text = json.dumps(dict(value), ensure_ascii=False)
    if len(text) > MAX_PAYLOAD_CHARS:
        raise ToolRefusal("payload_too_large", f"payload 超过 {MAX_PAYLOAD_CHARS} 字上限")
    narrative = value.get("narrative")
    items = [narrative] if isinstance(narrative, str) else list(narrative or [])
    if not any(str(item).strip() for item in items):
        raise ToolRefusal("narrative_only", "narrative 不能为空")
    cleaned = {str(key): value[key] for key in sorted(AI_WRITABLE & set(value))}
    return cleaned


def _artifact_names(run_dir: Path) -> list[str]:
    return sorted(
        path.name
        for path in run_dir.iterdir()
        if path.is_file()
        and path.name != AUDIT_FILENAME
        and path.suffix.lstrip(".") in _EXTENSIONS
    )


def _stage_of(file_name: str) -> str:
    return file_name.split(DRAFT_SEPARATOR, 1)[0].rsplit(".", 1)[0]


def _existing_sizes(run_dir: Path, names: Sequence[str]) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for name in names:
        try:
            rows.append((name, (run_dir / name).stat().st_size))
        except OSError:
            continue
    return rows


# --------------------------------------------------------------------------- #
# 注册表（常量）
# --------------------------------------------------------------------------- #

_NUMBER = {"type": "number"}
_SEGMENT_SCHEMA = {"type": "string", "pattern": _SEGMENT.pattern, "minLength": 1, "maxLength": 40}

LIST_ARTIFACTS_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"stage": _SEGMENT_SCHEMA},
    "required": ["stage"],
    "additionalProperties": False,
}
READ_ARTIFACT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"stage": _SEGMENT_SCHEMA, "name": {"type": "string", "minLength": 1}},
    "required": ["stage", "name"],
    "additionalProperties": False,
}
COMPUTE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"op": {"type": "string", "enum": list(COMPUTE_OPS)}, "a": _NUMBER, "b": _NUMBER},
    "required": ["op", "a", "b"],
    "additionalProperties": False,
}
WRITE_STAGE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "stage": _SEGMENT_SCHEMA,
        "name": _SEGMENT_SCHEMA,
        "payload": {"type": "object", "minProperties": 1},
    },
    "required": ["stage", "name", "payload"],
    "additionalProperties": False,
}
WRITE_REPORT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"md": {"type": "string", "minLength": 1, "maxLength": MAX_PAYLOAD_CHARS}},
    "required": ["md"],
    "additionalProperties": False,
}
SEARCH_WEB_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
    "required": ["query"],
    "additionalProperties": False,
}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name=LIST_ARTIFACTS,
            description="列出本段已落盘的产物（canonical 与模型草稿），返回文件名与字节数。",
            parameters=LIST_ARTIFACTS_SCHEMA,
            handler=_list_artifacts,
            note="只列目录内的文件，不写；跨代码、跨日期够不着，因为根目录已由调用方定死。",
        ),
        ToolSpec(
            name=READ_ARTIFACT,
            description="读取本段某一产物的正文。name 是 list_artifacts 给出的文件名，不是路径。",
            parameters=READ_ARTIFACT_SCHEMA,
            handler=_read_artifact,
            note="读之前先验文件名单段、再验解析后仍在根目录内，软链直接拒。",
        ),
        ToolSpec(
            name=COMPUTE,
            description=(
                "确定性算术，op 仅限 add/sub/mul/div/pct_change。"
                "叙述里出现的数字必须是快照里的或本工具算出来的，不许心算。"
            ),
            parameters=COMPUTE_SCHEMA,
            handler=_compute,
            roots=(),
            note="纯计算不碰文件，所以 roots 为空；结果固定 6 位小数，保证可复现。",
        ),
        ToolSpec(
            name=WRITE_STAGE,
            description="把本段的叙述草稿落成 <stage>__<name>.json；只能写本段、只能写叙述字段。",
            parameters=WRITE_STAGE_SCHEMA,
            handler=_write_stage,
            note="写的是带 __ 的草稿；canonical <stage>.json 仍由程序按 15 键契约落盘。",
        ),
        ToolSpec(
            name=WRITE_REPORT,
            description="写最终报告的 Markdown 草稿（report__model.md）。只有报告段可以调。",
            parameters=WRITE_REPORT_SCHEMA,
            handler=_write_report,
            note="canonical report.md 由程序落盘，保证缺输入时不会留半截报告（DEC-024）。",
        ),
        ToolSpec(
            name=SEARCH_WEB,
            description=(
                "受控联网检索：默认关闭，需 LDST_WEB_SEARCH=1 且域名在白名单内；"
                "结果必须带来源标签，且一律 verified=false。"
            ),
            parameters=SEARCH_WEB_SCHEMA,
            handler=_search_web,
            roots=(),
            network=True,
            note="机制在 R2，取数实现属 R4；后端未接入时本工具只做拒绝，不产生任何外呼。",
        ),
    )
}


def validate_allowed(names: Sequence[str]) -> tuple[str, ...]:
    """白名单表自检：每一项都得是注册表里的名字（供 stages.py 与测试用）。"""
    for name in names:
        if name not in TOOLS:
            raise ValueError(f"白名单里有未注册的工具：{name}")
    return tuple(names)


def tool_descriptors(names: Sequence[str]) -> list[dict[str, Any]]:
    """OpenAI 兼容的 `tools` 数组：只描述本段白名单里的工具。"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOLS[name].description,
                "parameters": dict(TOOLS[name].parameters),
            },
        }
        for name in names
    ]


def tool_instruction(names: Sequence[str]) -> str:
    """拼进 system 的工具约束段：模型看到的工具与白名单永远一致。"""
    if not names:
        return ""
    lines = [
        "\n\n受控工具（本段只允许这几个，调用其他任何工具本段直接判失败）：",
    ]
    for name in names:
        lines.append(f"- {name}：{TOOLS[name].description}")
    lines += [
        "使用规则：",
        "1. 数字只能引用快照或 compute 的返回，禁止心算、禁止改写；",
        "2. 只能读写本次研究目录内的产物，不接受任何路径写法；",
        "3. 工具够用就停，最终仍按上面的 JSON 格式输出本段叙述。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 执行 + 审计
# --------------------------------------------------------------------------- #


def execute(ctx: ToolContext, name: str, arguments: Any) -> dict[str, Any]:
    """执行一次工具调用，全程审计。

    - 名字不在本段白名单（含编造的名字）→ 记审计后抛 `UnauthorizedToolCall`，**不重试**；
    - 授权但被拒（参数非法、路径越界、超预算……）→ 记审计后把拒因回喂给模型。

    审计日志本身落在本次运行目录里，所以 `RUN_ROOT` 必须在任何判定之前就要到：
    给不出目录 = 调用方接线错误，连「被拒」都留不下痕迹，直接抛出，不放行。
    """
    started = time.monotonic()
    allowed = sorted(ctx.allowed)
    ctx.require_roots((RUN_ROOT,))
    if name not in ctx.allowed:
        _audit(ctx, name, arguments, None, started, rejected=True, reason="unauthorized_tool")
        ctx.add_call({"tool": name, "rejected": True, "reason": "unauthorized_tool"})
        raise UnauthorizedToolCall(name, stage=ctx.stage, allowed=allowed)
    entry: dict[str, Any] = {"tool": name, "rejected": False, "reason": None}
    try:
        payload = _coerce_arguments(arguments)
        _validate_arguments(TOOLS[name].parameters, payload)
        ctx.require_roots(TOOLS[name].roots)
        if TOOLS[name].network and not env_enabled(WEB_SEARCH_ENV):
            raise ToolRefusal(
                "web_search_disabled",
                f"联网检索默认关闭（DEC-018），需要 {WEB_SEARCH_ENV}=1；未设即拒绝，不产生外呼",
            )
        if ctx.call_count >= MAX_CALLS_PER_STAGE:
            raise ToolRefusal(
                "call_budget_exceeded", f"本段工具调用已达上限 {MAX_CALLS_PER_STAGE} 次"
            )
        content = TOOLS[name].handler(ctx, payload)
    except (ToolRefusal, ValueError, TypeError) as exc:
        reason = getattr(exc, "reason", "bad_arguments")
        message = str(exc) or reason
        entry.update({"rejected": True, "reason": reason})
        ctx.add_call(entry)
        _audit(ctx, name, arguments, message, started, rejected=True, reason=reason)
        return {"ok": False, "tool": name, "reason": reason, "content": f"工具被拒绝：{message}"}
    ctx.add_call(entry)
    _audit(ctx, name, payload, content, started, rejected=False, reason=None)
    return {"ok": True, "tool": name, "reason": None, "content": content[:MAX_OUTPUT_CHARS]}


def _coerce_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            raise ToolRefusal("bad_arguments", "工具入参不是合法 JSON") from None
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ToolRefusal("bad_arguments", "工具入参需要是 JSON 对象")


def _validate_arguments(schema: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(dict(schema)).iter_errors(dict(payload)),
        key=lambda err: list(err.path),
    )
    if errors:
        raise ToolRefusal("bad_arguments", f"入参不符合 Schema：{errors[0].message}")


def audit_path(ctx: ToolContext) -> Path:
    return ctx.run_dir() / AUDIT_FILENAME


def _audit(
    ctx: ToolContext,
    tool: str,
    arguments: Any,
    content: str | None,
    started: float,
    *,
    rejected: bool,
    reason: str | None,
) -> None:
    """追加一行审计记录；目录还没建就先建（越权尝试也必须留痕）。"""
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "stage": ctx.stage,
        "tool": tool,
        "model": ctx.model,
        "args": _summarize(arguments),
        "result": None if rejected else _summarize(content),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "rejected": rejected,
        "reason": reason,
    }
    path = audit_path(ctx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # 审计写不进去也不许放行：抛出去让该段失败
        raise ToolRefusal("audit_unwritable", "工具审计日志写入失败") from None


def _summarize(value: Any) -> str:
    if isinstance(value, (Mapping, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif value is None:
        return ""
    else:
        text = str(value)
    return text.replace("\n", " ").replace("\r", " ")[:MAX_SUMMARY_CHARS]


def audit_records(path: Path) -> list[dict[str, Any]]:
    """读审计日志（R3 溯源与报告审计门禁用；损坏行跳过，不整条链路崩）。"""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


__all__ = [
    "AUDIT_FILENAME",
    "COMPUTE",
    "COMPUTE_OPS",
    "LIST_ARTIFACTS",
    "MAX_CALLS_PER_STAGE",
    "READ_ARTIFACT",
    "REPORT_DRAFT_NAME",
    "RUN_ROOT",
    "SEARCH_WEB",
    "TOOL_NAMES",
    "TOOLS",
    "UnauthorizedToolCall",
    "WEB_SEARCH_ENV",
    "WEB_SEARCH_HOSTS_ENV",
    "WRITE_REPORT",
    "WRITE_STAGE",
    "ToolContext",
    "ToolRefusal",
    "ToolSpec",
    "allowlisted_hosts",
    "audit_path",
    "audit_records",
    "env_enabled",
    "execute",
    "tool_descriptors",
    "tool_instruction",
    "validate_allowed",
]
