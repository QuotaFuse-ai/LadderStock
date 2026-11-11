"""R12 收口：资讯雷达 / 事件日历 / 候选池 / 我的持仓（QUOT-279 / QUOT-285）。

前端四页（`frontend/src/screens/radar/`）已经在 main 上，字段写死在
`frontend/src/api/radar.ts` 与 `docs/specs/SPEC-RADAR-PAGES.md` §3–§6.2。这里按那份契约
做投影，前端零改动即可从演示态切真实通道。

四条口径（详见 `docs/decisions/DEC-030-radar-http-endpoints.md` 与 DEC-033）：

1. **只做投影，不重算、不改口径**：资讯流投影 R6 的本机资讯库
   （`news.store.load_items()` + `news.events.extract_events()`）、日历走 R6
   `news.calendar.build_calendar()`、候选池走 R7 `core.pool.compute_pool()`、
   持仓走 R7 `journal.position.compute_metrics()` + 与 CLI 同一份现价容忍表
   （`core.quotes.price_map()`）。四个读接口一个字都不重新计算指标。
2. **GET 不取数、不写库**：资讯是「按需按时间窗」的（`config/sources.yaml` 注释、
   `ldst news` 的 `--store` 写路径），采集归 CLI / 计划任务；页面按 8 秒超时读接口，
   一次跨 8 栏 × ≤31 天的实时取数永远做不到「刷新即出数」，所以 `/api/news` 只读本机
   `news.sqlite`，栏目状态从 `news_runs` 的账本回放（挂了就是挂了，不粉饰）。
   日历的两个有源小节（解禁 / 业绩预告）**本机没有落库路径**，不取数就永远是空的，
   所以 `/api/events` 保留实时取数（两个数据集、有界），来源挂掉时按 §4 出
   `no_source` + `reason` + `ask`。
3. **闸门**：`/api/news`、`/api/events`、`/api/pool` 是公开数据，跟 `/api/sentiment` 一样
   属于数据面，不加回环独占；`/api/positions` 是隐私数据，**回环 + `Host` 白名单独占、
   平面 JSON 不进信封**（SPEC-RADAR-PAGES §6.1 / DEC-015 同一条例外）。
4. **写面四条、全是 POST**（QUOT-285 / QUOT-419）：`POST /api/positions` 按 `code` upsert、
   `POST /api/positions/{code}/delete` 硬删除；`POST /api/watchlist` 加一条自选（幂等）、
   `POST /api/watchlist/{code}/delete` 删一条。不提供 `PUT` / `PATCH` / `DELETE` 动词，
   也不留软删除标记与审计链——本地 SQLite 就是唯一副本，要留底由用户自己导出。
   写方法在回环 + `Host` 之外还要**同源 `Origin` 或 `X-LDST-Client: 1`**（复用控制面
   的 `write_source_allowed`，不新增第二种鉴权）。

错误码照 §7 缺口清单：参数非法 `422`（控制面用 400，这里前端按 422 写死，不能跟）、
本机库读不动 `500`、非回环访问持仓 `403`；「上游全挂但结构完整」是 `200` +
`data_source_status="missing"`，不是 404，也不伪造行。

载荷里 `payload.data_as_of` 是与信封同值的副本：前端 `unwrap()` 只下钻 `payload`，
信封顶层它读不到（同 `web/industry.py` 的处理）。
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.calc import safe_ratio
from ladderstock.core import indices as indices_module
from ladderstock.core import pool as pool_module
from ladderstock.core.boundary import owner_of
from ladderstock.core.models import (
    Confidence,
    Domain,
    Freshness,
    LadderEnvelope,
    SourceStatus,
)
from ladderstock.core.portfolio import COST_METHODS as PORTFOLIO_METHODS
from ladderstock.core.portfolio import HoldingRow, round4
from ladderstock.core.portfolio import replay as replay_portfolio
from ladderstock.core.portfolio_perf import DEFAULT_DAYS as PERF_DEFAULT_DAYS
from ladderstock.core.portfolio_perf import MAX_DAYS as PERF_MAX_DAYS
from ladderstock.core.portfolio_perf import build_perf, load_close_series
from ladderstock.core.portfolio_replay import SCENARIO_IDS, build_replay
from ladderstock.core.portfolio_xray import build_view as build_xray_view
from ladderstock.core.privacy import strip_private
from ladderstock.core.quotes import PRICE_DATASET, daily_change_map, price_map
from ladderstock.core.watch_prices import DATASET as SPOT_QUOTE_DATASET
from ladderstock.core.watch_prices import fetch_prices, judge_freshness
from ladderstock.data import archive
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_SOURCE,
    EXIT_USAGE,
    LadderStockError,
)
from ladderstock.journal.portfolio import add_trade as add_portfolio_trade
from ladderstock.journal.portfolio import adopt_legacy as adopt_portfolio_legacy
from ladderstock.journal.portfolio import delete_trade as delete_portfolio_trade
from ladderstock.journal.portfolio import dismiss_legacy as dismiss_portfolio_legacy
from ladderstock.journal.portfolio import legacy_rows as portfolio_legacy_rows
from ladderstock.journal.portfolio import list_trades as list_portfolio_trades
from ladderstock.journal.portfolio import migrate_legacy as migrate_portfolio_legacy
from ladderstock.journal.portfolio import trade_as_dict as portfolio_trade_dict
from ladderstock.journal.portfolio_import import import_rows as import_portfolio_rows
from ladderstock.journal.portfolio_import import preview as preview_portfolio_import
from ladderstock.journal.position import (
    add_position,
    compute_metrics,
    list_journal_trades,
    list_positions,
    remove_position,
)
from ladderstock.journal.watchlist import add_watch, list_watch, remove_watch
from ladderstock.news import store as news_store
from ladderstock.news import stream as news_stream
from ladderstock.news.calendar import DEFAULT_LIMIT as CALENDAR_DEFAULT_LIMIT
from ladderstock.news.calendar import STATUS_NO_SOURCE, STATUS_SOURCED, build_calendar, parse_month
from ladderstock.news.events import extract_events
from ladderstock.news.models import normalize_code
from ladderstock.news.timeline import build_timeline, parse_timeline_days
from ladderstock.web import control as control_plane

_T = TypeVar("_T")

#: 四个接口共用 `/api` 前缀：路径照 SPEC §3–§6.1，不另开变体
RADAR_PREFIX = "/api"

NEWS_DOMAIN: Domain = "news"
NEWS_TOPIC = "news_stream"
CALENDAR_TOPIC = "event_calendar"
POOL_DOMAIN: Domain = "sentiment"
POOL_TOPIC = "candidate_pool"

#: 首页指数条（QUOT-380）：与候选池同为公开行情面，`domain` 沿用 `sentiment`，
#: `topic` 直接用数据集名，前端 `radar.ts` 按 topic 下钻。
INDICES_DOMAIN: Domain = "sentiment"
INDICES_TOPIC = "index_quote"

#: 栏目账本的状态词表沿用 R6（`ok / empty / skipped / failed`），不另立一套
STATUS_OK = news_stream.STATUS_OK
STATUS_EMPTY = news_stream.STATUS_EMPTY
STATUS_SKIPPED = news_stream.STATUS_SKIPPED
STATUS_FAILED = news_stream.STATUS_FAILED

#: 窗口内该栏目一条都没有时的说明（页面「降级 / 正常」那一列的 title 悬停文案）
NO_ROWS_DETAIL = "本机资讯库在本窗口没有该栏目的条目（取数：ldst news <date> --until <until>）"

#: 空库与「查过但本窗口没有」的文案（页面在空态 / partial 里读 `payload.reason`）
NEWS_STORE_MISSING = (
    "本机还没有资讯库：GET 不代取数，先跑一次 `ldst news <date>`（数据只落本机 data/news.sqlite）"
)
NEWS_WINDOW_EMPTY = (
    "本机缓存里没有这个窗口的资讯：把日期往前挪一档，或补跑 ldst news <date> --until …"
)

#: 单次投影最多读回多少行（`limit` 是展示条数，这是读取上限，两者不是一回事）
NEWS_READ_MAX = 1000

#: `SPEC-RADAR-PAGES` §3 / §5：`limit` 默认 30；§4 沿用 R6 日历的 200
NEWS_DEFAULT_LIMIT = 30
POOL_DEFAULT_LIMIT = 30

#: `limit` 的对外合法区间：上限取 R6/R7 各自内部封顶的最大值，超过就是调用方写错
LIMIT_MIN = 1
LIMIT_MAX = 1000

#: 资讯窗跨度上限（与 `stream.MAX_WINDOW_DAYS` 同一条，先在这里挡住，不白打一轮 peer）
MAX_WINDOW_DAYS = news_stream.MAX_WINDOW_DAYS

#: R6 目前不产出行业标签（SPEC §7 缺口 2）：键给空数组，页面退化为关键词过滤
INDUSTRIES_PLACEHOLDER: list[str] = []

READ_FAILURE = "本机数据读取失败"
WRITE_FAILURE = "本机持仓写入失败"
POSITIONS_DENIED = "持仓只接受回环来源"
#: 自选读面（DEC-052）：闸门判据与持仓一模一样，只是文案指向本单编号
WATCHLIST_DENIED = "自选只接受回环来源（DEC-052）"
TIMELINE_DENIED = "事件时间轴读取自选与持仓，只接受回环来源（DEC-015）"
EMPTY_WATCHLIST = "本机还没有自选：在「我的自选」页加一只 6 位代码（只写本机 SQLite）"
POSITIONS_WRITE_DENIED = "持仓写入缺少来源证据：需要本机页面 Origin，或显式头 X-LDST-Client: 1"
#: 自选写面（QUOT-419 / DEC-055）：与持仓写面同一条来源证据判据，不改宽松度
WATCHLIST_WRITE_DENIED = "自选写入缺少来源证据：需要本机页面 Origin，或显式头 X-LDST-Client: 1"

#: `POST /api/positions` 的请求体白名单（**多一个键就 400**，不做模糊匹配）。
#: 券商账号 / 资金密码类字段不在其中，白名单本身就是拒绝清单。
POSITIONS_WRITE_FIELDS: tuple[str, ...] = ("code", "cost", "qty", "entry_date", "industry", "note")
#: 三个必填字段：缺任一个都是 400（其余三个可选）
POSITIONS_REQUIRED_FIELDS: tuple[str, ...] = ("code", "cost", "qty")

_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_CODE_SEPARATORS = re.compile(r"[,;]")


# --------------------------------------------------------------------------- #
# 参数校验（一律 422）
# --------------------------------------------------------------------------- #

def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def parse_day(raw: str | None, *, name: str = "date", default_today: bool = True) -> date:
    """严格 `YYYY-MM-DD`：缺省今天，其余非法一律 422（不静默当成今天）。"""
    text = (raw or "").strip()
    if not text:
        if default_today:
            return date.today()
        raise _invalid(f"缺少 {name}（YYYY-MM-DD）")
    detail = f"非法日期：{name}={raw!r}（需要 YYYY-MM-DD）"
    if not _DAY_PATTERN.match(text):
        raise _invalid(detail)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise _invalid(detail) from None


def parse_month_param(raw: str | None) -> date:
    """`?month=YYYY-MM`（必填）→ 当月首日。缺失或非法 422。"""
    text = (raw or "").strip()
    if not text:
        raise _invalid("缺少 month（YYYY-MM）")
    if not _MONTH_PATTERN.match(text):
        raise _invalid(f"非法月份：{raw!r}（需要 YYYY-MM，例如 2026-10）")
    start, _ = parse_month(text)
    return start


def parse_codes(values: Sequence[str] | None, *, name: str = "code") -> list[str]:
    """`?code=` 可重复也可逗号 / 分号分隔；逐个规范化成 6 位代码，非法取值 422。

    判据与 R6 `normalize_code()` 同一份：`600519.SH` / `sh600519` / `600519` 都收，
    认不出来的直接拒——悄悄丢掉一个代码会让人以为「这只票今天没有公告」。
    """
    out: list[str] = []
    for raw in values or ():
        for part in _CODE_SEPARATORS.split(str(raw)):
            token = part.strip()
            if not token:
                continue
            code = normalize_code(token)
            if not code:
                raise _invalid(f"非法证券代码：{name}={token!r}（需要 6 位 A 股代码）")
            if code not in out:
                out.append(code)
    return out


def parse_sources(values: Sequence[str] | None) -> list[str]:
    """`?source=`：取值域 = `core.boundary.NEWS_STREAM_DATASETS`，未知栏目 422。

    不给筛选时返回**全部登记栏目**（与 `ldst news` 同口径）：页面的栏目筛选框就是
    按这条清单渲染的，这里回空数组等于告诉页面「今天没有任何栏目可看」。
    """
    wanted = [
        item for raw in values or () for item in _CODE_SEPARATORS.split(str(raw)) if item.strip()
    ]
    try:
        resolved = news_stream.resolve_datasets(wanted)
    except LadderStockError as exc:
        if exc.code == EXIT_USAGE:
            raise _invalid(exc.message) from None
        raise
    return list(resolved)


def parse_days(raw: int | None) -> int:
    """`?days=`：缺省 365；`0` 表示全历史，其余负数 / 超上限都是调用方写错 → 422。"""
    if raw is None:
        return PERF_DEFAULT_DAYS
    if raw < 0 or raw > PERF_MAX_DAYS:
        raise _invalid(
            f"非法 days：{raw}（需要在 0~{PERF_MAX_DAYS} 之间；0 表示全历史）"
        )
    return raw


def parse_limit(raw: int | None, *, default: int) -> int:
    """`?limit=`：缺省用各接口默认值；`0 / 负数 / 超过 {LIMIT_MAX}` 都是调用方写错 → 422。"""
    if raw is None:
        return default
    if raw < LIMIT_MIN or raw > LIMIT_MAX:
        raise _invalid(f"非法 limit：{raw}（需要在 {LIMIT_MIN}~{LIMIT_MAX} 之间）")
    return raw


# --------------------------------------------------------------------------- #
# 持仓写入体校验（写面用 400，读面的参数非法仍是 422）
# --------------------------------------------------------------------------- #

def _bad_body(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _positive_number(raw: Any, key: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _bad_body(f"{key} 必须是数值，收到 {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise _bad_body(f"{key} 必须是有限数值，收到 {raw!r}")
    if value < 0:
        raise _bad_body(f"{key} 不能为负，收到 {raw!r}")
    return value


def _position_text(raw: Any, key: str) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise _bad_body(f"{key} 必须是字符串，收到 {raw!r}")
    return raw.strip()


def _position_entry_date(raw: Any) -> str | None:
    """缺省 / 空串 → `None`（新增取今天、覆盖保留原值）；其余必须是严格 `YYYY-MM-DD`。"""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _bad_body(f"entry_date 必须是 YYYY-MM-DD 字符串，收到 {raw!r}")
    text = raw.strip()
    if not text:
        return None
    if not _DAY_PATTERN.match(text):
        raise _bad_body(f"非法 entry_date：{raw!r}（需要 YYYY-MM-DD）")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise _bad_body(f"非法 entry_date：{raw!r}（需要 YYYY-MM-DD）") from None
    return text


def position_write_fields(raw: Any) -> dict[str, Any]:
    """把 `POST /api/positions` 的请求体收成可落库的六个字段。

    白名单是硬拒绝：多一个键就 400（券商账号 / 资金密码这类字段因此根本没有入口）。
    `cost` / `qty` 只接受 JSON 数值（不把 `"12.3"` 偷偷转成数字）；0 留给
    `journal.position._positive` 按 CLI 同口径拒绝。
    """
    if not isinstance(raw, dict):
        raise _bad_body("请求体必须是 JSON 对象")
    extra = sorted(set(raw) - set(POSITIONS_WRITE_FIELDS))
    if extra:
        raise _bad_body(
            "不接受的字段：" + ", ".join(extra)
            + "（持仓只认 code / cost / qty / entry_date / industry / note）"
        )
    missing = [key for key in POSITIONS_REQUIRED_FIELDS if key not in raw]
    if missing:
        raise _bad_body(f"缺少必填字段：{', '.join(missing)}")
    code = raw["code"]
    if not isinstance(code, str) or not code.strip():
        raise _bad_body("code 必须是非空字符串")
    return {
        "code": code.strip(),
        "cost": _positive_number(raw["cost"], "cost"),
        "qty": _positive_number(raw["qty"], "qty"),
        "entry_date": _position_entry_date(raw.get("entry_date")),
        "industry": _position_text(raw.get("industry"), "industry"),
        "note": _position_text(raw.get("note"), "note"),
    }


def watch_write_code(raw: Any) -> str:
    """把 `POST /api/watchlist` 的请求体收成一只代码。

    只认 `code` 一个键（多一个就 400）；值接受 6 位数字的字符串或 JSON 整数，
    前缀归一化交给 `journal.watchlist.clean_code()`——HTTP 层不另写一套代码规则。
    """
    if not isinstance(raw, dict):
        raise _bad_body("请求体必须是 JSON 对象")
    extra = sorted(set(raw) - {"code"})
    if extra:
        raise _bad_body("不接受的字段：" + ", ".join(extra) + "（自选只认 code）")
    if "code" not in raw:
        raise _bad_body("缺少必填字段：code")
    value = raw["code"]
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _bad_body(f"code 必须是 6 位数字（可带 sh/sz/bj 前缀），收到 {value!r}")
    return str(value)


# --------------------------------------------------------------------------- #
# 持仓账本（QUOT-430 / DEC-056）：成本法、数字口径与平面投影
# --------------------------------------------------------------------------- #

PORTFOLIO_DEFAULT_METHOD = "average"


def portfolio_method(raw: str | None) -> Literal["average", "fifo"]:
    """`?method=` → 成本法；缺省移动加权平均，非法值是 422。"""
    text = str(raw or "").strip().lower() or PORTFOLIO_DEFAULT_METHOD
    if text == "average":
        return "average"
    if text == "fifo":
        return "fifo"
    raise _invalid(f"成本法只能是 fifo（先进先出）或 average（移动加权平均），收到 {raw!r}")


def replay_scenario(raw: str | None) -> str:
    """`?scenario=` → 固定压力窗口 id 或 `all`；缺省 `all`，其余（含空串）422。"""
    if raw is None:
        return "all"
    if raw == "all" or raw in SCENARIO_IDS:
        return raw
    raise _invalid(
        f"非法 scenario：{raw!r}（合法值：{', '.join(SCENARIO_IDS)}, all）"
    )


def portfolio_id(raw: Any, name: str) -> int:
    """路径参数 / 请求体里的整数 id；不是正整数就是 400。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _bad_body(f"{name} 必须是整数，收到 {raw!r}") from None
    if value <= 0:
        raise _bad_body(f"{name} 必须是正整数，收到 {raw!r}")
    return value


