"""候选池打分（程序直出，DEC-006 闸门 5）。

输入固定五样，全部走本机缓存回放（`data/archive.py`，不新增任何采集入口）：

| 输入 | 数据集 | 用途 |
|---|---|---|
| 当日涨停池 | `limit_pool` | 候选集本身、连板数、换手率、题材标签（降级口径） |
| 连板梯队 | 同上（复用 `core/ladder.compute_ladder`） | 当日最高板 → 「地位」的相对位置 |
| 题材成员 | `concept_board` | 题材标签 → 板块涨幅 → 「题材」强度 |
| 个股↔板块归属 | `concept_membership` | 逐只「这只股票挂在哪些概念板块」，替代关键词切分 |
| 资金流 | `fund_flow` | 个股净额在候选集内的分位 → 「资金」；当日分区缺失时回落到最近可用交易日 |

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
from datetime import date, timedelta
from typing import Any

from ladderstock.core import ladder as ladder_module
from ladderstock.core import limit_pool as limit_pool_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core.ladder import as_float, as_int, dataset_snapshot, dedupe_by_code, pick
from ladderstock.data import archive

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
#: 参与溯源与 `missing` 的固定数据集顺序。`concept_membership` 排在最后：
#: 它是**可选**的第四路输入，缺它只让题材降级（不进 `missing`），但必须进溯源
#: 与 `degraded_inputs`（QUOT-364 B-2：归属整列取不到时 `missing=[]` + `status=ok`，
#: 消费方看不见题材缺数）。信封 `data_as_of` 只取首位（limit_pool）的口径日期，
#: 加在末尾不会改变对外信封的日期语义。
POOL_DATASETS: tuple[str, ...] = ("limit_pool", "concept_board", "fund_flow", "concept_membership")
#: 资金回落窗口（自然日）：日线主力净额当日盘后才发布，当日分区缺失是**常态**而不是异常。
#: 6 天够跨一个周末；再早就不是「落后一天」而是这条链路断了，宁缺不补（DEC-050）。
FUND_LOOKBACK_DAYS = 6
#: 单只候选在归属这一路上的状态（QUOT-364 B-1：「没落库」与「落库但板块不在榜」
#: 是两件事，混成一个 null 就没法判断该找同步还是该找上游榜单）
MEMBERSHIP_HIT = "hit"
MEMBERSHIP_LANDED_OFF_BOARD = "landed_off_board"
MEMBERSHIP_NOT_FETCHED = "not_fetched"

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
    行里那个 `change_pct` **不参与算分**（QUOT-364 B-4 / DEC-050）：它是那次调用时刻的板块涨幅，
    与 `concept_board` 同日同名可以符号相反（实测 `融资融券` −1.27 vs +1.2782），
    题材分的涨幅来源只有一个——榜单侧。
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


def dataset_snapshot_as_of(
    dataset: str, day: date | str, root: Any = None
) -> tuple[list[dict[str, Any]], str, date | None]:
    """`dataset_snapshot()` 的带口径日期版本：除了记录与后端，再给出 `as_of`。

    `ladder.dataset_snapshot()` 的签名与现有调用点一个都不动（天梯 / 情绪都在用它的
    二元组，QUOT-361 的硬约束），要拿到「这批数是哪天的」只能走 `archive.snapshot()`。
    `as_of` 是**记录里 `trade_date` 的最大值**；缓存不可读或没有记录时给 `None`——
    拿请求日期顶上去，就是把回落读出来的数伪装成当日数（QUOT-364 B-3 的根因）。
    """
    try:
        snapshot = archive.snapshot(dataset, day, root=root)
    except Exception:  # noqa: BLE001 - 缓存损坏 / 非法日期都不该让整条打分崩掉
        return [], "error", None
    return snapshot.records, snapshot.backend, (snapshot.as_of if snapshot.records else None)


def _request_day(day: date | str) -> date | None:
    """把 `compute_pool` 的 `day`（可能是字符串）规约成 `date`；解析不出来就不回落。"""
    if isinstance(day, date):
        return day
    try:
        return date.fromisoformat(str(day).strip())
    except ValueError:
        return None


def fund_flow_snapshot(
    day: date | str, root: Any = None
) -> tuple[list[dict[str, Any]], str, date | None, bool]:
    """资金取数顺序：**当日实时 → 当日日线分区 → 最近可用交易日**（QUOT-361 / 364 / 371）。

    返回 `(records, backend, as_of, fell_back)`。

    第一顺位是 `fund_flow_realtime`（`stock_fund_flow_batch` 落的当日实时分区，
    QUOT-371）：盘中就能拿到资金分项，不必等盘后发布；一个请求覆盖整个代码池，
    不再按只 fan-out。它是当日快照，`as_of` 就是取数时刻那天，不是「上一个交易日」，
    所以这条命中时 `fell_back=False`。

    第二、三顺位是原来的日线路径：日线主力净额是当日盘后才发布的，「请求日没有分区」
    是常态而不是异常；只读请求日就等于在盘后早段把 `capital` 整列判空。
    `fell_back=True` 时输出侧必须把 `as_of` 写在明面上（`meta.fund_data_as_of`
    与 `source_detail`），不能让它冒充请求日的数。
    窗口外仍找不到就给空列表，走既有的「`capital=null` + 权重从分母剔除」，不补 0。
    """
    when = _request_day(day)
    if when is not None:
        realtime, rt_backend, rt_as_of = dataset_snapshot_as_of(
            "fund_flow_realtime", when, root
        )
        if realtime:
            return realtime, rt_backend, rt_as_of or when, False
    if when is None:
        records, backend, as_of = dataset_snapshot_as_of("fund_flow", day, root)
        return records, backend, as_of, False
    backend = "none"
    for offset in range(FUND_LOOKBACK_DAYS + 1):
        probe = when - timedelta(days=offset)
        records, backend, as_of = dataset_snapshot_as_of("fund_flow", probe, root)
        if records:
            return records, backend, as_of or probe, offset > 0
    return [], backend, None, False


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
    flow_records, flow_backend, flow_as_of, flow_fell_back = fund_flow_snapshot(day, root)
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
    #: 「可选输入不可用」与「口径日期不是请求日」都进这里：`missing` 的语义
    #: （候选池缺哪一路**必需**输入）一个都不动，消费方却看得见降级（QUOT-364 B-2/B-3）。
    request_day = _request_day(day)
    fund_is_request_day = flow_as_of is not None and flow_as_of == request_day
    degraded_inputs = []
    if not member_records:
        degraded_inputs.append("concept_membership")
    if flow_records and not fund_is_request_day:
        degraded_inputs.append("fund_flow")

    fund_note = f"（{len(flow_records)} 条"
    if flow_as_of is not None and not fund_is_request_day:
        fund_note += (
            f"，口径日期 {flow_as_of.isoformat()}"
            f" ≠ 请求日 {day}{'，回落读取' if flow_fell_back else ''}"
        )
    fund_note += "）"
    detail = (
        f"limit_pool: {backend}（{raw_total} 条）; "
        f"concept_board: {concept_backend}（{len(concept_records)} 条）; "
        f"fund_flow: {flow_backend}{fund_note}; "
        f"concept_membership: {member_backend}（{len(member_records)} 条，"
        f"{len(landed_codes)} 只落库，{len(members)} 只命中当日概念榜）"
    )
    if dropped:
        detail = detail.replace(
            f"limit_pool: {backend}（{raw_total} 条）",
            f"limit_pool: {backend}（{raw_total} 条，重复 code 去重 {dropped} 条）",
            1,
        )

    base: dict[str, Any] = {
        "date": str(day),
        "reason": None,
        "source_detail": detail,
        "height": height,
        "candidate_count": len(rows),
        "candidates": rows,
        "weights": dict(WEIGHTS),
        "formula": SCORE_FORMULA,
        "meta": _pool_meta(
            rows, member_records, members, landed_codes, flow_as_of, fund_is_request_day
        ),
        "counts": {
            "limit_pool_rows": raw_total,
            "dropped_duplicates": dropped,
            "unknown_code": unknown,
            "theme_boards": len(boards),
            "fund_flow_rows": len(flow_records),
            "concept_membership_rows": len(member_records),
        },
        "missing": missing_datasets,
        "degraded_inputs": degraded_inputs,
    }
    if not rows:
        return {
            **base,
            "available": False,
            # 与 web / MCP 同一约定：载荷里始终显式带数据状态，缺失/降级一眼可见
            "data_source_status": "missing",
            "reason": (
                "涨停池没有可解析的候选（可能尚未同步）" if not unique else "涨停池记录缺少代码字段"
            ),
        }
    return {**base, "available": True, "data_source_status": "ok"}


def _pool_meta(
    rows: list[dict[str, Any]],
    member_records: list[dict[str, Any]],
    members: dict[str, list[str]],
    landed_codes: set[str],
    flow_as_of: date | None,
    fund_is_request_day: bool,
) -> dict[str, Any]:
    """这轮分数是**怎么**取到的、覆盖到本次返回的哪几只候选（QUOT-362 / QUOT-364）。

    口径纪律只有一条：**`membership` 与 `parts_non_null` 里带 `candidates` 字样的数字，
    分母都是 `len(rows)`（`limit` 截断之后真正对外的那批候选）**。QUOT-364 B-1 的教训是
    这些计数原先按「归属表全量」统计，报出 21 只而响应里只有 18 只候选 `theme` 非空——
    覆盖率不能靠读的人自己数。表级覆盖（落库几只）单独放在 `membership.table_*`，
    两种口径刻意不同名。
    """
    source_counts: dict[str, int] = {THEME_SOURCE_MEMBERSHIP: 0, THEME_SOURCE_KEYWORDS: 0}
    states: dict[str, int] = {
        MEMBERSHIP_HIT: 0,
        MEMBERSHIP_LANDED_OFF_BOARD: 0,
        MEMBERSHIP_NOT_FETCHED: 0,
    }
    not_fetched: list[str] = []
    for row in rows:
        theme_source = row["raw"]["theme_source"]
        if theme_source is not None:
            source_counts[theme_source] += 1
        code = row["code"]
        if code in members:
            states[MEMBERSHIP_HIT] += 1
        elif code in landed_codes:
            # 归属行取到了，但它挂的板块名不在当日概念榜里（榜只有 100 行，是热度榜）
            states[MEMBERSHIP_LANDED_OFF_BOARD] += 1
        else:
            # 归属这一路根本没覆盖这只：该找同步扇出的代码池，不是找解析
            states[MEMBERSHIP_NOT_FETCHED] += 1
            not_fetched.append(code)

    return {
        "theme_source": next(
            (
                name
                for name in (THEME_SOURCE_MEMBERSHIP, THEME_SOURCE_KEYWORDS)
                if source_counts[name]
            ),
            None,
        ),
        "theme_source_counts": dict(source_counts),
        "parts_non_null": {
            key: sum(1 for row in rows if row["parts"][key] is not None) for key in SUB_SCORES
        },
        "membership": {
            "rows": len(member_records),
            "table_codes": len(landed_codes),
            "table_board_matched_codes": len(members),
            "candidates": len(rows),
            "states": states,
            "not_fetched_codes": not_fetched,
        },
        "fund_data_as_of": None if flow_as_of is None else flow_as_of.isoformat(),
        "fund_is_request_day": fund_is_request_day,
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
    "FUND_LOOKBACK_DAYS",
    "MEMBERSHIP_CODE_KEYS",
    "MEMBERSHIP_HIT",
    "MEMBERSHIP_LANDED_OFF_BOARD",
    "MEMBERSHIP_NOT_FETCHED",
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
    "dataset_snapshot_as_of",
    "fund_flow_snapshot",
    "fund_inflow_map",
    "membership_index",
    "rank_percentiles",
    "summary",
    "theme_board_index",
    "theme_tokens",
]
