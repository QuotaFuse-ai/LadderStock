"""答案块协议：块类型、payload 校验与装配（任务书 §6 / §7，阶段 A）。

一次回答从「一整段纯文本」升级为「一串有类型的内容块」：

```json
{ "type": "conclusion", "payload": { "text": "...", "tone": "negative", "confidence": 0.62 } }
```

三条硬边界（任务书第 11 章）：

- **块只能由本模块拼**：模型产出的是 `AI_WRITABLE` 那套叙述 payload，`blocks`
  一律由 `build_blocks()` 从 `metrics + ai` 装配（红线 2）；
- **叙述文本里不出现未经核实的数字**：`conclusion.text` / `notice.message` 里的数字
  默认剥掉，数字只走 `metric` 块且必须是 `number`（红线 4）；调用方确认本轮数字
  带出处（`sources` 非空，或回答自述了来源 / as_of，如标的档案召回）时传
  `keep_numbers=True`，这类已核对数字保留原文（QUOT-612）。
- **没有出处的数宁可不显示**：进 `metric` 的每个值都要过 `Provenance`，
  没过就改 `notice(missing_data)`（红线 6）。

纯函数：无 IO、无全局可变状态、不调用模型。web 层怎么接（阶段 B）不在本模块内。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ladderstock.ai.intent import (
    INTENT_CAPABILITY,
    INTENT_CHITCHAT,
    INTENT_CONCEPT,
    INTENT_DATA_QUERY,
)
from ladderstock.core.boundary import (
    assert_provenance,
)

# --------------------------------------------------------------------------- #
# 块类型（§6.2 七种）
# --------------------------------------------------------------------------- #

BLOCK_CONCLUSION = "conclusion"
BLOCK_METRIC = "metric"
BLOCK_CHART = "chart"
BLOCK_TABLE = "table"
BLOCK_CITATION = "citation"
BLOCK_NOTICE = "notice"
BLOCK_ACTION = "action"
#: 第 8 种（QUOT-6xx）：**对话正文块**。本轮没调任何工具（纯问答 / 能力说明 /
#: 概念解释）时整轮只出这一块，正文原样、按句不切、不折叠。
#: 为什么必须独立：`conclusion` 的语义是「只取首句当结论、剥数字、正文 4 行折叠」，
#: 拿它承载闲聊会把「你好，我是研究助手。我能做的事：- 查行情 - 查涨停池 …」
#: 切成「结论·中性」+「补充说明（4 行截断）」，同一段话被劈成两处还丢内容。
BLOCK_NARRATIVE = "narrative"

BLOCK_TYPES: tuple[str, ...] = (
    BLOCK_CONCLUSION,
    BLOCK_METRIC,
    BLOCK_CHART,
    BLOCK_TABLE,
    BLOCK_CITATION,
    BLOCK_NOTICE,
    BLOCK_ACTION,
    BLOCK_NARRATIVE,
)

#: D2 拍板取 6（QUOT-581 收口）。超限时由 `_enforce_limit` 瘦身 / 丢中段可省块。
MAX_BLOCKS = 6

#: §6.4-4 折叠阈值：表格超 5 行折叠、正文超 4 行折叠（§9.2）
TABLE_COLLAPSE_ROWS = 5
MAX_TEXT_LINES = 4

#: §6.3 chart.points ≤ 120；超出按步长降采样并在 caption 注明
MAX_CHART_POINTS = 120

#: metric 块最多 2–4 项（§6.2 / §7「metrics 关键项（≤4）」）
MAX_METRIC_ITEMS = 4

TONES: tuple[str, ...] = ("neutral", "positive", "negative", "warning")
DIRECTIONS: tuple[str, ...] = ("up", "down", "flat")
CHART_KINDS: tuple[str, ...] = ("line", "bar", "area")

#: `AiErrorNotice` 的 6 种失败 kind（§3.4）。`missing_data` 是 D7 新增的第 7 种，
#: 决策为「不新增」时删掉这一项即可。
NOTICE_KINDS: tuple[str, ...] = (
    "network",
    "quota",
    "format",
    "truncated",
    "internal",
    "degraded",
)
NOTICE_MISSING_DATA = "missing_data"
#: 第 8 种、**不是失败**：承载「能力」「风险提示」「补充说明」「佐证」这类正常内容。
#: 此前这些块借 `degraded`，前端于是显示成「AI 叙述降级 · 已用备用路径 / 本地回放」——
#: 明明没降级却说降级，是错的话术，故独立成中性 kind。
NOTICE_NOTE = "note"

#: 与 `web/chat.py:434` `_source_note` 逐字一致（服务端文案，不是模型编的）。
SOURCE_NOTE_WITHOUT_DATA = "（说明：本轮没有任何可核实的数据来源，以下内容不含具体行情数据。）"
SOURCE_NOTE_NO_RECORD = "（说明：本轮页内数据未附带取数记录，回答不列外部来源。）"
#: QUOT-612：页面上下文没带行情、但回答本身引用了已核对来源（标的档案召回等）时的
#: 第三种措辞——不再断言「没有任何可核实的数据来源」，也不伪造 citation。
SOURCE_NOTE_PROFILE_ONLY = (
    "（说明：本页上下文没带行情数据；以上数字取自标的档案等已核对来源，"
    "实时行情可到个股研究页查看。）"
)

#: QUOT-617：capability 意图的能力清单。固定服务端文案，不让模型自由生成
#: （避免每次措辞漂移）；与 `web/assistant.py` 的 `PAGE_SYSTEM_PROMPT` 能力描述同源
#: ——描述的是同一套「页面内轻量问答」的能力边界。
CAPABILITY_NOTE = (
    "我可以：查当前页面的行情与指标数据、解释概念术语、做多标的对比、"
    "读页面上下文回答页内问题。我不做单只股票的深度研究，"
    "需要深挖时请到个股研究页提问。"
)

#: 「你会什么 / 你用哪些工具」的**能力边界清单**，注入首页与页面抽屉的提示词。
#: 只固定「能做 / 不能做」的范围，措辞交给模型自由组织：同一个问题问两次允许说得不一样
#: （用户明确要「每次稍有变动」），但不许超出这份范围，也不许把能力问题答成「这项没取到」。
#: 清单里刻意不写具体代码与点位（红线：固定文案不带数字）。
CAPABILITY_BRIEF = (
    "个股实时行情、最近若干交易日的日线走势；"
    "大盘主要指数的点位与涨跌；"
    "涨停池、炸板名单、连板梯队、板块与概念强弱；"
    "资金流向、龙虎榜、人气热股榜；"
    "快讯与题材纪要、财务指标与业绩预告；"
    "用户自己的自选股与持仓。"
    "还能接长期盯盘：用户说盯哪几只、什么节奏（每天盘后 / 盘中间隔 / 有异动）、"
    "比哪几项变化，就按节奏推进，结论进「提醒」。"
    "不做的事：不给买卖建议、不预测价格；除实时报价与大盘指数外，其余读本机缓存的"
    "最新交易日数据，要带上日期，不冒充实时；查不到的数据直接说没取到，不拿记忆补。"
)

#: `action.target` 只能指向既有页路由（frontend/src/routes.ts 的 `ROUTE_PATHS`，
#: 去掉 `*` 与 `/index.html` 两个非页面条目）。
ROUTE_TARGETS: frozenset[str] = frozenset(
    {
        "/",
        "/market",
        "/watch",
        "/intraday",
        "/review",
        "/review/ai",
        "/weekly",
        "/journal",
        "/validate",
        "/backtest",
        "/limit-up",
        "/news",
        "/calendar",
        "/pool",
        "/my-stocks",
        "/inbox",
        "/watchlist",
        "/positions",
        "/industry",
        "/industry/:name",
        "/signals",
        "/settings",
        "/research",
        "/research/:code",
        "/research/:code/report",
        "/skills",
        "/skills/:id",
        "/skills/pkg/:name",
    }
)

#: 固定动作表：key → (label, action, target)
ACTION_OPEN_SCREEN = "open_screen"
ACTION_COMPARE = "compare"
ACTION_ADD_WATCH = "add_watch"

ACTIONS: dict[str, tuple[str, str, str]] = {
    ACTION_OPEN_SCREEN: ("展开完整面板", "open_screen", "/research"),
    ACTION_COMPARE: ("加入对比", "compare", "/my-stocks"),
    ACTION_ADD_WATCH: ("查看自选", "add_watch", "/my-stocks"),
}

#: 字段白名单草案（需产品确认）：只认下列键进 `metric`，不按模型挑。
#: 来源：`review/pipeline.py` 五段快照 + `core/sentiment.py:narrative_metrics`。
#: 与 `METRIC_LABELS` 的键集合必须一致（有测试锁住）。
METRIC_LABELS: dict[str, str] = {
    "temperature": "情绪温度",
    "promotion_rate": "晋级率",
    "seal_rate": "封板率",
    "limit_up_count": "涨停家数",
    "broken_count": "炸板家数",
    "yesterday_limit_up_count": "昨日涨停家数",
    "max_board": "最高连板",
    "height": "天梯高度",
    "count": "梯队数量",
    "gap": "断层档位",
    "gap_levels": "断层档位",
    # QUOT-616 问答取数接线：spot_quote / kline_chart 的行情键（程序从 peer 取的）
    "close": "最新价",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "prev_close": "昨收",
    "change_pct": "涨跌幅",
}

#: 白名单键的单位（缺省空串）。显示用，不参与计算。
METRIC_UNITS: dict[str, str] = {
    "temperature": "",
    "promotion_rate": "%",
    "seal_rate": "%",
    "limit_up_count": "家",
    "broken_count": "家",
    "yesterday_limit_up_count": "家",
    "max_board": "板",
    "height": "板",
    "count": "只",
    "gap": "档",
    "close": "元",
    "open": "元",
    "high": "元",
    "low": "元",
    "prev_close": "元",
    "change_pct": "%",
}

#: 序列类指标：这些键的值是 `[(x, y), ...]`，交给 `chart` 而不是 `metric`。
SERIES_METRIC_KEYS: frozenset[str] = frozenset({"gap_levels"})

# 数字：带可选的千分位、小数、百分号、正负号
_NUMBER_RE = re.compile(r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*[%％]?")
_SENTENCE_END = re.compile(r"(?<=[。！？!?])\s*")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnswerBlockError(ValueError):
    """块 payload 不合契约（施工期错误，与 `BoundaryViolation` 同级）。"""


# --------------------------------------------------------------------------- #
# 小工具：数字剥离 / 秒级
# --------------------------------------------------------------------------- #


def strip_numbers(text: str) -> str:
    """去掉叙述里的数字；剩下的空标点一并收回。

    「近 20 日震荡偏弱」→「近日震荡偏弱」；不追求完美中文，只保证结果里没有数字。
    """
    cleaned = _NUMBER_RE.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t　]{2,}", " ", cleaned)
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])[ \t　]+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"[（(]\s*[)）]", "", cleaned)
    cleaned = re.sub(r"的\s*的", "的", cleaned)
    return cleaned.strip(" ，,、;；:：")


def source_note(*, has_data: bool, sources: Sequence[Any]) -> str:
    """没有来源时补在正文尾部的服务端说明；有来源 / 无事可说时返回空串。

    原文与 `web/chat.py` 的 `_source_note` 逐字一致（两侧共用本函数，QUOT-580
    D3 迁移时收口到纯函数层，避免一份文案两处漂移）。
    """
    if sources:
        return ""
    if has_data:
        return SOURCE_NOTE_NO_RECORD
    return SOURCE_NOTE_WITHOUT_DATA


def strip_source_note(answer: str, *, has_data: bool, sources: Sequence[Any]) -> str:
    """去掉 `source_note` 拼在正文尾部的说明——块路径把它单独出成 `notice`。"""
    note = source_note(has_data=has_data, sources=sources)
    if not note:
        return answer
    suffix = f"\n\n{note}"
    return answer[: -len(suffix)] if answer.endswith(suffix) else answer


#: QUOT-612：回答正文自述了来源 / as_of 的线索——命中即视为「数字已核对」，
#: `build_blocks` 不再剥离叙述里的数字（降级路径：标的档案召回带 as_of 与来源名）。
#: 来源名限定 ASCII 标识符（`tencent_quote_batch` 这类）——服务端说明里的
#: 「数据来源，以下…」这类中文接续不算自述来源。
_SELF_SOURCE_RE = re.compile(
    r"来源[:：]?\s*[A-Za-z][A-Za-z0-9_-]+|as_of\s*[=＝]?\s*\d{4}-\d{2}-\d{2}"
)


def _answer_cites_own_source(ai: Any) -> bool:
    """叙述四键里是否自述了来源（「来源 xxx」/「as_of=YYYY-MM-DD」）。"""
    if not isinstance(ai, Mapping):
        return False
    for field in _NARRATIVE_FIELDS:
        for line in ai.get(field) or ():
            if _SELF_SOURCE_RE.search(str(line or "")):
                return True
    return False


def first_sentence(text: str) -> str:
    """取第一句（保留句末标点）。"""
    parts = [part for part in _SENTENCE_END.split(str(text or "").strip()) if part.strip()]
    return parts[0].strip() if parts else ""


def contains_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(str(text or "")))


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnswerBlockError(f"{where} 必须是对象，收到 {type(value).__name__}")
    return value


def _require_keys(
    payload: Mapping[str, Any], required: Sequence[str], allowed: Sequence[str], where: str
) -> None:
    extra = set(payload) - set(allowed)
    if extra:
        raise AnswerBlockError(f"{where} 含多余键：" + ", ".join(sorted(extra)))
    missing = [key for key in required if key not in payload]
    if missing:
        raise AnswerBlockError(f"{where} 缺少必填键：" + ", ".join(missing))


def _require_str(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AnswerBlockError(f"{where} 必须是字符串，收到 {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise AnswerBlockError(f"{where} 不能为空")
    return value


def _require_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnswerBlockError(f"{where} 必须是 number（不是字符串、不是布尔），收到 {value!r}")
    if value != value or value in (float("inf"), float("-inf")):
        raise AnswerBlockError(f"{where} 不是有限数字：{value!r}")
    return float(value)


def _require_list(value: Any, where: str, *, min_items: int = 0) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise AnswerBlockError(f"{where} 必须是数组，收到 {type(value).__name__}")
    if len(value) < min_items:
        raise AnswerBlockError(f"{where} 至少 {min_items} 项，收到 {len(value)} 项")
    return list(value)


def _check_date(value: str, where: str) -> None:
    if not _ISO_DATE.match(value):
        raise AnswerBlockError(f"{where} 需为 YYYY-MM-DD，收到 {value!r}")


# --------------------------------------------------------------------------- #
# §6.3 逐字段校验
# --------------------------------------------------------------------------- #


def validate_conclusion(payload: Any, *, allow_numbers: bool = False) -> dict[str, Any]:
    """校验 conclusion payload；`allow_numbers=True` 放行已核对数字（QUOT-612，
    供 `build_blocks` 降级路径内部使用，不改变 payload schema）。"""
    body = _require_mapping(payload, "conclusion")
    _require_keys(body, ("text", "tone"), ("text", "tone", "confidence"), "conclusion")
    text = _require_str(body["text"], "conclusion.text")
    if contains_number(text) and not allow_numbers:
        raise AnswerBlockError(f"conclusion.text 含数字（红线 4）：{text!r}")
    tone = _require_str(body["tone"], "conclusion.tone")
    if tone not in TONES:
        raise AnswerBlockError(f"conclusion.tone 必须取自 {TONES}，收到 {tone!r}")
    cleaned: dict[str, Any] = {"text": text, "tone": tone}
    if "confidence" in body:
        confidence = _require_number(body["confidence"], "conclusion.confidence")
        if not 0.0 <= confidence <= 1.0:
            raise AnswerBlockError(f"conclusion.confidence 需在 0–1，收到 {confidence}")
        cleaned["confidence"] = confidence
    return cleaned


def validate_metric(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "metric")
    _require_keys(body, ("items",), ("items",), "metric")
    items = _require_list(body["items"], "metric.items", min_items=1)
    if len(items) > MAX_METRIC_ITEMS:
        raise AnswerBlockError(f"metric.items 最多 {MAX_METRIC_ITEMS} 项，收到 {len(items)} 项")
    cleaned_items: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        where = f"metric.items[{index}]"
        item = _require_mapping(raw, where)
        _require_keys(
            item, ("label", "value"), ("label", "value", "unit", "direction", "as_of"), where
        )
        label = _require_str(item["label"], f"{where}.label")
        value = _require_number(item["value"], f"{where}.value")
        entry: dict[str, Any] = {"label": label, "value": value}
        if "unit" in item:
            entry["unit"] = _require_str(item["unit"], f"{where}.unit", allow_empty=True)
        if "direction" in item:
            direction = _require_str(item["direction"], f"{where}.direction")
            if direction not in DIRECTIONS:
                raise AnswerBlockError(
                    f"{where}.direction 必须取自 {DIRECTIONS}，收到 {direction!r}"
                )
            entry["direction"] = direction
        if "as_of" in item:
            as_of = _require_str(item["as_of"], f"{where}.as_of")
            _check_date(as_of, f"{where}.as_of")
            entry["as_of"] = as_of
        cleaned_items.append(entry)
    return {"items": cleaned_items}


def _validate_points(points: Any, where: str) -> list[list[Any]]:
    raw_points = _require_list(points, where, min_items=1)
    if len(raw_points) > MAX_CHART_POINTS:
        raise AnswerBlockError(
            f"{where} 超过 {MAX_CHART_POINTS} 个点（收到 {len(raw_points)}），先降采样再进块"
        )
    cleaned: list[list[Any]] = []
    for index, pair in enumerate(raw_points):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise AnswerBlockError(f"{where}[{index}] 必须是 [x, y] 二元组")
        x, y = pair
        if isinstance(x, bool) or not isinstance(x, (str, int, float)):
            raise AnswerBlockError(f"{where}[{index}] 的 x 必须是字符串或数字")
        cleaned.append([x, _require_number(y, f"{where}[{index}].y")])
    return cleaned


def validate_chart(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "chart")
    _require_keys(
        body, ("kind", "series"), ("kind", "x_axis", "y_unit", "series", "caption"), "chart"
    )
    kind = _require_str(body["kind"], "chart.kind")
    if kind not in CHART_KINDS:
        raise AnswerBlockError(f"chart.kind 必须取自 {CHART_KINDS}，收到 {kind!r}")
    series = _require_list(body["series"], "chart.series", min_items=1)
    cleaned_series: list[dict[str, Any]] = []
    for index, raw in enumerate(series):
        where = f"chart.series[{index}]"
        entry = _require_mapping(raw, where)
        _require_keys(entry, ("name", "points"), ("name", "points"), where)
        cleaned_series.append(
            {
                "name": _require_str(entry["name"], f"{where}.name"),
                "points": _validate_points(entry["points"], f"{where}.points"),
            }
        )
    cleaned: dict[str, Any] = {"kind": kind, "series": cleaned_series}
    if "x_axis" in body:
        cleaned["x_axis"] = _require_str(body["x_axis"], "chart.x_axis", allow_empty=True)
    if "y_unit" in body:
        cleaned["y_unit"] = _require_str(body["y_unit"], "chart.y_unit", allow_empty=True)
    if "caption" in body:
        cleaned["caption"] = _require_str(body["caption"], "chart.caption", allow_empty=True)
    return cleaned


def _table_cell(value: Any, where: str) -> Any:
    if isinstance(value, bool):
        raise AnswerBlockError(f"{where} 不接受布尔值")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value
    if value is None:
        return None
    raise AnswerBlockError(f"{where} 只能是字符串 / 数字 / null，收到 {type(value).__name__}")


def validate_table(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "table")
    _require_keys(
        body,
        ("columns", "rows"),
        ("columns", "rows", "rows_total", "collapsed", "caption"),
        "table",
    )
    columns = _require_list(body["columns"], "table.columns", min_items=1)
    column_names = [_require_str(name, f"table.columns[{i}]") for i, name in enumerate(columns)]
    rows = _require_list(body["rows"], "table.rows")
    cleaned_rows: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        cells = _require_list(row, f"table.rows[{row_index}]")
        if len(cells) != len(column_names):
            raise AnswerBlockError(
                f"table.rows[{row_index}] 有 {len(cells)} 列，"
                f"与 columns 的 {len(column_names)} 列不一致"
            )
        cleaned_rows.append(
            [_table_cell(cell, f"table.rows[{row_index}][{col}]") for col, cell in enumerate(cells)]
        )
    cleaned: dict[str, Any] = {"columns": column_names, "rows": cleaned_rows}
    rows_total = len(cleaned_rows)
    if "rows_total" in body:
        if isinstance(body["rows_total"], bool) or not isinstance(body["rows_total"], int):
            raise AnswerBlockError("table.rows_total 必须是整数")
        rows_total = body["rows_total"]
        if rows_total < len(cleaned_rows):
            raise AnswerBlockError(
                f"table.rows_total（{rows_total}）不能小于已给行数（{len(cleaned_rows)}）"
            )
    cleaned["rows_total"] = rows_total
    if "collapsed" in body:
        if not isinstance(body["collapsed"], bool):
            raise AnswerBlockError("table.collapsed 必须是布尔值")
        cleaned["collapsed"] = body["collapsed"]
    else:
        cleaned["collapsed"] = rows_total > TABLE_COLLAPSE_ROWS
    if "caption" in body:
        cleaned["caption"] = _require_str(body["caption"], "table.caption", allow_empty=True)
    return cleaned


def validate_citation(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "citation")
    _require_keys(body, ("items",), ("items",), "citation")
    items = _require_list(body["items"], "citation.items", min_items=1)
    cleaned_items: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        where = f"citation.items[{index}]"
        item = _require_mapping(raw, where)
        _require_keys(
            item, ("name", "fetched_at"), ("name", "fetched_at", "url", "symbol", "as_of"), where
        )
        entry: dict[str, Any] = {
            "name": _require_str(item["name"], f"{where}.name"),
            "fetched_at": _require_str(item["fetched_at"], f"{where}.fetched_at"),
        }
        for key in ("url", "symbol", "as_of"):
            if key in item:
                value = item[key]
                entry[key] = (
                    None
                    if value is None
                    else _require_str(value, f"{where}.{key}", allow_empty=True)
                )
        cleaned_items.append(entry)
    return {"items": cleaned_items}


def validate_notice(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "notice")
    _require_keys(body, ("kind",), ("kind", "section", "message", "actions"), "notice")
    kind = _require_str(body["kind"], "notice.kind")
    allowed = NOTICE_KINDS + (NOTICE_MISSING_DATA, NOTICE_NOTE)
    if kind not in allowed:
        raise AnswerBlockError(f"notice.kind 必须取自 {allowed}，收到 {kind!r}")
    cleaned: dict[str, Any] = {"kind": kind}
    if "section" in body:
        cleaned["section"] = _require_str(body["section"], "notice.section", allow_empty=True)
    message = _require_str(body.get("message", ""), "notice.message", allow_empty=True)
    if contains_number(message):
        raise AnswerBlockError(f"notice.message 含数字（红线 4）：{message!r}")
    cleaned["message"] = message
    if "actions" in body:
        actions = _require_list(body["actions"], "notice.actions")
        cleaned["actions"] = [
            _require_str(action, f"notice.actions[{i}]", allow_empty=False)
            for i, action in enumerate(actions)
        ]
    return cleaned


def validate_narrative(payload: Any) -> dict[str, Any]:
    """对话正文块：整段回答原样展示（不按句切、不折叠、不剥数字）。

    与研究卡的 `conclusion` 刻意分开：没调工具的轮次没有「首句即结论」这种结构，
    压成结论 + 补充说明只会把正文切碎，还丢掉超 4 行的那部分。正文是模型原话；
    数字可信度由 `sources` / `metrics` 决定——走到本块的轮次 `metrics` 恒为空。
    """
    body = _require_mapping(payload, "narrative")
    _require_keys(body, ("text",), ("text",), "narrative")
    return {"text": _require_str(body["text"], "narrative.text")}


def validate_action(payload: Any) -> dict[str, Any]:
    body = _require_mapping(payload, "action")
    _require_keys(body, ("items",), ("items",), "action")
    items = _require_list(body["items"], "action.items", min_items=1)
    cleaned_items: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        where = f"action.items[{index}]"
        item = _require_mapping(raw, where)
        _require_keys(item, ("label", "action", "target"), ("label", "action", "target"), where)
        target = _require_str(item["target"], f"{where}.target")
        if not _route_ok(target):
            raise AnswerBlockError(f"{where}.target 不在既有页路由内（红线 7）：{target!r}")
        label = _require_str(item["label"], f"{where}.label")
        if contains_number(label):
            raise AnswerBlockError(f"{where}.label 含数字：{label!r}")
        cleaned_items.append(
            {
                "label": label,
                "action": _require_str(item["action"], f"{where}.action"),
                "target": target,
            }
        )
    return {"items": cleaned_items}


def _route_ok(target: str) -> bool:
    """路径部分必须在 `ROUTE_TARGETS` 里；`?query` / `#hash` 允许但必须是单一查询串。"""
    path = target.split("#", 1)[0].split("?", 1)[0]
    return path in ROUTE_TARGETS


VALIDATORS = {
    BLOCK_CONCLUSION: validate_conclusion,
    BLOCK_METRIC: validate_metric,
    BLOCK_CHART: validate_chart,
    BLOCK_TABLE: validate_table,
    BLOCK_CITATION: validate_citation,
    BLOCK_NOTICE: validate_notice,
    BLOCK_ACTION: validate_action,
    BLOCK_NARRATIVE: validate_narrative,
}


def build_block(block_type: str, payload: Any) -> dict[str, Any]:
    """校验并构造一个块：`{"type": ..., "payload": 清洗后的 payload}`。"""
    if block_type not in VALIDATORS:
        raise AnswerBlockError(f"未知块类型 {block_type!r}，只能是 {BLOCK_TYPES}")
    return {"type": block_type, "payload": VALIDATORS[block_type](payload)}


def validate_blocks(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    """校验整串块：逐个 payload + §6.4 的顺序 / 数量规则。返回清洗后的副本。"""
    raw = _require_list(blocks, "blocks")
    cleaned: list[dict[str, Any]] = []
    for index, block in enumerate(raw):
        body = _require_mapping(block, f"blocks[{index}]")
        _require_keys(body, ("type", "payload"), ("type", "payload"), f"blocks[{index}]")
        cleaned.append(build_block(body["type"], body["payload"]))
    check_ordering(cleaned)
    return cleaned


def check_ordering(blocks: Sequence[Mapping[str, Any]]) -> None:
    """§6.4 五条：首位结论、citation 末尾、notice 不在首位、总数 ≤ `MAX_BLOCKS`、失败单条。"""
    if not blocks:
        raise AnswerBlockError("blocks 不能为空")
    if len(blocks) > MAX_BLOCKS:
        raise AnswerBlockError(f"块数 {len(blocks)} 超过上限 {MAX_BLOCKS}（§6.4-4）")
    types = [block.get("type") for block in blocks]
    failure_only = len(types) == 1 and types[0] == BLOCK_NOTICE
    # 对话正文块同理：整轮没有取数时正文就是答案本身，没有「结论」可放首位。
    narrative_only = len(types) == 1 and types[0] == BLOCK_NARRATIVE
    if not failure_only and not narrative_only:
        if types[0] != BLOCK_CONCLUSION:
            raise AnswerBlockError(f"blocks[0] 必须是 conclusion（§6.4-1），收到 {types[0]!r}")
        for index, block_type in enumerate(types):
            if block_type == BLOCK_NOTICE and index == 0:
                raise AnswerBlockError("notice 不得在首位（§6.4-3）")
    citation_positions = [i for i, block_type in enumerate(types) if block_type == BLOCK_CITATION]
    if citation_positions and citation_positions != [len(types) - 1]:
        raise AnswerBlockError("citation 必须且只能是最后一块（§6.4-2）")


# --------------------------------------------------------------------------- #
# AI 叙述输入（`AI_WRITABLE` 那套键；与现役 `assistant.py` 的 `ai` 字符串不同）
# --------------------------------------------------------------------------- #

_NARRATIVE_FIELDS: tuple[str, ...] = ("narrative", "highlights", "risks", "evidence")


def normalize_ai(ai: Any) -> dict[str, list[str]]:
    """把上游的结构化叙述归一成四个字符串数组。

    上游形态：`{"narrative": [...], "highlights": [...], "risks": [...], "evidence": [...]}`
    （`boundary.AI_WRITABLE` 的子集）。`narrative` 允许单字符串，统一成数组。
    多余键一律拒绝——数字必须由程序计算（DEC-006 闸门 5）。
    """
    body = {} if ai is None else _require_mapping(ai, "ai")
    extra = set(body) - set(_NARRATIVE_FIELDS)
    if extra:
        raise AnswerBlockError(
            "ai 含非叙述字段：" + ", ".join(sorted(extra)) + "（数字必须由程序计算）"
        )
    cleaned: dict[str, list[str]] = {}
    for field in _NARRATIVE_FIELDS:
        value = body.get(field)
        if value is None:
            cleaned[field] = []
        elif isinstance(value, str):
            cleaned[field] = [value.strip()] if value.strip() else []
        elif isinstance(value, (list, tuple)):
            cleaned[field] = [
                str(item).strip()
                for item in value
                if isinstance(item, (str, int, float)) and str(item).strip()
            ]
        else:
            raise AnswerBlockError(f"ai.{field} 必须是字符串数组")
    if not cleaned["narrative"]:
        cleaned["narrative"] = ["本轮没有生成结论。"]
    return cleaned


# --------------------------------------------------------------------------- #
# 来源（`ChatSource` 以及阶段 B 升级后的 `symbol / as_of`）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Source:
    name: str
    fetched_at: str
    url: str | None = None
    symbol: str | None = None
    as_of: str | None = None


def _coerce_source(raw: Any) -> _Source:
    """`ChatSource`（pydantic 模型 / dict / 带属性的对象）→ `_Source`。

    缺失字段如实留空，**不补默认值**（任务书 §6.3 注）。
    """
    get: Callable[[str], Any] = (
        raw.get if isinstance(raw, Mapping) else lambda key: getattr(raw, key, None)
    )
    name = str(get("name") or "").strip()
    fetched_at = str(get("fetched_at") or "").strip()
    url = get("url")
    symbol = get("symbol")
    as_of = get("as_of")
    return _Source(
        name=name,
        fetched_at=fetched_at,
        url=None if not url else str(url),
        symbol=None if not symbol else str(symbol),
        as_of=None if not as_of else str(as_of),
    )


# --------------------------------------------------------------------------- #
# metrics → metric / chart / table
# --------------------------------------------------------------------------- #


def _pick_metrics(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """按字段白名单挑关键项：白名单顺序 + 数值可用 + 有合法出处。

    没过 `assert_provenance` 的值不显示（红线 6）；无法构成 metric 时由调用方
    改出 `notice(missing_data)`。
    """
    if not metrics:
        return []
    audited = assert_provenance(metrics)
    items: list[dict[str, Any]] = []
    for key, label in METRIC_LABELS.items():
        if key not in metrics or key in SERIES_METRIC_KEYS:
            continue
        if key in audited.unverified or key in audited.non_numeric:
            continue
        record = audited.records.get(key)
        if record is None:
            continue
        value = audited.metrics.get(key)
        number = _number_or_none(value)
        if number is None:
            continue
        item: dict[str, Any] = {
            "label": label,
            "value": number,
            "unit": record.unit or METRIC_UNITS.get(key, ""),
        }
        if record.as_of:
            item["as_of"] = record.as_of
        direction = _direction(metrics, key)
        if direction is not None:
            item["direction"] = direction
        items.append(item)
        if len(items) >= MAX_METRIC_ITEMS:
            break
    return items


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from ladderstock.core.boundary import numeric_value

    return numeric_value(value)


def _direction(metrics: Mapping[str, Any], key: str) -> str | None:
    """涨跌方向只表达「变化」，不表达「绝对量的大小」。

    `limit_up_count=30`、`temperature=60` 这类绝对量恒为正，按符号判会一律标成
    「上涨」，误导读者。所以只有拿到环比依据时才给 direction：
    `metrics[f"{key}_delta"]`（数值或带出处的值）决定方向；没有就省略。
    """
    delta = _number_or_none(_unwrap_value(metrics.get(f"{key}_delta")))
    if delta is None:
        return None
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _unwrap_value(value: Any) -> Any:
    """带出处的指标值可能是 `Provenance` 包装，取里面的原始 value。"""
    return getattr(value, "value", value)


def _series_block(metrics: Mapping[str, Any]) -> dict[str, Any] | None:
    """序列类指标 → chart；超过 `MAX_CHART_POINTS` 先等步长降采样并在 caption 注明。"""
    if not metrics:
        return None
    audited = assert_provenance(metrics)
    for key in SERIES_METRIC_KEYS:
        # 序列天生不是标量，会落进 audited.non_numeric；只看出处是否已核实
        if key not in metrics or key in audited.unverified:
            continue
        record = audited.records.get(key)
        if record is None or not record.verified:
            continue
        points = _normalize_points(record.value)
        if len(points) < 2:
            continue
        total = len(points)
        caption = f"{METRIC_LABELS.get(key, key)}走势"
        if total > MAX_CHART_POINTS:
            points = _downsample(points)
            caption = f"{caption}（原始 {total} 点，已降采样至 {len(points)} 点）"
        payload: dict[str, Any] = {
            "kind": "line",
            "x_axis": "交易日",
            "series": [{"name": METRIC_LABELS.get(key, key), "points": points}],
            "caption": caption,
        }
        if record is not None and record.unit:
            payload["y_unit"] = record.unit
        else:
            payload["y_unit"] = ""
        return payload
    return None


def _normalize_points(raw: Any) -> list[list[Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    points: list[list[Any]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item
            value = _number_or_none(y)
            if value is not None and isinstance(x, (str, int, float)) and not isinstance(x, bool):
                points.append([x, value])
    return points


def _downsample(points: list[list[Any]]) -> list[list[Any]]:
    """等距取索引，首尾必留，结果严格 ≤ `MAX_CHART_POINTS`。

    n 恰为 `MAX_CHART_POINTS` 整数倍、步长 >1 时，`points[::step]` 已含尾点；
    「尾点缺失才补」一旦补上就会变成 121 点。这里改成精确取 ≤N 个索引。
    """
    total = len(points)
    if total <= MAX_CHART_POINTS:
        return list(points)
    indexes = sorted(
        {round(i * (total - 1) / (MAX_CHART_POINTS - 1)) for i in range(MAX_CHART_POINTS)}
    )
    return [points[index] for index in indexes]


def _table_block(metrics: Mapping[str, Any]) -> dict[str, Any] | None:
    """表格类结果（`metrics["table"]`）：超 `TABLE_COLLAPSE_ROWS` 行折叠，保留真实总数。"""
    if not metrics:
        return None
    raw = metrics.get("table")
    if not isinstance(raw, Mapping):
        return None
    columns = raw.get("columns")
    rows = raw.get("rows")
    if not isinstance(columns, (list, tuple)) or not columns:
        return None
    if not isinstance(rows, (list, tuple)):
        return None
    total = len(rows)
    visible = list(rows[:TABLE_COLLAPSE_ROWS]) if total > TABLE_COLLAPSE_ROWS else list(rows)
    payload: dict[str, Any] = {
        "columns": [str(name) for name in columns],
        "rows": [list(row) for row in visible],
        "rows_total": total,
        "collapsed": total > TABLE_COLLAPSE_ROWS,
    }
    caption = raw.get("caption")
    if caption:
        payload["caption"] = str(caption)
    return payload


# --------------------------------------------------------------------------- #
# failure → notice
# --------------------------------------------------------------------------- #


def _failure_notice(failure: Any) -> dict[str, Any]:
    """`ChatFailure` → `notice`。`message` → `message`，`action` → `actions`。"""
    get: Callable[[str], Any] = (
        failure.get if isinstance(failure, Mapping) else lambda key: getattr(failure, key, None)
    )
    kind = str(get("kind") or "internal")
    if kind not in NOTICE_KINDS and kind != NOTICE_MISSING_DATA:
        kind = "internal"
    message = re.sub(_NUMBER_RE, "", str(get("message") or "")).strip()
    payload: dict[str, Any] = {"kind": kind, "message": message}
    action = str(get("action") or "").strip()
    if action:
        payload["actions"] = [re.sub(r"\d+", "", action).strip()]
    return {"type": BLOCK_NOTICE, "payload": validate_notice(payload)}


# --------------------------------------------------------------------------- #
# 装配入口
# --------------------------------------------------------------------------- #


def build_blocks(
    metrics: Mapping[str, Any] | None,
    ai: Any,
    sources: Sequence[Any] | None,
    failure: Any = None,
    # 默认 False = 与现役非流式路径（assistant.py 三路由，只传 3 个位置参）的
    # 「无来源 → WITHOUT_DATA」契约一致；有页内数据的调用方（chat.py 流式）
    # 必须显式传 page_data=True，才会出 NO_RECORD。
    page_data: bool = False,
    # QUOT-612：叙述里的数字已带出处（sources 非空，或回答自述了来源 / as_of，
    # 如标的档案召回）时保留原文，不再剥离——残句「收盘价元」比数字更糟。
    keep_numbers: bool = False,
    # QUOT-615：意图分流。默认 data_query 保持既有行为（旧调用方不改不回归）；
    # chitchat 只出 conclusion（不出两类 missing_data notice），concept 不出指标 notice。
    intent: str = INTENT_DATA_QUERY,
    # 本轮是否**真的发起过取数**。默认 True 保持既有行为。
    # 传 False 时不摆「指标 / 来源没取到」——那是「没查」，不是「查了没取到」：
    # 用户问「该查哪些数据」「这家公司基本面怎么样（但没说哪家）」时本就没取数，
    # 报一屏缺失等于答错问题。这个判断由调用方按实际取数计划给，不靠关键词猜意图。
    fetch_attempted: bool = True,
) -> list[dict[str, Any]]:
    """§7 装配：AI 只决定「说什么话」，程序决定「给什么数」和「怎么摆」。

    - `metrics`：程序算好的指标；值是标量或 `Provenance` / 出处记录 dict。
      可选键 `table`（查询结果）与 `SERIES_METRIC_KEYS` 中的序列。
    - `ai`：`{narrative, highlights, risks, evidence}`（`AI_WRITABLE` 子集）。
    - `sources`：`ChatSource` 形态的来源清单（`name / fetched_at / url`，
      可选 `symbol / as_of`）。
    - `failure`：`ChatFailure` 或同形 dict；非空即「整轮失败」，只回一条 notice。
    - `page_data`：本轮是否带了页内数据；只影响「来源为空」那条例外说明的措辞，
      默认 False（与现役非流式 assistant.py 的 3 参调用契约一致），有页内数据的
      调用方（chat.py 流式）显式传 True。措辞与 `source_note(has_data=...)` 共源，
      与 `web/chat.py` `_source_note(has_data=...)` 一一对应（同一句只出一次）。
    - `keep_numbers`：叙述文本是否保留数字。默认 False（红线 4 原契约）；本轮数字
      带出处时传 True——conclusion / 风险 / 补充说明跳过 `strip_numbers`，
      避免「收盘价元」这类空值残句下发。

    纯函数：不读写文件、不发请求、不碰全局状态。
    """
    metrics = dict(metrics or {})
    source_list = [_coerce_source(raw) for raw in (sources or [])]
    # QUOT-612：回答自述了来源（「来源 xxx」「as_of」）也算数字有出处——
    # 无 sources 的降级路径（标的档案召回）数字同样已核对，不再剥离。
    if not keep_numbers and not source_list:
        keep_numbers = _answer_cites_own_source(ai)
    # 附录类块先定：超限瘦身要保证它们不被「尾部丢块」剪掉（§6.4-2 / D7）
    citation = _citation_from_sources(source_list)
    # QUOT-612：无 citation 时按三种实情措辞，不再一律断言「没有任何可核实的数据来源」——
    # 回答自述了来源（标的档案召回）时应说「数字取自档案」，而不是与正文自相矛盾。
    note_message = (
        SOURCE_NOTE_PROFILE_ONLY
        if _answer_cites_own_source(ai)
        else source_note(has_data=page_data, sources=[])
    )
    source_notice = (
        None
        if citation is not None
        else build_block(
            BLOCK_NOTICE,
            {
                "kind": NOTICE_MISSING_DATA,
                "section": "来源",
                "message": note_message,
            },
        )
    )

    if failure is not None:
        return [_failure_notice(failure)]

    narrative = normalize_ai(ai)
    blocks: list[dict[str, Any]] = []

    # QUOT-617：capability 意图只出 conclusion + 一条固定能力清单 notice，
    # 不出 missing_data（问「你能做什么」挂数据告警即答非所问）。
    if intent == INTENT_CAPABILITY:
        headline = strip_numbers(first_sentence(narrative["narrative"][0]))
        if not headline:
            headline = "本轮没有可展示的结论。"
        blocks.append(build_block(BLOCK_CONCLUSION, {"text": headline, "tone": "neutral"}))
        blocks.append(
            build_block(
                BLOCK_NOTICE,
                {"kind": NOTICE_NOTE, "section": "能力", "message": CAPABILITY_NOTE},
            )
        )
        check_ordering(blocks)
        return blocks

    # QUOT-615 意图分流：chitchat / concept / needs_symbol 不出「指标」missing_data notice；
    # chitchat 连「来源」notice 也不出（问候语挂数据告警即答非所问）。
    # needs_symbol 同理：用户还没说是哪家公司，这时报「没取到数据」是答错问题——
    # 该反问标的，不是罗列缺失项。
    skip_metric_notice = (
        intent
        in (
            INTENT_CHITCHAT,
            INTENT_CONCEPT,
        )
        or not fetch_attempted
    )
    skip_source_notice = intent in (INTENT_CHITCHAT,) or not fetch_attempted
    emit_evidence = intent == INTENT_CONCEPT and bool(narrative["evidence"])

    # 1. 结论：narrative[0] 取第一句、剥数字（tone 中性、置信度不评级）；
    #    QUOT-612：数字已核对（keep_numbers）时保留原文
    raw_headline = first_sentence(narrative["narrative"][0])
    headline = raw_headline if keep_numbers else strip_numbers(raw_headline)
    if not headline:
        headline = "本轮没有可展示的结论。"
    conclusion: dict[str, Any] = {"text": headline, "tone": "neutral"}
    if keep_numbers:
        # 已核对数字保留原句（QUOT-612）：payload 仍是 `{text, tone}` 两键，schema 不变
        # （前端 47 项 vitest 依赖），只放行数字校验。
        conclusion_block = {
            "type": BLOCK_CONCLUSION,
            "payload": validate_conclusion(conclusion, allow_numbers=True),
        }
    else:
        conclusion_block = build_block(BLOCK_CONCLUSION, conclusion)
    blocks.append(conclusion_block)

    # 2. 指标：白名单 + 出处；拿不到时如实说没取到，而不是凭空造数
    items = _pick_metrics(metrics)
    chart = _series_block(metrics)
    table = _table_block(metrics)
    if items:
        blocks.append(build_block(BLOCK_METRIC, {"items": items}))
    elif not skip_metric_notice and chart is None and table is None:
        # 指标段为空 ≠ 本轮没取到数据：多行结果走「表格」、序列走「图」，这两种
        # 形态本来就没有白名单标量键（四只指数的行情表就是一例）。这时再报
        # 「本轮没有取到可核实的指标数据」，等于对着一屏真实数据说没数。
        blocks.append(
            build_block(
                BLOCK_NOTICE,
                {
                    "kind": NOTICE_MISSING_DATA,
                    "section": "指标",
                    "message": "本轮没有取到可核实的指标数据",
                },
            )
        )

    # 3. 序列 → 图
    if chart is not None:
        blocks.append(build_block(BLOCK_CHART, chart))

    # 4. 表格
    if table is not None:
        blocks.append(build_block(BLOCK_TABLE, table))

    # 5. 风险：必须可见，不折叠；每条独立 notice（notice 不得在首位）
    #    风险提示是正常内容，不是降级：用中性 `note`，标题取段名。
    #    notice **永远剥数字**：校验器禁止 notice 携带数字（红线 4），`keep_numbers`
    #    是给「正文允许保留有出处的数字」用的，不能给 notice 开口子——否则回答里
    #    一出现带行情数字的分点，装配就抛 `AnswerBlockError`，整轮变成 500。
    for risk in narrative["risks"][:2]:
        text = strip_numbers(risk)
        if text:
            blocks.append(
                build_block(
                    BLOCK_NOTICE,
                    {"kind": NOTICE_NOTE, "section": "风险提示", "message": text},
                )
            )

    # 6. narrative[1:] + highlights：合并成一条 notice，每块 ≤4 行
    #    同样是正常内容，用 `note`（见第 5 步）。
    #    **含数字的行直接丢弃，不剥成残句**：agent 会把工具结果写成「最新点位 3634.35」
    #    这样的分点，而 notice 不许带数字（红线 4）；剥完剩「最新点位」半句比不说更糟。
    #    数字已经由 metric / table 块承载，正文只留定性描述。
    extras = [
        line
        for line in (narrative["narrative"][1:] + narrative["highlights"])
        if line and not contains_number(line)
    ]
    if extras:
        blocks.append(
            build_block(
                BLOCK_NOTICE,
                {
                    "kind": NOTICE_NOTE,
                    "section": "补充说明",
                    "message": "\n".join(extras[:MAX_TEXT_LINES]),
                },
            )
        )

    # 6.5 QUOT-615：concept 意图把 evidence 作为佐证块补充（notice 之后、来源之前）
    if emit_evidence:
        for line in (strip_numbers(e) for e in narrative["evidence"]):
            if line:
                blocks.append(
                    build_block(
                        BLOCK_NOTICE,
                        {"kind": NOTICE_NOTE, "section": "佐证", "message": line},
                    )
                )

    # 7. 来源：空则不生成 citation，改成服务端文案 notice
    tail = citation if citation is not None else (None if skip_source_notice else source_notice)
    if tail is None:
        # chitchat 且无来源：以补充说明位置补一条空 notice 占位不必要，直接出 citation
        # 兜底——但 citation 为 None 说明没有可引用来源，按协议仍需末尾块，用结论重复
        # 不合适；协议要求 citation 末尾存在，chitchat 缺来源时跳过尾块。
        blocks = _enforce_limit(blocks, protected=())
        check_ordering(blocks)
        return blocks
    blocks.append(tail)

    blocks = _enforce_limit(blocks, protected=(tail,))
    check_ordering(blocks)
    return blocks


def _citation_from_sources(sources: Sequence[_Source]) -> dict[str, Any] | None:
    """来源齐全（`name` + `fetched_at`）才构成 citation；缺到无法构成时返回 `None`。"""
    items: list[dict[str, Any]] = []
    for source in sources:
        if not source.name or not source.fetched_at:
            continue
        items.append(
            {
                "name": source.name,
                "fetched_at": source.fetched_at,
                "url": source.url,
                "symbol": source.symbol,
                "as_of": source.as_of,
            }
        )
    if not items:
        return None
    return build_block(BLOCK_CITATION, {"items": items})


def _enforce_limit(
    blocks: list[dict[str, Any]], *, protected: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """§6.4-4：总数 ≤ `MAX_BLOCKS`。

    步骤 1、2 只做**内容瘦身**（折表行、砍指标项），块数不变；真正压块数的只有
    步骤 3（从尾部丢非保护块）。瘦身先做，是为了超限时优先保留信息密度更高的块。

    citation 永远保位（§6.4-2），conclusion 永远保位（§6.4-1），`protected` 里的
    块（来源为空时的 missing_data notice）也不丢——丢的总是中段可省块。
    """
    if len(blocks) <= MAX_BLOCKS:
        return blocks
    locked = [id(block) for block in protected]
    result = copy.deepcopy(blocks)
    # 1) 内容瘦身：表格折叠到只留首行（collapsed + rows_total 保留真实总数），块数不变。
    #    首行/总行数保持原样，`table` 的样例与回放才对得上。
    for block in result:
        if block["type"] != BLOCK_TABLE:
            continue
        payload = block["payload"]
        if len(payload["rows"]) > TABLE_COLLAPSE_ROWS:
            payload["rows"] = payload["rows"][:TABLE_COLLAPSE_ROWS]
            payload["collapsed"] = True
    # 2) 内容瘦身：不再砍指标项——块与样例一一对应，砍了回放就对不上
    # 3) 仍超限：从尾部往前丢非保护块（citation / conclusion / protected 除外）
    while len(result) > MAX_BLOCKS:
        for index in range(len(result) - 1, 0, -1):
            if result[index]["type"] in (BLOCK_CITATION, BLOCK_CONCLUSION):
                continue
            if id(blocks[index]) in locked:
                continue
            del result[index]
            break
        else:
            break
    return result


__all__ = [
    "ACTIONS",
    "ACTION_ADD_WATCH",
    "ACTION_COMPARE",
    "ACTION_OPEN_SCREEN",
    "AnswerBlockError",
    "BLOCK_ACTION",
    "BLOCK_CHART",
    "BLOCK_CITATION",
    "BLOCK_CONCLUSION",
    "BLOCK_METRIC",
    "BLOCK_NOTICE",
    "BLOCK_TABLE",
    "BLOCK_TYPES",
    "CHART_KINDS",
    "CAPABILITY_NOTE",
    "DIRECTIONS",
    "MAX_BLOCKS",
    "MAX_CHART_POINTS",
    "MAX_METRIC_ITEMS",
    "METRIC_LABELS",
    "METRIC_UNITS",
    "NOTICE_KINDS",
    "NOTICE_MISSING_DATA",
    "NOTICE_NOTE",
    "ROUTE_TARGETS",
    "SOURCE_NOTE_NO_RECORD",
    "SOURCE_NOTE_PROFILE_ONLY",
    "SOURCE_NOTE_WITHOUT_DATA",
    "SERIES_METRIC_KEYS",
    "TABLE_COLLAPSE_ROWS",
    "TONES",
    "build_block",
    "build_blocks",
    "check_ordering",
    "contains_number",
    "first_sentence",
    "normalize_ai",
    "source_note",
    "strip_numbers",
    "strip_source_note",
    "validate_action",
    "validate_blocks",
    "validate_chart",
    "validate_citation",
    "validate_conclusion",
    "validate_metric",
    "validate_notice",
    "validate_table",
]