def _empty_migration() -> dict[str, Any]:
    return {
        "backup": None,
        "migrated": [],
        "removed_stale": [],
        "failed": [],
        "error": None,
    }


def _price_reason(
    symbol: str, price_lookup: Mapping[str, Any]
) -> str:
    day = price_lookup.get("date")
    if day:
        return f"{PRICE_DATASET} 在 {day} 没有 {symbol} 的收盘价（停牌或未同步）"
    return str(price_lookup.get("reason") or f"本机缓存里没有 {PRICE_DATASET} 数据")


def holding_item(
    row: HoldingRow,
    prices: Mapping[str, float],
    daily: Mapping[str, Mapping[str, Any]],
    price_lookup: Mapping[str, Any],
) -> dict[str, Any]:
    """重放出来的一行 + 现价拼接。取不到现价就是 `null` + 原因，**不拿成本价顶替**。"""
    price = prices.get(row.symbol)
    daily_row = daily.get(row.symbol) or {}
    change = daily_row.get("change")
    prev_close = daily_row.get("prev_close")
    change_pct: float | None = None
    if change is not None and prev_close:
        daily_share = safe_ratio(float(change), float(prev_close))
        if daily_share.ok and daily_share.value is not None:
            change_pct = round4(float(daily_share.value) * 100.0)
    market_value = round4(price * row.shares) if price is not None else None
    pnl = round4(market_value - row.cost_total) if market_value is not None else None
    pnl_pct: float | None = None
    if pnl is not None and row.cost_total > 0:
        cost_share = safe_ratio(pnl, row.cost_total)
        if cost_share.ok and cost_share.value is not None:
            pnl_pct = round4(float(cost_share.value) * 100.0)
    return {
        "symbol": row.symbol,
        "name": row.name,
        "shares": row.shares,
        "unit_cost": row.unit_cost,
        "cost_total": row.cost_total,
        "opened_at": row.opened_at,
        "lots": [
            {
                "trade_id": lot.trade_id,
                "date": lot.date,
                "shares": lot.shares,
                "unit_cost": lot.unit_cost,
            }
            for lot in row.lots
        ],
        "realized_pnl": row.realized_pnl,
        "dividends": row.dividends,
        "fees": row.fees,
        "trade_count": row.trade_count,
        "latest_price": price,
        "change": change,
        "change_pct": change_pct,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "price_reason": None if price is not None else _price_reason(row.symbol, price_lookup),
    }


