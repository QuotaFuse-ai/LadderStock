"""首页指数条：A 股四个 + 美股两个 + 港股两个指数的实时报价投影（QUOT-380 / QUOT-471 / QUOT-560）。

**白名单写死在本层八条**，不向 peer 动态要清单：实测 `index_list` 是按涨幅排序的
100 条榜，不含首页这些指数，还混进老三板个股（`400224 苏阳光5`）。榜是日单一的，
照它建注册表等于首页那几格「今天有、明天没」。

三块指数走三条不同的路（**不是**同一工具换参数）：

- **A 股 4 格**（SH/SZ）走 `index_quote`（peer `tickhub`），一次批量请求拿全。
- **美股 2 格**（US）走 `index_quote_us` → `th_us_get_quote`（peer `tickhub_us`），
  **逐 symbol** 一次一个（该工具必填单个 `symbol`，没有批量入参）。
- **港股 2 格**（HK）走 `index_quote_hk` → 同一个 `th_us_get_quote`，同样逐 symbol，
  入参是规范形 `HSI.HK` / `HSTECH.HK`（QUOT-560：peer 侧加了港股指数别名表，
  tencent/sina 免费公开源实测可取，零 key、Tier C 非商用）。首页一次拉取 =
  1 个 CN 批量请求 + 4 个 US peer 请求。

A 股那批实测出来的坑（2026-11-03 对真实 peer `index_quote` 复测），判据逐条对着它们写：

1. **入参必须带 `sh`/`sz` 前缀，且只允许前缀形式**。裸 `000001` 不报错——它回
   **平安银行**（QUOT-379 当时记录的是 `isError`，现在已变成静默回错票，只会更危险）；
   点号写法 `000001.SH` 同样回平安银行。入参由 `INDEX_WHITELIST` 拼，外部传不进来。
2. **绝不能用 `spot_quote` / `tencent_quote_batch` 取指数**：批量工具剥掉前缀后按 6 位
   代码回键，指数与个股撞车（实测四回三、`000001` 成了平安银行且 `failed=0`）。
   指数只走 `index_quote` 这一个工具。
3. **返回行的 `code` 不带前缀**，所以匹配按 `(market, code)`，不按 `code` 建字典。
   `is_index` 一并核验：它是必要条件之一，但**不是充分条件**（平安银行那条也标
   `is_index: true`），单看它挡不住坑 1。

美股 / 港股那批（2026-11-06 / 2026-11-11 对真实 peer `th_us_get_quote` 复测）：回包形状
与 CN 不同（`payload` 对象，非 `ok/data/meta` 壳），由 `data/adapters/tickhub_us.py`
落成一行；`compliance_tier=C`（`sina` / `tencent`）与警告句落进信封 `source_detail`，
「非商用」前提在数据层可见。港股行的 `market=HK`、`symbol=HSI.HK`（规范形），
匹配键与美股同用 `(market, symbol)`。

数字照 peer 原样投影，不做二次换算、不补 0（DEC-028）；取不到就是
`available=false` + 原因——不自采（DEC-006 闸门 4）、不换供应商、不猜价。
非交易时段 peer 回上一交易日收盘，`as_of` 如实带出，配 `FOOTNOTE` 说明是快照。

**fail-fast（QUOT-571）**：首页是「打开即看」，取数必须在前端门限内返回——哪怕降级。
peer 挂掉时不自愈（adopt-only 不拉起外部托管进程，DEC-078），所以取数层要自己快：

1. **健康通道短路**：`tickhub_us` 已被自愈标 `needs_human` / 进程已退出（QUOT-556
   事件通道的跃迁）时，美股 / 港股四格一次请求都不发，直接出 missing + 原因。
   只在「确认坏了」时短路，不在「恢复中 / 不确定」时短路（宁可多打一次次正常请求）。
2. **块级熔断**：同一 peer 的那一块第一次失败就判整块不可用，剩余格不再逐格
   等超时（4 格串行 × 10s = 40s 就是「banner 挂 8s」的来源）。
3. **并行取数**：美股 / 港股两块线程池并行；块内逐 symbol 仍串行（peer 闸门
   本来就限制同 peer 并发，块内并行只会排队，不会更快）。
4. **总预算**：`fetch_indices` 在总预算内返回；预算耗尽的那块按「超时未返回」
   出 missing，不再等。已经拿到的真数据照常上屏。
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from ladderstock.core.ladder import as_float

_log = logging.getLogger(__name__)

#: A 股批量取数入口：`DATASET_CATALOG` 里登记的 `index_quote`（peer `tickhub`，同工具名）
DATASET = "index_quote"

#: 美股逐 symbol 取数入口：`DATASET_CATALOG` 里登记的 `index_quote_us`
#: （peer `tickhub_us` / 工具 `th_us_get_quote`）。**不进 sync 台账**，同 `index_quote`。
DATASET_US = "index_quote_us"

#: 港股逐 symbol 取数入口：`DATASET_CATALOG` 里登记的 `index_quote_hk`
#: （同一个 peer / 工具，入参 `HSI.HK` / `HSTECH.HK`，QUOT-560）。同样不进 sync 台账。
DATASET_HK = "index_quote_hk"

#: 首页脚注（`HOME-V2-DATA-SURFACE.md` A-1 的口径：这不是逐笔实时）
FOOTNOTE = "快照，不是逐笔实时"

#: 整批取数失败时写进 `reason` 的前缀
FETCH_FAILED = "index_quote 取数失败"
FETCH_US_FAILED = "index_quote_us 取数失败"
FETCH_HK_FAILED = "index_quote_hk 取数失败"
NO_ROWS = "index_quote 没有返回任何匹配的行"

Status = Literal["ok", "partial", "missing"]

#: 单格请求预算（秒）：3 秒没回就当这一格/这一块挂了。真实的 4 格实测是亚秒级，
#: 3 秒足够容纳冷握手；比 `sources.yaml` 的 10s + 2 次重试小一个量级，是为了
#: 「一个 down peer 不拖垮整屏」，不是给上游加压。
DEFAULT_CELL_TIMEOUT_SEC = 3.0

#: 八格总预算（秒）：预算内返回，超预算的块出 missing。前端门限 8s，这里留一半余量。
DEFAULT_TOTAL_BUDGET_SEC = 4.5

#: 健康通道里「确认坏了」的跃迁（`supervisor/health_events.py` 的事件名，QUOT-556）。
#: 只认这两种：恢复中 / 进程在但是探针没回来都不算坏，照常发请求。
PEER_DOWN_EVENTS: frozenset[str] = frozenset({"crash", "needs_human"})

#: 数据集 → 归属 peer（`core/boundary.py` 的 `DATASET_CATALOG`；短路的信号按块归属查）
PEER_OF_DATASET: dict[str, str] = {
    DATASET: "tickhub",
    DATASET_US: "tickhub_us",
    DATASET_HK: "tickhub_us",
}

#: peer「健康复原」的跃迁：短路态只在这些事件之后才可能解除
_PEER_BACK_EVENTS: frozenset[str] = frozenset({"recovered"})

#: 短路 / 熔断 / 超预算的缺失原因前缀（白话，前端直接展示）
PEER_DOWN_EVENT = "数据源暂不可用"
BLOCK_FAIL_FAST = "数据源无响应，本块余下格子不再逐格等待"
BLOCK_BUDGET = "八格总预算内未取回"

#: 触发「整块不再逐格等」的异常：超时 / 连接失败本身就是 peer 级故障。
#: 业务型失败（`SourceUnavailable` 且是上游拒收 / 无数据）只算单格失败，不熔断——
#: 那可能只是这个 symbol 没行情，另一格还取得到。
_FAST_FAIL_ERRORS = (TimeoutError, ConnectionError)


@dataclass(frozen=True)
class IndexSpec:
    """一格指数。`code` 是给 UI 的稳定键（带前缀），`market`/`symbol` 是 peer 行里的形状。"""

    code: str
    market: str
    symbol: str
    name: str


#: A 股四格：一次 `index_quote` 批量请求拿全（peer `tickhub`）
CN_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("sh000001", "SH", "000001", "上证指数"),
    IndexSpec("sh000300", "SH", "000300", "沪深300"),
    IndexSpec("sz399001", "SZ", "399001", "深证成指"),
    IndexSpec("sz399006", "SZ", "399006", "创业板指"),
)

#: 美股两格：逐个 `symbol` 打 `th_us_get_quote`（peer `tickhub_us`）
#: `market` 与 `symbol` 同形（适配器把 `symbol` 落成行键，`market` 默认 `US`）
US_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("DJI", "US", "DJI", "道琼斯"),
    IndexSpec("IXIC", "US", "IXIC", "纳斯达克综合"),
)

#: 港股两格：逐个 `symbol` 打 `th_us_get_quote`（peer `tickhub_us`，QUOT-560）。
#: `symbol` 用 peer 的规范形 `HSI.HK` / `HSTECH.HK`（回包 `symbol` 同形，匹配键才对得上）；
#: `code` 仍是给 UI 的稳定键 `HSI` / `HSTECH`，前端按它渲染，不随上游写法变。
HK_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("HSI", "HK", "HSI.HK", "恒生指数"),
    IndexSpec("HSTECH", "HK", "HSTECH.HK", "恒生科技"),
)

#: 首页八格全表（顺序即上屏顺序）：A 股 → 美股 → 港股
INDEX_WHITELIST: tuple[IndexSpec, ...] = CN_WHITELIST + US_WHITELIST + HK_WHITELIST

#: 每格对外的数值字段：peer 行里的键 → 载荷里的键，一一对应，不做任何加工
_VALUE_KEYS: tuple[str, ...] = ("price", "change", "change_pct", "prev_close")

#: peer 行情时间在载荷里不透出（对外只有 `as_of` 一个口径），投影前先摘掉
_INTERNAL_KEYS: frozenset[str] = frozenset({"row_timestamp"})


@dataclass(frozen=True)
class IndexStrip:
    """一次投影的结果，交给接口层套信封（`payload` 是上屏的四格）。"""

    payload: dict[str, Any]
    status: Status
    day: date | None
    source_detail: str


@dataclass(frozen=True)
class _Block:
    """一块取数的结果；`detail` 是该块的合规详述（美股 / 港股读 peer 信封）。"""

    rows: list[dict[str, Any]]
    reason: str | None
    detail: str = ""
    cell_fails: dict[str, str] = field(default_factory=dict)


def _run_block(job: Callable[[], _Block]) -> _Block:
    """跑一块；块内异常全部落到 missing + 原因，首页不能因为一个上游就 500。"""
    try:
        return job()
    except Exception as exc:  # noqa: BLE001 - 兜底：任何块内缺陷都只缺那一块
        _log.warning("指数块取数异常：%s", exc)
        return _Block(rows=[], reason=f"{FETCH_FAILED}：{exc}")


def _blocked(reason: str, fail_prefix: str, specs: tuple[IndexSpec, ...]) -> _Block:
    """健康通道判 peer 已坏：不发请求，整块出 missing + 原因。"""
    return _Block(
        rows=[],
        reason=f"{fail_prefix}：{reason}",
        cell_fails={spec.symbol: f"{fail_prefix}：{reason}" for spec in specs},
    )


def _run_parallel(
    jobs: Mapping[str, Callable[[], _Block]], *, deadline: float
) -> tuple[_Block, ...]:
    """并行跑美股 / 港股两块，**总预算内返回**；超预算的块出 missing，不阻塞返回。

    超时的线程不取消（同步 transport 没法安全杀），但它的结果丢弃——它只是在
    后台把这次 peer 调用跑完；页面不等它。
    """
    results: dict[str, _Block] = {}
    guard = threading.Lock()

    def worker(key: str, job: Callable[[], _Block]) -> None:
        try:
            block = _run_block(job)
        except BaseException as exc:  # noqa: BLE001 - 块内致命错误也只缺这一块
            block = _Block(rows=[], reason=f"{FETCH_FAILED}：{exc}")
        with guard:
            results.setdefault(key, block)

    threads = [
        threading.Thread(target=worker, args=(key, job), name=f"indices-{key}", daemon=True)
        for key, job in jobs.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    with guard:
        snapshot = dict(results)
    out: list[_Block] = []
    for key in jobs:
        block = snapshot.get(key)
        out.append(
            block
            if block is not None
            else _Block(rows=[], reason=f"{BLOCK_BUDGET}（{key}，本块照缺不补）")
        )
    return tuple(out)


@dataclass(frozen=True)
class PeerDownSignal:
    """peer 已被健康通道判「确认坏了」，可供取数层短路。

    `subscribe` 由装配侧接 `HealthEventBus.since`（QUOT-556）——取数层只读事件，
    不依赖 supervisor 模块（那是上层，core 不反向导入）。签名固定成
    `subscribe(seq) -> 该 peer 的事件列表（按 seq 升序）`：事件类型过滤留在
    `check()` 里，装配侧只需 `functools.partial(bus.since, module=peer)`。
    """

    peer: str
    subscribe: Callable[[int], Any]
    reasons: Mapping[str, str]

    def check(self) -> str | None:
        """这个 peer 是否在「最后一次坏跃迁之后还没恢复」；是则回白话原因。

        只需要「最近一条坏 / 恢复事件」，所以从最新往回读、读到第一条相关事件就停
        ——环形缓冲里该 peer 的历史再长，也只读一页，不会被旧事件淹没。
        """
        try:
            items = list(self.subscribe(0) or ())
        except Exception as exc:  # noqa: BLE001 - 健康通道坏了不能拖垮取数
            _log.debug("peer 健康通道读取失败（%s），照常取数：%s", self.peer, exc)
            return None
        for item in reversed(items):
            if getattr(item, "module", "") != self.peer:
                continue
            name = getattr(item, "event", "")
            if name in PEER_DOWN_EVENTS:
                return self.reasons.get(name, PEER_DOWN_EVENT)
            if name in _PEER_BACK_EVENTS:
                return None
        return None


def _down_reason(peer_down: Mapping[str, PeerDownSignal] | None, dataset: str) -> str | None:
    """该块归属 peer 若已被健康通道判坏，回白话原因；没有信号 / 没坏则 None。"""
    signal = (peer_down or {}).get(PEER_OF_DATASET.get(dataset, ""))
    return signal.check() if signal is not None else None


def fetch_indices(
    registry: Any,
    *,
    cell_timeout: float | None = None,
    total_budget: float | None = None,
    peer_down: Mapping[str, PeerDownSignal] | None = None,
) -> IndexStrip:
    """一次请求取回八格，且在总预算内返回（QUOT-571 fail-fast）。

    取数按块分头走、互不连坐：A 股一次的批量 `index_quote` 失败不影响美股 / 港股，
    美股或港股某一 symbol 失败也只影响那一格。某一块整体不可用 → 那一块的格子出原因，
    其余照常显示。

    - 三块全部线程池并行（A 股一次批量、美股 / 港股各逐 symbol）；块内串行——
      peer 闸门本就限制同 peer 并发，块内并行只会排队，不会更快；
    - `peer_down` 里登记了且健康通道判坏的块，一次请求都不发（两块都走 `tickhub_us`
      时共用同一份判断，同一轮最多读一次）；
    - `cell_timeout` 是单格预算，`total_budget` 是八格总预算；超预算的块出 missing。
    """
    timeout = DEFAULT_CELL_TIMEOUT_SEC if cell_timeout is None else max(0.1, cell_timeout)
    budget = DEFAULT_TOTAL_BUDGET_SEC if total_budget is None else max(0.1, total_budget)
    deadline = time.monotonic() + budget

    down: dict[str, str | None] = {}

    def down_for(dataset: str) -> str | None:
        peer = PEER_OF_DATASET[dataset]
        if peer not in down:
            down[peer] = _down_reason(peer_down, dataset)
        return down[peer]

    jobs: dict[str, Callable[[], _Block]] = {}
    for dataset, specs, fail_prefix in (
        (DATASET, CN_WHITELIST, FETCH_FAILED),
        (DATASET_US, US_WHITELIST, FETCH_US_FAILED),
        (DATASET_HK, HK_WHITELIST, FETCH_HK_FAILED),
    ):
        reason = down_for(dataset)
        if reason:
            jobs[dataset] = functools.partial(_blocked, reason, fail_prefix, specs)
        elif dataset == DATASET:
            jobs[dataset] = functools.partial(_fetch_cn, registry, timeout=timeout)
        else:
            jobs[dataset] = functools.partial(
                _fetch_per_symbol, registry, dataset, specs, fail_prefix, timeout=timeout
            )
    cn, us, hk = _run_parallel(jobs, deadline=deadline)

    cn_reason = cn.reason
    cells = _cells_for(CN_WHITELIST, _index_by_market_code(cn.rows), reason=cn_reason)
    cells.extend(
        _cells_for(
            US_WHITELIST, _index_by_symbol(us.rows), reason=us.reason, cell_reasons=us.cell_fails
        )
    )
    cells.extend(
        _cells_for(
            HK_WHITELIST, _index_by_symbol(hk.rows), reason=hk.reason, cell_reasons=hk.cell_fails
        )
    )
    us_reason, us_detail = us.reason, us.detail
    hk_reason, hk_detail = hk.reason, hk.detail

    filled = [cell for cell in cells if cell["available"]]
    day = max(
        (parsed for cell in filled if (parsed := _row_day(cell.get("row_timestamp")))),
        default=None,
    )
    status: Status
    if not filled:
        status = "missing"
    elif len(filled) == len(cells):
        status = "ok"
    else:
        status = "partial"
    miss_reason: str | None = None if filled else _miss_detail(cn_reason, us_reason, hk_reason)
    return IndexStrip(
        payload=_payload(cells, available=bool(filled), reason=miss_reason),
        status=status,
        day=day,
        source_detail=_source_detail(us_detail, hk_detail, cn_reason, us_reason, hk_reason),
    )


def _fetch_cn(registry: Any, *, timeout: float) -> _Block:
    """A 股一批：`index_quote` 一次拿四行。失败 → 空行 + 原因（不抛）。"""
    params = {"codes": ",".join(spec.code for spec in CN_WHITELIST)}
    try:
        outcome = registry.fetch_live(DATASET, params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 首页不能因为一个上游就 500
        failure = f"{FETCH_FAILED}：{getattr(exc, 'message', None) or exc}"
        _log.warning("index_quote 取数失败：%s", failure)
        return _Block(rows=[], reason=failure)
    rows = _rows(outcome.envelope)
    reason: str | None = None if rows else f"{NO_ROWS}（index_quote 返回 0 行）"
    return _Block(rows=rows, reason=reason)


def _fetch_per_symbol(
    registry: Any,
    dataset: str,
    specs: tuple[IndexSpec, ...],
    fail_prefix: str,
    *,
    timeout: float,
) -> _Block:
    """美股 / 港股共用：逐 `symbol` 调 `th_us_get_quote`（peer `tickhub_us`，一 symbol 一次）。

    任何一格失败都只影响那一格：单格失败的原因按 symbol 记进 `cell_fails`，
    由 `_cells_for` 落到那一格上（不冒充「上游静默丢行」）；整块全失败时另回一个
    整体原因。`detail` 是该块的合规详述（从信封 `payload` 读 `compliance_tier` /
    `warnings`——`ProviderRegistry` 会在成功路径用轨迹覆盖信封 `source_detail`，
    所以合规字段从载荷读，不靠它）。

    块级熔断（QUOT-571）：一格撞上超时 / 连接失败，说明 peer 整块不通——剩下的格
    不再逐格等满上游超时，直接按「无响应」出 missing（`_FAST_FAIL_ERRORS`）。
    业务型失败（上游拒收 / 无数据）不熔断，下一格照常发。
    """
    rows: list[dict[str, Any]] = []
    fail_reasons: list[str] = []
    fail_by_symbol: dict[str, str] = {}
    tiers: set[str] = set()
    warnings: list[str] = []
    down: str | None = None
    for spec in specs:
        if down is not None:
            miss = f"{fail_prefix}（{spec.symbol}）：{BLOCK_FAIL_FAST}"
            fail_reasons.append(miss)
            fail_by_symbol[spec.symbol] = miss
            continue
        params = {"symbol": spec.symbol, "commercial_use": False}
        try:
            outcome = registry.fetch_live(dataset, params=params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 一格失败不连坐另一格
            failure = f"{fail_prefix}（{spec.symbol}）：{getattr(exc, 'message', None) or exc}"
            _log.warning("%s", failure)
            fail_reasons.append(failure)
            fail_by_symbol[spec.symbol] = failure
            if isinstance(exc, _FAST_FAIL_ERRORS):
                down = failure
            continue
        rows.extend(_rows(outcome.envelope))
        tier, warns = _peer_compliance(outcome.envelope)
        if tier:
            tiers.add(tier)
        warnings.extend(w for w in warns if w not in warnings)
    reason: str | None = _block_overall_reason(dataset, rows, fail_reasons)
    if reason is not None and down is not None:
        reason = f"{reason}；{BLOCK_FAIL_FAST}"
    return _Block(
        rows=rows,
        reason=reason,
        detail=_peer_detail(tiers, warnings),
        cell_fails=fail_by_symbol,
    )


def _peer_compliance(envelope: Any) -> tuple[str, list[str]]:
    """从 US peer 信封 `payload` 取 (compliance_tier, warnings)——合规字段的落地位置。"""
    payload = getattr(envelope, "payload", {}) or {}
    tier = str(payload.get("compliance_tier") or "").strip().upper()
    raw = payload.get("warnings")
    warns = [str(w).strip() for w in raw if str(w).strip()] if isinstance(raw, list) else []
    return tier, warns


def _peer_detail(tiers: set[str], warnings: list[str]) -> str:
    """美股 / 港股块 `source_detail`：`th_us_get_quote` + 合规分级 + 警告句（不吞）。"""
    if not tiers and not warnings:
        return ""
    tier_text = "/".join(sorted(tiers)) if tiers else "未知"
    parts = [f"th_us_get_quote（compliance_tier={tier_text}"]
    if warnings:
        parts.append("；" + "；".join(warnings))
    parts.append("）")
    return "".join(parts)


def _block_overall_reason(
    dataset: str, rows: list[dict[str, Any]], fail_reasons: list[str]
) -> str | None:
    """整块的兜底原因：**只在该块所有格都没数据**时才有值（单格失败留给按格原因）。"""
    if rows:
        return None
    if fail_reasons:
        return "；".join(fail_reasons)
    return f"{dataset} 没有返回任何匹配的行"


def _source_detail(
    us_detail: str,
    hk_detail: str,
    cn_reason: str | None,
    us_reason: str | None,
    hk_reason: str | None,
) -> str:
    """信封 `source_detail`：peer 成功详述（含合规字段）+ 各块失败原因。

    美股 / 港股走同一个工具，两块都成功时详述常常逐字相同——去重保序，不重复刷屏。
    """
    parts = [us_detail, hk_detail, *[x for x in (cn_reason, us_reason, hk_reason) if x]]
    return "；".join(dict.fromkeys(x for x in parts if x))


def _index_by_market_code(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """按 `(market, code)` 建键（坑 3：只按 `code` 会把 sh000001 与 sz000001 撞成一格）。

    `is_index=false` 的行不算数：它是必要条件（挡住把个股行当指数）。`is_index=true`
    仍不是充分条件（平安银行那条也标 true），所以 `(market, code)` 精确匹配不能省。
    """
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not row.get("is_index"):
            continue
        market = str(row.get("market") or "").strip().upper()
        symbol = str(row.get("code") or "").strip()
        if market and symbol:
            matched.setdefault((market, symbol), row)
    return matched


def _index_by_symbol(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """美股 / 港股按 `(market, symbol)` 建键：这类行没有 `code`，`symbol` 才是行键。

    `market` 缺省按 `US`（美股回包不带 market 字段）；港股行带 `market=HK`，照读。
    """
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "US").strip().upper()
        symbol = str(row.get("symbol") or row.get("code") or "").strip()
        if market and symbol:
            matched.setdefault((market, symbol), row)
    return matched


def _cells_for(
    specs: tuple[IndexSpec, ...],
    by_key: Mapping[tuple[str, str], dict[str, Any]],
    *,
    reason: str | None = None,
    cell_reasons: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按 `specs` 的顺序出格：匹配到就照抄数值，匹配不到就 `null` + 原因。

    缺格原因三档，按真实程度取：该 symbol 自己的取数失败（`cell_reasons`）→
    整块原因（`reason`）→ 「上游没回这一行」的兜底。失败不能冒充静默丢行。

    `is_index` 只有 CN 行有，所以这里**不强制**该字段——CN 行的坑 1 由
    `(market, code)` 精确匹配 + 白名单入参双重兜住（`_index_by_market_code` 已经
    把裸 `000001` 那条拼不到 `(SH, 000001)` 上）。
    """
    cells: list[dict[str, Any]] = []
    for spec in specs:
        row = by_key.get((spec.market, spec.symbol))
        if row is None:
            miss = (cell_reasons or {}).get(spec.symbol) or reason or _cell_miss_detail(spec)
            cells.append(_missing_cell(spec, miss))
            continue
        cells.append(_filled_cell(spec, row))
    return cells


