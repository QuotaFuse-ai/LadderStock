"""`/api/research/*`：个股研究六段的只读视图 + 单段续跑（QUOT-275，SPEC-RESEARCH-PAGES §2）。

浏览器读不到本机文件，R8 的两页（`/research/:code`、`/research/:code/report`）只认这三个
接口。载荷与 MCP `research_status` 同形，字段名**照 SPEC §2 定稿**，前端不改字段即可切换。

三条设计取舍：

- **读取用既有真源**：六段状态取 `research/checkpoint.status_summary()`，产物取
  `research/artifacts`，这里只做组装与降级判定，不重算任何数字、也不补齐缺失值。
  「研究不存在」→ 404，绝不返回一份空壳冒充已生成（原单硬约束：不为跑通伪造产物）。
- **续跑走子进程**：`POST /{code}/run` 等价于 `ldst research <code> --date <date> --only <stage>`，
  与 AI 生成任务同一执行方式（参数数组 + `shell=False` + 显式超时，DEC-014 硬约束 2），
  复用 `web/control.py` 的 `Job` 模型与轮询形状，不新开一套任务机制。
- **闸门照控制面、开关不受控制面影响**：写方法要正向来源证据（QUOT-178），但
  `LDST_CONTROL_ENABLED=0` 只关控制面——研究读取属于数据面，跟着关会让页面永久空态。

状态词表沿用 checkpoint 的四态（`ok / degraded / failed / pending`）；任务状态在它之上
补 `queued / running`（与 `GET /api/control/jobs/{id}` 同一口径）。
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ladderstock.cli.paths import project_root
from ladderstock.core.ladder import as_float, pick
from ladderstock.core.privacy import strip_private
from ladderstock.data import archive
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK
from ladderstock.research import artifacts, checkpoint
from ladderstock.web import control as control_plane

RESEARCH_PREFIX = "/api/research"

#: 单段续跑的子进程硬上限：与 AI 生成任务同档（1800s），超时即失败，不无限挂起
RESEARCH_JOB_TIMEOUT_SEC = 1800.0

#: `peer` 位在 `Job` 里只做日志标签（研究任务没有 peer，取本进程产品名）
RESEARCH_JOB_LABEL = "ldst"

#: 概览接口一次回放的日 K 根数（QUOT-400：最近 ≥120 个交易日；缓存不足时如实给现有的）
OVERVIEW_BARS = 120

#: 三种 404 / 一种 503 的固定文案（QUOT-400：前端按 `detail` 里的这些词区分语义，
#: 改词必须同步 `frontend/src/api/research.ts` 的判据）
EMPTY_RECORD_DETAIL = "{code} 还没有任何研究记录（先运行 ldst research {code}）"
UNKNOWN_CODE_DETAIL = (
    "{code} 是未知证券代码（A 股个股只支持沪市 60/68、深市 00/30、"
    "北交所 43/83/87/92 开头的 6 位代码，可带 .SH/.SZ/.BJ 后缀）"
)
ENGINE_UNAVAILABLE_DETAIL = "研究引擎不可用：读取研究状态失败（{reason}）"

#: 六段状态词表（与 `ai/checkpoint.STATUSES` 一致，SPEC §2.1 同形）
STAGE_STATUSES = frozenset({"ok", "degraded", "failed", "pending"})
#: 任务状态词表（SPEC §2.3）
JOB_STATUSES = frozenset({"queued", "running", "ok", "degraded", "failed"})

#: `date` 必须严格 `YYYY-MM-DD`（`date.fromisoformat` 还认 `20261029`）
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #

def _invalid(detail: str) -> HTTPException:
    """参数非法：SPEC 定的就是 422（控制面用 400，这里不能跟着改，前端按 422 写）。"""
    return HTTPException(status_code=422, detail=detail)


def parse_code(raw: Any) -> str:
    """证券代码规范化；非法代码 422（`normalize_code` 同时挡住目录穿越）。"""
    try:
        return checkpoint.normalize_code(raw)
    except ValueError as exc:
        raise _invalid(str(exc)) from None


#: A 股现货的交易所前缀（QUOT-400「代码不存在」态的判据）。
#: 格式合法（6 位数字）但前缀不可能是一只 A 股个股（如 999999）→ 404「未知证券代码」，
#: 与「格式对、但还没跑过研究」的业务空态区分开。判断只看前缀，**不查任何网络源**。
A_SHARE_PREFIXES: dict[str, tuple[str, ...]] = {
    "SH": ("600", "601", "603", "605", "688", "689"),
    "SZ": ("000", "001", "002", "003", "300", "301"),
    "BJ": ("43", "83", "87", "92"),
}


def code_exists(code: str) -> bool:
    """格式合法的代码是否落在 A 股现货的前缀白名单内（纯静态判断，离线可判）。"""
    bare, _, suffix = code.partition(".")
    if suffix:
        prefixes = A_SHARE_PREFIXES.get(suffix.upper())
        return bool(prefixes) and bare.startswith(prefixes)
    return any(
        bare.startswith(prefix) for prefixes in A_SHARE_PREFIXES.values() for prefix in prefixes
    )


def require_known_code(code: str) -> None:
    """「代码不存在」态：格式合法但前缀不可能是 A 股个股 → 404，与「没跑过研究」区分。"""
    if not code_exists(code):
        raise HTTPException(status_code=404, detail=UNKNOWN_CODE_DETAIL.format(code=code))


def validate_day_text(text: str) -> str:
    """`YYYY-MM-DD` 文本 → ISO 日期；不合式 / 不合法 → 422（`parse_day` 与概览共用）。"""
    if not DAY_PATTERN.match(text):
        raise _invalid(f"date 需要 YYYY-MM-DD，收到 {text!r}")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise _invalid(f"date 不是合法日期：{text!r}") from None


def parse_day(raw: Any, *, code: str, root: Path) -> str:
    """`date` → `YYYY-MM-DD`；缺省取该代码最近一次运行日，一次记录都没有则 404。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        latest = checkpoint.latest_day(code, root)
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail=EMPTY_RECORD_DETAIL.format(code=code),
            )
        return latest
    text = str(raw).strip()
    return validate_day_text(text)


