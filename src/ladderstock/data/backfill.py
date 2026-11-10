"""回补缺口计算与报表渲染（QUOT-501）。

纯函数层：输入「预期交易日」（基准指数的 trade_date 序列）与「标的已落库 bar」，
输出每只票的覆盖区间、缺口率、amount 缺失率，再渲染 Markdown / CSV。

口径（DEC-006 闸门 2：不猜字段、不补数据）：

- **预期交易日** = 基准（上证指数）在窗口内的 trade_date。指数每个交易日都有 bar，
  不依赖第三方交易日历，也不把「取数日」当交易日。
- **缺口** = 预期交易日 − 该票实际有 bar 的交易日。**未接入停牌日历**，停牌日同口径
  计入缺口；报表里明说「可能是停牌或上游缺数，不编造」。
- **amount 缺失** = payload 里没有 `amount` 或值为 null；只统计，不补 0、不估算。
- `target_met` 只看窗口两端是否覆盖（最早 bar ≤ 首个预期交易日、最后 bar ≥ 最末预期
  交易日）；区间内缺口由 `gap_alert`（> 阈值）单独列名并解释。
- **去重证明**来自 `daily_k` 总行数的前后差：重跑时 `new_rows` 必须为 0。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _iso(value: Any) -> str | None:
    """把 trade_date 归一成 `YYYY-MM-DD`；取不到就回 None（不猜）。"""
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def trade_days(rows: Iterable[dict[str, Any]]) -> list[str]:
    """记录行里的交易日（去重、升序）。"""
    days = {day for row in rows if (day := _iso(row.get("trade_date"))) is not None}
    return sorted(days)


def clip_days(days: Iterable[str], start: str, end: str | None) -> list[str]:
    return [day for day in days if day >= start and (end is None or day <= end)]


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def compress_ranges(days: Iterable[str], *, limit: int = 3) -> dict[str, Any]:
    """把缺失交易日压成连续区间，最多展示 `limit` 段（其余只报段数）。"""
    ordered = sorted({day for day in days if _iso(day)})
    ranges: list[list[str]] = []
    for day in ordered:
        if ranges and _next_day(ranges[-1][1]) == day:
            ranges[-1][1] = day
        else:
            ranges.append([day, day])
    shown = [
        first if first == last else f"{first}~{last}" for first, last in ranges[:limit]
    ]
    return {
        "ranges": shown,
        "total_ranges": len(ranges),
        "total_days": len(ordered),
        "truncated": len(ranges) > limit,
    }


def _amount_missing(row: dict[str, Any]) -> bool:
    value = row.get("amount")
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def code_stats(
    *,
    code: str,
    rows: list[dict[str, Any]],
    expected: list[str],
    start: str,
    end: str | None,
    target_years: float,
    gap_threshold: float,
    name: str = "",
    board: str = "",
) -> dict[str, Any]:
    """一只票（或基准）的覆盖统计。`rows` 是该代码在库里的全部记录。"""
    expected = clip_days(sorted(set(expected)), start, end)
    actual_all = trade_days(rows)
    actual = clip_days(actual_all, start, end)
    actual_set = set(actual)
    first = actual[0] if actual else None
    last = actual[-1] if actual else None

    in_span = [
        day for day in expected if first is not None and last is not None and first <= day <= last
    ]
    missing = [day for day in in_span if day not in actual_set]
    head_missing = sum(1 for day in expected if first is not None and day < first)
    tail_missing = sum(1 for day in expected if last is not None and day > last)
    missing_window = [day for day in expected if day not in actual_set]

    # 缺口率按**整个窗口**算（预期 − 实际）：只算区间内会让「整段头部缺失」
    # 反而显示 0%（腾讯 640 根 vs 5 年 1212 天就是这种情况），阈值就形同虚设。
    gap_rate = len(missing_window) / len(expected) if expected else (0.0 if actual else 1.0)
    coverage_years = (
        round((date.fromisoformat(last) - date.fromisoformat(first)).days / 365.25, 2)
        if first and last
        else 0.0
    )
    target_met = bool(
        first
        and last
        and head_missing == 0
        and tail_missing == 0
        and coverage_years >= target_years - 0.05
    )

    window_rows = [
        row for row in rows
        if (day := _iso(row.get("trade_date"))) and day in actual_set
    ]
    amount_missing = sum(1 for row in window_rows if _amount_missing(row))
    amount_missing_rate = amount_missing / len(window_rows) if window_rows else 0.0
    sources = sorted(
        {
            str(row.get("_source") or row.get("source") or "").strip()
            for row in window_rows
        }
        - {""}
    )

    return {
        "code": code,
        "name": name,
        "board": board,
        "rows_total": len(rows),
        "rows_in_window": len(actual),
        "first_date": first,
        "last_date": last,
        "expected_days": len(expected),
        "actual_days": len(actual),
        "missing_days_window": len(missing_window),
        "missing_days_in_span": len(missing),
        "head_missing": head_missing,
        "tail_missing": tail_missing,
        "gap_rate": round(gap_rate, 4),
        "gap_alert": bool(actual) and gap_rate > gap_threshold,
        "coverage_years": coverage_years,
        "target_met": target_met,
        "amount_missing_days": amount_missing,
        "amount_missing_rate": round(amount_missing_rate, 4),
        "sources": sources,
        "missing_ranges": compress_ranges(missing),
    }


def explain_gap(row: dict[str, Any], *, stage: str) -> str:
    """缺口 > 阈值的票，给一句能落到行动上的解释（不猜停牌，不编数）。"""
    notes: list[str] = []
    if row.get("status") == "failed":
        return f"取数失败：{row.get('error') or '-'}"
    if row.get("status") == "empty":
        return "上游返回 0 行，未写库；缺口按全窗口计"
    if row.get("head_missing"):
        notes.append(
            f"{row['head_missing']} 个预期交易日在最早可得 K 线（{row['first_date']}）之前，"
            f"属源窗口上限（{stage}）"
        )
    if row.get("tail_missing"):
        notes.append(
            f"{row['tail_missing']} 个预期交易日在其后（{row['last_date']}），"
            "待最新交易日数据补齐"
        )
    if row.get("missing_days_in_span"):
        notes.append(
            f"区间内缺 {row['missing_days_in_span']} 天：未接停牌日历，"
            "停牌或上游缺数同口径计入，未编造"
        )
    if row.get("amount_missing_rate", 0) >= 1.0:
        notes.append("amount 全缺：该源回包不含成交额，payload 按 null 保留")
    elif row.get("amount_missing_rate", 0) > 0:
        notes.append(f"amount 缺失 {row['amount_missing_rate']:.1%}")
    return "；".join(notes) or "无缺口"


def build_report(
    *,
    stage: str,
    source: str,
    source_detail: str,
    start: str,
    end: str | None,
    target_years: float,
    gap_threshold: float,
    expected_days: int,
    window_end: str | None,
    benchmark: dict[str, Any],
    codes: list[dict[str, Any]],
    failures: list[str],
    not_attempted: list[str],
    dedupe: dict[str, Any],
    index_dedupe: dict[str, Any],
    generated_at: str,
    report_dir: str,
) -> dict[str, Any]:
    """把运行结果整理成可渲染、可机读的报表载荷。"""
    ok = [row for row in codes if row.get("status") == "ok"]
    empty = [row for row in codes if row.get("status") == "empty"]
    failed_rows = [row for row in codes if row.get("status") == "failed"]
    alerts = [row for row in codes if row.get("gap_alert")]
    top = sorted(
        (row for row in alerts if row.get("actual_days")),
        key=lambda row: (-row["gap_rate"], -row.get("missing_days_window", 0)),
    )[:3]

    return {
        "generated_at": generated_at,
        "stage": stage,
        "source": source,
        "source_detail": source_detail,
        "window": {
            "start": start,
            "end": end or window_end,
            "target_years": target_years,
            "expected_days": expected_days,
        },
        "benchmark": benchmark,
        "summary": {
            "universe": len(codes),
            "ok": len(ok),
            "empty": len(empty),
            "failed": len(failed_rows),
            "not_attempted": len(not_attempted),
            "target_met": sum(1 for row in codes if row.get("target_met")),
            "gap_alert": len(alerts),
            "gap_threshold": gap_threshold,
            "amount_all_missing": sum(
                1 for row in codes if row.get("amount_missing_rate", 0) >= 1.0
            ),
        },
        "codes": codes,
        "top_gaps": [
            {**row, "explanation": explain_gap(row, stage=stage)} for row in top
        ],
        "failures": failures,
        "not_attempted": not_attempted,
        "dedupe": dedupe,
        "index_dedupe": index_dedupe,
        "report_dir": report_dir,
    }


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

_CSV_COLUMNS: tuple[str, ...] = (
    "kind",
    "code",
    "name",
    "board",
    "status",
    "first_date",
    "last_date",
    "rows_total",
    "rows_in_window",
    "expected_days",
    "actual_days",
    "missing_days_window",
    "missing_days_in_span",
    "head_missing",
    "tail_missing",
    "gap_rate",
    "coverage_years",
    "target_met",
    "gap_alert",
    "amount_missing_days",
    "amount_missing_rate",
    "sources",
    "missing_ranges",
    "error",
)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return ";".join(str(item) for item in value.get("ranges") or [])
    return value


def render_csv(report: dict[str, Any]) -> str:
    """CSV：第一行基准，随后每只票一行；列口径与 Markdown 明细表一致。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    bench = {"kind": "benchmark", "status": "ok", **report["benchmark"]}
    writer.writerow([_cell(bench.get(key)) for key in _CSV_COLUMNS])
    for row in report["codes"]:
        record = {"kind": "stock", **row}
        writer.writerow([_cell(record.get(key)) for key in _CSV_COLUMNS])
    return buf.getvalue()


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        cells = [_cell(value) for value in row]
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value:.2%}"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    window = report["window"]
    bench = report["benchmark"]
    dedupe = report["dedupe"]
    index = report["index_dedupe"]

    lines: list[str] = []
    lines.append("# A 股日频历史行情回补缺口报表（QUOT-501）")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 阶段：{report['stage']}")
    lines.append(f"- 数据源：`{report['source']}`（{report['source_detail'] or '-'}）")
    lines.append(
        f"- 目标窗口：{window['start']} ~ {window['end'] or '-'}"
        f"（目标 {window['target_years']:g} 年，预期交易日 {window['expected_days']} 个）"
    )
    lines.append(
        f"- 基准：{bench['name'] or bench['code']} `{bench['code']}`，"
        f"{bench['first_date'] or '-'} ~ {bench['last_date'] or '-'}，"
        f"窗口内 {bench['actual_days']} 根"
    )
    lines.append(
        "- 口径：预期交易日 = 基准指数 bar 的 trade_date；缺口 = 预期 − 实际。"
        "未接入停牌日历，停牌日同口径计入缺口，**不编造**；"
        "amount 缺失只统计、不补 0。"
    )
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(
        _md_table(
            ["指标", "值"],
            [
                ["票池", summary["universe"]],
                ["取数成功", summary["ok"]],
                ["空结果", summary["empty"]],
                ["取数失败", summary["failed"]],
                ["止损未发起", summary["not_attempted"]],
                [f"达标（>={window['target_years']:g} 年且两端无缺口）", summary["target_met"]],
                [f"缺口率 > {summary['gap_threshold']:.0%}", summary["gap_alert"]],
                ["amount 全缺", summary["amount_all_missing"]],
            ],
        )
    )
    lines.append("")
    lines.append("## 票池明细")
    lines.append("")
    details = []
    for row in report["codes"]:
        details.append(
            [
                row["code"],
                row.get("name") or "-",
                row.get("board") or "-",
                row.get("status") or "-",
                row["first_date"] or "-",
                row["last_date"] or "-",
                row["actual_days"],
                row["expected_days"],
                row.get("missing_days_window", 0),
                row["missing_days_in_span"],
                _pct(row["gap_rate"]),
                f"{row['coverage_years']:g}",
                "是" if row["target_met"] else "否",
                _pct(row["amount_missing_rate"]),
            ]
        )
    lines.append(
        _md_table(
            [
                "代码",
                "名称",
                "板块",
                "状态",
                "首日",
                "末日",
                "实际",
                "预期",
                "窗口缺口",
                "区间缺口",
                "缺口率",
                "覆盖年数",
                "达标",
                "amount缺失",
            ],
            details,
        )
    )
    lines.append("")
    lines.append(f"## 缺口率 > {summary['gap_threshold']:.0%} 的股票（单独列名）")
    lines.append("")
    if report["top_gaps"]:
        lines.append(
            _md_table(
                [
                    "代码", "名称", "缺口率", "窗口缺口", "头缺",
                    "尾缺", "区间缺口", "覆盖年数", "解释",
                ],
                [
                    [
                        row["code"],
                        row.get("name") or "-",
                        _pct(row["gap_rate"]),
                        row.get("missing_days_window", 0),
                        row["head_missing"],
                        row["tail_missing"],
                        row["missing_days_in_span"],
                        f"{row['coverage_years']:g}",
                        row["explanation"],
                    ]
                    for row in report["top_gaps"]
                ],
            )
        )
        rest = summary["gap_alert"] - len(report["top_gaps"])
        if rest > 0:
            lines.append("")
            lines.append(f"其余 {rest} 只缺口率同样超阈值，完整清单见同目录 CSV。")
    else:
        lines.append("无。")
    lines.append("")
    lines.append("## 失败 / 未发起")
    lines.append("")
    if report["failures"]:
        lines.append("取数失败（单只失败不中断整批）：")
        lines.extend(f"- {item}" for item in report["failures"])
    else:
        lines.append("取数失败：无。")
    lines.append("")
    if report["not_attempted"]:
        lines.append(
            "连续失败止损后未发起的标的："
            + "、".join(report["not_attempted"])
        )
    else:
        lines.append("止损未发起：无。")
    lines.append("")
    lines.append("## 去重与幂等（重跑依据）")
    lines.append("")
    lines.append(
        _md_table(
            ["项目", "本次"],
            [
                ["daily_k 回补前总行数", dedupe["before_rows"]],
                ["本次提交行数（批内按 (code, trade_date) 折叠后）", dedupe["written"]],
                ["daily_k 回补后总行数", dedupe["after_rows"]],
                ["**新增行数**", dedupe["new_rows"]],
                ["**已存在重复行数**", dedupe["existing_rows"]],
                [
                    "daily_k 水位",
                    f"{dedupe.get('watermark_before') or '-'} → "
                    f"{dedupe.get('watermark_after') or '-'}",
                ],
                ["index_daily 行数", f"{index['before_rows']} → {index['after_rows']}"],
                ["index_daily 新增行数", index["new_rows"]],
            ],
        )
    )
    lines.append("")
    lines.append(
        "重跑判定：同一命令再跑一遍时 `new_rows` 应为 0、`existing_rows` 应等于本次提交行数"
        "（append 模式按 `(code, trade_date)` upsert）。"
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict[str, Any], out_dir: Path | str) -> dict[str, str]:
    """落 Markdown + CSV（UTF-8；CSV 带 BOM 方便 Excel 直接打开中文）。"""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    markdown = target / "gap-report.md"
    csv_path = target / "gap-report.csv"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_csv(report), encoding="utf-8-sig")
    return {"dir": str(target), "markdown": str(markdown), "csv": str(csv_path)}