def cny_totals(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """人民币合计：只汇总取到现价的行；缺价的不计入，原因由调用方写进 `price_lookup`。"""
    priced = [item for item in items if item.get("latest_price") is not None]
    unpriced = len(items) - len(priced)
    if not items:
        reason: str | None = "本机还没有持仓流水"
    elif not priced:
        reason = "持仓都还没有现价，人民币合计算不出来"
    elif unpriced:
        reason = f"{unpriced} 只持仓没有现价，未计入人民币合计"
    else:
        reason = None
    if not priced:
        return {
            "currency": "CNY",
            "available": False,
            "market_value": None,
            "cost": None,
            "pnl": None,
            "pnl_pct": None,
            "priced_count": 0,
            "unpriced_count": unpriced,
            "reason": reason,
        }
    cost = round4(sum(float(item["cost_total"]) for item in priced))
    market_value = round4(sum(float(item["market_value"]) for item in priced))
    pnl = round4(market_value - cost)
    pnl_pct: float | None = None
    if cost > 0:
        total_share = safe_ratio(pnl, cost)
        if total_share.ok and total_share.value is not None:
            pnl_pct = round4(float(total_share.value) * 100.0)
    return {
        "currency": "CNY",
        "available": True,
        "market_value": market_value,
        "cost": cost,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "priced_count": len(priced),
        "unpriced_count": unpriced,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# 状态判定（与前端 radar.ts 的推导同一套规则，两侧不会读出两个状态）
# --------------------------------------------------------------------------- #

Status = Literal["ok", "partial", "missing"]

_CONFIDENCE: dict[Status, Confidence] = {
    "ok": "high",
    "partial": "medium",
    "missing": "low",
}


def stream_status(statuses: Sequence[Mapping[str, Any]], items: int) -> Status:
    """栏目账本 + 条数 → 页面状态：没有条目就是 missing，有降级/挂掉的就是 partial。

    判据与前端 `radar.ts` 的 `newsStatus()` 同一条（全挂 missing、任一路降级 partial），
    两侧不会读出两个状态。
    """
    usable = [
        row for row in statuses if row.get("status") == STATUS_OK and not row.get("degraded")
    ]
    if not items:
        return "missing"
    if not statuses:
        return "ok"
    if not usable:
        return "partial"
    return "ok" if len(usable) == len(statuses) else "partial"


def calendar_status(sections: Sequence[Any]) -> Status:
    """日历：有源小节全挂 = missing；任一降级 = partial；其余 ok。"""
    sourced = [section for section in sections if section.status == STATUS_SOURCED]
    failed = [
        section
        for section in sections
        if section.status == STATUS_NO_SOURCE and section.degraded
    ]
    if not sourced:
        return "missing"
    if failed or any(section.degraded for section in sourced):
        return "partial"
    return "ok"


def _envelope_source_status(status: Status) -> SourceStatus:
    """外壳词表只有 `ok / degraded / partial`（没有 missing），缺失在这层写作 partial。"""
    return "ok" if status == "ok" else "partial"


# --------------------------------------------------------------------------- #
# 读取面
# --------------------------------------------------------------------------- #

class RadarPlane:
    """四个只读接口的取数与投影。

    一次请求读一次，不做跨请求缓存：本地缓存由采集任务写，接口层再加一层缓存只会让
    页面与 CLI 给出两个答案（同 `web/industry.py` 的取舍，DEC-006）。
    """

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        registry: ProviderRegistry | None = None,
        ensure: Any | None = None,
    ) -> None:
        self.data_root = Path(data_root) if data_root is not None else None
        self._registry = registry
        # 打开即补取（QUOT-555 / DEC-080）：缺省 None = 纯只读，行为与改造前逐字节一致。
        self._ensure = ensure

    # -------------------------------------------------------------- 资讯流 §3

    def news_view(
        self,
        raw_date: str | None,
        raw_until: str | None,
        raw_sources: Sequence[str] | None,
        raw_codes: Sequence[str] | None,
        raw_limit: int | None,
    ) -> dict[str, Any]:
        day = parse_day(raw_date)
        end = (
            parse_day(raw_until, name="until", default_today=False)
            if (raw_until or "").strip()
            else day
        )
        if end < day:
            raise _invalid(f"时间窗非法：until={end.isoformat()} 早于 date={day.isoformat()}")
        if (end - day).days + 1 > MAX_WINDOW_DAYS:
            raise _invalid(
                f"时间窗 {day.isoformat()}→{end.isoformat()} 超过单次上限 {MAX_WINDOW_DAYS} 天"
            )
        datasets = parse_sources(raw_sources)
        codes = parse_codes(raw_codes)
        limit = parse_limit(raw_limit, default=NEWS_DEFAULT_LIMIT)

        note = self._ensure_note("news", day=day, end=end)
        view = self._read(lambda: self._news_projection(day, end, datasets, codes, limit))
        detail = view["source_detail"]
        if note:
            detail = f"{detail}；{note}" if detail else note
        return _envelope(
            domain=NEWS_DOMAIN,
            topic=NEWS_TOPIC,
            day=end,
            payload=view["payload"],
            status=view["status"],
            source_detail=detail,
            freshness="cached",
        )

    def _news_projection(
        self,
        day: date,
        end: date,
        datasets: Sequence[str],
        codes: Sequence[str],
        limit: int,
    ) -> dict[str, Any]:
        """本机 `news.sqlite` → §3 的载荷：一次只读连接，不取数、不写库。

        键与 `ldst news --output json` 的载荷逐项对齐（`StreamResult.as_dict()`），
        差集只有两处刻意为之：`new_items` / `known_items` / `outside_window` 是**取数那一刻**
        才知道的数，投影问不出来就置 `null`，不拿窗口条数冒充；`events[]` 是 §3 的页面字段，
        对「这一屏真正给出的条目」重跑一遍规则，保证标注与条目一致。
        """
        conn = news_store.connect(self.data_root, create=False)
        if conn is None:
            return _empty_news_view(day, end, datasets, codes)
        try:
            # 读上限守住最坏情况：窗口 ≤31 天，但一次读全表不该由一个 GET 决定
            rows = news_store.load_items(
                conn, start=day, end=end, sources=list(datasets), limit=NEWS_READ_MAX
            )
            counts = news_store.source_counts(conn, start=day, end=end)
            run = news_store.covering_run(conn, start=day, end=end)
        finally:
            news_store.close(conn)

        dated = [item for item in rows if item.day is not None]
        dropped_undated = len(rows) - len(dated)
        matched = [
            item for item in dated if not codes or set(codes) & set(item.related_codes)
        ]
        shown = matched[:limit]
        statuses = _news_statuses(datasets, counts, run)
        report = extract_events(shown)
        events = [event.as_dict() for event in report.events]
        status = stream_status(statuses, len(shown))
        payload: dict[str, Any] = {
            "ok": status != "missing",
            "domain": NEWS_DOMAIN,
            "topic": NEWS_TOPIC,
            "window": {"start": day.isoformat(), "end": end.isoformat()},
            "sources": list(datasets),
            "codes": list(codes),
            "summary": {
                "items": len(shown),
                "fetched": _run_number(run, "fetched"),
                "new_items": None,
                "known_items": None,
                "merged_duplicates": _run_number(run, "deduped"),
                "dropped_undated": dropped_undated,
                "outside_window": None,
                "stored": _run_number(run, "stored"),
                "events": len(events),
                "persisted": True,
            },
            "sources_status": statuses,
            "extraction": report.as_dict(),
            "items": [
                {**item.as_dict(), "industries": list(INDUSTRIES_PLACEHOLDER)}
                for item in shown
            ],
            "events": events,
            "exit_code": _ledger_exit_code(statuses),
        }
        reason = _news_reason(statuses, shown)
        if reason:
            payload["reason"] = reason
        return {
            "payload": payload,
            "status": status,
            "source_detail": _news_detail(day, end, statuses, run),
        }

    # -------------------------------------------------------------- 事件日历 §4

    def events_view(
        self,
        raw_month: str | None,
        raw_codes: Sequence[str] | None,
        raw_limit: int | None,
    ) -> dict[str, Any]:
        month_start = parse_month_param(raw_month)
        codes = parse_codes(raw_codes)
        limit = parse_limit(raw_limit, default=CALENDAR_DEFAULT_LIMIT)
        month = month_start.strftime("%Y-%m")

        result = self._read(
            lambda: build_calendar(
                month,
                codes=codes,
                registry=self._active_registry(),
                root=self.data_root,
                limit=limit,
            )
        )
        payload = result.as_dict()
        status = calendar_status(result.sections)
        return _envelope(
            domain=NEWS_DOMAIN,
            topic=CALENDAR_TOPIC,
            day=month_start,
            payload=payload,
            status=status,
            source_detail=_calendar_detail(result),
            freshness="realtime",
        )

    # ------------------------------------------------------ 事件时间轴（QUOT-436）

    def timeline_view(self, raw_days: int | None) -> dict[str, Any]:
        """`GET /api/events/timeline`：自选 + 持仓的未来 7 / 30 / 90 天公司事件。

        平面 JSON（不进 `LadderEnvelope`）：标的集合来自本机自选与持仓账本，
        属于 DEC-015 的隐私面——回环独占 + `no-store`，与 `/api/portfolio` 同一条闸门。
        取数失败是 `gaps` 里的一条，不是 500，也不补日期（DEC-018 R6）。
        """
        try:
            days = parse_timeline_days(raw_days)
        except ValueError as exc:
            raise _invalid(str(exc)) from None
        return self._read(
            lambda: build_timeline(
                days,
                root=self.data_root,
                registry=self._active_registry(),
            )
        )

    # -------------------------------------------------------------- 指数条 §9.1

    def indices_view(self) -> dict[str, Any]:
        """首页指数条（QUOT-380 / QUOT-471 / QUOT-560）：一次批量 + 四次逐 symbol 投影八格。

        公开行情面，**不加回环独占**（判据与 `/api/news` `/api/pool` 同一条）。
        八格的顺序、条数、名称由 `core.indices` 的白名单写死（A 股 4 + 美股 2 + 港股 2）；
        peer 回什么就用什么，取不到的格子是 `available=false` + 原因，`data_source_status` 走
        `ok / partial / missing`（全挂是 missing，不是 404，也不伪造行）。
        港股两格走 `index_quote_hk`（peer 端别名映射，腾讯/新浪免费源，零 key）。
        """
        registry = self._active_registry()
        active = registry if registry is not None else ProviderRegistry()
        result = self._read(lambda: indices_module.fetch_indices(active))
        return _envelope(
            domain=INDICES_DOMAIN,
            topic=INDICES_TOPIC,
            day=result.day,
            payload=result.payload,
            status=result.status,
            source_detail=result.source_detail,
            freshness="realtime",
        )

    # ---------------------------------------------------------------- 候选池 §5

    def pool_view(self, raw_date: str | None, raw_limit: int | None) -> dict[str, Any]:
        day = parse_day(raw_date)
        limit = parse_limit(raw_limit, default=POOL_DEFAULT_LIMIT)
        note = self._ensure_note("pool", day=day)
        result = self._read(
            lambda: pool_module.compute_pool(day, root=self.data_root, limit=limit)
        )
        available = bool(result.get("available"))
        fund_freshness, fund_fetched_at, fund_pcts = _pool_fund_provenance(
            day, result, self.data_root
        )
        # 净占比透传到每只候选的 raw（QUOT-372）：批量工具定稿字段 `main_net_pct`，
        # 取不到就是 None，前端显示「—」，不拿 0 冒充。
        for row in result.get("candidates") or []:
            raw = row.get("raw") if isinstance(row, Mapping) else None
            if isinstance(raw, dict):
                code = str(row.get("code") or "")
                raw["main_net_pct"] = fund_pcts.get(code)
        pool_detail = str(result.get("source_detail") or "")
        if note:
            pool_detail = f"{pool_detail}；{note}" if pool_detail else note
        return _envelope(
            domain=POOL_DOMAIN,
            topic=POOL_TOPIC,
            day=day,
            payload=result,
            status="ok" if available else "missing",
            source_detail=pool_detail,
            # freshness 按资金数据的真实来源给（QUOT-372）：命中当日实时批量分区
            # 才是 realtime；回落日线仍是本地缓存回放，如实标 cached。
            freshness="realtime" if fund_freshness == "realtime" else "cached",
            fund_freshness=fund_freshness,
            fund_fetched_at=fund_fetched_at,
        )

    # ---------------------------------------------------------------- 我的持仓 §6.1

    def positions_view(self, raw_date: str | None) -> dict[str, Any]:
        """平面 JSON（**不进 `LadderEnvelope`**）：载荷 = `ldst position list --output json`。

        只读：`list_positions()` 与 `list_journal_trades()` 都用 `create=False`，
        本机没有持仓库 / 没记过成交时不会建空库。现价取不到就是 `null`，
        原因随 `price_lookup.reason` 给出，不补 0、不猜价。

        已实现盈亏不在这儿另算一遍：流水取到之后交给 `compute_metrics()`，
        与 CLI 走同一个函数、同一份数字（QUOT-292 / DEC-038）。

        当日盈亏同理：`core.quotes.daily_change_map()` 读**同一份 `daily_k` 缓存**算
        `close(T) − close(T−1)`，这里只传进去，不重新算差价，也**没有**因为加这个字段
        把持仓现价改成实时报价（QUOT-380 拍板口径 4）。算不出就是 `null`，原因随
        `daily_lookup` / `items[].daily_pnl_reason` 给出。
        """
        day = raw_date or None
        if day and day.strip():
            parse_day(day)  # 先按 422 挡住，不让 CLI 的用法错误冒成 500
        else:
            day = None
        rows = self._read(lambda: list_positions(root=self.data_root))
        codes = [str(row["code"]) for row in rows]
        prices, lookup = self._read(lambda: price_map(codes, day, root=self.data_root))
        trades = self._read(lambda: list_journal_trades(root=self.data_root))
        daily, daily_lookup = self._read(
            lambda: daily_change_map(codes, lookup["date"], root=self.data_root)
        )
        payload = compute_metrics(
            rows, prices, as_of=lookup["date"], trades=trades, daily=daily
        )
        payload["price_lookup"] = lookup
        payload["daily_lookup"] = daily_lookup
        return payload

    # ------------------------------------------------- 持仓账本 §6.2（QUOT-430 / DEC-056）

    def portfolio_view(self, raw_method: str | None) -> dict[str, Any]:
        """`GET /api/portfolio`：流水重放 + 行情拼接 + 旧仓分类 + 迁移结果。

        平面 JSON（**不进 `LadderEnvelope`**）、`no-store`、回环独占。
        顶层键刻意避开 `holdings` / `positions` / `trades`——它们在
        `core/privacy.py` 的 `PRIVATE_KEYS` 里，走任何信封出口会被静默抹掉。

        GET 会先尝试自动迁移 `pending` 的旧记录（这是本接口唯一会写库的分支，
        且只在确实要迁移时才 `create=True`）；迁移失败不拖垮读面，原因放进
        `migration.error`。本机没有账本时返回空视图，不建空库。
        """
        method = portfolio_method(raw_method)
        migration = self._auto_migrate()
        trades = self._read(lambda: list_portfolio_trades(root=self.data_root))
        legacy = self._read(lambda: portfolio_legacy_rows(root=self.data_root))
        result = replay_portfolio(trades, method)

        codes = [row.symbol for row in result.holdings]
        prices, price_lookup = self._read(
            lambda: price_map(codes, None, root=self.data_root)
        )
        daily, daily_lookup = self._read(
            lambda: daily_change_map(codes, price_lookup["date"], root=self.data_root)
        )
        items = [holding_item(row, prices, daily, price_lookup) for row in result.holdings]
        totals = cny_totals(items)
        if totals["unpriced_count"]:
            note = f"{totals['unpriced_count']} 只持仓没有现价，未计入人民币合计"
            price_lookup = {
                **price_lookup,
                "reason": (
                    f"{price_lookup['reason']}；{note}"
                    if price_lookup.get("reason")
                    else note
                ),
            }

        return {
            "local_only": True,
            "store": "local_sqlite",
            "method": method,
            "method_label": PORTFOLIO_METHODS[method],
            "methods": [
                {"id": key, "label": label}
                for key, label in PORTFOLIO_METHODS.items()
            ],
            "today": date.today().isoformat(),
            "price_lookup": price_lookup,
            "daily_lookup": daily_lookup,
            "items": items,
            "closed": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "realized_pnl": row.realized_pnl,
                    "dividends": row.dividends,
                    "fees": row.fees,
                    "last_date": row.last_date,
                }
                for row in result.closed
            ],
            "realized": [
                {
                    "trade_id": row.trade_id,
                    "symbol": row.symbol,
                    "date": row.date,
                    "shares": row.shares,
                    "proceeds": row.proceeds,
                    "cost": row.cost,
                    "pnl": row.pnl,
                }
                for row in result.realized
            ],
            "dividends": [
                {
                    "trade_id": row.trade_id,
                    "symbol": row.symbol,
                    "date": row.date,
                    "amount": row.amount,
                }
                for row in result.dividends
            ],
            "issues": [
                {
                    "id": row.id,
                    "symbol": row.symbol,
                    "date": row.date,
                    "type": row.type,
                    "why": row.why,
                }
                for row in result.issues
            ],
            "ledger": [portfolio_trade_dict(trade) for trade in trades],
            "legacy": legacy,
            "migration_pending": sum(1 for row in legacy if row["state"] == "pending"),
            "migration": migration,
            "totals_cny": totals,
        }

    # --------------------------------------------- 持仓收益率（QUOT-433 / DEC-064）

    def portfolio_perf_view(self, raw_days: int | None) -> dict[str, Any]:
        """`GET /api/portfolio/perf`：TWR / 资金加权 / 净值序列。

        平面 JSON、`no-store`、回环独占（与 `/api/portfolio` 同一条闸门）。
        只读本机 `daily_k` 缓存：不联网、不落盘、不调模型。流水为空 → `groups=[]`
        + `notes` 说明，不报 500；缺行情 → `gaps` 逐条原因，不补价、不插值。

        `days` 只裁剪走势序列的回看天数（缺省 365，0 = 全历史）；收益指标始终按
        全部流水计算，`start / end / span_days` 也是全部流水的区间。
        """
        days = parse_days(raw_days)
        trades = self._read(lambda: list_portfolio_trades(root=self.data_root))
        return self._read(lambda: build_perf(trades, root=self.data_root, days=days))

    # ------------------------------------------------- 持仓 X 光（QUOT-437）

    def portfolio_xray_view(self) -> dict[str, Any]:
        """`GET /api/portfolio/xray`：权重 / 集中度 / 行业暴露 / 相关系数 / 缺口。

        平面 JSON、`no-store`、回环独占（与 `/api/portfolio` 同一条闸门）。权重按
        现价市值算，只依赖重放出来的**持有数量**——两种成本法持有数量一样，所以
        这里不收 `method`，避免让页面以为换个成本法会算出另一份权重。

        现价只读本机 `daily_k`（`price_map`），相关系数只读本机 `daily_k` 收盘序列
        （`load_close_series`）：缺价 / 缺日线的持仓进 `gaps` 并说明原因，不补 0、
        不拿成本价顶替，也不联网。
        """
        trades = self._read(lambda: list_portfolio_trades(root=self.data_root))
        holdings = replay_portfolio(trades, "average").holdings
        codes = [row.symbol for row in holdings]
        prices, price_lookup = self._read(
            lambda: price_map(codes, None, root=self.data_root)
        )
        positions = self._read(lambda: list_positions(root=self.data_root))
        industries = {
            str(row.get("code") or "").strip().upper(): str(row.get("industry") or "")
            for row in positions
        }
        closes = self._read(
            lambda: load_close_series(codes, root=self.data_root)
        )
        view = build_xray_view(
            holdings, prices, industries, closes, as_of=price_lookup["date"]
        )
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "price_source": PRICE_DATASET,
            "price_lookup": price_lookup,
            **view,
        }

    def portfolio_import_preview(self, body: Any) -> dict[str, Any]:
        """`POST /api/portfolio/ledger/import/preview`：逐行校验 + 重复标记，不写库。

        用法类错误（行不合法 / 超限）一律 422（SPEC §7），`_read` 已经按这条映射；
        预览连备份都不落，所以复用读面的错误映射。
        """
        view = self._read(
            lambda: preview_portfolio_import(body, root=self.data_root)
        )
        return {"local_only": True, "store": "local_sqlite", **view}

    def portfolio_import(self, body: Any) -> dict[str, Any]:
        """`POST /api/portfolio/ledger/import`：再校验 → 备份 → 事务内整批写入。

        任何一行不合法 / 重复 / 会让某笔卖出超过持有，整批 422、一行都不写；
        用法类错误按 SPEC §7 用 422（单笔写入沿用 400，这里不改它）。
        """
        result = self._import_write(
            lambda: import_portfolio_rows(body, root=self.data_root)
        )
        return {"local_only": True, "store": "local_sqlite", **result}
    # --------------------------------------------- 情景回放（QUOT-466）

    def portfolio_replay_view(self, raw_scenario: str | None) -> dict[str, Any]:
        """`GET /api/portfolio/replay`：按当前持仓市值权重回放四段固定压力窗口。

        平面 JSON、`no-store`、与 `/api/portfolio` 同一条回环闸门。只读本机日线：
        不联网、不落盘、不建库；空账本 → 200 + 空 `groups`，缺行情 → `gaps`
        逐条原因，不给任何组合收益。
        """
        scenario = replay_scenario(raw_scenario)
        trades = self._read(lambda: list_portfolio_trades(root=self.data_root))
        return self._read(
            lambda: build_replay(trades, root=self.data_root, scenario=scenario)
        )

    def _auto_migrate(self) -> dict[str, Any]:
        """自动迁移旧记录；失败不拖垮读面，原因写进 `migration.error`。"""
        try:
            result = migrate_portfolio_legacy(root=self.data_root)
            return {**_empty_migration(), **result}
        except LadderStockError as exc:
            return {**_empty_migration(), "error": exc.message}
        except (OSError, sqlite3.Error) as exc:
            return {**_empty_migration(), "error": str(exc)}

    def portfolio_add_ledger(self, body: Any) -> dict[str, Any]:
        """`POST /api/portfolio/ledger`：新增一笔流水（只写 `portfolio_trades`）。"""
        trade = self._write(lambda: add_portfolio_trade(body, root=self.data_root))
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "trade": trade,
        }

    def portfolio_delete_ledger(self, raw_id: Any) -> dict[str, Any]:
        """`POST /api/portfolio/ledger/{id}/delete`：硬删一笔流水，删前先备份。"""
        trade_id = portfolio_id(raw_id, "流水 id")
        removed = self._write(
            lambda: delete_portfolio_trade(trade_id, root=self.data_root)
        )
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            **removed,
        }

    def portfolio_adopt(self, raw_id: Any) -> dict[str, Any]:
        """`POST /api/portfolio/legacy/{position_id}/adopt`：确认转成期初流水。

        已经按它转过一笔的**原地替换**那一笔，不新增第二笔。
        """
        position_id = portfolio_id(raw_id, "旧记录 id")
        migration = self._write(
            lambda: adopt_portfolio_legacy(position_id, root=self.data_root)
        )
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "migration": migration,
        }

    def portfolio_dismiss(self, raw_id: Any) -> dict[str, Any]:
        """`POST /api/portfolio/legacy/{position_id}/dismiss`：只写 mark，不删旧行。"""
        position_id = portfolio_id(raw_id, "旧记录 id")
        mark = self._write(
            lambda: dismiss_portfolio_legacy(position_id, root=self.data_root)
        )
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "mark": mark,
        }

    # ---------------------------------------------------------------- 我的自选 §9.2

    def watchlist_view(self) -> dict[str, Any]:
        """`GET /api/watchlist`：**平面 JSON、只 GET、回环独占**（DEC-052 拍板口径 1）。

        载荷 = 本机自选清单（`code` + 加入时间）+ 每条的现价与涨跌
        （`core.watch_prices` 复用已登记的 `spot_quote`）。QUOT-427 起每条再多
        `pe_ttm` / `pb` / `turnover_pct` 三列与逐条 `metrics_reason`：上游给什么就
        投影什么，缺的列固定 `null`，不补 0、不估算。四条纪律都落在这个形状里：

        - **不进 `LadderEnvelope`**：`payload` / `domain` / `fetched_at` 一个键都不出现。
          这是隐私数据到不了 export / MCP / AI 三条出口的**结构性**原因（DEC-015），
          不是装饰；
        - **字段名不叫 `watchlist` / `自选`**：这两个词在 `core/privacy.py` 的
          `PRIVATE_KEYS` 里，走任何信封出口会被 `strip_private()` 静默删字段。
          顶层用 `items` + `watch_count`；
        - **`note` 不出**（QUOT-380 拍板口径 2）：投影在 `journal/watchlist.py` 就不读那一列；
        - **空态是 200**：本机没有自选就回 `available=false` + 指向「我的自选」页的文案，
          不是 404、不伪造行；
        - **`freshness` 是顶层唯一显式给的溯源档**（QUOT-478）：按 `core.watch_prices.
          judge_freshness()` 的时点形状判 §5.1 三档，判不出回 `null`（前端出「未知」，
          不给默认值）。`source_status` / `fetched_at` 仍不出——那是信封键，平面响应
          不碰（DEC-052）。
        """
        requested_at = datetime.now().astimezone()
        rows = self._read(lambda: list_watch(root=self.data_root))
        codes = [str(row["code"]) for row in rows]
        registry = self._active_registry()
        active = registry if registry is not None else ProviderRegistry()
        strip = self._read(lambda: fetch_prices(active, codes))

        items: list[dict[str, Any]] = []
        for row in rows:
            code = str(row["code"])
            quote = strip.prices.get(code) or {
                "available": False,
                "name": None,
                "market": None,
                "latest_price": None,
                "change": None,
                "change_pct": None,
                "prev_close": None,
                "price_as_of": None,
                "price_status": "unavailable",
                "price_reason": "这批自选没有取到报价",
                "pe_ttm": None,
                "pb": None,
                "turnover_pct": None,
                "metrics_reason": "这批自选没有取到报价",
            }
            items.append({"code": code, "added_at": row.get("created_at"), **quote})

        return {
            "local_only": True,
            "store": "local_sqlite",
            "available": bool(items),
            "reason": None if items else EMPTY_WATCHLIST,
            "watch_count": len(items),
            "data_source_status": strip.status,
            "price_source": SPOT_QUOTE_DATASET,
            "price_as_of": strip.as_of,
            "freshness": judge_freshness(
                strip.as_of,
                priced=strip.status != "missing",
                cache_replay=strip.cache_replay,
                now=requested_at,
            ),
            "source_detail": strip.source_detail,
            "items": items,
        }

    # -------------------------------------------------------------- 自选写入 §9.2

    def watch_add(self, body: Any) -> dict[str, Any]:
        """`POST /api/watchlist`：加一条自选，平面 JSON、不进信封（QUOT-419 / DEC-055）。

        落库走 `journal.watchlist.add_watch()`，与 CLI `ldst watch add` 同一个函数：
        已存在就原样返回，不刷新 `created_at`、不报错。
        """
        code = watch_write_code(body)
        row = self._write(lambda: add_watch(code, root=self.data_root))
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "watch": row,
        }

    def watch_delete(self, raw_code: str) -> dict[str, Any]:
        """`POST /api/watchlist/{code}/delete`：硬删除一条自选；不在清单里就是 404。"""
        code = str(raw_code or "").strip()
        if not code:
            raise _bad_body("code 不能为空")
        removed = self._write(lambda: remove_watch(code, root=self.data_root))
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "removed": {"code": removed},
        }

    # -------------------------------------------------------------- 持仓写入 §6.2

    def position_upsert(self, body: Any) -> dict[str, Any]:
        """`POST /api/positions`：一条持仓按 `code` upsert，平面 JSON、不进信封。

        不新增存储、不改表结构：落库仍走 `journal.position.add_position(upsert=True)`，
        SQLite 的 `positions` 表就是唯一真相。
        """
        fields = position_write_fields(body)
        row = self._write(lambda: add_position(**fields, upsert=True, root=self.data_root))
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "position": row,
        }

    def position_delete(self, raw_code: str) -> dict[str, Any]:
        """`POST /api/positions/{code}/delete`：硬删除，不留软删除标记、不加审计链。"""
        code = str(raw_code or "").strip()
        if not code:
            raise _bad_body("code 不能为空")
        outcome = self._write(lambda: remove_position(code, root=self.data_root))
        return {
            "ok": True,
            "local_only": True,
            "store": "local_sqlite",
            "removed": {
                "code": outcome["code"],
                "cost": outcome["cost"],
                "qty": outcome["qty"],
            },
        }

    # ------------------------------------------------------------------ 内部

    def _ensure_note(self, group: str, *, day: date, end: date | None = None) -> str:
        """打开即补取（QUOT-555）：未接线返回空串；接线时触发懒补取并取回一个说明短语。

        补取永不抛异常（`OpenEnsure` 内部全收），失败也只产出一句 note，GET 仍按本机真实
        读取出空态——不回落演示数据（SPEC-HOME-V2 §5.6）。
        """
        if self._ensure is None:
            return ""
        return self._ensure.ensure(group, day=day, end=end).note()

    def _active_registry(self) -> ProviderRegistry | None:
        """测试注入的注册表优先；默认每次请求按 `config/sources.yaml` 现建一个。"""
        return self._registry

    def _read(self, thunk: Callable[[], _T]) -> _T:
        """错误映射：用法类（code 2）→ 422、配置类（code 3）→ 503、其余读不动 → 500。"""
        try:
            return thunk()
        except LadderStockError as exc:
            if exc.code == EXIT_USAGE:
                raise _invalid(exc.message) from None
            if exc.code == EXIT_CONFIG:
                raise HTTPException(status_code=503, detail=exc.message) from None
            raise HTTPException(status_code=500, detail=f"{READ_FAILURE}：{exc.message}") from None
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise HTTPException(status_code=500, detail=f"{READ_FAILURE}：{exc}") from None

    def _write(self, thunk: Callable[[], _T]) -> _T:
        """写面错误映射：用法类 → 400、无结果 → 404、其余写不动 → 500（不冒充 200）。"""
        try:
            return thunk()
        except LadderStockError as exc:
            if exc.code == EXIT_NOT_FOUND:
                raise HTTPException(status_code=404, detail=exc.message) from None
            if exc.code == EXIT_USAGE:
                raise _bad_body(exc.message) from None
            raise HTTPException(
                status_code=500, detail=f"{WRITE_FAILURE}：{exc.message}"
            ) from None
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=500, detail=f"{WRITE_FAILURE}：{exc}") from None

    def _import_write(self, thunk: Callable[[], _T]) -> _T:
        """批量导入的错误映射：用法类（行不对 / 超限 / 重复）按 SPEC §7 一律 422。"""
        try:
            return thunk()
        except LadderStockError as exc:
            if exc.code == EXIT_USAGE:
                raise _invalid(exc.message) from None
            if exc.code == EXIT_CONFIG:
                raise HTTPException(status_code=503, detail=exc.message) from None
            raise HTTPException(
                status_code=500, detail=f"{WRITE_FAILURE}：{exc.message}"
            ) from None
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=500, detail=f"{WRITE_FAILURE}：{exc}") from None