def parse_stage(raw: Any) -> str:
    """续跑段名：只允许六段常量之一。"""
    text = str(raw).strip() if isinstance(raw, str) else ""
    if text not in checkpoint.STAGES:
        raise _invalid(
            f"only 只能是六段之一：{', '.join(checkpoint.STAGES)}，收到 {raw!r}"
        )
    return text


# --------------------------------------------------------------------------- #
# 产物读取
# --------------------------------------------------------------------------- #

def _read_markdown(code: str, day: str, stage: str, root: Path) -> str | None:
    """读人读件 `<stage>.md`；不存在或读不出来返回 None（不编造正文）。"""
    _json_path, md_path = artifacts.stage_paths(code, day, stage, root)
    if not md_path.is_file():
        return None
    try:
        return md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _artifact_slot(code: str, day: str, stage: str, root: Path) -> dict[str, Any] | None:
    """一件阶段的 `{document, markdown}`；两件都没有 → None（前端据此判「本段无产物」）。"""
    document = artifacts.read_stage(code, day, stage, root)
    markdown = _read_markdown(code, day, stage, root)
    if document is None and markdown is None:
        return None
    return {"document": document, "markdown": markdown}


def _mtime_iso(code: str, day: str, stage: str, root: Path) -> str | None:
    """产物文件的落盘时间 → ISO（checkpoint 缺失、只有产物时用它兜 `updated_at`）。"""
    json_path, md_path = artifacts.stage_paths(code, day, stage, root)
    stamps: list[float] = []
    for path in (json_path, md_path):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #

@dataclass
class ResearchJob(control_plane.Job):
    """一次单段续跑：复用控制面的 `Job`（日志环形缓冲 + 锁 + 进度字段），补两个研究字段。"""

    code: str = ""
    stage: str = ""

    def research_view(self) -> dict[str, Any]:
        """SPEC §2.3 的六个键，一个不多一个不少（`step` 是给人看的当前动作）。"""
        return {
            "id": self.id,
            "status": self.status if self.status in JOB_STATUSES else "running",
            "stage": self.stage or None,
            "progress": round(min(1.0, max(0.0, self.progress)), 3),
            "step": self.step,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# 概览：行级小工具（缓存行的键名沿用 industry/snapshot 的别名口径）
# --------------------------------------------------------------------------- #

TRADE_DATE_KEYS: tuple[str, ...] = ("trade_date", "date")
OPEN_KEYS: tuple[str, ...] = ("open", "开盘")
HIGH_KEYS: tuple[str, ...] = ("high", "最高")
LOW_KEYS: tuple[str, ...] = ("low", "最低")
CLOSE_KEYS: tuple[str, ...] = ("close", "收盘")
VOLUME_KEYS: tuple[str, ...] = ("volume", "vol", "成交量")
AMOUNT_KEYS: tuple[str, ...] = ("amount", "成交额")
NAME_KEYS: tuple[str, ...] = ("name", "stock_name", "名称")
TIMESTAMP_KEYS: tuple[str, ...] = ("timestamp",)
PRICE_KEYS: tuple[str, ...] = ("price", "last", "现价")
PREV_CLOSE_KEYS: tuple[str, ...] = ("prev_close", "昨收")
CHANGE_PCT_KEYS: tuple[str, ...] = ("change_pct", "pct_chg", "涨跌幅")
REPORT_NAME_KEYS: tuple[str, ...] = ("report_name",)
EPS_KEYS: tuple[str, ...] = ("eps",)
REVENUE_KEYS: tuple[str, ...] = ("revenue",)
NET_PROFIT_KEYS: tuple[str, ...] = ("net_profit",)
ROE_KEYS: tuple[str, ...] = ("roe", "roe_weighted")
REVENUE_YOY_KEYS: tuple[str, ...] = ("revenue_yoy", "revenue_growth_yoy")
PROFIT_YOY_KEYS: tuple[str, ...] = ("profit_yoy", "net_profit_yoy")

#: 概览 quote 块的字段 → 缓存行里的别名键（只投影，不换算单位）
QUOTE_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("name", NAME_KEYS),
    ("price", PRICE_KEYS),
    ("prev_close", PREV_CLOSE_KEYS),
    ("change_pct", CHANGE_PCT_KEYS),
    ("volume", VOLUME_KEYS),
    ("amount", AMOUNT_KEYS),
    ("timestamp", TIMESTAMP_KEYS),
)
FINANCIAL_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("report_name", REPORT_NAME_KEYS),
    ("eps", EPS_KEYS),
    ("revenue", REVENUE_KEYS),
    ("net_profit", NET_PROFIT_KEYS),
    ("roe", ROE_KEYS),
    ("revenue_yoy", REVENUE_YOY_KEYS),
    ("profit_yoy", PROFIT_YOY_KEYS),
)

NO_QUOTE_CACHE = "本地 spot_quote 缓存里没有这只代码的快照（未同步或非盘中时段），不编价"
NO_FINANCE_CACHE = (
    "本地 finance_indicators 无缓存，"
    "需先 `ldst sync --dataset finance_indicators --codes <代码>`，不估算"
)


def _row_code(row: dict[str, Any]) -> str:
    return str(pick(row, ("code",)) or "").strip()


def _row_day(row: dict[str, Any]) -> str:
    return str(pick(row, TRADE_DATE_KEYS) or "")[:10]


def _row_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    return as_float(pick(row, keys))


def _latest_day_of(rows: list[dict[str, Any]]) -> str | None:
    days = sorted({_row_day(row) for row in rows if _row_day(row)})
    return days[-1] if days else None


