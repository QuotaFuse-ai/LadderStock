"""R12 收口：资讯雷达 / 事件日历 / 候选池 / 我的持仓（QUOT-279 / QUOT-285）。

前端四页（`frontend/src/screens/radar/`）已经在 main 上，字段写死在
`frontend/src/api/radar.ts` 与 `docs/specs/SPEC-RADAR-PAGES.md` §3–§6.2。这里按那份契约
做投影，前端零改动即可从演示态切真实通道。

四条口径（详见 `docs/decisions/DEC-030-radar-http-endpoints.md` 与 DEC-032）：

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
4. **写面只有两条、全是 POST**（QUOT-285）：`POST /api/positions` 按 `code` upsert，
   `POST /api/positions/{code}/delete` 硬删除。不提供 `PUT` / `PATCH` / `DELETE` 动词，
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

from ladderstock.core import pool as pool_module
from ladderstock.core.boundary import owner_of
from ladderstock.core.models import (
    Confidence,
    Domain,
    Freshness,
    LadderEnvelope,
    SourceStatus,
)
from ladderstock.core.privacy import strip_private
from ladderstock.core.quotes import price_map
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
from ladderstock.journal.position import (
    add_position,
    compute_metrics,
    list_positions,
    remove_position,
)
from ladderstock.news import store as news_store
from ladderstock.news import stream as news_stream
from ladderstock.news.calendar import DEFAULT_LIMIT as CALENDAR_DEFAULT_LIMIT
from ladderstock.news.calendar import STATUS_NO_SOURCE, STATUS_SOURCED, build_calendar, parse_month
from ladderstock.news.events import extract_events
from ladderstock.news.models import normalize_code
from ladderstock.web import control as control_plane

_T = TypeVar("_T")

#: 四个接口共用 `/api` 前缀：路径照 SPEC §3–§6.1，不另开变体
RADAR_PREFIX = "/api"

NEWS_DOMAIN: Domain = "news"
NEWS_TOPIC = "news_stream"
CALENDAR_TOPIC = "event_calendar"
POOL_DOMAIN: Domain = "sentiment"
POOL_TOPIC = "candidate_pool"

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
POSITIONS_WRITE_DENIED = "持仓写入缺少来源证据：需要本机页面 Origin，或显式头 X-LDST-Client: 1"

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
    ) -> None:
        self.data_root = Path(data_root) if data_root is not None else None
        self._registry = registry

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

        view = self._read(lambda: self._news_projection(day, end, datasets, codes, limit))
        return _envelope(
            domain=NEWS_DOMAIN,
            topic=NEWS_TOPIC,
            day=end,
            payload=view["payload"],
            status=view["status"],
            source_detail=view["source_detail"],
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

    # ---------------------------------------------------------------- 候选池 §5

    def pool_view(self, raw_date: str | None, raw_limit: int | None) -> dict[str, Any]:
        day = parse_day(raw_date)
        limit = parse_limit(raw_limit, default=POOL_DEFAULT_LIMIT)
        result = self._read(
            lambda: pool_module.compute_pool(day, root=self.data_root, limit=limit)
        )
        available = bool(result.get("available"))
        return _envelope(
            domain=POOL_DOMAIN,
            topic=POOL_TOPIC,
            day=day,
            payload=result,
            status="ok" if available else "missing",
            source_detail=str(result.get("source_detail") or ""),
            freshness="cached",
        )

    # ---------------------------------------------------------------- 我的持仓 §6.1

    def positions_view(self, raw_date: str | None) -> dict[str, Any]:
        """平面 JSON（**不进 `LadderEnvelope`**）：载荷 = `ldst position list --output json`。

        只读：`list_positions()` 用 `create=False`，本机没有持仓库时不会建空库。
        现价取不到就是 `null`，原因随 `price_lookup.reason` 给出，不补 0、不猜价。
        """
        day = raw_date or None
        if day and day.strip():
            parse_day(day)  # 先按 422 挡住，不让 CLI 的用法错误冒成 500
        else:
            day = None
        rows = self._read(lambda: list_positions(root=self.data_root))
        codes = [str(row["code"]) for row in rows]
        prices, lookup = self._read(lambda: price_map(codes, day, root=self.data_root))
        payload = compute_metrics(rows, prices, as_of=lookup["date"])
        payload["price_lookup"] = lookup
        return payload

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

def _envelope(
    *,
    domain: Domain,
    topic: str,
    day: date | None,
    payload: dict[str, Any],
    status: Status,
    source_detail: str,
    freshness: Freshness,
) -> dict[str, Any]:
    """套 `LadderEnvelope`，并把 `data_source_status` / `data_as_of` 复制进载荷。

    `status="missing"` 时 `data_as_of=None`：无数据不拿请求日期顶替数据时点
    （METRICS.md §4.3 / D-02）。
    """
    as_of = day if status != "missing" else None
    body = dict(strip_private(payload))
    body["data_source_status"] = status
    body["data_as_of"] = as_of.isoformat() if as_of else None
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

async def _positions_guard(request: Request) -> None:
    """持仓的回环独占闸门（SPEC-RADAR-PAGES §6.1 / 同 DEC-015 §3.1）。

    判据与控制面 / 日志面同一套函数；**不看 `LDST_CONTROL_ENABLED`**（那是写面总开关），
    也**不看远程访问 Token**——Token 管「谁连得进来」，不管「隐私数据该不该出去」。
    """
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail=POSITIONS_DENIED)
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（持仓只接受 127.0.0.1 / localhost / ::1）"
            ),
        )


async def _positions_write_guard(request: Request) -> None:
    """持仓写面闸门 = 读面闸门 + 写方法来源证据（QUOT-285）。

    写方法必须是同源 `Origin` 或显式 `X-LDST-Client: 1`（复用控制面的
    `write_source_allowed`，不新增第二种鉴权）；带 `Sec-Fetch-Site` 时只认
    `same-origin` / `none`。删除也走 `POST`，所以这条判据覆盖全部写操作。
    """
    await _positions_guard(request)
    if request.method.upper() not in control_plane.WRITE_METHODS:
        return
    if not control_plane.write_source_allowed(request):
        raise HTTPException(status_code=403, detail=POSITIONS_WRITE_DENIED)
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.strip().lower() not in control_plane.SAME_ORIGIN_SITES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"跨站来源被拒绝：Sec-Fetch-Site={site}"
                "（只接受 same-origin / none）"
            ),
        )


def build_router(plane: RadarPlane) -> APIRouter:
    """SPEC §3–§6.2：四个只读路由 + 两条持仓写入路由（都是 `POST`）。"""
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
    "stream_status",
]