def _missing_cell(spec: IndexSpec, reason: str) -> dict[str, Any]:
    return {
        "code": spec.code,
        "market": spec.market,
        "name": spec.name,
        "available": False,
        "price": None,
        "change": None,
        "change_pct": None,
        "prev_close": None,
        "as_of": None,
        "reason": reason,
    }


def _filled_cell(spec: IndexSpec, row: Mapping[str, Any]) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "code": spec.code,
        "market": spec.market,
        "name": spec.name,
        "available": True,
        "reason": None,
        "as_of": _row_as_of(row),
        "row_timestamp": str(row.get("timestamp") or ""),
    }
    # 照抄，不换算：peer 给 `price` 我们就显 `price`，缺一个字段就是 `null`
    for key in _VALUE_KEYS:
        cell[key] = as_float(row.get(key))
    return cell


def _row_as_of(row: Mapping[str, Any]) -> str | None:
    """`as_of`：CN 行是 14 位行情时间，US/HK 行可能直接给 ISO 或 `YYYY-MM-DD HH:MM:SS`。"""
    raw = str(row.get("timestamp") or row.get("as_of") or "")
    return iso_timestamp(raw) or (raw or None)


def _cells(
    by_market_code: Mapping[tuple[str, str], dict[str, Any]] | None = None,
    *,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    """兼容旧签名：只用 A 股白名单出格（保留给既有测试 / 调用方）。"""
    return _cells_for(CN_WHITELIST, by_market_code or {}, reason=reason)


def _payload(cells: list[dict[str, Any]], *, available: bool, reason: str | None) -> dict[str, Any]:
    """摘掉内部字段后的载荷——对外每一格只有契约里的字段。"""
    return {
        "available": available,
        "reason": reason,
        "footnote": FOOTNOTE,
        # 上屏主口径仍是 A 股那条（页面脚注「源：」跟它）；多源在 `datasets` 里全带出
        "price_source": DATASET,
        "datasets": [DATASET, DATASET_US, DATASET_HK],
        "cells": [
            {key: value for key, value in cell.items() if key not in _INTERNAL_KEYS}
            for cell in cells
        ],
    }


def _cell_miss_detail(spec: IndexSpec) -> str:
    dataset = {"US": DATASET_US, "HK": DATASET_HK}.get(spec.market, DATASET)
    return (
        f"{dataset} 未返回 ({spec.market}, {spec.symbol}) 的行"
        f"（入参 {spec.code} 被上游静默丢弃，不补值）"
    )


def _miss_detail(cn_reason: str | None, us_reason: str | None, hk_reason: str | None) -> str:
    """全盘无数据时的整体原因（三块原因都并进来）。"""
    parts = [x for x in (cn_reason, us_reason, hk_reason) if x]
    return "；".join(parts) or NO_ROWS


def _row_day(raw: Any) -> date | None:
    text = iso_timestamp(str(raw or ""))
    return date.fromisoformat(text[:10]) if text else None


def iso_timestamp(text: str) -> str | None:
    """peer 的行情时间 `YYYYMMDDHHMMSS` → `YYYY-MM-DDTHH:MM:SS`（同一时刻的另一种写法）。

    不补时区：上游没给，猜一个就是造数。解析不了就是 `null`，数值字段照旧保留。
    """
    digits = "".join(char for char in (text or "") if char.isdigit())
    if len(digits) != 14:
        return None
    return (
        f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        f"T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
    )


def _rows(envelope: Any) -> list[dict[str, Any]]:
    payload = getattr(envelope, "payload", {}) or {}
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [dict(row) for row in records if isinstance(row, Mapping)]


__all__ = [
    "DATASET",
    "DATASET_US",
    "DATASET_HK",
    "FOOTNOTE",
    "CN_WHITELIST",
    "US_WHITELIST",
    "HK_WHITELIST",
    "INDEX_WHITELIST",
    "iso_timestamp",
    "IndexSpec",
    "IndexStrip",
    "fetch_indices",
]