def _kline_bars(rows: list[dict[str, Any]], as_of: str | None) -> list[dict[str, Any]]:
    """该代码的日线序列（升序，最近 `OVERVIEW_BARS` 根）：date/open/high/low/close/volume。

    原值投影：数值读不出来就 `null`，不做单位换算、不插值、不补点。
    """
    picked: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        day = _row_day(row)
        if not day:
            continue
        if as_of is not None and day > as_of:
            continue
        picked.append((day, row))
    picked.sort(key=lambda item: item[0])
    bars: list[dict[str, Any]] = []
    for day, row in picked[-OVERVIEW_BARS:]:
        bars.append(
            {
                "date": day,
                "open": _row_number(row, OPEN_KEYS),
                "high": _row_number(row, HIGH_KEYS),
                "low": _row_number(row, LOW_KEYS),
                "close": _row_number(row, CLOSE_KEYS),
                "volume": _row_number(row, VOLUME_KEYS),
            }
        )
    return bars


def _latest_row(rows: list[dict[str, Any]], bare: str, key: str) -> dict[str, Any] | None:
    """同代码多行取最新一条：quote 按 `timestamp`，财务按 `trade_date`。"""
    mine = [row for row in rows if _row_code(row) == bare]
    if not mine:
        return None
    return sorted(mine, key=lambda row: str(pick(row, (key,)) or ""))[-1]


def _latest_quote(
    rows: list[dict[str, Any]], bare: str
) -> tuple[dict[str, Any] | None, str | None]:
    row = _latest_row(rows, bare, "timestamp")
    if row is None:
        return None, NO_QUOTE_CACHE
    quote = {name: pick(row, keys) for name, keys in QUOTE_FIELDS}
    return quote, None


def _latest_financials(
    rows: list[dict[str, Any]], bare: str
) -> tuple[dict[str, Any] | None, str | None]:
    row = _latest_row(rows, bare, "trade_date")
    if row is None:
        return None, NO_FINANCE_CACHE
    financials: dict[str, Any] = {
        "report_date": _row_day(row),
        **{name: pick(row, keys) for name, keys in FINANCIAL_FIELDS},
    }
    return financials, None


# --------------------------------------------------------------------------- #
# 研究面
# --------------------------------------------------------------------------- #