def _empty_news_view(
    day: date, end: date, datasets: Sequence[str], codes: Sequence[str]
) -> dict[str, Any]:
    """本机还没有资讯库：结构完整的 `200` + `data_source_status="missing"`，不是 404。

    `sources_status` 出空数组而不是「全栏目标 empty」：空库的真相是「从来没取过」，
    把 8 个栏目都写成 empty 会让人以为查过 8 路都没有。
    """
    return {
        "payload": {
            "ok": False,
            "domain": NEWS_DOMAIN,
            "topic": NEWS_TOPIC,
            "window": {"start": day.isoformat(), "end": end.isoformat()},
            "sources": list(datasets),
            "codes": list(codes),
            "summary": {
                "items": 0,
                "fetched": None,
                "new_items": None,
                "known_items": None,
                "merged_duplicates": None,
                "dropped_undated": 0,
                "outside_window": None,
                "stored": None,
                "events": 0,
                "persisted": False,
            },
            "sources_status": [],
            "extraction": {
                "events": 0,
                "by_type": {},
                "skipped_no_url": 0,
                "skipped_excluded_source": 0,
                "suppressed_by_negation": [],
            },
            "items": [],
            "events": [],
            "exit_code": EXIT_OK,
            "reason": NEWS_STORE_MISSING,
        },
        "status": "missing",
        "source_detail": (
            f"本机资讯库不存在（{news_store.DB_FILENAME}）：GET 不取数，请先 ldst news"
        ),
    }


