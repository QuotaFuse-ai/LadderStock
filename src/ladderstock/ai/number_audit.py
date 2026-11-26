"""F6 数字自检：正文里的每个数，逐个对回本轮工具返回。

为什么要有它：F4 让用户看得到「这个数出自哪个接口」，但那只覆盖进了 metric 块的数；
正文里模型还会顺手写「市盈率约 30 倍」「较年初涨 12%」这类**没进块**的数，读者无从核对。
F4 给人看，F6 给系统自己查——两者是一套东西的两半。

判定原则（宁可少报，不可错杀）：

- 只查**小数与百分比**。整数误报率太高（「三个方面」「第 3 季度」「前 5 名」都不是数据），
  查它只会让读者学会忽略这条提示。
- 日期、年份、股票代码一律跳过：它们是标识，不是被测的量。
- 允许常见量纲换算：`1.48 亿` 与 `148000000` 等价，`0.0175` 与 `1.75%` 等价；
  容差取相对 0.5%、绝对 0.01，四舍五入到两位的写法都能对上。
- 对不上就**只标注、不删改**。删掉数字会留下「收盘价元」这种残句，比留一个被标记的数更糟。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

#: 小数（允许千分位）：`1,485.75` / `-0.42`
_DECIMAL = re.compile(r"[-+]?\d{1,3}(?:,\d{3})*\.\d+|[-+]?\d+\.\d+")
#: 百分比：`1.75%` / `-2%`
_PERCENT = re.compile(r"[-+]?\d+(?:\.\d+)?\s*%")

#: 数字紧跟着这些字，说明它在表达日期 / 期次 / 名次，不参与核对
_SKIP_AFTER = ("年", "月", "日", "季", "号", "版", "章", "节", "条", "名", "位", "页", "倍仓")
#: 数字前面紧贴这些字，说明是序号 / 期次
_SKIP_BEFORE = ("第", "前", "近")

#: 量纲换算倍数（值与它的这些倍率都算「同一数」）
_SCALES = (1.0, 100.0, 0.01, 1e4, 1e-4, 1e8, 1e-8)


def _numbers_in(value: Any) -> Iterable[float]:
    """从 metrics / Provenance 里挖出所有数值（递归，跳过布尔）。"""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if isinstance(value, str):
        # 出处记录里的 `value` 偶尔是带单位的字符串（「38.62 元」）
        for token in re.findall(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "")):
            yield float(token)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _numbers_in(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _numbers_in(item)


def allowed_values(metrics: Mapping[str, Any] | None) -> list[float]:
    """本轮工具返回里所有可用作对照的数值（含量纲等价形式）。"""
    values = list(_numbers_in(dict(metrics or {})))
    expanded: list[float] = []
    for value in values:
        for scale in _SCALES:
            scaled = value * scale
            if scaled == 0 or abs(scaled) > 1e15:
                continue
            expanded.append(scaled)
    return expanded


def _close(token: float, candidates: list[float]) -> bool:
    for candidate in candidates:
        tolerance = max(0.01, abs(candidate) * 0.005)
        if abs(token - candidate) <= tolerance:
            return True
    return False


def audit(text: str, metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """核对正文里的小数与百分比。

    返回 `{checked, matched, unmatched}`：`unmatched` 是**原样 token**（`1.75%` / `1480.60`），
    便于直接拼进给读者看的说明里。
    """
    body = text or ""
    candidates = allowed_values(metrics)
    checked = 0
    matched = 0
    unmatched: list[str] = []
    if not candidates:
        return {"checked": 0, "matched": 0, "unmatched": []}

    seen: set[str] = set()
    percent_spans: list[tuple[int, int]] = []
    for pattern in (_PERCENT, _DECIMAL):
        for match in pattern.finditer(body):
            token_text = match.group(0).strip()
            start, end = match.span()
            # 百分比已经算过一遍，别让小数正则把 `1.75` 再数一次
            if pattern is _DECIMAL and any(s <= start and end <= e for s, e in percent_spans):
                continue
            if token_text in seen:
                continue
            seen.add(token_text)
            if pattern is _PERCENT:
                percent_spans.append((start, end))
            before = body[max(0, start - 1) : start]
            after = body[end : end + 1]
            if after and after in _SKIP_AFTER:
                continue
            if before and before in _SKIP_BEFORE:
                continue
            raw = token_text.rstrip("%").replace(",", "").strip()
            try:
                token = float(raw)
            except ValueError:
                continue
            checked += 1
            if _close(token, candidates):
                matched += 1
            elif len(unmatched) < 8:
                unmatched.append(token_text)
    return {"checked": checked, "matched": matched, "unmatched": unmatched}


def notice_text(result: Mapping[str, Any]) -> str | None:
    """把核对结果写成一条给读者看的说明；全部对上时返回 None。

    这条文案**允许带数字**（notice kind = `unverified`）：它存在的意义就是指出
    「这几个数没对回本轮取数结果」，把数字拿掉就等于什么都没说。
    """
    unmatched = list(result.get("unmatched") or [])
    if not unmatched:
        return None
    listed = " / ".join(unmatched[:5])
    more = f"等 {len(unmatched)} 处" if len(unmatched) > 5 else ""
    return (
        f"以下数字没对回本轮工具返回，可能是估算或引用了别的口径：{listed}{more}。"
        "要用精确值，让我重新取一次。"
    )


__all__ = ["allowed_values", "audit", "notice_text"]