class ResearchPlane:
    """三个接口的实现；`run` / `command` 可注入，测试用假子进程顶掉。"""

    def __init__(
        self,
        *,
        data_root: Path,
        run: Callable[[list[str], Path, float], control_plane.StepResult] | None = None,
        command: list[str] | None = None,
        timeout: float = RESEARCH_JOB_TIMEOUT_SEC,
    ) -> None:
        self.data_root = Path(data_root)
        self._run = run or control_plane.run_step
        self._command = command
        self._timeout = timeout
        self._lock = threading.Lock()
        self._jobs: dict[str, ResearchJob] = {}
        #: 「同标的同日只允许一个任务」的登记表：键 `(code, day)`
        self._running: dict[tuple[str, str], ResearchJob] = {}
        self._executor: ThreadPoolExecutor | None = None

    # ---------------------------------------------------------------- 读取

    def status(self, raw_code: Any, raw_day: Any = None) -> dict[str, Any]:
        """六段状态 + 各段产物（SPEC §2.1）。

        状态取 checkpoint，产物取 `artifacts`；两者都查不到 → 404「研究不存在」。
        checkpoint 在但六段没跑齐 → 200 + `available=true / complete=false` + `reason`
        点名缺哪几段（与 MCP `research_status` 同一口径）。

        QUOT-400 的三态语义：未知代码 → 404「未知证券代码」；没跑过研究 →
        404「还没有任何研究记录」；盘面读不动（OSError）→ 503「研究引擎不可用」。
        """
        code = parse_code(raw_code)
        require_known_code(code)
        try:
            day = parse_day(raw_day, code=code, root=self.data_root)
            summary = self._require_research(code, day)
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail=ENGINE_UNAVAILABLE_DETAIL.format(reason=exc)
            ) from None
        return self._status_payload(code, day, summary)

    def _require_research(self, code: str, day: str) -> Mapping[str, Any]:
        """「研究不存在」的唯一判据：checkpoint 与产物都查不到 → 404。

        读取和续跑用同一条规则，否则会出现「GET 说没有、POST 却起了个任务」。
        """
        summary = checkpoint.status_summary(code, day, self.data_root)
        paths = artifacts.artifact_paths(code, day, self.data_root)
        has_artifact = any(bool(item.get("exists")) for item in paths.values())
        if not summary["exists"] and not has_artifact:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{code} 在 {day} 没有研究产物"
                    f"（先运行 ldst research {code} --date {day}）"
                ),
            )
        return summary

    def _status_payload(
        self, code: str, day: str, summary: Mapping[str, Any]
    ) -> dict[str, Any]:
        stages: list[dict[str, Any]] = []
        degraded_any = False
        for entry in summary["stages"]:
            name = str(entry["name"])
            slot = _artifact_slot(code, day, name, self.data_root)
            document = (slot or {}).get("document") or {}
            raw_status = str(entry.get("status") or "")
            status = (
                raw_status
                if raw_status in STAGE_STATUSES
                else str(document.get("status") or "pending")
            )
            degraded = bool(entry.get("degraded")) or document.get("degraded") is True
            degraded_any = degraded_any or degraded
            stages.append(
                {
                    "name": name,
                    "title": str(entry.get("title") or checkpoint.STAGE_TITLES.get(name, name)),
                    "status": status if status in STAGE_STATUSES else "pending",
                    "mode": entry.get("mode"),
                    "degraded": degraded,
                    "artifact": slot,
                }
            )

        complete = bool(summary["complete"])
        missing = [str(name) for name in summary["failed"]] + [
            str(name) for name in summary["pending"]
        ]
        updated_at = summary.get("updated_at") or next(
            (
                stamp
                for name in checkpoint.STAGES
                if (stamp := _mtime_iso(code, day, name, self.data_root)) is not None
            ),
            None,
        )
        if complete and not degraded_any:
            source_status = "ok"
        elif degraded_any:
            source_status = "degraded"
        else:
            source_status = "partial"
        return {
            "code": code,
            "date": day,
            "available": True,
            "complete": complete,
            "checkpoint": str(summary["checkpoint"]),
            "updated_at": str(updated_at) if updated_at else None,
            "stages": stages,
            "pending": [str(name) for name in summary["pending"]],
            "failed": [str(name) for name in summary["failed"]],
            "source_status": source_status,
            "reason": None if complete else _reason(missing),
            "data_source_status": "ok" if complete else "partial",
        }

    # ---------------------------------------------------------------- 概览

    def overview(self, raw_code: Any, raw_day: Any = None) -> dict[str, Any]:
        """`GET /api/research/{code}/overview` 的载荷（QUOT-400，四段信封）。

        概览**不依赖六段产物**：没跑过研究也必须能出数——K 线 / 快照 / 财务指标
        只读本机缓存（`archive`，Parquet → DuckDB 同一条回放路），读不到的块
        显式 `null` + reason，不补 0、不联网、不编造。

        信封照 data 面同款四段：`payload / data_source_status / data_as_of / meta`。
        `data_source_status` 只看主指标（K 线）：有根 → `ok`，一根都没有 → `missing`；
        quote / financials 的有无在 `payload` 里用 `null` + reason 说明。
        """
        code = parse_code(raw_code)
        require_known_code(code)
        bare = code.partition(".")[0]
        try:
            kline_rows = archive.read_dataset("daily_k", None, root=self.data_root)
            quote_rows = archive.read_dataset("spot_quote", None, root=self.data_root)
            finance_rows = archive.read_dataset("finance_indicators", None, root=self.data_root)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"概览数据读取失败：{exc}"
            ) from None

        mine = [row for row in kline_rows if _row_code(row) == bare]
        as_of = (
            validate_day_text(str(raw_day).strip())
            if raw_day is not None and str(raw_day).strip()
            else _latest_day_of(mine)
        )
        bars = _kline_bars(mine, as_of)

        if as_of is not None and not bars:
            kline_reason = f"本地 daily_k 缓存在 {as_of} 及之前没有 {bare} 的行（未同步或已停牌）"
        elif as_of is None:
            kline_reason = (
                "本地 daily_k 缓存里没有这只代码的行，"
                "需先 `ldst sync --dataset daily_k --codes <代码>`"
            )
        elif len(bars) < OVERVIEW_BARS:
            kline_reason = (
                f"本地缓存只有 {len(bars)} 个交易日（不足 {OVERVIEW_BARS} 个），如实回放、不补点"
            )
        else:
            kline_reason = None

        quote, quote_reason = _latest_quote(quote_rows, bare)
        financials, financials_reason = _latest_financials(finance_rows, bare)
        name = quote.get("name") if quote else None
        available = bool(bars)
        status = "ok" if available else "missing"
        payload: dict[str, Any] = {
            "code": code,
            "name": name,
            "as_of": as_of,
            "quote": quote,
            "quote_reason": quote_reason,
            "kline": {
                "bars": bars,
                "count": len(bars),
                "requested": OVERVIEW_BARS,
                "reason": kline_reason,
            },
            "financials": financials,
            "financials_reason": financials_reason,
            "data_source_status": status,
        }
        return {
            "payload": strip_private(payload),
            "data_source_status": status,
            "data_as_of": as_of,
            "meta": {
                "topic": "research_overview",
                "source": "ladderstock",
                "freshness": "cached",
                "fetched_at": _now_iso(),
            },
        }

    # ---------------------------------------------------------------- 续跑

    def start_run(self, raw_code: Any, raw_day: Any, raw_only: Any) -> dict[str, Any]:
        """起一个单段续跑任务，返回 SPEC §2.2 的两键应答。

        判据顺序：参数非法 → 422；研究不存在 → 404（与读取同一个 `_require_research`）；
        同标的同日已有任务在跑 → `RuntimeError`（路由转 409）。
        `status` 在提交线程池**之前**取样，所以应答恒为 `queued`——
        跑到 `running` 是进度接口（§2.3）的事，不该让起始应答跟着抖动。
        """
        code = parse_code(raw_code)
        stage = parse_stage(raw_only)
        day = parse_day(raw_day, code=code, root=self.data_root)
        self._require_research(code, day)
        key = (code, day)
        with self._lock:
            active = self._running.get(key)
            if active is not None and active.status in {"queued", "running"}:
                raise RuntimeError(
                    f"{code} 在 {day} 已有研究任务在跑（段 {active.stage}，"
                    f"任务 {active.id}），请等待其结束"
                )
            job = ResearchJob(
                id=uuid.uuid4().hex,
                peer=RESEARCH_JOB_LABEL,
                action="research",
                day=day,
                stage=stage,
                code=code,
            )
            ack = {"job_id": job.id, "status": job.status}
            self._jobs[job.id] = job
            self._running[key] = job
            self._executor = self._executor or ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="ldst-research"
            )
            self._executor.submit(self._execute, job)
        return ack

    def get_job(self, job_id: str) -> ResearchJob | None:
        return self._jobs.get(job_id)

    def job_view(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"未知任务：{job_id}")
        return job.research_view()

    def command_for(self, job: ResearchJob) -> list[str]:
        """等价 `ldst research <code> --date <day> --only <stage>`（参数数组，不拼 shell）。"""
        args = list(self._command or control_plane._ldst_command())
        return [
            *args,
            "research",
            job.code,
            "--date",
            job.day,
            "--only",
            job.stage,
            "--output",
            "json",
        ]

    def _execute(self, job: ResearchJob) -> None:
        job.status = "running"
        job.started_at = _now_iso()
        job.step = f"{checkpoint.STAGE_TITLES.get(job.stage, job.stage)}：续跑本段"
        job.progress = 0.1
        try:
            self._run_stage(job)
        except Exception as exc:  # noqa: BLE001 - 任务线程不能把异常吞成静默
            job.error = f"内部错误：{exc}"
            job.status = "failed"
            job.log(f"内部错误：{exc!r}")
        finally:
            if job.status in {"ok", "degraded"}:
                job.progress = 1.0
            job.ended_at = _now_iso()
            with self._lock:
                if self._running.get((job.code, job.day)) is job:
                    del self._running[(job.code, job.day)]

    def _run_stage(self, job: ResearchJob) -> None:
        args = self.command_for(job)
        job.log(f"$ {' '.join(args)}")
        result = self._run(args, project_root(), self._timeout)
        for line in control_plane.tail_lines(result.output, control_plane.JOB_TAIL_LINES * 2):
            job.log(f"  {line}")
        if result.returncode == EXIT_OK:
            job.status = "ok"
            job.log("完成")
            return
        if result.returncode == EXIT_AI_DEGRADED:
            # 本段降级（占位/纯文本输出）：产物已落盘，页面照常回填，但要如实标注
            job.status = "degraded"
            job.error = None
            job.log("本段为降级产物（退出码 9）")
            return
        job.status = "failed"
        job.error = _run_failure_detail(result.returncode, result.output)
        job.log(f"失败：{job.error}")


