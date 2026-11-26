"""F9 观点账本：把一句判断变成一条可以被事后判定对错的记录。

为什么这是"裁判"而不是"又一个功能"：现在整条链路里，判断的对错全靠读者自己判。
F5 写了翻转条件、F4 标了数据出处、F6 校验了数字——都在提高"这句话可信"的概率，
但没有一处回答"后来它对了吗"。没有这一步，投研 Agent 和"分析得挺像那么回事"没有区别。

三条边界：

- **只判方向，不判幅度**。幅度误差留着看，但不作为命中标准——否则「涨 3% 算不算准」
  会变成一场无休止的口径争论。方向是唯一没有歧义的判据。
- **翻转优先于方向**。翻转条件是作者自己写的"我什么时候错"，它一旦触发，后续价格
  怎么走都不该算这条判断对——这正是翻转条件的意义。
- **判不了就说判不了**。没有基准价、或翻转条件里写的是毛利率这类非价格指标，
  一律 `undecided`，不猜。猜错的账本比没有账本更坏。
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

#: 可落账的方向。`neutral` 不落账——"看不出方向"本身没有可判对错的内容。
BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"

#: 默认回访期限（交易日近似按自然日算，宁可晚判不可早判）
DEFAULT_HORIZON = 20
HORIZONS = (5, 20, 60)

_DIRECTION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (BULLISH, ("偏多", "看多", "利多", "乐观", "上行", "bullish", "positive")),
    (BEARISH, ("偏空", "看空", "利空", "悲观", "下行", "bearish", "negative")),
)
#: 全文计票用的词表（与 `_DIRECTION_WORDS` 同源，但按出现次数比多少）
_BULL_WORDS = _DIRECTION_WORDS[0][1]
_BEAR_WORDS = _DIRECTION_WORDS[1][1]

#: 翻转条件里的价格阈值：「跌破 1180」「低于 1180 元」「高于 1600」
_THRESHOLD_BELOW = re.compile(r"(?:跌破|低于|跌穿|失守)\s*([0-9]+(?:\.[0-9]+)?)")
_THRESHOLD_ABOVE = re.compile(r"(?:高于|涨破|突破|站上|超过)\s*([0-9]+(?:\.[0-9]+)?)")
#: 非价格阈值（毛利率 / 营收增速 / 市占率 …）：判不了，直接标 undecided
_NON_PRICE = re.compile(r"(毛利率|净利率|营收|收入|增速|市占率|份额|ROE|负债率)")


def parse_direction(tone: str | None, text: str | None = None) -> str:
    """从结论块的 `tone`（中文为主）认出方向；认不出返回 `neutral`。"""
    for source in (tone or "", text or ""):
        lowered = source.lower()
        for direction, words in _DIRECTION_WORDS:
            if any(word in lowered for word in words):
                return direction
    return NEUTRAL


def horizon_for(question: str | None, default: int = DEFAULT_HORIZON) -> int:
    """从问题里读期限（「一周 / 一个月 / 三个月」），读不出用默认。

    只认这几种明确说法：把「近期」「短中期」这类模糊表述映射成固定天数，
    等于替用户定了口径，账本会跟着失真。
    """
    text = question or ""
    if any(word in text for word in ("一周", "1周", "五个交易日", "5个交易日")):
        return 5
    if any(word in text for word in ("一个月", "1个月", "20个交易日", "一个月内")):
        return 20
    if any(word in text for word in ("三个月", "3个月", "一个季度", "60个交易日")):
        return 60
    return default


def parse_threshold(falsifier: str | None) -> dict[str, Any] | None:
    """解析翻转条件里的价格阈值。

    返回 `{"kind": "below"|"above", "value": float}`；
    非价格指标（毛利率等）返回 `{"kind": "manual"}`——交给人工，不自动判。
    """
    text = (falsifier or "").strip()
    if not text:
        return None
    if _NON_PRICE.search(text):
        return {"kind": "manual"}
    match = _THRESHOLD_BELOW.search(text)
    if match:
        return {"kind": "below", "value": float(match.group(1))}
    match = _THRESHOLD_ABOVE.search(text)
    if match:
        return {"kind": "above", "value": float(match.group(1))}
    return None


def due_date_for(base_date: str, horizon_days: int) -> str:
    """到期日 = 基准日 + 期限（自然日）。"""
    parsed = _as_date(base_date)
    return (parsed + timedelta(days=max(1, int(horizon_days)))).isoformat()


def judge(
    record: Mapping[str, Any],
    bars: Sequence[tuple[str, float]],
) -> dict[str, Any]:
    """对一条记录给出判定。

    `bars` 是 `(日期, 收盘)` 的升序序列，**只包含基准日之后**的交易日；
    返回 `{status, reason, pct_change, flip_date, judge_date}`。
    """
    direction = str(record.get("direction") or NEUTRAL)
    base_price = record.get("base_price")
    falsifier = record.get("falsifier")
    due = record.get("due_date")
    entries = [(str(day), float(close)) for day, close in bars if close is not None]

    if direction not in (BULLISH, BEARISH):
        return _verdict("undecided", "没有可判的方向", None)
    if base_price is None or not entries:
        return _verdict("undecided", "缺基准价或没有可用行情", None)

    base = float(base_price)
    threshold = parse_threshold(falsifier)
    if threshold and threshold.get("kind") == "manual":
        return _verdict("undecided", "翻转条件含非价格指标，需人工判", None)

    flip_date: str | None = None
    if threshold and threshold.get("kind") == "below":
        for day, close in entries:
            if close < float(threshold["value"]):
                flip_date = day
                break
    elif threshold and threshold.get("kind") == "above":
        for day, close in entries:
            if close > float(threshold["value"]):
                flip_date = day
                break

    # 翻转优先：作者自己写了「出现即本条失效」，触发之后价格怎么走都不该算对
    if flip_date is not None:
        return _verdict("flipped", f"翻转条件已触发（{flip_date}）", _pct(entries[-1][1], base), flip_date)

    if due is None or _as_date(entries[-1][0]) < _as_date(due):
        return _verdict("undecided", "还没到到期日", _pct(entries[-1][1], base))

    close = entries[-1][1]
    pct = _pct(close, base)
    if direction == BULLISH:
        ok = close > base
    else:
        ok = close < base
    reason = f"{'方向应验' if ok else '方向未应验'}（到期收盘 {close:g} vs 基准 {base:g}）"
    return _verdict("hit" if ok else "miss", reason, pct)


#: 结论块要求落账时，问题原文的截断长度（与 `chat_turn` 同量级，只作索引不重放）
MAX_QUESTION = 200
#: 指标卡里可能承载「基准价」的标签：只看这些词，不从任意指标里猜
_PRICE_WORDS = ("最新价", "现价", "收盘价", "收盘", "股价", "价格")


def direction_from_text(text: str | None) -> str:
    """整段正文的方向计票：哪一侧的词出现得多就是哪一侧，打平或都没提到算中性。

    为什么不用「先到先得」：真机回答常写成「偏多与偏空条件势均力敌，略微偏向多头」
    这种对称结构，第一个命中的词往往只是章节标题，按它落账会把方向记反。
    打平即不判——账本里记错方向，比空缺更坏。
    """
    lowered = (text or "").lower()
    bull = sum(lowered.count(word) for word in _BULL_WORDS)
    bear = sum(lowered.count(word) for word in _BEAR_WORDS)
    if bull == 0 and bear == 0:
        return NEUTRAL
    if bull == bear:
        return NEUTRAL
    return BULLISH if bull > bear else BEARISH


def record_from_turn(
    question: str,
    blocks: Sequence[Mapping[str, Any]],
    *,
    symbol: str | None = None,
    session_id: str | None = None,
    seq: int | None = None,
    today: str | None = None,
) -> dict[str, Any] | None:
    """把一轮已装配好的回答变成一条待判记录；构不成可判判断时返回 `None`。

    只从**块**里取事实（结论卡 `tone` / `falsifier`、指标卡的价与 `as_of`），
    不从正文里再猜一遍：正文是模型写的，块是程序拼的，后者才是可审计的那一份。

    两种情况不落账：

    - 方向是 `neutral` / `warning`：没有可判对错的内容；
    - 拿不到基准价：没有基准价的判断永远判不了，落进去只会让「未判」越堆越多，
      也违背「判不了就说判不了」——不落账本身就是一种如实的表法。
    """
    conclusion = _conclusion_payload(blocks)
    if conclusion is None:
        return None
    # 方向优先取 `tone`；tone 是 neutral 时看正文计票。真机返回里常见
    # 「tone=neutral 但正文写着偏多的证据略占优」——只认 tone 会让绝大多数判断落不了账，
    # 而只认结论首句又会漏（首句被改成「收盘起，到收 ，一个半月上行约 」这种剥数字后的残句）。
    direction = parse_direction(str(conclusion.get("tone") or ""))
    if direction == NEUTRAL:
        direction = direction_from_text(_turn_text(blocks))
    if direction == NEUTRAL:
        return None
    price, as_of = _base_price(blocks)
    if price is None:
        return None
    day = (as_of or today or _today())[:10]
    horizon = horizon_for(question)
    falsifier = str(conclusion.get("falsifier") or "").strip() or None
    return {
        "record_id": uuid.uuid4().hex,
        "question": str(question or "").strip()[:MAX_QUESTION],
        "direction": direction,
        "horizon_days": horizon,
        "session_id": session_id,
        "seq": seq,
        "symbol": symbol,
        "base_price": price,
        "base_date": day,
        "falsifier": falsifier,
        "due_date": due_date_for(day, horizon),
    }


def _conclusion_payload(blocks: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for block in blocks:
        if str(block.get("type") or "") != "conclusion":
            continue
        payload = block.get("payload")
        if isinstance(payload, Mapping):
            return payload
    return None


def _turn_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    """这一轮用户能读到的正文：结论卡 + 正文块（不含表格 / 指标 / 来源）。"""
    parts: list[str] = []
    for block in blocks:
        if str(block.get("type") or "") not in ("conclusion", "narrative"):
            continue
        payload = block.get("payload")
        if isinstance(payload, Mapping):
            text = str(payload.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _base_price(blocks: Sequence[Mapping[str, Any]]) -> tuple[float | None, str | None]:
    """从指标卡里取基准价与数据日（`as_of`）。

    取第一条「标签像价格」且为正数的指标。`as_of` 是服务端写进 payload 的取数日，
    比「今天」更接近那条判断实际看到的价——用今天会把跨日/收盘后的判断记错基准。
    """
    for block in blocks:
        if str(block.get("type") or "") != "metric":
            continue
        payload = block.get("payload")
        items = payload.get("items") if isinstance(payload, Mapping) else None
        for item in items or ():
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("label") or "")
            if not any(word in label for word in _PRICE_WORDS):
                continue
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if float(value) <= 0:
                continue
            as_of = str(item.get("as_of") or "").strip()[:10] or None
            return float(value), as_of
    return None, None


def _today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _verdict(
    status: str,
    reason: str,
    pct_change: float | None,
    flip_date: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "pct_change": pct_change,
        "flip_date": flip_date,
    }


def _pct(close: float, base: float) -> float | None:
    if base == 0:
        return None
    return round((close - base) / base, 6)


def _as_date(value: str) -> date:
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return datetime.fromisoformat(text).date()


__all__ = [
    "BEARISH",
    "BULLISH",
    "DEFAULT_HORIZON",
    "HORIZONS",
    "MAX_QUESTION",
    "NEUTRAL",
    "due_date_for",
    "direction_from_text",
    "horizon_for",
    "judge",
    "parse_direction",
    "parse_threshold",
    "record_from_turn",
]
