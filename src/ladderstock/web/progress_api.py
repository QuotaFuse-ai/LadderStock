"""采集进度的只读视图：回答"到底采了多少、是不是在正常采"。

页面上的三张表（定时任务/数据同步/历史回填）只说明"有没有分区"，答不了
"财报采到第几期了、公告为什么一条没有"。这里补三样东西：

1. **域进度**（`domains`）——每个数据域给出 目标 / 实到 / 覆盖率 / 最新时点 / 状态。
   目标一律取代码池（`sync.yaml` 的 `base_pool`，当前 85 只），不是全市场 5,400：
   拿全市场当分母，所有域都会显示 2%，看不出"票池已经采全了"。
2. **采集器状态矩阵**（`matrix`）——`news.sqlite` 的 `news_runs` 里每次运行都记了
   逐数据源的 `status / items / detail`。这是"为什么是 0"的唯一证据来源：
   公告不是没跑，是每轮都被 `需要指定 code` 挡成 skipped。
3. **任务级最近结果**（`runs`）——`data/schedule_runs/*.json` 的 status 与 detail。
   `skipped` 必须带原因上屏（自选为空 / 非交易时段 / 缺验证条件），否则一屏红色
   会让人以为全挂了。

状态口径（`status`）：

- `ok`        目标已覆盖，且最新时点在应有窗口内
- `partial`   有数据但没盖满票池（如调研 55/85、一致预期 83/85）
- `never`     一条都没有，也没有任何运行记录
- `blocked`   跑过，但每轮都被前置条件挡掉（缺参数之类），数据永远进不来
- `lost`      采到了，但没落到归档/查询层，等于没采
- `ondemand`  本来就不做每日全量（日线、估值按需拉），不当故障看

`lag_days` 按日历日算，不扣周末/节假日——交易日历数据集 `lookup_calendar`
本身还没入库（见 domains 里那一行），拿它算滞后会自欺。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

from ladderstock.data import duckdb_store

PROGRESS_PREFIX = "/api/config"

# 采集器状态里这几种都算"这一轮没拿到数"，但原因不同，页面上要分开说
_EMPTY_STATUSES = {"failed", "empty", "skipped"}


def _project_root(root: Path | None) -> Path:
    from ladderstock.web.config_api import _project_root as impl

    return impl(root)


def _data_root(root: Path | None, project: Path) -> Path:
    from ladderstock.web.config_api import _data_root as impl

    return impl(root, project)


def _yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _base_pool(project: Path) -> list[str]:
    """代码池：取 `sync.yaml` 里显式写了 codes 的那个数据集（daily_k 用 &base_pool）。"""
    datasets = (_yaml(project / "config" / "sync.yaml").get("sync") or {}).get("datasets") or {}
    longest: list[str] = []
    for spec in datasets.values():
        if isinstance(spec, dict) and isinstance(spec.get("codes"), list):
            codes = [str(item) for item in spec["codes"]]
            if len(codes) > len(longest):
                longest = codes
    return longest


def _scalar(conn: Any, sql: str) -> Any:
    try:
        row = conn.execute(sql).fetchone()
    except Exception:  # noqa: BLE001 - 单条统计失败不该让整页 500
        return None
    return row[0] if row else None


def _news_rows(data_root: Path, sql: str) -> Any:
    path = data_root / "news.sqlite"
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return None
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 域进度
# --------------------------------------------------------------------------- #


def _domain(
    *,
    key: str,
    label: str,
    source: str,
    got: int,
    target: int,
    latest: str | None,
    today: dt.date,
    status: str,
    cadence: str,
    detail: str,
    rows: int | None = None,
    unit: str = "只",
    lag: int | None = None,
    show_lag: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = round(got / target, 4) if target else None
    payload: dict[str, Any] = {
        "key": key,
        "label": label,
        "source": source,
        "got": got,
        "unit": unit,
        "target": target,
        "coverage": coverage,
        "rows": rows,
        "latest": latest,
        # 季报口径的"距今 51 天"不是断档，是还没到下一期；这类域显式不给滞后
        "lag_days": ((lag if lag is not None else _lag(latest, today)) if show_lag else None),
        "status": status,
        "cadence": cadence,
        "detail": detail,
    }
    if extra:
        payload.update(extra)
    return payload


def _lag(latest: str | None, today: dt.date) -> int | None:
    if not latest:
        return None
    try:
        return (today - dt.date.fromisoformat(latest[:10])).days
    except ValueError:
        return None


def _duckdb_domains(conn: Any, pool: int, today: dt.date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # 行情日线：票池内有长历史，其余是"用到才拉一根"的快照
    bars = _scalar(conn, "select count(distinct code) from daily_k") or 0
    latest_k = _scalar(conn, "select max(trade_date) from daily_k")
    full = _scalar(conn, "select count(*) from (select code from daily_k group by 1 having count(*) > 100)")
    rows.append(
        _domain(
            key="daily_k",
            label="行情日线",
            source="duckdb.daily_k",
            got=int(full or 0),
            target=pool,
            latest=str(latest_k) if latest_k else None,
            today=today,
            status="ondemand",
            cadence="按需缓存",
            detail=(
                f"票池 {full or 0} 只有 600+ 根完整历史；另有 {max(bars - int(full or 0), 0):,} 只"
                "只有快照根（用到才拉）。日线不在收盘同步清单里，所以"
                "「最新只到某天」不代表同步失败。"
            ),
        )
    )

    # 财报：报告期口径，不是日历口径
    periods = _scalar(conn, "select count(distinct trade_date) from finance_indicators") or 0
    fin_codes = _scalar(conn, "select count(distinct code) from finance_indicators") or 0
    fin_rows = _scalar(conn, "select count(*) from finance_indicators") or 0
    fin_latest = _scalar(conn, "select max(trade_date) from finance_indicators")
    rows.append(
        _domain(
            key="finance",
            label="财报指标",
            source="duckdb.finance_indicators",
            got=int(fin_codes),
            target=pool,
            rows=int(fin_rows),
            latest=str(fin_latest) if fin_latest else None,
            today=today,
            status="ok" if fin_codes >= pool else "partial",
            cadence="季报（每期一次）",
            show_lag=False,
            detail=(
                f"{fin_codes}/{pool} 只有财报（{round(fin_codes / pool * 100, 1) if pool else 0}%），"
                f"{periods} 个报告期共 {fin_rows} 行，最近一期 {fin_latest}；"
                f"按全市场 5,400 只口径是 {round(fin_codes / 5400 * 100, 1)}%。"
                + ("池子已经从 85 只扩过，但财报仍是那 85 只的，没有跟着扩。" if pool > fin_codes else "")
            ),
        )
    )

    # 资金流
    ff_codes = _scalar(conn, "select count(distinct code) from fund_flow") or 0
    ff_latest = _scalar(conn, "select max(trade_date) from fund_flow")
    rows.append(
        _domain(
            key="fund_flow",
            label="个股资金流",
            source="duckdb.fund_flow",
            got=int(ff_codes),
            target=pool,
            rows=_scalar(conn, "select count(*) from fund_flow"),
            latest=str(ff_latest) if ff_latest else None,
            today=today,
            status="ok" if ff_latest and _lag(str(ff_latest), today) <= 1 else "partial",
            cadence="每个交易日",
            detail="收盘同步写入；票池内每只每天一条。",
        )
    )

    # 龙虎榜
    dt_latest = _scalar(conn, "select max(trade_date) from dragon_tiger")
    rows.append(
        _domain(
            key="dragon_tiger",
            label="龙虎榜",
            source="duckdb.dragon_tiger",
            got=_scalar(conn, "select count(distinct code) from dragon_tiger") or 0,
            target=0,  # 榜单股池每天变，没有固定分母
            latest=str(dt_latest) if dt_latest else None,
            today=today,
            status="ok" if dt_latest and _lag(str(dt_latest), today) <= 1 else "partial",
            cadence="每个交易日 18:30",
            detail="当日上榜股全采；席位明细另见 dragon_tiger_seats。",
        )
    )

    # 板块 / 涨停池（分母是"当日应有"，用今日是否有数判断）
    for key, label, table in [
        ("limit_pool", "涨停池", "limit_pool"),
        ("broken_board", "炸板池", "broken_board"),
        ("concept_board", "概念板块", "concept_board"),
        ("yesterday_limit_up", "昨日涨停", "yesterday_limit_up"),
    ]:
        latest = _scalar(conn, f"select max(trade_date) from \"{table}\"")
        rows.append(
            _domain(
                key=key,
                label=label,
                source=f"duckdb.{table}",
                got=0,
                target=0,
                rows=_scalar(conn, f"select count(*) from \"{table}\""),
                latest=str(latest) if latest else None,
                today=today,
                status="ok" if latest and _lag(str(latest), today) <= 1 else "partial",
                cadence="每个交易日",
                detail="收盘同步写入；分母随当日盘面变化，只看时点与行数。",
            )
        )

    # 估值 / 一致预期 / 机构调研：这三行的覆盖率差异最有信息量
    val_codes = _scalar(conn, "select count(distinct code) from valuation_history") or 0
    val_latest = _scalar(conn, "select max(trade_date) from valuation_history")
    rows.append(
        _domain(
            key="valuation",
            label="估值历史",
            source="duckdb.valuation_history",
            got=int(val_codes),
            target=pool,
            rows=_scalar(conn, "select count(*) from valuation_history"),
            latest=str(val_latest) if val_latest else None,
            today=today,
            status="ondemand",
            cadence="按需回补",
            detail=f"{val_codes}/{pool} 只有估值序列，随日线回补一起长。",
        )
    )

    cons_codes = _scalar(conn, "select count(distinct code) from consensus_forecast") or 0
    cons_latest = _scalar(conn, "select max(trade_date) from consensus_forecast")
    rows.append(
        _domain(
            key="consensus",
            label="一致预期",
            source="duckdb.consensus_forecast",
            got=int(cons_codes),
            target=pool,
            rows=_scalar(conn, "select count(*) from consensus_forecast"),
            latest=str(cons_latest) if cons_latest else None,
            today=today,
            status="ok" if cons_codes >= pool else "partial",
            cadence="每个交易日",
            detail=(
                f"{cons_codes}/{pool} 只有机构预期；"
                f"最近一次同步状态是 degraded（只补到 {cons_codes} 只）。"
            ),
        )
    )

    sur_codes = _scalar(conn, "select count(distinct code) from institution_survey") or 0
    sur_latest = _scalar(conn, "select max(trade_date) from institution_survey")
    rows.append(
        _domain(
            key="survey",
            label="机构调研",
            source="duckdb.institution_survey",
            got=int(sur_codes),
            target=pool,
            rows=_scalar(conn, "select count(*) from institution_survey"),
            latest=str(sur_latest) if sur_latest else None,
            today=today,
            status="partial" if sur_codes < pool else "ok",
            cadence="事件驱动（有调研才有一条）",
            detail=(
                f"{sur_codes}/{pool} 只票有过调研记录（{round(sur_codes / pool * 100, 1) if pool else 0}%）。"
                "这个分母天然采不满——没机构去调研的公司本来就没有这条数据，不是采集漏了。"
            ),
        )
    )
    return rows


def _archive_stamp(data_root: Path, dataset: str) -> tuple[str | None, str | None]:
    """最近一个分区的 (日期, 文件修改时间)。

    为什么要 mtime：`theme_news` 今天 07:32 确实同步成功了，但分区日期写的是
    上游的 `data_as_of`（11-18），文件在 11-18 目录下被整块 replace。
    只看目录日期会以为「两天没采」，看 mtime 才知道「今天采的，落错了格子」。
    """
    base = data_root / "archive"
    if not base.is_dir():
        return None, None
    for year in sorted(base.iterdir(), reverse=True):
        if not (year.is_dir() and year.name.isdigit()):
            continue
        for month in sorted(year.iterdir(), reverse=True):
            if not (month.is_dir() and month.name.isdigit()):
                continue
            for day in sorted(month.iterdir(), reverse=True):
                if not (day.is_dir() and day.name.isdigit()):
                    continue
                target = day / f"{dataset}.parquet"
                if target.is_file():
                    try:
                        stamp = dt.datetime.fromtimestamp(target.stat().st_mtime).replace(microsecond=0)
                        day_text = dt.date(int(year.name), int(month.name), int(day.name)).isoformat()
                    except (OSError, ValueError):
                        continue
                    return day_text, stamp.isoformat()
    return None, None


def _feed_domains(
    data_root: Path,
    pool: int,
    today: dt.date,
    runs: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    conn: Any = None,
    pool_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """研报 / 公告 / 快讯：这三个域的口径从 `news.sqlite` 与采集器轨迹里取。"""
    rows: list[dict[str, Any]] = []
    by_source = {str(src): int(n) for src, n in (_news_rows(data_root, "select source, count(*) from news_items group by 1") or [])}
    latest_item = None
    got_latest = _news_rows(data_root, "select max(published_at) from news_items")
    if got_latest and got_latest[0][0]:
        latest_item = str(got_latest[0][0])[:10]

    # 抓最近一轮里每个数据源的状态，作为"为什么是 0"的证据
    feed_state: dict[str, dict[str, Any]] = {}
    for run in runs:
        for cell in run.get("cells") or []:
            feed_state.setdefault(str(cell.get("dataset")), cell)

    def _state(dataset: str) -> dict[str, Any]:
        return feed_state.get(dataset) or {}

    report_n = by_source.get("research_report", 0)
    report_cell = _state("research_report")
    report_latest, _report_mtime = _archive_stamp(data_root, "research_report")
    if report_archived := (report_n > 0 and bool(report_latest)):
        report_status = "ok"
        report_detail = (
            f"{report_n} 条已补进归档：最近分区 {report_latest}"
            "（`data/archive/.../research_report.parquet`），`market.duckdb` 也有 research_report 表。"
            f"采集器每轮 status={report_cell.get('status') or '未知'}、"
            f"items={report_cell.get('items')}。"
        )
    elif report_n:
        report_status = "lost"
        report_detail = (
            f"采集器每轮 status={report_cell.get('status') or '未知'}、"
            f"items={report_cell.get('items')}，但 `data/archive` 下没有 research_report 分区、"
            f"duckdb 也没有这张表：真正进了 `news_items` 的只有 {report_n} 条。"
            "采到不入归档 = 查询层拿不到。"
        )
    else:
        report_status = "never"
        report_detail = "还没有任何研报入库记录。"
    rows.append(
        _domain(
            key="research_report",
            label="研报",
            source="news.sqlite.news_items(source=research_report)",
            got=report_n,
            target=0,
            unit="条",
            rows=report_n,
            latest=report_latest or latest_item,
            today=today,
            status=report_status,
            cadence="每次批量 100 条",
            detail=report_detail,
        )
    )

    # 公告：三件事分开判——采不到（上游 skipped）/ 采到了没落库 / 落库了但最近一轮停了。
    # 原来这段是硬编码「一条都没有 + enabled:false」，配置一改就变成假话；
    # 也与研报一样改成从 news_items 与采集器轨迹现算。
    ann_n = by_source.get("announcement_cninfo", 0) + by_source.get("announcement_szse", 0)
    ann_cells = [_state("announcement_cninfo"), _state("announcement_szse")]
    ann_known = [cell for cell in ann_cells if cell.get("dataset")]
    ann_items = sum(int(cell.get("items") or 0) for cell in ann_known)
    ann_skipped = bool(ann_known) and all(cell.get("status") == "skipped" for cell in ann_known)
    ann_reason = next(
        (str(cell.get("detail")) for cell in ann_known if cell.get("detail")),
        "没有任何运行记录",
    )
    ann_latest = None
    got_ann_latest = _news_rows(
        data_root, "select max(published_at) from news_items where source like 'announcement_%'"
    )
    if got_ann_latest and got_ann_latest[0][0]:
        ann_latest = str(got_ann_latest[0][0])[:10]
    # 公告候选口径（A+B）：分母 = 池 ∪ 当日异动（池外），并集去重。
    # 采集还没跑过时也现算一遍，让页面能回答"这一轮该采几只"，而不是等 sidecar。
    ann_cand: dict[str, Any] | None = None
    ann_sidecar: dict[str, Any] | None = None
    if pool_codes:
        try:
            from ladderstock.news import candidates as news_candidates

            ann_cand = news_candidates.announcement_candidates(pool_codes, data_root=data_root)
            ann_sidecar = news_candidates.read_candidates(data_root, today.isoformat())
        except Exception:  # noqa: BLE001 - 观测面不能因为一轮筛选就整页报错
            ann_cand = None
            ann_sidecar = None
    ann_denominator = len(ann_cand["union"]) if ann_cand else pool
    ann_pool_size = pool
    ann_extra = list((ann_cand or {}).get("extra") or [])
    ann_extra_total = len(ann_extra)
    ann_movers_day = (ann_cand or {}).get("snapshot_day")
    ann_threshold = (ann_cand or {}).get("threshold")
    ann_movers_total = int((ann_cand or {}).get("movers_total") or 0)
    ann_movers_in_pool = int((ann_cand or {}).get("movers_in_pool") or 0)
    ann_movers_top = [
        {"code": str(item.get("code")), "pct": item.get("pct")}
        for item in list((ann_cand or {}).get("movers") or [])[:8]
    ]
    ann_filtered = bool(ann_cand and not ann_cand.get("fallback"))
    ann_fallback_reason = str((ann_cand or {}).get("fallback_reason") or "")
    ann_cand_note = str((ann_cand or {}).get("note") or "")
    if ann_sidecar:
        # 当天已经有采集记录时，分母与拆解一律以 sidecar 为准：那是这一轮真正
        # 用过的名单。否则行情快照一更新，页面上的「本轮分母」会和当轮记录打架。
        side_denom = int(ann_sidecar.get("union_size") or 0)
        if side_denom:
            ann_denominator = side_denom
        ann_pool_size = int(ann_sidecar.get("pool_size") or 0) or ann_pool_size
        if "extra" in ann_sidecar or "extra_total" in ann_sidecar:
            ann_extra = list(ann_sidecar.get("extra") or [])
            ann_extra_total = int(ann_sidecar.get("extra_total") or 0) or len(ann_extra)
        ann_movers_day = ann_sidecar.get("snapshot_day") or ann_movers_day
        ann_threshold = ann_sidecar.get("threshold") or ann_threshold
        if "filtered" in ann_sidecar:
            ann_filtered = bool(ann_sidecar.get("filtered"))
        ann_fallback_reason = str(
            ann_sidecar.get("fallback_reason") or ann_fallback_reason
        )
        ann_cand_note = str(ann_sidecar.get("note") or ann_cand_note)
    ann_covered = int(ann_sidecar.get("covered") or 0) if ann_sidecar else None
    # 请求名单只含异动票（池内异动 + 池外异动），`requested` 就是本轮真正打出去的数量；
    # 「按策略未采」只算分母里没被请求的票——请求了却没取回是另一回事，不能混进来。
    ann_requested = (
        int(ann_sidecar.get("requested") or 0)
        if (ann_sidecar and ann_sidecar.get("requested") is not None)
        else None
    )
    ann_strategy_skip = (
        max(0, ann_denominator - ann_requested)
        if (ann_sidecar and ann_requested is not None)
        else None
    )
    ann_request_missing = (
        max(0, ann_requested - ann_covered)
        if (ann_requested is not None and ann_covered is not None)
        else None
    )

    if ann_n and not ann_skipped:
        ann_status = "ok"
        ann_detail = (
            f"{ann_n} 条公告已入库（最近一条 {ann_latest or '?'}）。"
            f"最近一轮两个公告源："
            + "、".join(f"{cell.get('dataset')}={cell.get('status')}" for cell in ann_known)
            + f"，items={ann_items}。"
        )
    elif ann_n:
        ann_status = "partial"
        ann_detail = (
            f"已入库 {ann_n} 条（最近一条 {ann_latest or '?'}），但最近一轮没在采："
            f"{'、'.join(str(cell.get('dataset')) + '=' + str(cell.get('status')) for cell in ann_known)}，"
            f"原话「{ann_reason[:80]}」。历史数据还在，只是不再增长。"
        )
    elif ann_items:
        ann_status = "lost"
        ann_detail = (
            f"采集器本轮 items={ann_items} 条，但 `news_items` 里公告 0 条："
            "采到没落库 = 查询层拿不到。先查归档/落库路径，不是上游没给数据。"
            f"采集器原话「{ann_reason[:80]}」"
        )
    elif ann_skipped:
        ann_status = "blocked"
        ann_detail = (
            f"一条都没有：最近一轮两个公告源都 skipped，原话「{ann_reason[:80]}」。"
            "公告栏目上游要求逐票指定 code，批量入口原来不传 code，于是每轮空转。"
            "入口已接线：`news_collect` 按批传 code，公告两源现在走前置筛"
            "（池 ∪ 当日异动，见 news/candidates.py），其余栏目仍按全池；"
            "是否启用看「定时任务」页那一条的 status，别把「还没启用」当成「采集失败」。"
        )
    else:
        ann_status = "never"
        ann_detail = "还没有任何公告采集记录——既没采到，也没有可判断的失败原因。"

    if ann_cand_note:
        if ann_sidecar and ann_covered is not None:
            req = ann_requested if ann_requested is not None else ann_covered
            miss = ann_request_missing or 0
            ann_detail += (
                f"{'；' if ann_detail else ''}候选口径：{ann_cand_note}；"
                f"本轮只请求异动票 {req} 只，采过 {ann_covered} 只"
                + (f"（{miss} 只请求了没取回）" if miss else "")
                + f"；按策略未采 {ann_strategy_skip} 只（池内非异动票，不算缺口）。"
            )
        else:
            ann_detail += (
                f"{'；' if ann_detail else ''}候选口径：{ann_cand_note}；"
                f"本轮还没跑过前置筛采集（news_radar 未启用），分母是预告，"
                f"只有异动票会被逐票请求。"
            )

    rows.append(
        _domain(
            key="announcement",
            label="公告",
            source="news.sqlite.news_items(source=announcement_*)",
            got=ann_n,
            target=0,
            unit="条",
            rows=ann_n,
            latest=ann_latest,
            today=today,
            status=ann_status,
            cadence="每轮批量（逐票 code；分母=池∪当日异动）",
            detail=ann_detail,
            extra={
                "denominator": ann_denominator,
                "pool_size": ann_pool_size,
                "extra_movers": ann_extra[:20],
                "extra_total": ann_extra_total,
                "movers_total": ann_movers_total,
                "movers_in_pool": ann_movers_in_pool,
                "movers_top": ann_movers_top,
                "movers_day": ann_movers_day,
                "movers_threshold": ann_threshold,
                "candidates_filtered": ann_filtered,
                "candidates_fallback": ann_fallback_reason,
                "covered": ann_covered,
                "requested": ann_requested,
                "requested_missing": ann_request_missing,
                "strategy_skipped": ann_strategy_skip,
                "candidates_note": ann_cand_note,
            },
        )
    )

    flash_n = by_source.get("flash_eastmoney", 0) + by_source.get("flash_wallstreetcn", 0)
    flash_cells = [_state("flash_eastmoney"), _state("flash_wallstreetcn"), _state("flash_cls")]
    flash_ok = [c for c in flash_cells if c.get("status") == "ok"]
    rows.append(
        _domain(
            key="flash",
            label="快讯",
            source="news.sqlite.news_items(source=flash_*)",
            got=flash_n,
            target=0,
            unit="条",
            rows=flash_n,
            latest=latest_item,
            today=today,
            status="ok" if latest_item and _lag(latest_item, today) <= 1 else "partial",
            cadence="每轮批量 100 条/源",
            detail=(
                f"{flash_n} 条，{len(flash_ok)}/{len(flash_cells)} 个快讯源 ok（"
                + "、".join(
                    f"{c.get('dataset')}={c.get('status')}" for c in flash_cells if c.get("dataset")
                )
                + "）。财联社那一路长期 empty/failed。"
            ),
        )
    )

    # 题材资讯：07:30 的 wire_news_daily 先跑 qfw run 再同步 theme_news。
    # 采集与落库是两段，落库的**日期**又是第三件事：这一轮同步成功，但分区日期
    # 取的是上游 data_as_of，于是今天采的内容被写进更早的目录里。
    theme_latest, theme_mtime = _archive_stamp(data_root, "theme_news")
    theme_rows = None
    if conn is not None:
        theme_rows = _scalar(conn, "select count(*) from theme_news")
    wire_job = next((job for job in jobs if job["job_id"] == "wire_news_daily"), None)
    wire_detail = (wire_job or {}).get("detail") or ""
    fetched = None
    match = re.search(r"采集\s*([0-9,]+)\s*条", wire_detail)
    if match:
        fetched = int(match.group(1).replace(",", ""))
    synced = re.search(r"同步：([0-9]+)\s*行", wire_detail)
    misfiled = bool(theme_mtime) and theme_mtime[:10] == today.isoformat() and theme_latest != today.isoformat()
    rows.append(
        _domain(
            key="theme_news",
            label="题材资讯",
            source="archive.theme_news（源 = wire_news_daily 的 qfw run）",
            got=int(theme_rows or 0),
            target=0,
            unit="条",
            rows=int(theme_rows) if theme_rows is not None else None,
            latest=theme_latest,
            today=today,
            status=("lost" if misfiled else ("ok" if theme_latest and _lag(theme_latest, today) <= 1 else "partial")),
            cadence="每天 07:30（含周末）",
            detail=(
                f"最近一轮采集 {fetched if fetched is not None else '?'} 条资讯（{(wire_job or {}).get('status') or '无记录'}），"
                f"同步写出 {synced.group(1) if synced else '?'} 行。"
                f"但分区落在 **{theme_latest}** 那一天（文件写入时间是 {theme_mtime}）"
                + (
                    "——今天采的内容被写进了更早的目录并整块覆盖，所以「最新一天」永远追不上今天。"
                    "问题在落库的日期口径取了上游 data_as_of，不是采集失败。"
                    if misfiled
                    else "。"
                )
                + f" 查询层 theme_news {theme_rows if theme_rows is not None else '?'} 行。"
            ),
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# 运行记录
# --------------------------------------------------------------------------- #


_STATUS_HINT = {
    "ok": "正常完成",
    "degraded": "完成但有缺件（多为通知后端/备源降级），数据不丢",
    "failed": "真失败，数据这一轮没写出",
    "skipped": "按条件主动跳过（自选为空/非交易时段/缺前置），不是故障",
}


def _schedule_runs(data_root: Path, now: dt.datetime) -> list[dict[str, Any]]:
    """`data/schedule_runs/<job>.json`：每个任务只留最近一次结果。"""
    base = data_root / "schedule_runs"
    rows: list[dict[str, Any]] = []
    if not base.is_dir():
        return rows
    for path in sorted(base.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        started = str(doc.get("started_at") or "")
        age = None
        try:
            age = round((now - dt.datetime.fromisoformat(started)).total_seconds() / 3600, 1)
        except ValueError:
            age = None
        status = str(doc.get("status") or "unknown")
        all_unavailable = [
            item
            for item in (doc.get("sources_unavailable") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        rows.append(
            {
                "job_id": str(doc.get("job_id") or path.stem),
                "status": status,
                "hint": _STATUS_HINT.get(status, ""),
                "started_at": started,
                "finished_at": doc.get("finished_at"),
                "age_hours": age,
                "detail": " ".join(str(doc.get("detail") or "").split())[:220],
                # 只给结构化名单（wire 采集的「哪几个源不可用」）；页面不必再解析 detail。
                # 总数单独给：名单只传前 12 条，卡片仍要能说「共 29 个」。
                "sources_unavailable": all_unavailable[:12],
                "sources_unavailable_total": len(all_unavailable),
            }
        )
    order = {"failed": 0, "degraded": 1, "ok": 2, "skipped": 3, "unknown": 4}
    rows.sort(key=lambda row: (order.get(row["status"], 9), -(row["age_hours"] or 0)))
    return rows


def _news_runs(data_root: Path, limit: int = 6) -> list[dict[str, Any]]:
    """采集器状态矩阵：一行一次运行，一格一个数据源。"""
    raw = _news_rows(data_root, "select started_at, window_start, window_end, fetched, stored, detail from news_runs order by id desc limit %d" % limit)
    rows: list[dict[str, Any]] = []
    for started_at, window_start, window_end, fetched, stored, detail in raw or []:
        try:
            doc = json.loads(detail or "{}")
        except ValueError:
            doc = {}
        cells = []
        for item in doc.get("sources_status") or []:
            if not isinstance(item, dict):
                continue
            cells.append(
                {
                    "dataset": str(item.get("dataset") or ""),
                    "status": str(item.get("status") or ""),
                    "items": item.get("items"),
                    "detail": " ".join(str(item.get("detail") or "").split())[:120],
                }
            )
        rows.append(
            {
                "at": str(started_at or ""),
                "window": f"{window_start} → {window_end}",
                "fetched": fetched,
                "stored": stored,
                "cells": cells,
            }
        )
    return rows


def _progress_view(project: Path, data_root: Path, now: dt.datetime) -> dict[str, Any]:
    today = now.date()
    pool_codes = _base_pool(project)
    pool = len(pool_codes)
    runs = _news_runs(data_root)

    domains: list[dict[str, Any]] = []
    duckdb_error = None
    conn = None
    db = data_root / "market.duckdb"
    if db.is_file():
        try:
            conn = duckdb_store.connect(db, read_only=True)
            domains.extend(_duckdb_domains(conn, pool, today))
        except Exception as exc:  # noqa: BLE001 - 查询层被占用时整页仍要能看
            duckdb_error = " ".join(str(exc).split())[:200]

    jobs = _schedule_runs(data_root, now)
    try:
        domains.extend(_feed_domains(data_root, pool, today, runs, jobs, conn, pool_codes))
    finally:
        if conn is not None:
            duckdb_store.close(conn)
    if not db.is_file():
        duckdb_error = f"没有历史库：{db}"

    counts = {
        "ok": sum(1 for d in domains if d["status"] == "ok"),
        "partial": sum(1 for d in domains if d["status"] == "partial"),
        "ondemand": sum(1 for d in domains if d["status"] == "ondemand"),
        "broken": sum(1 for d in domains if d["status"] in {"never", "blocked", "lost"}),
    }
    return {
        "ok": True,
        "today": today.isoformat(),
        "as_of": now.replace(microsecond=0).isoformat(),
        "base_pool": pool,
        "domains": domains,
        "counts": counts,
        "runs": jobs,
        "matrix": runs,
        "duckdb_error": duckdb_error,
        "note": "覆盖率分母是代码池（sync.yaml 的 base_pool），不是全市场 5,400 只。",
    }


def build_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=PROGRESS_PREFIX)
    project = _project_root(root)
    data_root = _data_root(root, project)

    @router.get("/progress")
    def progress_view() -> dict[str, Any]:
        return _progress_view(project, data_root, dt.datetime.now())

    return router


__all__ = ["PROGRESS_PREFIX", "build_router"]
