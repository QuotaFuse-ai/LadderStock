"""三类本机摘要：盘前 / 收盘 / 自选变化（DEC-058）。

三份摘要都**只写有来源的事实**，不写买卖建议、不写评分：

- `premarket`（09:05）：自选 + 持仓代码的现价 / 昨收 / 涨跌，走已登记的 `spot_quote`
  （`core/watch_prices.fetch_prices`，同一张字段容忍表）。取不到的代码逐条写
  「未取到（原因）」，**不补 0、不拿成本价顶替**。已过 11:30 的补跑直接 `skipped`，
  不把收盘数据冒充成盘前。
- `close`（15:40）：持仓当日盈亏（`core/quotes.daily_change_map` 口径，close(T) − close(T−1)）、
  自选涨跌榜（只列取到的）、当日已实现 / 分红（来自 `portfolio_trades` 的流水重放）。
  只读本机 `daily_k` 缓存，不联网。
- `watch`（16:15）：自选与**前一交易日**的收盘价 / 成交量变化。本机缓存只有一条历史时
  明确写「没有上一交易日可比」，不编。

缺失纪律：金额与百分比缺失一律写 `—`；某只票没取到时写「未取到（原因）」。
`dedupe_key = f"{job}:{trade_day}"` 由 `journal/inbox.add_item` 保证同一交易日只留一条。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core import quotes
from ladderstock.core.intraday import local_moment
from ladderstock.core.ladder import CODE_KEYS, as_float, dataset_snapshot, pick
from ladderstock.core.portfolio import ReplayResult, Trade
from ladderstock.core.portfolio import replay as replay_portfolio
from ladderstock.core.watch_prices import fetch_prices
from ladderstock.data import archive
from ladderstock.journal import portfolio as portfolio_store
from ladderstock.journal import watchlist as watchlist_store

#: `schedule.yaml` 里 `kind: digest` 的 `summary` 取值
SUMMARY_PREMARKET = "premarket"
SUMMARY_CLOSE = "close"
SUMMARY_WATCH = "watch"

SUMMARIES: tuple[str, ...] = (SUMMARY_PREMARKET, SUMMARY_CLOSE, SUMMARY_WATCH)

SUMMARY_LABELS: dict[str, str] = {
    SUMMARY_PREMARKET: "盘前摘要",
    SUMMARY_CLOSE: "收盘摘要",
    SUMMARY_WATCH: "自选变化",
}

#: 盘前摘要的补跑红线：过了 11:30 的盘前数据已经没有意义
PREMARKET_CUTOFF_HOUR = 11
PREMARKET_CUTOFF_MINUTE = 30

#: 与 `core/quotes.PREV_LOOKBACK_DAYS` 同一条「有分区才算交易日」的判据
PREV_LOOKBACK_DAYS = 30

#: `daily_k` 里成交量的候选键（与 `web/research.py` 同一张容差表）
VOLUME_KEYS: tuple[str, ...] = ("volume", "vol", "成交量")

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

MISSING = "—"

#: 摘要里的跳转目标（只用前端已存在的路由，不给未交付的页面造链接）
LINK_OF: dict[str, str] = {
    SUMMARY_PREMARKET: "/positions",
    SUMMARY_CLOSE: "/positions",
    SUMMARY_WATCH: "/watchlist",
}


@dataclass
class Digest:
    """一份待投递的摘要；`lines` 是给人看的正文行。"""

    job: str
    summary: str
    status: str
    detail: str
    trade_day: str
    title: str = ""
    lines: list[str] = field(default_factory=list)
    link: str | None = None

    @property
    def dedupe_key(self) -> str:
        return f"{self.job}:{self.trade_day}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "summary": self.summary,
            "status": self.status,
            "detail": self.detail,
            "trade_day": self.trade_day,
            "title": self.title,
            "lines": list(self.lines),
            "link": self.link,
            "dedupe_key": self.dedupe_key,
        }


# --------------------------------------------------------------------------- #
# 格式化：缺失写 `—`，数字带单位
# --------------------------------------------------------------------------- #

def _money(value: float | None) -> str:
    return MISSING if value is None else f"{value:,.2f} 元"


def _pct(value: float | None) -> str:
    return MISSING if value is None else f"{value:+.2f}%"


def _price(value: float | None) -> str:
    return MISSING if value is None else f"{value:.2f} 元"


def _amount(value: float | None) -> str:
    """成交量按上游原始单位展示——本机缓存没有统一的「手 / 股」换算依据，不猜。"""
    return MISSING if value is None else f"{value:,.0f}"


def _shares(value: float | None) -> str:
    if value is None:
        return MISSING
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _label(name: str, code: str) -> str:
    return f"{code} {name}".strip() if name else code


# --------------------------------------------------------------------------- #
# 取数小工具（只读本机 / 已登记数据集）
# --------------------------------------------------------------------------- #

def _lazy_registry(root: Path | None) -> Any:
    """调度器默认不注入 registry；这里按本机配置建一个（构造本身不出网）。"""
    try:
        from ladderstock.data.provider_registry import ProviderRegistry

        return ProviderRegistry(root=root)
    except Exception:  # noqa: BLE001 - 配置坏了只是「这会儿取不到」
        return None


def _watch_codes(root: Path | None) -> list[str]:
    return [str(row.get("code") or "") for row in watchlist_store.list_watch(root=root)]


def _trades(root: Path | None) -> list[Trade]:
    return portfolio_store.list_trades(root=root)


def _replay(trades: list[Trade]) -> ReplayResult:
    """摘要与持仓页同一成本法（缺省移动加权平均）。"""
    return replay_portfolio(trades, "average")


def _latest_day(root: Path | None) -> date:
    return archive.latest_day(quotes.PRICE_DATASET, root=root) or date.today()


def _previous_cached_day(day: date, root: Path | None) -> date | None:
    """`day` 之前最近一个**本机有分区**的交易日；找不到就是 `None`（不猜节日）。"""
    cursor = day - timedelta(days=1)
    for _ in range(PREV_LOOKBACK_DAYS):
        if cursor.weekday() < 5 and archive.has_partition(
            quotes.PRICE_DATASET, cursor, root=root
        ):
            return cursor
        cursor -= timedelta(days=1)
    return None


def _daily_rows(day: date, root: Path | None) -> dict[str, dict[str, Any]]:
    """`daily_k` 日分区 → `code -> {close, volume}`；只收有收盘价的代码。"""
    records, _backend = dataset_snapshot(quotes.PRICE_DATASET, day, root)
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip().upper()
        if not code or code in out:
            continue
        close = _number(pick(record, quotes.PRICE_KEYS))
        volume = _number(pick(record, VOLUME_KEYS))
        if close is None or close <= 0:
            continue
        out[code] = {"close": close, "volume": volume}
    return out


def _number(raw: Any) -> float | None:
    """只认有限数值；`0` 是有效读数，`nan` / `inf` / 空值都是缺。"""
    value = as_float(raw)
    if value is None or not math.isfinite(value):
        return None
    return value


# --------------------------------------------------------------------------- #
# 盘前（09:05）
# --------------------------------------------------------------------------- #

def _premarket(
    job: str,
    *,
    moment: datetime,
    root: Path | None,
    registry: Any,
) -> Digest:
    day = moment.date()
    trade_day = day.isoformat()
    if day.weekday() >= 5:
        return Digest(
            job,
            SUMMARY_PREMARKET,
            STATUS_SKIPPED,
            "非交易日（周末），不写盘前摘要",
            trade_day,
        )

    clock = moment.time().replace(microsecond=0)
    if (clock.hour, clock.minute) >= (PREMARKET_CUTOFF_HOUR, PREMARKET_CUTOFF_MINUTE):
        return Digest(
            job,
            SUMMARY_PREMARKET,
            STATUS_SKIPPED,
            "已过 11:30，盘前摘要不补跑（不把收盘数据冒充盘前）",
            trade_day,
        )

    replay = _replay(_trades(root))
    holdings = {row.symbol: row.name for row in replay.holdings}
    watch = _watch_codes(root)
    codes: list[str] = []
    for code in [*holdings, *watch]:
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return Digest(
            job,
            SUMMARY_PREMARKET,
            STATUS_SKIPPED,
            "本机没有自选或持仓，盘前摘要无内容",
            trade_day,
        )

    active = registry if registry is not None else _lazy_registry(root)
    if active is None:
        strip_prices: dict[str, dict[str, Any]] = {}
        source_detail = ""
        fallback_reason = "本机没有可用的取数通道（检查 sources.yaml）"
    else:
        strip = fetch_prices(active, codes)
        strip_prices = strip.prices
        source_detail = str(strip.source_detail or "")
        fallback_reason = "上游没有返回这只有效报价"

    lines = [
        f"持仓 {len(holdings)} 只 · 自选 {len(watch)} 只"
        + (f" · 报价源 {source_detail}" if source_detail else "")
    ]
    for code in codes:
        name = holdings.get(code) or ""
        info = strip_prices.get(code) or {}
        price = info.get("latest_price")
        if info.get("available") and price is not None:
            prev = info.get("prev_close")
            lines.append(
                f"{_label(str(info.get('name') or name), code)} "
                f"现价 {_price(float(price))} 昨收 {_price(_float(prev))} "
                f"涨跌 {_pct(_float(info.get('change_pct')))}"
            )
            continue
        reason = str(info.get("price_reason") or fallback_reason)
        lines.append(f"{_label(name, code)} 未取到（{reason}）")

    title = f"盘前摘要 {trade_day}"
    return Digest(
        job,
        SUMMARY_PREMARKET,
        STATUS_OK,
        f"盘前摘要已生成（{len(codes)} 只代码，报价源 {source_detail or '未知'}）",
        trade_day,
        title=title,
        lines=lines,
        link=LINK_OF[SUMMARY_PREMARKET],
    )


def _float(raw: Any) -> float | None:
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 收盘（15:40）
# --------------------------------------------------------------------------- #

def _close(
    job: str,
    *,
    root: Path | None,
) -> Digest:
    day = _latest_day(root)
    trade_day = day.isoformat()
    if day.weekday() >= 5:
        return Digest(
            job,
            SUMMARY_CLOSE,
            STATUS_SKIPPED,
            "非交易日（周末），不写收盘摘要",
            trade_day,
        )

    trades = _trades(root)
    replay = _replay(trades)
    watch = _watch_codes(root)
    lines: list[str] = []

    if replay.holdings:
        symbols = [row.symbol for row in replay.holdings]
        daily, daily_status = quotes.daily_change_map(symbols, trade_day, root=root)
        total = 0.0
        priced = 0
        lines.append(f"持仓 {len(symbols)} 只（成本法 移动加权平均）")
        for row in replay.holdings:
            info = daily.get(row.symbol) or {}
            change = _float(info.get("change"))
            if change is None:
                reason = str(info.get("reason") or daily_status.get("reason") or "没有当日收盘价")
                lines.append(f"{_label(row.name, row.symbol)} 当日盈亏 未取到（{reason}）")
                continue
            prev = _float(info.get("prev_close"))
            pct = (change / prev * 100.0) if prev else None
            pnl = change * row.shares
            total += pnl
            priced += 1
            lines.append(
                f"{_label(row.name, row.symbol)} 收盘 {_price(_float(info.get('close')))} "
                f"当日 {_pct(pct)} × {_shares(row.shares)} 股 = {_money(pnl)}"
            )
        suffix = "" if priced == len(symbols) else f"（{priced}/{len(symbols)} 只可算，其余未计入）"
        lines.append(f"持仓当日盈亏合计：{_money(total if priced else None)}{suffix}")
    else:
        lines.append("本机账本没有持仓（尚未录入交易流水）")

    if watch:
        wdaily, wstatus = quotes.daily_change_map(watch, trade_day, root=root)
        pairs: list[tuple[float, str, float]] = []
        for code in watch:
            info = wdaily.get(code) or {}
            close = _float(info.get("close"))
            prev = _float(info.get("prev_close"))
            if info.get("status") == quotes.STATUS_OK and close is not None and prev:
                pairs.append(((close - prev) / prev * 100.0, code, close))
        if pairs:
            pairs.sort(key=lambda item: (-item[0], item[1]))
            lines.append(f"自选涨跌榜（{len(pairs)}/{len(watch)} 只有可比收盘价）")
            for pct, code, close in pairs:
                lines.append(f"{code} 收盘 {_price(close)} {_pct(pct)}")
        else:
            reason = str(wstatus.get("reason") or "缓存缺当日或上一交易日收盘价")
            lines.append(f"自选 {len(watch)} 只没有可比的当日收盘价（{reason}）")
    else:
        lines.append("本机自选为空")

    today_realized = [row for row in replay.realized if row.date == trade_day]
    if today_realized:
        lines.append(
            f"当日已实现：{_money(sum(row.pnl for row in today_realized))}"
            f"（{len(today_realized)} 笔卖出）"
        )
    elif not trades:
        lines.append("当日已实现：本机账本为空，无从计算")
    else:
        lines.append("当日已实现：今日没有卖出流水（0 笔）")

    today_dividends = [row for row in replay.dividends if row.date == trade_day]
    if today_dividends:
        lines.append(f"当日分红：{_money(sum(row.amount for row in today_dividends))}")

    title = f"收盘摘要 {trade_day}"
    return Digest(
        job,
        SUMMARY_CLOSE,
        STATUS_OK,
        f"收盘摘要已生成（交易日 {trade_day}）",
        trade_day,
        title=title,
        lines=lines,
        link=LINK_OF[SUMMARY_CLOSE],
    )


# --------------------------------------------------------------------------- #
# 自选变化（16:15）
# --------------------------------------------------------------------------- #

def _watch_changes(
    job: str,
    *,
    root: Path | None,
) -> Digest:
    day = _latest_day(root)
    trade_day = day.isoformat()
    if day.weekday() >= 5:
        return Digest(
            job,
            SUMMARY_WATCH,
            STATUS_SKIPPED,
            "非交易日（周末），不写自选变化",
            trade_day,
        )

    watch = _watch_codes(root)
    if not watch:
        return Digest(job, SUMMARY_WATCH, STATUS_SKIPPED, "本机自选为空，自选变化无内容", trade_day)

    today = _daily_rows(day, root)
    prev_day = _previous_cached_day(day, root)
    compared = prev_day.isoformat() if prev_day else "（无）"
    lines = [
        f"自选 {len(watch)} 只 · {day.isoformat()} 对比 {compared}",
        "成交量按上游原始单位",
    ]

    if prev_day is None:
        lines.append(f"本机 daily_k 只有 {day.isoformat()} 这一天的缓存，没有上一交易日可比")
        for code in watch:
            row = today.get(code)
            if row is None:
                lines.append(f"{code} 未取到（daily_k 在 {day.isoformat()} 没有这一行）")
                continue
            lines.append(f"{code} 收盘 {_price(row['close'])} 成交量 {_amount(row.get('volume'))}")
        return Digest(
            job,
            SUMMARY_WATCH,
            STATUS_OK,
            f"自选变化已生成（只有 {day.isoformat()} 一天缓存，没有上一交易日可比）",
            trade_day,
            title=f"自选变化 {trade_day}",
            lines=lines,
            link=LINK_OF[SUMMARY_WATCH],
        )

    previous = _daily_rows(prev_day, root)
    for code in watch:
        now_row = today.get(code)
        then_row = previous.get(code)
        if now_row is None:
            lines.append(f"{code} 未取到（daily_k 在 {day.isoformat()} 没有这一行）")
            continue
        if then_row is None:
            lines.append(
                f"{code} 收盘 {_price(now_row['close'])}"
                f"（daily_k 在 {prev_day.isoformat()} 没有这一行，价格变化无从取值）"
            )
            continue
        close_pct = (now_row["close"] - then_row["close"]) / then_row["close"] * 100.0
        volume_pct = None
        if now_row.get("volume") is not None and then_row.get("volume"):
            volume_pct = (
                (now_row["volume"] - then_row["volume"]) / then_row["volume"] * 100.0
            )
        lines.append(
            f"{code} 收盘 {_price(now_row['close'])}（{_pct(close_pct)}）"
            f"成交量 {_amount(now_row.get('volume'))}（{_pct(volume_pct)}）"
        )

    return Digest(
        job,
        SUMMARY_WATCH,
        STATUS_OK,
        f"自选变化已生成（{day.isoformat()} 对比 {prev_day.isoformat()}）",
        trade_day,
        title=f"自选变化 {trade_day}",
        lines=lines,
        link=LINK_OF[SUMMARY_WATCH],
    )


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #

def build(
    job: str,
    summary: str,
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> Digest:
    """生成一份摘要；**任何异常都不向上抛**，折成 `status=failed` 的结果。

    取数缺失不等于失败：缺哪条就写哪条「未取到」，摘要整体仍是 `ok`。
    """
    name = str(job or "").strip()
    kind = str(summary or "").strip()
    moment = local_moment(now, timezone)
    trade_day = moment.date().isoformat()
    if kind not in SUMMARIES:
        return Digest(
            name,
            kind,
            STATUS_FAILED,
            f"不认识的摘要类型 {kind!r}（只支持 {' / '.join(SUMMARIES)}）",
            trade_day,
        )
    try:
        if kind == SUMMARY_PREMARKET:
            return _premarket(name, moment=moment, root=root, registry=registry)
        if kind == SUMMARY_CLOSE:
            return _close(name, root=root)
        return _watch_changes(name, root=root)
    except Exception as exc:  # noqa: BLE001 - 摘要失败只折算结果，不拖垮调度器
        reason = " ".join(str(exc).split()) or exc.__class__.__name__
        return Digest(name, kind, STATUS_FAILED, f"摘要生成异常：{reason}", trade_day)


__all__ = [
    "LINK_OF",
    "PREMARKET_CUTOFF_HOUR",
    "PREMARKET_CUTOFF_MINUTE",
    "PREV_LOOKBACK_DAYS",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "SUMMARIES",
    "SUMMARY_CLOSE",
    "SUMMARY_LABELS",
    "SUMMARY_PREMARKET",
    "SUMMARY_WATCH",
    "VOLUME_KEYS",
    "Digest",
    "build",
]