def _news_statuses(
    datasets: Sequence[str], counts: Mapping[str, int], run: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """栏目账本：条数来自本机库，失败与降级的原因来自那一次取数的账本（不重新打 peer）。

    「窗口内 0 条」有两种含义，这里分得开：账本里记着 `failed` / `skipped` 的照原样回放
    （挂了就是挂了）；账本里是 `ok` 但本机没有行，说明稿子落在窗口或 limit 之外，
    按 `empty` 说清去哪个命令补。
    """
    recorded: dict[str, Mapping[str, Any]] = {}
    detail = run.get("detail") if isinstance(run, Mapping) else None
    rows = detail.get("sources_status") if isinstance(detail, Mapping) else None
    for row in rows or ():
        if isinstance(row, Mapping) and row.get("dataset"):
            recorded[str(row["dataset"])] = row
    out: list[dict[str, Any]] = []
    for dataset in datasets:
        binding = owner_of(dataset)
        head = recorded.get(dataset)
        count = int(counts.get(dataset, 0))
        if count:
            degraded = bool((head or {}).get("degraded"))
            out.append(
                {
                    "dataset": dataset,
                    "tool": binding.tool,
                    "status": STATUS_OK,
                    "items": count,
                    # 有条目就是这一路的结论；账本里的 detail 只在「有条目但仍降级」时才有意义
                    "detail": str((head or {}).get("detail") or "") if degraded else "",
                    "source": str((head or {}).get("source") or binding.peer),
                    "source_detail": str((head or {}).get("source_detail") or ""),
                    "freshness": str((head or {}).get("freshness") or "cached"),
                    "confidence": str((head or {}).get("confidence") or "medium"),
                    "degraded": degraded,
                }
            )
            continue
        if head is not None and head.get("status") in (STATUS_FAILED, STATUS_SKIPPED):
            out.append({**dict(head), "dataset": dataset, "tool": binding.tool, "items": 0})
            continue
        out.append(
            {
                "dataset": dataset,
                "tool": binding.tool,
                "status": STATUS_EMPTY,
                "items": 0,
                "detail": NO_ROWS_DETAIL,
                "source": str((head or {}).get("source") or binding.peer),
                "source_detail": str((head or {}).get("source_detail") or ""),
                "freshness": "cached",
                "confidence": "medium",
                "degraded": bool((head or {}).get("degraded")),
            }
        )
    return out


def _news_reason(statuses: Sequence[Mapping[str, Any]], shown: Sequence[Any]) -> str:
    """空态与降级态的一句话（页面在空态 / partial 里读 `payload.reason`）。"""
    if shown:
        return ""
    blocked = [row for row in statuses if row.get("status") in (STATUS_FAILED, STATUS_SKIPPED)]
    if not blocked:
        # 全是「窗口内没有行」：8 栏各说一遍是噪声，一句「这个窗口没有」更有用
        return NEWS_WINDOW_EMPTY
    return "；".join(
        f"{row.get('dataset')}：{row.get('detail') or row.get('status')}" for row in blocked
    )


def _run_number(run: Mapping[str, Any] | None, key: str) -> int | None:
    """取数账本里的整数列；没有覆盖本窗口的账本 = 无从得知，置 `null` 不补 0。"""
    if run is None:
        return None
    value = run.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ledger_exit_code(statuses: Sequence[Mapping[str, Any]]) -> int:
    """沿用 R6 的退出码口径：部分栏目挂 8、一个都没成 4、其余 0。"""
    failed = sum(1 for row in statuses if row.get("status") == STATUS_FAILED)
    succeeded = sum(
        1 for row in statuses if row.get("status") in {STATUS_OK, STATUS_EMPTY}
    )
    if failed and succeeded:
        return EXIT_PARTIAL
    if failed:
        return EXIT_SOURCE
    return EXIT_OK


def _news_detail(
    day: date,
    end: date,
    statuses: Sequence[Mapping[str, Any]],
    run: Mapping[str, Any] | None,
) -> str:
    counts: dict[str, int] = {}
    for row in statuses:
        key = str(row.get("status"))
        counts[key] = counts.get(key, 0) + 1
    joined = "；".join(f"{key} {value}" for key, value in sorted(counts.items()))
    ledger = (
        f"取数账本 {run['window_start']}→{run['window_end']}（{run['started_at']}）"
        if run
        else "本机没有覆盖这个窗口的取数账本（news_runs）"
    )
    return (
        f"news.sqlite 投影 {day.isoformat()}→{end.isoformat()}"
        f"（只读，不取数不写库）：{ledger}；{joined or '无栏目'}"
    )


def _calendar_detail(result: Any) -> str:
    joined = "；".join(
        f"{section.key} {section.status}" + ("（降级）" if section.degraded else "")
        for section in result.sections
    )
    return f"event_calendar {result.month}：{joined or '无小节'}"


# --------------------------------------------------------------------------- #
# 信封
# --------------------------------------------------------------------------- #

#: 候选池资金分项的 freshness 三态（QUOT-372）：与信封 `Freshness` 枚举解耦，
#: 载荷字段可以比信封枚举说得更细（回落日线要跟「实时」明确区分开）。
FUND_FRESHNESS_REALTIME = "realtime"
FUND_FRESHNESS_DAILY_FALLBACK = "daily_fallback"
FUND_FRESHNESS_NONE = "none"

#: 资金两路数据集：当日实时批量（QUOT-371）与日线历史（QUOT-361 既有链路）
FUND_REALTIME_DATASET = "fund_flow_realtime"
FUND_DAILY_DATASET = "fund_flow"


def _minute_stamp(stamp: datetime | None) -> str | None:
    """落库时刻的分钟级 ISO 文本；没有就 None，前端如实显示「—」，不伪造。"""
    return stamp.isoformat(timespec="minutes") if stamp is not None else None


def _fund_pct_map(records: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """code → `main_net_pct`（批量工具定稿字段，单位 %）；这列缺失的行给 None。"""
    pcts: dict[str, float | None] = {}
    for record in records:
        code = str(record.get("code") or "").strip()
        if not code:
            continue
        value = record.get("main_net_pct")
        try:
            pcts[code] = float(value) if value is not None else None
        except (TypeError, ValueError):
            pcts[code] = None
    return pcts


def _pool_fund_provenance(
    day: date, result: Mapping[str, Any], root: Any
) -> tuple[str, str | None, dict[str, float | None]]:
    """资金分项的溯源三态（QUOT-372）：`(fund_freshness, fund_fetched_at, 净占比映射)`。

    - `realtime`：`fund_flow_realtime` 当日分区命中（`core.pool.fund_flow_snapshot`
      的第一顺位，命中时载荷 `meta.fund_data_as_of` 就是请求日）；
    - `daily_fallback`：回落日线 `fund_flow`（当日盘后分区或更早交易日）——
      这批数不是「当日实时」，要跟实时口径明确区分；
    - `none`：没有任何资金数据，`fetched_at=None`，净占比不冒充。

    `fund_fetched_at` 取记录的 `_ingested_at`（`archive.snapshot` 的 `cached_at`，
    真实落库时刻）到分钟；缓存读不动时不让整条接口崩，如实降级成 `daily_fallback`
    / `None`。这里只读缓存，不改 `core/pool.py` 的取数与算分（QUOT-361 地盘）。
    """
    counts = result.get("counts")
    flow_rows = int(counts.get("fund_flow_rows") or 0) if isinstance(counts, Mapping) else 0
    if flow_rows <= 0:
        return FUND_FRESHNESS_NONE, None, {}
    try:
        snap = archive.snapshot(FUND_REALTIME_DATASET, day, root=root)
    except Exception:  # noqa: BLE001 - 缓存损坏不崩接口，如实降级
        snap = None
    if snap is not None and snap.records:
        return (
            FUND_FRESHNESS_REALTIME,
            _minute_stamp(snap.cached_at),
            _fund_pct_map(snap.records),
        )
    meta = result.get("meta")
    as_of_raw = str(meta.get("fund_data_as_of") or "") if isinstance(meta, Mapping) else ""
    try:
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else None
    except ValueError:
        as_of = None
    if as_of is None:
        return FUND_FRESHNESS_DAILY_FALLBACK, None, {}
    try:
        daily = archive.snapshot(FUND_DAILY_DATASET, as_of, root=root)
    except Exception:  # noqa: BLE001
        return FUND_FRESHNESS_DAILY_FALLBACK, None, {}
    return (
        FUND_FRESHNESS_DAILY_FALLBACK,
        _minute_stamp(daily.cached_at),
        _fund_pct_map(daily.records),
    )


def _envelope(
    *,
    domain: Domain,
    topic: str,
    day: date | None,
    payload: dict[str, Any],
    status: Status,
    source_detail: str,
    freshness: Freshness,
    fund_freshness: str | None = None,
    fund_fetched_at: str | None = None,
) -> dict[str, Any]:
    """套 `LadderEnvelope`，并把 `data_source_status` / `data_as_of` 复制进载荷。

    `status="missing"` 时 `data_as_of=None`：无数据不拿请求日期顶替数据时点
    （METRICS.md §4.3 / D-02）。

    `fund_freshness` / `fund_fetched_at` 是候选池资金分项的口径与取数时刻
    （QUOT-372），同样复制进载荷（前端 `unwrap()` 只下钻 `payload`，信封顶层
    读不到）；它们是**新增字段**，不动 `data_as_of` 的日期语义。
    """
    as_of = day if status != "missing" else None
    body = dict(strip_private(payload))
    body["data_source_status"] = status
    body["data_as_of"] = as_of.isoformat() if as_of else None
    if fund_freshness is not None:
        body["fund_freshness"] = fund_freshness
    if fund_fetched_at is not None or fund_freshness is not None:
        body["fund_fetched_at"] = fund_fetched_at
    envelope = LadderEnvelope(
        domain=domain,
        topic=topic,
        data_as_of=as_of,
        payload=body,
        source="ladderstock",
        source_detail=source_detail,
        fetched_at=datetime.now(UTC),
        freshness=freshness,
        confidence=_CONFIDENCE[status],
        source_status=_envelope_source_status(status),
    )
    return envelope.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# 闸门与路由
# --------------------------------------------------------------------------- #

async def privacy_read_guard(request: Request, *, denied: str, subject: str) -> None:
    """本机隐私读面的回环独占闸门（持仓 / 自选 / 收件箱共用这一份实现）。

    判据与控制面 / 日志面同一套函数；**不看 `LDST_CONTROL_ENABLED`**（那是写面总开关），
    也**不看远程访问 Token**——Token 管「谁连得进来」，不管「隐私数据该不该出去」。
    """
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail=denied)
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                f"（{subject}只接受 127.0.0.1 / localhost / ::1）"
            ),
        )


