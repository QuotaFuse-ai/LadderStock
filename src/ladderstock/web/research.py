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
from bisect import bisect_left
from collections import Counter
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
from ladderstock.core.models import LadderEnvelope
from ladderstock.core.privacy import strip_private
from ladderstock.data import archive, duckdb_store
from ladderstock.data.provider_registry import ProviderRegistry, SourceUnavailable
from ladderstock.data.watermark import WatermarkStore
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

#: K 线图端点（QUOT-410）的三档周期 × 三档复权：随请求**运行时透传**给
#: `tencent_kline`，本层不重实现周 / 月聚合、不臆算复权（DEC-055）。
KLINE_PERIODS = frozenset({"daily", "weekly", "monthly"})
KLINE_ADJUSTS = frozenset({"qfq", "hfq", "none"})
#: 根数缺省与硬上限：上游按 `limit` 自行截断，本层不再截一次、也不分页
KLINE_LIMIT_DEFAULT = 120
KLINE_LIMIT_MAX = 500

# ---- 财报前瞻 / 事件回看（QUOT-446）----

#: 财报明细一次回放的报告期数（缺省 6 期，上限 12 期）
EARNINGS_PERIODS_DEFAULT = 6
EARNINGS_PERIODS_MAX = 12

#: 事件窗口参数：事件日前 / 后各取几个交易日（0..60）
EVENT_WINDOW_BEFORE_DEFAULT = 5
EVENT_WINDOW_AFTER_DEFAULT = 20
EVENT_WINDOW_MAX = 60

#: 事件收益的基准指数；本地 `index_daily` 必须覆盖到窗口两端
BENCHMARK_CODE = "000300"

#: 样本不足就不给均值（前端统计句直接用这个判据，不自己做除法）
EVENT_SAMPLE_MIN = 8

#: 事件类型 → 本地数据集（顺序即返回顺序，`sources` 参数用键名）
EVENT_SOURCES: tuple[tuple[str, str], ...] = (
    ("holder_trades", "增持减持"),
    ("dragon_tiger", "龙虎榜"),
    ("earnings_preview", "业绩预告"),
    ("institution_survey", "调研"),
    ("share_buyback", "回购"),
    ("lockup_calendar", "解禁"),
)
EVENT_SOURCE_LABELS: dict[str, str] = dict(EVENT_SOURCES)

#: 三处缺口的原因文案：宁可显式 null，也不拿报告期末 / 抓取日冒充发布日
NO_PUBLISH_DATE = (
    "finance_indicators 没有公告日字段（TickHub 财务工具只回报告期），"
    "本地没有这只个股的报告披露日；不拿报告期末或抓取日冒充"
)
NO_REACTION_DATE = "缺发布日，无法定位披露后的首个交易日，反应日与当天涨跌不可得"
NO_FORECAST = "本地 earnings_preview 没有该报告期的业绩预告（该期未预告或未同步）"
NO_STOCK_BARS = (
    "本地 daily_k 在窗口边界日没有这只代码的收盘价（个股日线覆盖不足或停牌），不估算收益"
)

#: 周期 / 复权 / 市场的展示标签：纯静态映射，不查库、不联网（QUOT-428）。
KLINE_PERIOD_LABELS: dict[str, str] = {"daily": "日线", "weekly": "周线", "monthly": "月线"}
KLINE_ADJUST_LABELS: dict[str, str] = {"qfq": "前复权", "hfq": "后复权", "none": "不复权"}
#: 本端点只放行 A 股个股（`require_known_code`），市场固定一档。
KLINE_MARKET = "a_share"
KLINE_MARKET_LABEL = "A 股"
#: 载荷 `source` 给人看的上游口径；逐请求的 `source_detail` 仍在 payload 里。
KLINE_SOURCE = "TickHub-CN / tencent_kline"

#: 上游 `tencent_kline` 的 volume 单位是**手**：TickHub-CN 的 `crawler/store.py`
#: 明写 "Tencent's K-line sends volume in 手 and an A-share lot is 100 shares;
#: the warehouse column is 股"，入库时才 ×100 换成股。本端点现取现回，拿到的
#: 就是手——如实标注 `volume_unit`，不替上游换算（QUOT-428）。
KLINE_VOLUME_UNIT = "手"
#: 均线窗口：简单移动平均，只按本次返回的 bar 序列计算，不足窗口为 null。
KLINE_MA_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)
KLINE_MA_NOTE = "均线按本次返回的窗口计算，窗口不足的值为空"
KLINE_VOLUME_NOTE = (
    "volume 单位为手（1 手 = 100 股）：上游 tencent_kline 原样透传，本接口不换算"
)


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
        if prefixes is None:
            return False
        return bare.startswith(prefixes)
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


