"""候选池打分（程序直出，DEC-006 闸门 5）。

输入固定五样，全部走本机缓存回放（`data/archive.py`，不新增任何采集入口）：

| 输入 | 数据集 | 用途 |
|---|---|---|
| 当日涨停池 | `limit_pool` | 候选集本身、连板数、换手率、题材标签（降级口径） |
| 连板梯队 | 同上（复用 `core/ladder.compute_ladder`） | 当日最高板 → 「地位」的相对位置 |
| 题材成员 | `concept_board` | 题材标签 → 板块涨幅 → 「题材」强度 |
| 个股↔板块归属 | `concept_membership` | 逐只「这只股票挂在哪些概念板块」，替代关键词切分 |
| 资金流 | `fund_flow` | 个股净额在候选集内的分位 → 「资金」 |

分项固定五个，顺序即口径顺序，**不可增删**：`strength / turnover / standing / capital / theme`
（强度 / 换手 / 地位 / 资金 / 题材）。权重、算式、字段容忍表与手算对照全部写在
`docs/specs/SPEC-CANDIDATE-POOL.md`；夹具 `tests/fixtures/pool/P*.json` 与实现逐条对齐
（做法与 `tests/fixtures/metrics/S*.json` 相同）。

两条硬约束：

1. **本模块不读、也不引用本机隐私库**（`data/journal.sqlite`）——持仓不参与打分，
   候选池与我的持仓互不引用，隐私数据不可能经打分结果外溢到 MCP / AI；
2. **「地位」一律叫 `standing`**：`position` 是 MCP 隐私关键字
   （`mcp/tools.py:FORBIDDEN_KEYWORDS`），既不能用在本模块的输出键里，
   也不能出现在工具清单中。

缺数据一律给 `null` 并把该分项的权重从总分里剔除后重归一（不猜、不补 0），
与 `core/sentiment.py` 的温度算法同口径。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ladderstock.core import ladder as ladder_module
from ladderstock.core import limit_pool as limit_pool_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core.ladder import as_float, as_int, dataset_snapshot, dedupe_by_code, pick

#: 分项固定顺序（对外键名）
SUB_SCORES: tuple[str, ...] = ("strength", "turnover", "standing", "capital", "theme")
#: 分项中文名（只用于人类可读输出，不参与计算）
SUB_SCORE_LABELS: dict[str, str] = {
    "strength": "强度",
    "turnover": "换手",
    "standing": "地位",
    "capital": "资金",
    "theme": "题材",
}
#: 权重（SPEC-CANDIDATE-POOL.md §3，和为 1.00）
WEIGHTS: dict[str, float] = {
    "strength": 0.30,
    "turnover": 0.10,
    "standing": 0.25,
    "capital": 0.20,
    "theme": 0.15,
}

#: 换手率满分基准（%）：≥ 20% 记 100 分
TURNOVER_NORM = 20.0
#: 题材板块涨幅满分基准（%）：≥ 5% 记 100 分，负涨幅记 0 分
THEME_NORM = 5.0
#: 「强度」的连板高度基准：复用 METRICS.md §3.3 的 6 板基准，不另立口径
TOP_BOARD_NORM = sentiment_module.TOP_BOARD_NORM

#: 换手率容忍表（上游命名不统一，按优先级取第一个非空值；都没有就是 null）
TURNOVER_KEYS: tuple[str, ...] = (
    "turnover_rate",
    "turnover",
    "hb_rate",
    "actual_turnover",
    "换手率",
    "换手",
)
#: 个股资金净流入容忍表（只用于排名，所以不做单位换算——元/万元的歧义不影响名次）
NET_INFLOW_KEYS: tuple[str, ...] = (
    "net_inflow",
    "main_net_inflow",
    "net_amount",
    # 日频 `stock_fund_flow` 分区的字段名（单位：元）。候选池只读 `fund_flow`
    # 数据集，不读 `realtime_fund_flow`，不会把实时通道的分/非元口径灌进来。
    "main_net",
    "主力净流入",
    "净流入",
    "净额",
)
#: 涨停池记录上的题材/概念标签容忍表。**末尾复用 `core/limit_pool.py` 的 `REASON_KEYS`**
#: （QUOT-362：现网 `limit_up_pool` 主工具的涨停原因就叫 `reason`，另立一份清单等于两个口径；
#: 顺序放在最后，是因为 `hot_topic` 这类标签字段更干净，`reason` 是整句话，只在没有标签时兜底）
THEME_KEYS: tuple[str, ...] = (
    "hot_topic",
    "theme",
    "topic",
    "concept",
    "labels",
    "题材",
    "概念",
    "标签",
    "热点",
    *limit_pool_module.REASON_KEYS,
)
#: 题材标签的分隔符（一个字段里常写多个概念；涨停原因的真实写法是
#: `水产加工+鱼粉鱼油+“羊”字辈`，`+` 是主分隔符，QUOT-362 实测）
THEME_SEPARATOR = re.compile(r"[，,、;；/|\s+＋]+")

#: 概念板块涨幅容忍表（与 `mcp/server.py` 的 `STRENGTH_KEYS` 同一组上游命名）
CHANGE_PCT_KEYS: tuple[str, ...] = (
    "change_pct",
    "pct_chg",
    "pct_change",
    "changePercent",
    "change",
    "涨跌幅",
    "涨幅",
)
#: 板块名容忍表（QUOT-362 实测：`concept_boards` / `stock_concept_blocks` 的行都是
#: `board_name`，而 `ladder.NAME_KEYS` 只有 `name/stock_name/sec_name/名称`——
#: 板块侧的键从来不在那份清单里，所以板块索引一条都建不起来。
#: **不动 `ladder.NAME_KEYS`**：天梯与 sentiment 都在用它的「股票名」语义，
#: 板块名另立一份，且把 `name` 留在末尾兼容历史夹具。）
BOARD_NAME_KEYS: tuple[str, ...] = (
    "board_name",
    "concept_name",
    "板块名称",
    "板块名",
    "name",
    "名称",
)
#: 归属表行内「这是哪只股票」的键（`data.code` 透传下来的代码列）
MEMBERSHIP_CODE_KEYS: tuple[str, ...] = ("code", "symbol", "stock_code")
#: 题材取数口径（对外只有这两个值，读到就知道分数是哪条路出来的，QUOT-362）
THEME_SOURCE_MEMBERSHIP = "stock_concept_blocks"
THEME_SOURCE_KEYWORDS = "reason_keywords"
#: 参与溯源与 `missing` 的固定数据集顺序
POOL_DATASETS: tuple[str, ...] = ("limit_pool", "concept_board", "fund_flow")

SCORE_FORMULA = (
    "score = Σ(weight_i × part_i) / Σ(weight_i)，part_i ∈ [0,100]（1 位小数）；"
    "取不到数的分项 part_i = null，其权重从分母里剔除（重归一），不补 0"
)


def _percent(value: float) -> float:
    """0–1 的比例 → 0–100 的百分数，保留 1 位小数（METRICS.md §4.1 同口径）。"""
    return round(value * 100.0, 1)


def _clamped(value: float, upper: float) -> float:
    """`[0, upper]` 截断后换算成分：负值记 0，超过基准记 100。"""
    if value <= 0:
        return 0.0
    return _percent(min(1.0, value / upper))


def theme_tokens(record: dict[str, Any]) -> list[str]:
    """把题材标签拆成去重后的候选词（保持出现顺序，结果可复算）。"""
    raw = pick(record, THEME_KEYS)
    if raw is None:
        return []
    tokens: list[str] = []
    for token in THEME_SEPARATOR.split(str(raw)):
        cleaned = token.strip()
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def theme_board_index(records: list[dict[str, Any]]) -> dict[str, float]:
    """概念板块名 → 涨幅（%）。同名板块取最大涨幅，名称空白行丢弃。"""
    index: dict[str, float] = {}
    for record in records:
        name = str(pick(record, BOARD_NAME_KEYS) or "").strip()
        change = as_float(pick(record, CHANGE_PCT_KEYS))
        if not name or change is None:
            continue
        if name not in index or change > index[name]:
            index[name] = change
    return index


def membership_index(
    records: list[dict[str, Any]], boards: dict[str, float]
) -> dict[str, list[str]]:
    """个股代码 → 挂在的**概念**板块名（`concept_membership`，逐只实时快照）。

    `stock_concept_blocks` 的描述明写 industry / concept / region **mixed**，
    而板块代码是东财 `BKxxxx`，与 `concept_board` 的主键（实测 `gn_*`）**不同构**，
    所以两边只能按板块名 join——这同时就是概念板块的过滤规则：
    **只保留当日 `concept_board` 榜里出现的板块名**，行业与地域归属一律进不了题材分
    （2026-11-03 实测：行业段 `BK04xx` 28 行、地域段 `BK01xx` 29 行命中概念榜 0 行，
    14 个「XX板块」结尾的地域名同样 0 命中）。
    不在当日榜内的归属如实丢弃：没有当日涨幅就没有可打分的口径，不做任何补数。
    """
    index: dict[str, list[str]] = {}
    for record in records:
        code = str(pick(record, MEMBERSHIP_CODE_KEYS) or "").strip()
        name = str(pick(record, BOARD_NAME_KEYS) or "").strip()
        if not code or name not in boards:
            continue
        names = index.setdefault(code, [])
        if name not in names:
            names.append(name)
    return index


def fund_inflow_map(records: list[dict[str, Any]]) -> dict[str, float]:
    """代码 → 当日资金净额。同一代码重复出现保留首条（与天梯 D-04 去重口径一致）。"""
    flows: dict[str, float] = {}
    for record in records:
        code = str(pick(record, ladder_module.CODE_KEYS) or "").strip()
        value = as_float(pick(record, NET_INFLOW_KEYS))
        if not code or value is None or code in flows:
            continue
        flows[code] = value
    return flows


def rank_percentiles(values: dict[str, float]) -> dict[str, float]:
    """集合内分位名次（0–100，1 位小数）。

    算式：`percentile = 严格小于该值的样本数 / (n - 1) × 100`；`n == 1` 时没有比较对象，
    如实给中位 `50.0`；并列值「严格小于者」相同，因此同分。
    """
    items = sorted(values.items())
    total = len(items)
    if total == 0:
        return {}
    if total == 1:
        return {items[0][0]: 50.0}
    return {
        code: _percent(sum(1 for _, other in items if other < value) / (total - 1))
        for code, value in items
    }


def _weighted_total(parts: dict[str, float | None]) -> float | None:
    """按剩余可用权重重归一后加权求和，只在最后舍入一次（D-07 的舍入顺序）。"""
    usable = [
        (WEIGHTS[key], value) for key, value in parts.items() if value is not None
    ]
    if not usable:
        return None
    weight = sum(weight for weight, _ in usable)
    return round(sum(weight * value for weight, value in usable) / weight, 1)


def compute_pool(
    day: date | str,
    *,
    root: Any = None,
    records: list[dict[str, Any]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """按当日缓存给候选池打分；`records` 供测试注入涨停池。"""
    backend = "given"
    if records is None:
        records, backend = dataset_snapshot("limit_pool", day, root)

    raw_total = len(records)
    unique = dedupe_by_code(records)
    dropped = raw_total - len(unique)

    concept_records, concept_backend = dataset_snapshot("concept_board", day, root)
    flow_records, flow_backend = dataset_snapshot("fund_flow", day, root)
    member_records, member_backend = dataset_snapshot("concept_membership", day, root)
    boards = theme_board_index(concept_records)
    members = membership_index(member_records, boards)
    # 「落库了几只」与「几只挂在当日概念榜上」是两件事：前者是同步覆盖面，后者才是题材分能用的。
    landed_codes = {
        str(pick(record, MEMBERSHIP_CODE_KEYS) or "").strip() for record in member_records
    } - {""}
    flows = fund_inflow_map(flow_records)
    percentiles = rank_percentiles(flows)

    ladder = ladder_module.compute_ladder(day, root=root, records=records)
    height = ladder.get("height")

    rows: list[dict[str, Any]] = []
    theme_source_counts: dict[str, int] = {THEME_SOURCE_MEMBERSHIP: 0, THEME_SOURCE_KEYWORDS: 0}
    unknown = 0
    for record in unique:
        code = str(pick(record, ladder_module.CODE_KEYS) or "").strip()
        if not code:
            unknown += 1
            continue
        name = str(pick(record, ladder_module.NAME_KEYS) or "").strip()
        board_count = as_int(pick(record, ladder_module.BOARD_KEYS))
        turnover = as_float(pick(record, TURNOVER_KEYS))
        inflow_value = flows.get(code)
        owned = members.get(code)
        if owned:
            # 主口径：逐只归属（`stock_concept_blocks`）命中 → 只认挂在的概念板块
            tokens = list(owned)
            theme_source: str | None = THEME_SOURCE_MEMBERSHIP
        else:
            # 降级口径：归属表没有这只（当日没取到 / 池子没覆盖）→ 退回涨停原因的关键词切分。
            # **标成 reason_keywords**，别让读的人以为是成分归属。
            tokens = theme_tokens(record)
            theme_source = THEME_SOURCE_KEYWORDS if tokens else None
        matched = [(token, boards[token]) for token in tokens if token in boards]
        # 命中多个板块取最大涨幅（SPEC §3 既有口径：题材分回答「最强那条题材有多强」，
        # 求和会让挂在概念多的股票天然占便宜，见 DEC-049）
        theme_change = max((change for _, change in matched), default=None)
        if theme_source is not None:
            theme_source_counts[theme_source] += 1

        parts = {
            "strength": (
                _percent(min(1.0, board_count / TOP_BOARD_NORM))
                if board_count is not None and board_count > 0
                else None
            ),
            "turnover": _clamped(turnover, TURNOVER_NORM) if turnover is not None else None,
            "standing": (
                _percent(min(1.0, board_count / height))
                if board_count is not None and height
                else None
            ),
            "capital": percentiles.get(code),
            "theme": _clamped(theme_change, THEME_NORM) if theme_change is not None else None,
        }
        rows.append(
            {
                "code": code,
                "name": name,
                "score": _weighted_total(parts),
                "parts": parts,
                # 顶层 `missing` 已经是「缺哪个数据集」，这里换个名字，两个概念不重名
                "absent_parts": [key for key in SUB_SCORES if parts[key] is None],
                "raw": {
                    "boards": board_count,
                    "turnover_rate": turnover,
                    "height": height,
                    "net_inflow": inflow_value,
                    "theme_tags": tokens,
                    "theme_board_change_pct": theme_change,
                    "theme_source": theme_source,
                },
            }
        )

    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0.0), row["code"]))
    if limit is not None and limit > 0:
        rows = rows[:limit]

    missing_datasets = [
        dataset
        for dataset, source in (
            ("limit_pool", unique),
            ("concept_board", concept_records),
            ("fund_flow", flow_records),
        )
        if not source
    ]
    detail = (
        f"limit_pool: {backend}（{raw_total} 条）; "
        f"concept_board: {concept_backend}（{len(concept_records)} 条）; "
        f"fund_flow: {flow_backend}（{len(flow_records)} 条）; "
        f"concept_membership: {member_backend}（{len(member_records)} 条，"
        f"{len(landed_codes)} 只落库，{len(members)} 只命中当日概念榜）"
    )
    if dropped:
        detail = detail.replace(
            f"limit_pool: {backend}（{raw_total} 条）",
            f"limit_pool: {backend}（{raw_total} 条，重复 code 去重 {dropped} 条）",
            1,
        )

    if not rows:
        return {
            "date": str(day),
            "available": False,
            # 与 web / MCP 同一约定：载荷里始终显式带数据状态，缺失/降级一眼可见
            "data_source_status": "missing",
            "reason": (
                "涨停池没有可解析的候选（可能尚未同步）" if not unique else "涨停池记录缺少代码字段"
            ),
            "source_detail": detail,
            "height": height,
            "candidate_count": 0,
            "candidates": [],
            "weights": dict(WEIGHTS),
            "formula": SCORE_FORMULA,
            "meta": _theme_meta(member_records, members, theme_source_counts, landed_codes),
            "counts": {
                "limit_pool_rows": raw_total,
                "dropped_duplicates": dropped,
                "unknown_code": unknown,
                "theme_boards": len(boards),
                "fund_flow_rows": len(flow_records),
            },
            "missing": missing_datasets,
        }

    return {
        "date": str(day),
        "available": True,
        "data_source_status": "ok",
        "reason": None,
        "source_detail": detail,
        "height": height,
        "candidate_count": len(rows),
        "candidates": rows,
        "weights": dict(WEIGHTS),
        "formula": SCORE_FORMULA,
        "meta": _theme_meta(member_records, members, theme_source_counts, landed_codes),
        "counts": {
            "limit_pool_rows": raw_total,
            "dropped_duplicates": dropped,
            "unknown_code": unknown,
            "theme_boards": len(boards),
            "fund_flow_rows": len(flow_records),
        },
        "missing": missing_datasets,
    }


def _theme_meta(
    member_records: list[dict[str, Any]],
    members: dict[str, list[str]],
    theme_source_counts: dict[str, int],
    landed_codes: set[str],
) -> dict[str, Any]:
    """题材分项这轮是**怎么**取到数的——两条路的行数都摆在明面上，不靠读的人猜。"""
    return {
        # 本轮的主口径：有逐只归属就认归属，没有就是关键词降级（不是成分归属）
        "theme_source": THEME_SOURCE_MEMBERSHIP if members else THEME_SOURCE_KEYWORDS,
        "theme_membership_rows": len(member_records),
        # 落库覆盖几只 vs 其中几只挂的板块在当日概念榜里（题材分只能用后者）
        "theme_membership_landed_codes": len(landed_codes),
        "theme_membership_codes": len(members),
        "theme_source_counts": dict(theme_source_counts),
    }


def summary(pool: dict[str, Any]) -> str:
    """给人类看的一行结论（程序生成，AI 不得改写）。"""
    if not pool.get("available"):
        return "候选池：数据缺失"
    top = pool["candidates"][0] if pool["candidates"] else None
    if top is None:
        return "候选池：无候选"
    return (
        f"候选池：{pool['candidate_count']} 只候选，最高 {pool['height']} 板，"
        f"首位 {top['code']} {top['score']}"
    )


__all__ = [
    "BOARD_NAME_KEYS",
    "CHANGE_PCT_KEYS",
    "MEMBERSHIP_CODE_KEYS",
    "NET_INFLOW_KEYS",
    "POOL_DATASETS",
    "SUB_SCORE_LABELS",
    "SUB_SCORES",
    "THEME_KEYS",
    "THEME_NORM",
    "THEME_SOURCE_KEYWORDS",
    "THEME_SOURCE_MEMBERSHIP",
    "TOP_BOARD_NORM",
    "TURNOVER_KEYS",
    "TURNOVER_NORM",
    "WEIGHTS",
    "compute_pool",
    "fund_inflow_map",
    "membership_index",
    "rank_percentiles",
    "summary",
    "theme_board_index",
    "theme_tokens",
]