async def privacy_write_guard(
    request: Request, *, denied: str, subject: str, write_denied: str
) -> None:
    """本机隐私写面闸门 = 读面闸门 + 写方法来源证据（QUOT-285 / DEC-055）。

    写方法必须是同源 `Origin` 或显式 `X-LDST-Client: 1`（复用控制面的
    `write_source_allowed`，不新增第二种鉴权）；带 `Sec-Fetch-Site` 时只认
    `same-origin` / `none`。删除也走 `POST`，所以这条判据覆盖全部写操作。
    """
    await privacy_read_guard(request, denied=denied, subject=subject)
    if request.method.upper() not in control_plane.WRITE_METHODS:
        return
    if not control_plane.write_source_allowed(request):
        raise HTTPException(status_code=403, detail=write_denied)
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.strip().lower() not in control_plane.SAME_ORIGIN_SITES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"跨站来源被拒绝：Sec-Fetch-Site={site}"
                "（只接受 same-origin / none）"
            ),
        )


async def _positions_guard(request: Request) -> None:
    """持仓的回环独占闸门（SPEC-RADAR-PAGES §6.1 / 同 DEC-015 §3.1）。"""
    await privacy_read_guard(request, denied=POSITIONS_DENIED, subject="持仓")


async def _positions_write_guard(request: Request) -> None:
    """持仓写面闸门 = 读面闸门 + 写方法来源证据（QUOT-285）。"""
    await privacy_write_guard(
        request,
        denied=POSITIONS_DENIED,
        subject="持仓",
        write_denied=POSITIONS_WRITE_DENIED,
    )