def parse_period(raw: Any) -> str:
    """K 线周期：三档之一，缺省 `daily`；非法 422（SPEC §2.4 定的）。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "daily"
    text = str(raw).strip().lower()
    if text not in KLINE_PERIODS:
        raise _invalid(f"period 只允许 daily / weekly / monthly，收到 {raw!r}")
    return text


def parse_adjust(raw: Any) -> str:
    """复权档：三档之一，缺省 `qfq`（与 daily_k 的 sync 口径一致）。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "qfq"
    text = str(raw).strip().lower()
    if text not in KLINE_ADJUSTS:
        raise _invalid(f"adjust 只允许 qfq / hfq / none，收到 {raw!r}")
    return text


def parse_limit(raw: Any) -> int:
    """根数：缺省 120，范围 1..500；上游按它截断，本层不二次截断。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return KLINE_LIMIT_DEFAULT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise _invalid(f"limit 必须是整数（1..{KLINE_LIMIT_MAX}），收到 {raw!r}") from None
    if not 1 <= limit <= KLINE_LIMIT_MAX:
        raise _invalid(f"limit 必须在 1..{KLINE_LIMIT_MAX} 内，收到 {raw!r}")
    return limit


def parse_periods(raw: Any) -> int:
    """财报明细期数：缺省 6，范围 1..12。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return EARNINGS_PERIODS_DEFAULT
    try:
        periods = int(raw)
    except (TypeError, ValueError):
        raise _invalid(
            f"periods 必须是整数（1..{EARNINGS_PERIODS_MAX}），收到 {raw!r}"
        ) from None
    if not 1 <= periods <= EARNINGS_PERIODS_MAX:
        raise _invalid(f"periods 必须在 1..{EARNINGS_PERIODS_MAX} 内，收到 {raw!r}")
    return periods


def parse_sources(raw: Any) -> list[str]:
    """事件类型过滤：逗号分隔；缺省全部六类，未知类型 422。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return [key for key, _label in EVENT_SOURCES]
    names = [part.strip() for part in str(raw).split(",") if part.strip()]
    unknown = [name for name in names if name not in EVENT_SOURCE_LABELS]
    if unknown:
        raise _invalid(
            f"sources 只允许 {', '.join(EVENT_SOURCE_LABELS)}，收到 {', '.join(unknown)}"
        )
    return list(dict.fromkeys(names))


def parse_window(raw: Any, *, name: str, default: int) -> int:
    """窗口交易日数：缺省 5 / 20，范围 0..60。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _invalid(f"{name} 必须是整数（0..{EVENT_WINDOW_MAX}），收到 {raw!r}") from None
    if not 0 <= value <= EVENT_WINDOW_MAX:
        raise _invalid(f"{name} 必须在 0..{EVENT_WINDOW_MAX} 内，收到 {raw!r}")
    return value


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


def _bar_from_record(row: dict[str, Any]) -> dict[str, Any]:
    """上游一行（`klines` 形状归一化后）→ 蜡烛点；amount 上游可能为 null，如实透传、不补 0。"""
    return {
        "date": _row_day(row),
        "open": _row_number(row, OPEN_KEYS),
        "high": _row_number(row, HIGH_KEYS),
        "low": _row_number(row, LOW_KEYS),
        "close": _row_number(row, CLOSE_KEYS),
        "volume": _row_number(row, VOLUME_KEYS),
        "amount": _row_number(row, AMOUNT_KEYS),
    }


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
# 财报前瞻 / 事件回看：缓存行 → 载荷（QUOT-446）
# --------------------------------------------------------------------------- #

#: 财报明细里的「实际」字段：只投影、不换算单位
ACTUAL_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("eps", EPS_KEYS),
    ("bps", ("bps",)),
    ("revenue", REVENUE_KEYS),
    ("net_profit", NET_PROFIT_KEYS),
    ("roe", ROE_KEYS),
    ("gross_margin", ("gross_margin",)),
    ("net_margin", ("net_margin",)),
    ("revenue_yoy", REVENUE_YOY_KEYS),
    ("profit_yoy", PROFIT_YOY_KEYS),
)