def _run_failure_detail(returncode: int, output: str) -> str:
    """失败原因：优先给子进程自己说的那句（最后一行输出），否则只报退出码。"""
    lines = control_plane.tail_lines(output, 1)
    if not lines:
        return f"续跑失败（退出码 {returncode}）"
    return f"{lines[0]}（退出码 {returncode}）"


def _reason(missing: list[str]) -> str:
    if not missing:
        return "六段状态未全部完成（存在不可读的产物）"
    return f"六段未完成，缺：{'、'.join(missing)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 闸门与路由
# --------------------------------------------------------------------------- #

async def _guard(request: Request) -> None:
    """研究接口的来源闸门：回环来源 + `Host` 白名单，写方法另需正向来源证据。

    判据与控制面 `_guard` 完全同一套（`host_header_allowed` / `write_source_allowed`），
    差别只有一处：**不看 `LDST_CONTROL_ENABLED`**——读取属于数据面，控制面关掉时
    研究页仍要能出数（续跑接口在关掉控制面时仍可用，因为它跑的是本机 CLI，不是 peer）。
    """
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="研究接口只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )
    if request.method.upper() in control_plane.WRITE_METHODS:
        if not control_plane.write_source_allowed(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "写请求缺少来源证据：需要本机页面 Origin，或显式头 "
                    f"{control_plane.CLIENT_HEADER}: {control_plane.CLIENT_HEADER_VALUE}"
                ),
            )
        site = request.headers.get("sec-fetch-site")
        if site is not None and site.strip().lower() not in control_plane.SAME_ORIGIN_SITES:
            raise HTTPException(
                status_code=403,
                detail=f"跨站来源被拒绝：Sec-Fetch-Site={site}（只接受 same-origin / none）",
            )