async def _watchlist_guard(request: Request) -> None:
    """自选读面的回环独占闸门：**照抄 `_positions_guard` 的判据，一条都不改**（DEC-052 §2.1）。"""
    await privacy_read_guard(request, denied=WATCHLIST_DENIED, subject="自选")


async def _watchlist_write_guard(request: Request) -> None:
    """自选写面闸门 = 读面闸门 + 写方法来源证据（QUOT-419 / DEC-055）。"""
    await privacy_write_guard(
        request,
        denied=WATCHLIST_DENIED,
        subject="自选",
        write_denied=WATCHLIST_WRITE_DENIED,
    )


async def _timeline_guard(request: Request) -> None:
    """时间轴同时读自选与持仓：按隐私面闸门走，只接受回环（DEC-015 / DEC-052）。"""
    await privacy_read_guard(request, denied=TIMELINE_DENIED, subject="事件时间轴")


def build_router(plane: RadarPlane) -> APIRouter:
    """SPEC §3–§6.2 + §9：只读路由（含首页指数条、自选读面）+ 两对持仓 / 自选写入路由。"""
    router = APIRouter(prefix=RADAR_PREFIX)

    @router.get("/news")
    def news(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
        until: Annotated[str | None, Query(description="区间右端 YYYY-MM-DD（≤31 天）")] = None,
        source: Annotated[
            list[str] | None, Query(description="栏目名，可重复 / 逗号分隔")
        ] = None,
        code: Annotated[list[str] | None, Query(description="证券代码，可重复 / 逗号分隔")] = None,
        limit: Annotated[
            int | None, Query(description=f"每栏上限（默认 {NEWS_DEFAULT_LIMIT}）")
        ] = None,
    ) -> dict[str, Any]:
        return plane.news_view(date_str, until, source, code, limit)

    @router.get("/events")
    def events(
        month: Annotated[str | None, Query(description="YYYY-MM（必填）")] = None,
        code: Annotated[
            list[str] | None, Query(description="按代码过滤，可重复 / 逗号分隔")
        ] = None,
        limit: Annotated[
            int | None, Query(description=f"每节条数上限（默认 {CALENDAR_DEFAULT_LIMIT}）")
        ] = None,
    ) -> dict[str, Any]:
        return plane.events_view(month, code, limit)

    @router.get("/events/timeline", dependencies=[Depends(_timeline_guard)])
    def events_timeline(
        days: Annotated[
            int | None, Query(description="时间窗天数，只接受 7 / 30 / 90（缺省 30）")
        ] = None,
    ) -> JSONResponse:
        """自选 + 持仓的事件时间轴：隐私面，平面 JSON、`no-store`。"""
        return JSONResponse(
            plane.timeline_view(days), headers={"Cache-Control": "no-store"}
        )

    @router.get("/indices")
    def indices() -> dict[str, Any]:
        """首页指数条：无入参（四格由后端白名单写死），只 GET、公开数据面。"""
        return plane.indices_view()

    @router.get("/pool")
    def pool(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
        limit: Annotated[
            int | None, Query(description=f"最多返回几只（默认 {POOL_DEFAULT_LIMIT}）")
        ] = None,
    ) -> dict[str, Any]:
        return plane.pool_view(date_str, limit)

    @router.get("/positions", dependencies=[Depends(_positions_guard)])
    def positions(
        date_str: Annotated[
            str | None, Query(alias="date", description="行情日期 YYYY-MM-DD（缺省取缓存最后一天）")
        ] = None,
    ) -> JSONResponse:
        """隐私数据：平面 JSON、`no-store`、只 GET。"""
        return JSONResponse(
            plane.positions_view(date_str), headers={"Cache-Control": "no-store"}
        )

    @router.post("/positions", dependencies=[Depends(_positions_write_guard)])
    def positions_upsert(
        body: Annotated[Any, Body(description="一条持仓；六个字段白名单，多一个就 400")],
    ) -> JSONResponse:
        """按 `code` upsert 一条持仓：平面 JSON、`no-store`、不进信封。"""
        return JSONResponse(plane.position_upsert(body), headers={"Cache-Control": "no-store"})

    @router.post(
        "/positions/{code}/delete", dependencies=[Depends(_positions_write_guard)]
    )
    def positions_delete(code: str) -> JSONResponse:
        """硬删除一条持仓：不留软删除标记、不加审计链。"""
        return JSONResponse(
            plane.position_delete(code), headers={"Cache-Control": "no-store"}
        )

    @router.get("/portfolio", dependencies=[Depends(_positions_guard)])
    def portfolio(
        method: Annotated[
            str | None,
            Query(description="成本法：average（移动加权平均，缺省）/ fifo（先进先出）"),
        ] = None,
    ) -> JSONResponse:
        """持仓账本（隐私数据）：流水重放 + 行情拼接 + 旧仓分类，平面 JSON、`no-store`。"""
        return JSONResponse(
            plane.portfolio_view(method), headers={"Cache-Control": "no-store"}
        )

    @router.get("/portfolio/perf", dependencies=[Depends(_positions_guard)])
    def portfolio_perf(
        days: Annotated[
            int | None,
            Query(description=f"走势回看天数（缺省 {PERF_DEFAULT_DAYS}，0=全历史）"),
        ] = None,
    ) -> JSONResponse:
        """持仓收益率（隐私数据）：平面 JSON、`no-store`、只 GET、只读本机日线。"""
        return JSONResponse(
            plane.portfolio_perf_view(days), headers={"Cache-Control": "no-store"}
        )

    @router.get("/portfolio/replay", dependencies=[Depends(_positions_guard)])
    def portfolio_replay(
        scenario: Annotated[
            str | None,
            Query(
                description=(
                    "情景 id（2015-crash / 2018-bear / 2020-covid / 2024-liquidity），"
                    "缺省 all"
                )
            ),
        ] = None,
    ) -> JSONResponse:
        """情景回放（隐私数据）：平面 JSON、`no-store`、只 GET、只读本机日线。"""
        return JSONResponse(
            plane.portfolio_replay_view(scenario), headers={"Cache-Control": "no-store"}
        )

    @router.get("/portfolio/xray", dependencies=[Depends(_positions_guard)])
    def portfolio_xray() -> JSONResponse:
        """持仓 X 光（隐私数据）：权重 / 集中度 / 行业 / 相关系数 / 缺口，平面 JSON、`no-store`。"""
        return JSONResponse(
            plane.portfolio_xray_view(), headers={"Cache-Control": "no-store"}
        )

    @router.post(
        "/portfolio/ledger", dependencies=[Depends(_positions_write_guard)]
    )
    def portfolio_ledger_add(
        body: Annotated[Any, Body(description="一笔流水；十个字段白名单，多一个就 400")],
    ) -> JSONResponse:
        """新增一笔流水：平面 JSON、`no-store`、不进信封。"""
        return JSONResponse(
            plane.portfolio_add_ledger(body), headers={"Cache-Control": "no-store"}
        )

    @router.post(
        "/portfolio/ledger/import/preview",
        dependencies=[Depends(_positions_write_guard)],
    )
    def portfolio_ledger_import_preview(
        body: Annotated[Any, Body(description="批量流水；逐行校验，不写库")],
    ) -> JSONResponse:
        """导入预览：逐行 `problem` / `duplicate`，不写库、不备份；用法错误 422。"""
        return JSONResponse(
            plane.portfolio_import_preview(body), headers={"Cache-Control": "no-store"}
        )

    @router.post(
        "/portfolio/ledger/import",
        dependencies=[Depends(_positions_write_guard)],
    )
    def portfolio_ledger_import(
        body: Annotated[Any, Body(description="批量流水；任何一行不对整批不写")],
    ) -> JSONResponse:
        """批量导入：再校验 → 备份 → 事务整批写入；用法错误 422。"""
        return JSONResponse(
            plane.portfolio_import(body), headers={"Cache-Control": "no-store"}
        )

    @router.post(
        "/portfolio/ledger/{trade_id}/delete",
        dependencies=[Depends(_positions_write_guard)],
    )
    def portfolio_ledger_delete(trade_id: str) -> JSONResponse:
        """硬删除一笔流水（写前先备份）；不存在就是 404。"""
        return JSONResponse(
            plane.portfolio_delete_ledger(trade_id),
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/portfolio/legacy/{position_id}/adopt",
        dependencies=[Depends(_positions_write_guard)],
    )
    def portfolio_legacy_adopt(position_id: str) -> JSONResponse:
        """确认把旧 `positions` 记录转成期初流水；已转过的原地替换，不新增第二笔。"""
        return JSONResponse(
            plane.portfolio_adopt(position_id),
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/portfolio/legacy/{position_id}/dismiss",
        dependencies=[Depends(_positions_write_guard)],
    )
    def portfolio_legacy_dismiss(position_id: str) -> JSONResponse:
        """确认不要这条旧记录：只写 mark，不删 `positions` 行。"""
        return JSONResponse(
            plane.portfolio_dismiss(position_id),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/watchlist", dependencies=[Depends(_watchlist_guard)])
    def watchlist() -> JSONResponse:
        """我的自选（只读）：平面 JSON、`no-store`、只 GET。

        写面是下面两条 `POST`（QUOT-419 / DEC-055 修订 DEC-052 §2.3）：
        增删仍只写本机 SQLite，只是入口从 CLI 扩到本机页面。
        """
        return JSONResponse(plane.watchlist_view(), headers={"Cache-Control": "no-store"})

    @router.post("/watchlist", dependencies=[Depends(_watchlist_write_guard)])
    def watchlist_add(
        body: Annotated[Any, Body(description="一只自选；只认 code 一个字段")],
    ) -> JSONResponse:
        """加一条自选（幂等）：平面 JSON、`no-store`、不进信封。"""
        return JSONResponse(plane.watch_add(body), headers={"Cache-Control": "no-store"})

    @router.post(
        "/watchlist/{code}/delete", dependencies=[Depends(_watchlist_write_guard)]
    )
    def watchlist_delete(code: str) -> JSONResponse:
        """硬删除一条自选：不在本机清单里返回 404，不静默成功。"""
        return JSONResponse(
            plane.watch_delete(code), headers={"Cache-Control": "no-store"}
        )

    return router


__all__ = [
    "CALENDAR_DEFAULT_LIMIT",
    "CALENDAR_TOPIC",
    "INDUSTRIES_PLACEHOLDER",
    "NEWS_DEFAULT_LIMIT",
    "NEWS_DOMAIN",
    "NEWS_READ_MAX",
    "NEWS_STORE_MISSING",
    "NEWS_TOPIC",
    "NEWS_WINDOW_EMPTY",
    "NO_ROWS_DETAIL",
    "POOL_DEFAULT_LIMIT",
    "POOL_DOMAIN",
    "POOL_TOPIC",
    "POSITIONS_DENIED",
    "RADAR_PREFIX",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "RadarPlane",
    "build_router",
    "calendar_status",
    "parse_codes",
    "parse_day",
    "parse_limit",
    "parse_month_param",
    "parse_sources",
    "privacy_read_guard",
    "privacy_write_guard",
    "stream_status",
]
