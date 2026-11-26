"""F3 缺口自补：一轮回答**之前**先体检本地库，缺数就补，补不到如实报缺口。

为什么不在提示词里要求模型自己调 `ensure_data`：模型只在"想起来了"的时候才查，
而 83% 的答不上数样本恰恰是"查询失败后直接收尾"。补数必须由程序判定——查到哪一类
缺、缺的是不是这一只票、这个权限档允不允许联网采集，这三件事都不该交给生成模型。

三条硬约束（来自功能清单第八章 F1↔F3 冲突）：

1. **不占首屏**：起任务就返回，绝不同步等采集跑完（budget 只留 1.5 秒判断）。
2. **一次最多补两类**：`MAX_AUTO_KINDS`，且同一轮只起一个任务，防止死循环式重试。
3. **不改权限口径**：采集属 `collect` 档，policy 要确认就不自动起，只把缺口说清楚。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: 用户问法 → 数据类。命中多类时按这里出现的顺序取前 `MAX_AUTO_KINDS` 个。
#: 不做语义推断，只认这些明确的说法——猜错类目会白跑一次联网采集。
KIND_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("财报", "业绩", "营收", "收入", "净利润", "毛利", "季报", "年报", "eps", "估值", "pe", "pb", "扣非"), "financial"),
    (("公告", "分红", "回购", "股东", "减持", "增持", "重组", "停牌", "问询"), "announcement"),
    (("k线", "走势", "历史股价", "日线", "周线", "月线", "均线", "前复权", "后复权"), "kline"),
    (("行情", "最新价", "现价", "价格", "股价", "涨跌", "涨幅", "跌幅", "成交额", "成交量", "换手"), "quote"),
)

#: 一轮最多自动补几类：两类足够覆盖"查财报顺带要行情"，再多就该用户自己说
MAX_AUTO_KINDS = 2

#: 自动补数最多起几个任务（本轮内）
MAX_AUTO_TASKS = 1

#: 判定后立刻返回的等待预算；真实采集在后台线程跑
AUTO_BUDGET_MS = 1500

#: A 股代码前缀：与 `coverage.normalize_symbol` 同一口径，宁可漏认不可乱认
_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689")

_SIX_DIGITS = re.compile(r"(?<!\d)(\d{6})(?!\d)")

#: `mixed` 之类不该被当成标的的纯数字（日期、年份、金额）——只认前缀命中的
ACTION_NONE = "none"
ACTION_READY = "ready"
ACTION_AUTO = "auto"
ACTION_CONFIRM = "confirm"
ACTION_UNAVAILABLE = "unavailable"


def detect_symbol(text: str) -> str | None:
    """从问题里认出一个 A 股代码；认不出返回 None。

    公司名不做解析——名称到代码要查表，那一层已经有 `lookup_symbols` 工具，
    这里认错一次就会补错票的库，代价比不补高。
    """
    for match in _SIX_DIGITS.finditer(text or ""):
        code = match.group(1)
        if code.startswith(_A_SHARE_PREFIXES):
            return code
    return None


def kinds_for_question(text: str) -> list[str]:
    """问题里明确提到的数据类，按 `KIND_HINTS` 顺序，最多 `MAX_AUTO_KINDS` 类。"""
    lowered = (text or "").lower()
    if not lowered:
        return []
    hits: list[str] = []
    for words, kind in KIND_HINTS:
        if any(word in lowered for word in words) and kind not in hits:
            hits.append(kind)
        if len(hits) >= MAX_AUTO_KINDS:
            break
    return hits


def plan_backfill(
    question: str,
    *,
    data_root: Any = None,
    symbol_hint: str | None = None,
    kinds_hint: Iterable[str] | None = None,
) -> dict[str, Any]:
    """体检 + 决策。纯判定函数，返回值直接喂给 `notice_for()`。

    返回键：`symbol / kinds / missing / action / task_id / ready`。
    `action` 取 `none`（没标的或没提数据类）、`ready`（本地齐全）、`auto`（已起任务）、
    `confirm`（权限档要确认，没起）、`unavailable`（本地库不可用，属环境问题）。
    """
    from ladderstock.core import policy
    from ladderstock.data import coverage, tasks

    symbol = coverage.normalize_symbol(symbol_hint or "") or detect_symbol(question)
    kinds = [str(k).strip().lower() for k in (kinds_hint or []) if str(k).strip()]
    if not kinds:
        kinds = kinds_for_question(question)

    result: dict[str, Any] = {
        "symbol": symbol,
        "kinds": kinds,
        "missing": [],
        "ready": [],
        "action": ACTION_NONE,
        "task_id": None,
    }
    if not symbol or not kinds:
        return result

    try:
        before = coverage.check_coverage(symbol, kinds=kinds)
    except coverage.CoverageUnavailable:
        result["action"] = ACTION_UNAVAILABLE
        return result

    result["ready"] = list(before.get("ready") or [])
    result["missing"] = list(before.get("missing") or [])
    if not result["missing"]:
        result["action"] = ACTION_READY
        return result

    # 采集是 collect 档：policy 要确认就不动，只把缺口交回给回答去说明
    policy_value = policy.load_policy(data_root)["confirm_policy"]
    if policy.needs_confirm(policy.TIER_COLLECT, policy=policy_value):
        result["action"] = ACTION_CONFIRM
        return result

    limited = result["missing"][:MAX_AUTO_KINDS]
    record = tasks.submit(symbol, limited, budget_ms=AUTO_BUDGET_MS)
    result["action"] = ACTION_AUTO
    result["task_id"] = record.get("task_id")
    result["missing"] = limited
    return result


def notice_for(decision: dict[str, Any]) -> str | None:
    """把决策写成一句人能读的话；没什么可说的返回 None（不占一行）。

    文案里**不出现代码和任务号**：notice 块按红线 4 禁止携带数字（股票代码也是数字），
    带进去整轮装配会抛 `AnswerBlockError`。任务号改由 status 帧显示，那里是自由文本。
    `auto` 一定要说明"后台在补、这一版不含这一项"，否则读者会以为缺的那块是模型偷懒。
    """
    action = decision.get("action")
    missing = decision.get("missing") or []
    if action == ACTION_AUTO:
        return (
            f"本地缺「{'、'.join(missing)}」，已在后台补数。"
            "这一版回答不含该项；补完再问一次就会直接有数。"
        )
    if action == ACTION_CONFIRM:
        return (
            f"本地缺「{'、'.join(missing)}」。补齐要向 TickHub 联网采集（只写本地库），"
            "当前权限档要求先确认，所以没有自动补。"
        )
    if action == ACTION_UNAVAILABLE:
        return "本地库当前不可用，无法判定数据覆盖；这是环境问题，不是这只票没有数据。"
    return None


__all__ = [
    "ACTION_AUTO",
    "ACTION_CONFIRM",
    "ACTION_NONE",
    "ACTION_READY",
    "ACTION_UNAVAILABLE",
    "AUTO_BUDGET_MS",
    "KIND_HINTS",
    "MAX_AUTO_KINDS",
    "MAX_AUTO_TASKS",
    "detect_symbol",
    "kinds_for_question",
    "notice_for",
    "plan_backfill",
]