class ResearchRunRequest(BaseModel):
    """SPEC §2.2 的请求体：`{date, only}`。"""

    date: str | None = None
    only: str | None = None


def build_router(plane: ResearchPlane) -> APIRouter:
    """构造 `/api/research/*` 三个路由（SPEC §2）。"""
    router = APIRouter(prefix=RESEARCH_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/jobs/{job_id}")
    def job_progress(job_id: str) -> dict[str, Any]:
        return plane.job_view(job_id)

    @router.get("/{code}")
    def research_status(
        code: str,
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        return plane.status(code, date_str)

    @router.get("/{code}/overview")
    def research_overview(
        code: str,
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        """概览（QUOT-400）：K 线 + 快照 + 财务指标，只读缓存，不依赖六段产物。"""
        return plane.overview(code, date_str)

    @router.post("/{code}/run")
    def research_run(code: str, body: ResearchRunRequest | None = None) -> dict[str, Any]:
        """单段续跑：等价 `ldst research <code> --date <date> --only <stage>`。"""
        payload = body or ResearchRunRequest()
        try:
            return plane.start_run(code, payload.date, payload.only)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    return router


__all__ = [
    "A_SHARE_PREFIXES",
    "DAY_PATTERN",
    "ENGINE_UNAVAILABLE_DETAIL",
    "EMPTY_RECORD_DETAIL",
    "JOB_STATUSES",
    "OVERVIEW_BARS",
    "RESEARCH_JOB_TIMEOUT_SEC",
    "RESEARCH_PREFIX",
    "ResearchJob",
    "ResearchPlane",
    "STAGE_STATUSES",
    "UNKNOWN_CODE_DETAIL",
    "build_router",
    "code_exists",
    "parse_code",
    "parse_day",
    "parse_stage",
    "require_known_code",
]