#: 业绩预告里原样回给前端的字段（实际/预期对比用；金额单位沿用上游）
FORECAST_FIELDS: tuple[str, ...] = (
    "indicator",
    "forecast_type",
    "report_date",
    "notice_date",
    "amount_lower",
    "amount_upper",
    "change_pct_lower",
    "change_pct_upper",
    "prior_year_amount",
    "content",
    "reason",
)


def _consensus_view(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最新一份一致预期快照；嵌套的 consensus / history 原样带回，不摊平。"""
    if not rows:
        return None
    row = max(rows, key=_row_day)
    return {
        "as_of": _row_day(row),
        "name": pick(row, NAME_KEYS),
        "current_year": row.get("current_year"),
        "eps_current_year": row.get("eps_current_year"),
        "eps_next_year": row.get("eps_next_year"),
        "analyst_count": row.get("analyst_count"),
        "ratings": row.get("ratings"),
        "consensus": row.get("consensus") or [],
        "history": row.get("history") or [],
    }


def _forecast_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """报告期 → 该期最新一条业绩预告（同一期多条时按公告日取最新）。"""
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        report_date = str(pick(row, ("report_date",)) or "")[:10]
        if not report_date:
            continue
        picked = {name: row.get(name) for name in FORECAST_FIELDS if name in row}
        picked["report_date"] = report_date
        current = index.get(report_date)
        if current is None or str(picked.get("notice_date") or "") >= str(
            current.get("notice_date") or ""
        ):
            index[report_date] = picked
    return index


def _event_detail(dataset: str, row: dict[str, Any]) -> dict[str, Any]:
    """事件行里对页面有用的字段（只挑存在的键，原值透传）。"""
    keys: dict[str, tuple[str, ...]] = {
        "holder_trades": (
            "direction",
            "holder",
            "change_shares_10k",
            "change_pct_total",
            "change_pct_float",
            "avg_price",
            "channel",
            "start_date",
            "end_date",
        ),
        "dragon_tiger": (
            "reason",
            "close",
            "change_pct",
            "turnover_rate",
            "net_amount",
            "net_ratio",
        ),
        "earnings_preview": (
            "indicator",
            "forecast_type",
            "report_date",
            "amount_upper",
            "change_pct_upper",
        ),
        "institution_survey": (
            "institution",
            "survey_date",
            "receivers",
            "receive_way",
            "content",
        ),
        "share_buyback": (
            "buyback_type",
            "amount",
            "shares",
            "price_lower",
            "price_upper",
            "progress",
        ),
        "lockup_calendar": (
            "type",
            "shares_10k",
            "able_shares_10k",
            "ratio_pct",
            "lift_status",
        ),
    }
    return {name: row.get(name) for name in keys.get(dataset, ()) if name in row}


def _events_for(dataset: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """缓存行 → 事件列表；业绩预告按 (公告日, 报告期, 指标) 去重，其余逐行算一条。"""
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        day = _row_day(row)
        if not day:
            continue
        if dataset == "earnings_preview":
            key = (
                day,
                str(row.get("report_date") or ""),
                str(row.get("indicator") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
        events.append({"date": day, "detail": _event_detail(dataset, row)})
    events.sort(key=lambda item: item["date"])
    return events


def _window_for(
    event_day: str,
    *,
    stock_close: Mapping[str, float | None],
    bench_days: list[str],
    bench_close: Mapping[str, float | None],
    before: int,
    after: int,
) -> dict[str, Any]:
    """单个事件的窗口收益与对 000300 的超额（交易日按基准日历对齐）。

    公式固定为 `close[end] / close[start] - 1`：`start` 是事件日所在交易日前
    `before` 个交易日、`end` 是其后 `after` 个交易日；两端任一缺价就显式 null + reason。
    """
    window: dict[str, Any] = {
        "start": None,
        "end": None,
        "stock_return": None,
        "benchmark_return": None,
        "excess_return": None,
        "reason": None,
    }
    if not bench_days:
        window["reason"] = "本地 index_daily 没有 000300 的日线，回放不了事件窗口"
        return window
    index = bisect_left(bench_days, event_day)
    if index >= len(bench_days):
        window["reason"] = (
            f"事件日 {event_day} 晚于本地 000300 最后交易日 {bench_days[-1]}，窗口未发生完"
        )
        return window
    start_i, end_i = index - before, index + after
    if start_i < 0:
        window["reason"] = (
            f"事件日 {event_day} 之前本地 000300 只有 {index} 个交易日（需要 {before} 个；"
            f"本地基准自 {bench_days[0]} 起）"
        )
        return window
    if end_i >= len(bench_days):
        window["reason"] = (
            f"事件日 {event_day} 之后本地 000300 只有 {len(bench_days) - 1 - index} 个交易日"
            f"（需要 {after} 个）"
        )
        return window
    start_day, end_day = bench_days[start_i], bench_days[end_i]
    stock_start, stock_end = stock_close.get(start_day), stock_close.get(end_day)
    bench_start, bench_end = bench_close.get(start_day), bench_close.get(end_day)
    window.update({"start": start_day, "end": end_day})
    if stock_start is None or stock_end is None or not stock_start:
        window["reason"] = NO_STOCK_BARS
        return window
    if bench_start is None or bench_end is None or not bench_start:
        window["reason"] = "本地 000300 在窗口边界日缺收盘价"
        return window
    stock_return = stock_end / stock_start - 1
    benchmark_return = bench_end / bench_start - 1
    window.update(
        {
            "stock_return": round(stock_return, 6),
            "benchmark_return": round(benchmark_return, 6),
            "excess_return": round(stock_return - benchmark_return, 6),
        }
    )
    return window


def _last_run_status(dataset: str, root: Path) -> str | None:
    """该数据集最近一次同步状态（用于区分「未同步」与「上游真实为空」）。"""
    try:
        runs = WatermarkStore(root=root).runs(limit=0)
    except Exception:  # noqa: BLE001 - 状态文件坏了不影响行情回放
        return None
    matched = [run for run in runs if str(run.get("dataset") or "") == dataset]
    return str(matched[-1].get("status") or "") if matched else None


def _empty_event_reason(dataset: str, root: Path) -> str:
    status = _last_run_status(dataset, root)
    if status is None:
        return f"本地 {dataset} 没有任何同步记录（未同步）"
    if status in ("ok", "degraded"):
        return f"本地 {dataset} 已同步（上次 {status}），但上游对该代码返回 0 条（真实空）"
    if status == "empty":
        return f"本地 {dataset} 上次同步即 0 条（上游真实为空）"
    return f"本地 {dataset} 上次同步状态 {status}，没有可用记录"


# --------------------------------------------------------------------------- #
# K 线图：载荷纯函数（QUOT-428，参考库 klineView.ts 的消费形状）
# --------------------------------------------------------------------------- #

def _bar_float(bar: dict[str, Any], key: str) -> float | None:
    """已投影的 bar 里的数值；缺失 / null 如实为 None，不补 0。"""
    value = bar.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _change_series(
    closes: list[float | None], upstream_pct: list[float | None]
) -> list[float | None]:
    """涨跌幅序列（**小数**，`0.0123` = +1.23%）。

    优先上游 `change_pct`（百分数 → 除以 100）；缺失时用
    `close[i] / close[i-1] - 1` 推导。首根没有前收，恒为 `None`；缺前收同样为
    `None`——不补 0（0 会被前端读成「平盘」，与「没有数据」不是一回事）。
    """
    changes: list[float | None] = []
    for index, close in enumerate(closes):
        if index == 0:
            changes.append(None)
            continue
        upstream = upstream_pct[index] if index < len(upstream_pct) else None
        if upstream is not None:
            changes.append(upstream / 100.0)
            continue
        prev = closes[index - 1]
        if close is None or prev is None or prev == 0:
            changes.append(None)
            continue
        changes.append(close / prev - 1)
    return changes


def _moving_average(values: list[float | None], window: int) -> list[float | None]:
    """简单移动平均：不足 `window` 根、或窗口内有缺值 → `None`，不跳空补算。"""
    averages: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            averages.append(None)
            continue
        chunk = values[index + 1 - window : index + 1]
        numbers = [value for value in chunk if value is not None]
        if len(numbers) < window:
            averages.append(None)
            continue
        averages.append(sum(numbers) / window)
    return averages


def _kline_series(
    bars: list[dict[str, Any]], upstream_pct: list[float | None]
) -> dict[str, Any]:
    """`bars` → 参考库 `KlineSeries`：列式数组 + `change` + `ma`（窗口 5/10/20/60）。"""
    closes = [_bar_float(bar, "close") for bar in bars]
    return {
        "dates": [str(bar.get("date") or "") for bar in bars],
        "open": [_bar_float(bar, "open") for bar in bars],
        "high": [_bar_float(bar, "high") for bar in bars],
        "low": [_bar_float(bar, "low") for bar in bars],
        "close": closes,
        "volume": [_bar_float(bar, "volume") for bar in bars],
        "change": _change_series(closes, upstream_pct),
        "ma": {str(window): _moving_average(closes, window) for window in KLINE_MA_WINDOWS},
    }


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
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self._run = run or control_plane.run_step
        self._command = command
        self._timeout = timeout
        self._registry = registry
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

    # ---------------------------------------------------------------- K 线图

    def _kline_registry(self) -> ProviderRegistry:
        """K 线图端点的现取出口；测试注入假 registry，不联网。"""
        if self._registry is not None:
            return self._registry
        return ProviderRegistry(root=self.data_root)

    def kline(
        self,
        raw_code: Any,
        raw_period: Any = None,
        raw_adjust: Any = None,
        raw_limit: Any = None,
    ) -> dict[str, Any]:
        """`GET /{code}/kline`（QUOT-410，SPEC §2.4）：K 线图数据，现取现回。

        `period / adjust / limit` 随请求**运行时透传**给 `tencent_kline`，不沿用
        sync 写死的 `daily + qfq` 口径——周 / 月聚合与复权全部由上游完成，本层
        不聚合、不臆算复权、不落库（`kline_chart` 数据集，DEC-055）。

        取不到数（peer 不可达 / 超时 / 返回空）是显式空态：`available=false` +
        `data_source_status="missing"` + `reason`，200 正常响应，不编造、不补零。
        """
        code = parse_code(raw_code)
        require_known_code(code)
        period = parse_period(raw_period)
        adjust = parse_adjust(raw_adjust)
        limit = parse_limit(raw_limit)
        params = {
            "code": code.partition(".")[0],
            "period": period,
            "adjust": adjust,
            "limit": limit,
        }
        envelope: LadderEnvelope | None = None
        reason: str | None = None
        try:
            outcome = self._kline_registry().fetch_live(
                "kline_chart", params=params, allow_cache=False
            )
            envelope = outcome.envelope
        except SourceUnavailable as exc:
            reason = f"TickHub-CN 现取不可用：{exc}"
        except Exception as exc:  # noqa: BLE001 - 取不到数是空态，不是 500
            reason = f"现取失败（{type(exc).__name__}）：{exc}"

        records: list[Any] = []
        if envelope is not None and isinstance(envelope.payload, dict):
            maybe = envelope.payload.get("records")
            if isinstance(maybe, list):
                records = maybe
        bar_rows = [row for row in records if isinstance(row, dict)]
        bars = [_bar_from_record(row) for row in bar_rows]
        upstream_pct = [as_float(pick(row, CHANGE_PCT_KEYS)) for row in bar_rows]
        series = _kline_series(bars, upstream_pct)
        available = bool(bars)
        if reason is None and not available:
            reason = (
                f"tencent_kline 在 period={period} / adjust={adjust} 下没有返回任何 K 线"
                "（上游没有这批数据：未上市 / 已退市 / 长期停牌）"
            )
        if available and envelope is not None and envelope.source_status != "ok":
            status = "degraded"
        else:
            status = "ok" if available else "missing"
        data_as_of = (
            envelope.data_as_of.isoformat()
            if envelope is not None and envelope.data_as_of is not None
            else None
        )
        fetched_at = _now_iso()
        payload: dict[str, Any] = {
            "code": code,
            "period": period,
            "adjust": adjust,
            "limit": limit,
            "market": KLINE_MARKET,
            "market_label": KLINE_MARKET_LABEL,
            "period_label": KLINE_PERIOD_LABELS[period],
            "adjust_label": KLINE_ADJUST_LABELS[adjust],
            "source": KLINE_SOURCE,
            "fetched_at": fetched_at,
            "volume_unit": KLINE_VOLUME_UNIT,
            "bars": bars,
            "count": len(bars),
            "available": available,
            "reason": reason,
            "source_detail": envelope.source_detail if envelope is not None else None,
            "data_source_status": status,
            "series": series,
            "notes": [KLINE_MA_NOTE, KLINE_VOLUME_NOTE],
        }
        return {
            "payload": strip_private(payload),
            "data_source_status": status,
            "data_as_of": data_as_of,
            "meta": {
                "topic": "research_kline",
                "source": "ladderstock",
                "freshness": envelope.freshness if envelope is not None else "realtime",
                "fetched_at": fetched_at,
            },
        }

    # ---------------------------------------------------------------- 财报前瞻

    def earnings_brief(self, raw_code: Any, raw_periods: Any = None) -> dict[str, Any]:
        """`GET /{code}/earnings-brief`：一致预期 + 最近 N 期财报明细（只读缓存）。

        「实际」取 `finance_indicators`（EPS / 营收 / 净利 / ROE 等原值），「预期」取
        同报告期的 `earnings_preview` 业绩预告。**发布日 / 反应日 / 当天涨跌**当前
        无源（财务工具不带公告日字段），一律 `null` + reason——不拿报告期末或抓取日顶替。
        """
        code = parse_code(raw_code)
        require_known_code(code)
        bare = code.partition(".")[0]
        periods = parse_periods(raw_periods)
        try:
            finance_rows = duckdb_store.query_dataset(
                "finance_indicators", None, code=bare, root=self.data_root
            )
            preview_rows = duckdb_store.query_dataset(
                "earnings_preview", None, code=bare, root=self.data_root
            )
            consensus_rows = duckdb_store.query_dataset(
                "consensus_forecast", None, code=bare, root=self.data_root
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"财报数据读取失败：{exc}"
            ) from None

        consensus = _consensus_view(consensus_rows)
        forecasts = _forecast_index(preview_rows)
        ordered = sorted(finance_rows, key=_row_day, reverse=True)[:periods]
        name = (consensus or {}).get("name")
        earnings: list[dict[str, Any]] = []
        for row in ordered:
            report_date = _row_day(row)
            forecast = forecasts.get(report_date)
            if name is None:
                name = pick(row, NAME_KEYS)
            earnings.append(
                {
                    "report_date": report_date,
                    "report_name": pick(row, REPORT_NAME_KEYS),
                    "actual": {
                        field: pick(row, source) for field, source in ACTUAL_FIELDS
                    },
                    "forecast": forecast,
                    "forecast_reason": None if forecast else NO_FORECAST,
                    "publish_date": None,
                    "publish_date_reason": NO_PUBLISH_DATE,
                    "reaction_date": None,
                    "reaction_date_reason": NO_REACTION_DATE,
                    "reaction_change_pct": None,
                    "reaction_change_pct_reason": NO_REACTION_DATE,
                }
            )
        latest = earnings[0] if earnings else None
        stats = {
            "periods": len(earnings),
            "with_forecast": sum(1 for item in earnings if item["forecast"]),
            "with_publish_date": 0,
            "with_reaction": 0,
            "latest_report_date": latest["report_date"] if latest else None,
            "latest_report_eps": latest["actual"].get("eps") if latest else None,
            "consensus_analyst_count": (consensus or {}).get("analyst_count"),
            "consensus_eps_current_year": (consensus or {}).get("eps_current_year"),
            "consensus_eps_next_year": (consensus or {}).get("eps_next_year"),
        }
        as_of_candidates = [
            value
            for value in ((consensus or {}).get("as_of"), stats["latest_report_date"])
            if value
        ]
        as_of = max(as_of_candidates) if as_of_candidates else None
        available = bool(earnings) or consensus is not None
        status = "ok" if available else "missing"
        consensus_reason = (
            None
            if consensus is not None
            else "本地 consensus_forecast 无这只代码的缓存（未同步或上游无数据）"
        )
        earnings_reason = (
            None
            if earnings
            else f"本地 finance_indicators 没有 {bare} 的报告期记录（未同步）"
        )
        payload: dict[str, Any] = {
            "code": code,
            "name": name,
            "periods": periods,
            "consensus": consensus,
            "consensus_reason": consensus_reason,
            "earnings": earnings,
            "earnings_reason": earnings_reason,
            "stats": stats,
            "data_source_status": status,
        }
        return {
            "payload": strip_private(payload),
            "data_source_status": status,
            "data_as_of": as_of,
            "meta": {
                "topic": "research_earnings_brief",
                "source": "ladderstock",
                "freshness": "cached",
                "fetched_at": _now_iso(),
            },
        }

    # ---------------------------------------------------------------- 事件回看

    def event_study(
        self,
        raw_code: Any,
        raw_sources: Any = None,
        raw_before: Any = None,
        raw_after: Any = None,
    ) -> dict[str, Any]:
        """`GET /{code}/event-study`：六类事件的日期序列 + 每事件对 000300 的窗口超额。

        交易日按本地 `index_daily`（000300）对齐；个股缺窗口边界收盘价时该事件
        `excess_return=null` + reason。可计算样本 < `EVENT_SAMPLE_MIN`（8）时
        `sample.status=insufficient_sample`、均值为 `null`——统计句不在前端做除法。
        """
        code = parse_code(raw_code)
        require_known_code(code)
        bare = code.partition(".")[0]
        sources = parse_sources(raw_sources)
        before = parse_window(
            raw_before, name="window_before", default=EVENT_WINDOW_BEFORE_DEFAULT
        )
        after = parse_window(
            raw_after, name="window_after", default=EVENT_WINDOW_AFTER_DEFAULT
        )
        try:
            stock_rows = duckdb_store.query_dataset(
                "daily_k", None, code=bare, root=self.data_root
            )
            index_rows = archive.read_dataset("index_daily", None, root=self.data_root)
            event_rows = {
                dataset: duckdb_store.query_dataset(dataset, None, code=bare, root=self.data_root)
                for dataset in sources
            }
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"事件数据读取失败：{exc}"
            ) from None

        bench_rows = [row for row in index_rows if _row_code(row) == BENCHMARK_CODE]
        bench_days = sorted({_row_day(row) for row in bench_rows if _row_day(row)})
        bench_close = {_row_day(row): _row_number(row, CLOSE_KEYS) for row in bench_rows}
        stock_close = {_row_day(row): _row_number(row, CLOSE_KEYS) for row in stock_rows}
        name = next(
            (
                value
                for rows in event_rows.values()
                for row in rows
                if (value := pick(row, NAME_KEYS))
            ),
            None,
        )

        types: list[dict[str, Any]] = []
        computed_any = False
        for dataset, label in EVENT_SOURCES:
            if dataset not in event_rows:
                continue
            rows = event_rows[dataset]
            events = _events_for(dataset, rows)
            for event in events:
                event["window"] = _window_for(
                    event["date"],
                    stock_close=stock_close,
                    bench_days=bench_days,
                    bench_close=bench_close,
                    before=before,
                    after=after,
                )
            windows = [
                event["window"]
                for event in events
                if event["window"]["excess_return"] is not None
            ]
            computed_any = computed_any or bool(windows)
            count = len(events)
            date_series = sorted({event["date"] for event in events})
            sample: dict[str, Any] = {
                "required": EVENT_SAMPLE_MIN,
                "window_available": len(windows),
                "status": (
                    "ok" if len(windows) >= EVENT_SAMPLE_MIN else "insufficient_sample"
                ),
                "mean_stock_return": None,
                "mean_benchmark_return": None,
                "mean_excess_return": None,
                "reason": None,
            }
            if len(windows) >= EVENT_SAMPLE_MIN:
                sample.update(
                    {
                        "mean_stock_return": round(
                            sum(w["stock_return"] for w in windows) / len(windows), 6
                        ),
                        "mean_benchmark_return": round(
                            sum(w["benchmark_return"] for w in windows) / len(windows), 6
                        ),
                        "mean_excess_return": round(
                            sum(w["excess_return"] for w in windows) / len(windows), 6
                        ),
                    }
                )
            elif count == 0:
                sample["reason"] = _empty_event_reason(dataset, self.data_root)
            else:
                # 把「为什么算不出来」的原因带出来：只有「0 < 8」等于没说，
                # 页面要能看出是基准覆盖不到还是个股缺收盘价（QUOT-446 缺口清单）。
                causes = Counter(
                    event["window"]["reason"]
                    for event in events
                    if event["window"]["reason"]
                )
                top = causes.most_common(1)[0] if causes else None
                sample["reason"] = (
                    f"可计算窗口 {len(windows)} < {EVENT_SAMPLE_MIN}，按契约不给均值"
                    + (f"；{top[0]}（{top[1]}/{count} 个事件）" if top else "")
                )
            types.append(
                {
                    "dataset": dataset,
                    "label": label,
                    "count": count,
                    "dates": date_series,
                    "events": events,
                    "sample": sample,
                }
            )

        if not bench_days:
            status = "missing"
        elif computed_any:
            status = "ok"
        elif any(item["count"] for item in types):
            status = "partial"
        else:
            status = "missing"
        as_of = bench_days[-1] if bench_days else None
        payload: dict[str, Any] = {
            "code": code,
            "name": name,
            "window_before": before,
            "window_after": after,
            "benchmark": BENCHMARK_CODE,
            "formula": "窗口收益 = close[end] / close[start] - 1；超额 = 个股 - 000300",
            "trading_days": len(bench_days),
            "benchmark_rows": len(bench_rows),
            "event_types": types,
            "counts": {item["dataset"]: item["count"] for item in types},
            "data_source_status": status,
        }
        return {
            "payload": strip_private(payload),
            "data_source_status": status,
            "data_as_of": as_of,
            "meta": {
                "topic": "research_event_study",
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

    @router.get("/{code}/kline")
    def research_kline(
        code: str,
        period: Annotated[
            str | None, Query(description="daily / weekly / monthly，缺省 daily")
        ] = None,
        adjust: Annotated[
            str | None, Query(description="qfq / hfq / none，缺省 qfq")
        ] = None,
        limit: Annotated[
            int | None, Query(gt=0, description="根数，缺省 120，上限 500")
        ] = None,
    ) -> dict[str, Any]:
        """K 线图（QUOT-410）：三档周期 × 三档复权运行时透传给 `tencent_kline`。"""
        return plane.kline(code, period, adjust, limit)

    @router.get("/{code}/earnings-brief")
    def research_earnings_brief(
        code: str,
        periods: Annotated[
            int | None,
            Query(gt=0, le=EARNINGS_PERIODS_MAX, description="报告期数，缺省 6，上限 12"),
        ] = None,
    ) -> dict[str, Any]:
        """财报前瞻（QUOT-446）：一致预期 + 最近 N 期财报明细，只读缓存。"""
        return plane.earnings_brief(code, periods)

    @router.get("/{code}/event-study")
    def research_event_study(
        code: str,
        sources: Annotated[
            str | None,
            Query(description="事件类型，逗号分隔；缺省全部六类"),
        ] = None,
        window_before: Annotated[
            int | None, Query(ge=0, le=EVENT_WINDOW_MAX, description="事件日前交易日数")
        ] = None,
        window_after: Annotated[
            int | None, Query(ge=0, le=EVENT_WINDOW_MAX, description="事件日后交易日数")
        ] = None,
    ) -> dict[str, Any]:
        """事件回看（QUOT-446）：各类型日期序列 + 每事件对 000300 的窗口超额。"""
        return plane.event_study(code, sources, window_before, window_after)

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
    "BENCHMARK_CODE",
    "DAY_PATTERN",
    "EARNINGS_PERIODS_DEFAULT",
    "EARNINGS_PERIODS_MAX",
    "ENGINE_UNAVAILABLE_DETAIL",
    "EMPTY_RECORD_DETAIL",
    "EVENT_SAMPLE_MIN",
    "EVENT_SOURCES",
    "EVENT_SOURCE_LABELS",
    "EVENT_WINDOW_AFTER_DEFAULT",
    "EVENT_WINDOW_BEFORE_DEFAULT",
    "EVENT_WINDOW_MAX",
    "JOB_STATUSES",
    "KLINE_ADJUSTS",
    "KLINE_LIMIT_DEFAULT",
    "KLINE_LIMIT_MAX",
    "KLINE_PERIODS",
    "OVERVIEW_BARS",
    "RESEARCH_JOB_TIMEOUT_SEC",
    "RESEARCH_PREFIX",
    "ResearchJob",
    "ResearchPlane",
    "STAGE_STATUSES",
    "UNKNOWN_CODE_DETAIL",
    "build_router",
    "code_exists",
    "parse_adjust",
    "parse_code",
    "parse_day",
    "parse_limit",
    "parse_period",
    "parse_periods",
    "parse_sources",
    "parse_stage",
    "parse_window",
    "require_known_code",
]
