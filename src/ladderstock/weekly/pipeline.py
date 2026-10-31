"""AI 周报流水线（R11）：周度事实包 → 五段 AI 叙述 → 落盘。

与每日复盘同一条「先算数、后写字」的路：

1. `weekly/facts.py` 确定性聚合本周的每日产物（`state/review/`、
   `state/ai_checkpoint/`、`state/validation/`）与本机缓存 → 周度事实包；
2. `ai/orchestrator.Orchestrator` 按 情绪 → 资金 → 题材 → 龙虎榜 → 龙头 五段生成
   **叙述**，每段落 checkpoint（`state/weekly_checkpoint/<week>.json`），单段失败不吞前序成果；
3. 合并走 `core/boundary.py:merge_narrative`：**指标原样保留，AI 只追加 `ai` 块**；
   事实包子树里没有任何模型输出；
4. 产物落两处：`<data_dir>/weekly/<week>.json`（页面与 API 消费）+
   `<data_dir>/weekly/<week>.md`（人读）。

降级（要求 4）：模型失败或全部失败时，**事实包照写**，失败段落补一段程序生成的
模板叙述（`mode="template"`、`degraded=True`），命令按每日复盘同一条退出码语义
（`EXIT_AI_DEGRADED`=9）退出，绝不整体失败、绝不留半截产物（原子写）。

数据只来自本机缓存与本机产物，本模块不发起任何网络请求（DEC-006）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ladderstock.ai import fallback
from ladderstock.ai.orchestrator import Orchestrator, RunResult
from ladderstock.ai.structured_output import load_prompt
from ladderstock.weekly import checkpoint as weekly_checkpoint
from ladderstock.weekly import facts as facts_module
from ladderstock.weekly import week as week_module

WEEKLY_SUBDIR = "weekly"
FRESHNESS = "cached"

FOOTER = (
    "本报告指标由 LadderStock 程序从本周每日复盘产物与本机缓存聚合，"
    "AI 仅生成叙述文字；数据来自 TickHub-CN / QuotaFuse Wire / StockSenate 的本地缓存。"
)

#: 段落没跑出来时补的模板（程序写文字，不写新数字——只复述事实包已有的值）
TEMPLATE_NOTE = "模板降级：本段 AI 叙述未生成，以下要点由程序从事实包直出。"

WEEKLY_PROMPT = "prompts/weekly.md"


# --------------------------------------------------------------------------- #
# 路径与读写
# --------------------------------------------------------------------------- #

def _data_dir(root: Any = None) -> Path:
    from ladderstock.cli.paths import data_dir

    return Path(root) if root is not None else data_dir()


def weekly_path(week: Any, root: Any = None) -> Path:
    """周度事实包 + 叙述的结构化件：`<data_dir>/weekly/<week>.json`。"""
    return _data_dir(root) / WEEKLY_SUBDIR / f"{week_module.normalize_week(week)}.json"


def markdown_path(week: Any, root: Any = None) -> Path:
    return weekly_path(week, root).with_suffix(".md")


def load_report(week: Any, root: Any = None) -> dict[str, Any] | None:
    path = weekly_path(week, root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def latest_week(root: Any = None) -> str | None:
    """本机已有周报里最新的周标签（按文件名倒序，`YYYY-Www` 字典序即时间序）。"""
    base = _data_dir(root) / WEEKLY_SUBDIR
    names = (
        sorted((path.stem for path in base.glob("*.json")), reverse=True)
        if base.is_dir()
        else []
    )
    for name in names:
        try:
            week_module.normalize_week(name)
        except ValueError:
            continue
        return name
    return None


# --------------------------------------------------------------------------- #
# 事实包 → 五段指标快照（喂给 AI 的只读快照）
# --------------------------------------------------------------------------- #

def section_snapshots(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把事实包切成五段各自的快照（与每日复盘 `collect_metrics()` 同形：只读、扁平）。

    只搬运、不再算：每段的数字都是事实包里已有的那些。
    """
    sentiment = facts["sentiment"]
    ladder = facts["ladder"]
    breadth = facts["breadth"]
    themes = facts["themes"]
    validation = facts["validation"]
    compact_series = [
        {
            "date": row["date"],
            "available": row["available"],
            "temperature": row["temperature"],
            "promotion_rate": row["promotion_rate"],
            "seal_rate": row["seal_rate"],
            "limit_up_count": row["limit_up_count"],
            "broken_count": row["broken_count"],
            "max_board": row["max_board"],
            "gap_levels": row["gap_levels"],
            "reason": row["reason"],
        }
        for row in sentiment["series"]
    ]
    return {
        "sentiment": {
            "week": facts["week"],
            "range": facts["range"],
            "available": bool(facts["available"]),
            "reason": facts["reason"],
            "session_days": facts["session_days"],
            "missing_days": facts["missing_days"],
            "temperature": sentiment["temperature"],
            "promotion_rate": sentiment["promotion_rate"],
            "seal_rate": sentiment["seal_rate"],
            "limit_up_total": sentiment["limit_up_total"],
            "broken_total": sentiment["broken_total"],
            "gap_days": sentiment["gap_days"],
            "series": compact_series,
            "breadth": {
                "available": breadth["available"],
                "reason": breadth["reason"],
                "up_count": breadth["up_count"],
                "down_count": breadth["down_count"],
                "flat_count": breadth["flat_count"],
                "samples": breadth["samples"],
                "distribution": breadth["distribution"],
            },
        },
        "capital": {
            "week": facts["week"],
            "available": bool(facts["capital"]["days_available"]),
            "reason": (
                None
                if facts["capital"]["days_available"]
                else "本周没有 fund_flow 缓存（先运行 ldst sync --dataset fund_flow）"
            ),
            "days_available": facts["capital"]["days_available"],
            "records_total": facts["capital"]["records_total"],
            "series": facts["capital"]["series"],
        },
        "theme": {
            "week": facts["week"],
            "available": bool(themes["available"]),
            "reason": themes["reason"],
            "window": themes["window"],
            "observed": themes.get("observed", 0),
            "themes": themes["themes"],
            "rows_by_day": facts["theme_rows"]["series"],
            "days_available": facts["theme_rows"]["days_available"],
        },
        "dragon_tiger": {
            "week": facts["week"],
            "available": bool(facts["dragon_tiger"]["days_available"]),
            "reason": (
                None
                if facts["dragon_tiger"]["days_available"]
                else "本周没有龙虎榜缓存（先运行 ldst sync --dataset dragon_tiger）"
            ),
            "days_available": facts["dragon_tiger"]["days_available"],
            "board_rows_total": facts["dragon_tiger"]["board_rows_total"],
            "seat_rows_total": facts["dragon_tiger"]["seat_rows_total"],
            "series": facts["dragon_tiger"]["series"],
        },
        "leader": {
            "week": facts["week"],
            "available": bool(ladder["series"]),
            "height": ladder["height"],
            "peak_height": ladder["peak_height"],
            "count_total": ladder["count_total"],
            "gap_days": ladder["gap_days"],
            "unknown_board_total": ladder["unknown_board_total"],
            "leaders": ladder["leaders"],
            "series": ladder["series"],
            "promotion_rate": sentiment["promotion_rate"],
            "validation_pending": validation["pending"],
        },
    }


# --------------------------------------------------------------------------- #
# 编排：复用五段状态机，只换落盘布局与提示词
# --------------------------------------------------------------------------- #

class WeeklyOrchestrator(Orchestrator):
    """周度五段编排：模型链、三级降级、`merge_narrative` 闸门全部沿用父类。

    只改两处：
    - `run()` 用 `weekly/checkpoint.LAYOUT` 落盘（**不与每日 checkpoint 混目录**）；
    - `_messages()` 用周度提示词 + 「本周」措辞，别让模型以为在写某一天的复盘。
    """

    def run(
        self,
        week: Any,
        metrics_by_section: dict[str, dict[str, Any]],
        *,
        force: bool = False,
        retry_failed: bool = False,
        only: str | None = None,
    ) -> RunResult:
        tag = week_module.normalize_week(week)
        document = weekly_checkpoint.load(tag, self.root)
        targets = weekly_checkpoint.targets(
            document, force=force, retry_failed=retry_failed, only=only
        )
        for name in targets:
            record = self._generate(name, tag, metrics_by_section.get(name, {}))
            weekly_checkpoint.record(
                document,
                name,
                status=record["status"],
                mode=record["mode"],
                narrative=record["narrative"],
                model=record.get("model"),
                degraded=record["degraded"],
                notes=record["notes"],
                failure=record.get("failure"),
                attempts=record.get("attempts"),
            )
            weekly_checkpoint.save(document, self.root)
        result = self._result(tag, document, targets, metrics_by_section)
        # `_result()` 里的路径按每日布局算，周报要说的是自己那份
        result.checkpoint_path = str(weekly_checkpoint.checkpoint_path(tag, self.root))
        return result

    def _messages(self, name: str, day: Any, metrics: dict[str, Any]) -> tuple[str, str]:
        return build_messages(name, str(day), metrics, self._prompt(name))

    def _prompt(self, name: str) -> str:
        if name not in self._prompts:
            self._prompts[name] = load_prompt(WEEKLY_PROMPT)
        return self._prompts[name]


def build_messages(
    section: str, week: str, metrics: dict[str, Any], prompt_text: str
) -> tuple[str, str]:
    """周度 system / user 消息：与 `structured_output.build_messages()` 同构，只换时间粒度。"""
    title = weekly_checkpoint.SECTION_TITLES.get(section, section)
    system = (
        f"{prompt_text}\n\n"
        "硬约束：\n"
        "1. 只输出一个 JSON 对象，字段仅限 narrative / highlights / risks / evidence；\n"
        "2. 不得输出任何指标数字键（temperature、promotion_rate 等），数字由程序计算；\n"
        "3. 不得编造或新算指标；引用数字时必须与给定快照逐字一致；\n"
        "4. 快照里为 null 的字段就直说「没有读数」，不要填空；\n"
        "5. 中文输出，narrative 为字符串数组。"
    )
    user = (
        f"周期：{week}（周度汇总，不是某一个交易日）\n"
        f"段落：{title}\n"
        "程序周度指标快照（只读）：\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"请基于快照写「{title}」这一段的周度叙述，输出 JSON。"
    )
    return system, user


# --------------------------------------------------------------------------- #
# 生成与落盘
# --------------------------------------------------------------------------- #

def generate(
    week: Any,
    *,
    root: Any = None,
    config_root: Any = None,
    config: fallback.AiConfig | None = None,
    client: Any = None,
    force: bool = False,
    retry_failed: bool = False,
    only: str | None = None,
) -> tuple[dict[str, Any], RunResult, dict[str, Any]]:
    """跑一遍周报流水线并落盘；返回 `(report, run, facts)`。

    整周没有可用读数时**不调模型**：对着空数字写字只会写出幻觉，省一次额度也
    免得失败计数误导人。五段仍按模板补齐，事实包与两份产物照写（要求 4）。
    """
    tag = week_module.normalize_week(week)
    pack = facts_module.build_facts(tag, root=root)
    metrics = section_snapshots(pack)
    if pack["available"]:
        orchestrator = WeeklyOrchestrator(
            config=config, client=client, root=root, config_root=config_root
        )
        run = orchestrator.run(
            tag, metrics, force=force, retry_failed=retry_failed, only=only
        )
    else:
        run = RunResult(
            date=tag,
            checkpoint_path=str(weekly_checkpoint.checkpoint_path(tag, root)),
        )
    report = build_report(tag, run, pack, metrics)
    write_report(report, tag, root)
    return report, run, pack


def build_report(
    week: str,
    run: RunResult,
    pack: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """组装周报文档：`facts`（纯程序）与 `sections`（含 AI 叙述）分列两层。

    段落恒定五段且顺序固定：编排没跑（整周无数据）或某段没出叙述时，由程序补
    模板段落，页面与 `.md` 都不会拿到空段。
    """
    by_name = {str(item.get("name")): item for item in run.sections}
    sections: list[dict[str, Any]] = []
    for name in weekly_checkpoint.LAYOUT.names:
        item = by_name.get(name) or {}
        narrative = [str(line) for line in item.get("narrative") or []]
        notes = [str(note) for note in item.get("notes") or []]
        degraded = bool(item.get("degraded"))
        mode = str(item.get("mode") or "")
        status = item.get("status")
        if not narrative:
            # 降级路径：事实包 + 模板必须成对出现，页面与 .md 都不会拿到空段落
            narrative = template_narrative(name, pack)
            notes = [TEMPLATE_NOTE, *notes]
            degraded = True
            mode = "template"
            status = status or "degraded"
        ai = dict(item.get("ai") or {})
        ai["narrative"] = narrative
        ai["degraded"] = degraded
        sections.append(
            {
                "name": name,
                "title": weekly_checkpoint.SECTION_TITLES.get(name, name),
                "status": status,
                "mode": mode,
                "model": item.get("model"),
                "degraded": degraded,
                "narrative": narrative,
                "notes": notes,
                "ai": ai,
                "metrics": dict(metrics.get(name) or {}),
            }
        )
    degraded_any = bool(run.degraded) or any(section["degraded"] for section in sections)
    return {
        "title": f"{week} 周度复盘",
        "week": week,
        "range": pack["range"],
        "generated_at": _now(),
        "freshness": FRESHNESS,
        "source_status": _source_status(run, pack, degraded_any),
        "available": bool(pack["available"]),
        "reason": pack["reason"],
        "headline": headline(pack),
        "facts": pack,
        "ai": {"model": "、".join(run.models) or None, "degraded": degraded_any},
        "sections": sections,
        "ai_failures": list(run.ai_failures),
        "checkpoint": run.checkpoint_path,
        "footer": FOOTER,
    }


def _source_status(run: RunResult, pack: dict[str, Any], degraded: bool) -> str:
    if not pack.get("available") or run.failed:
        return "partial"
    return "degraded" if degraded else "ok"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _line(lead: str, *pairs: tuple[str, Any]) -> str:
    """模板行：`要点：a=1；b=2`，值为 `None` 的分项直接不写（不冒充读数）。"""
    parts = [
        f"{label}={value}"
        for label, value in pairs
        if value is not None and value != ""
    ]
    return f"{lead}：{'；'.join(parts)}" if parts else f"{lead}：本周没有可用读数"


def _percent(value: Any) -> str | None:
    return None if value is None else f"{value}%"


def _arrow(first: Any, last: Any) -> str | None:
    if first is None or last is None:
        return None
    return f"{first} → {last}"


def template_narrative(name: str, pack: dict[str, Any]) -> list[str]:
    """程序直出的周度要点（模板降级段落）：只复述事实包已有的数字，不新增计算。"""
    missing = "本周没有可用读数"
    if name == "sentiment":
        sentiment = pack["sentiment"]
        temperature = sentiment["temperature"]
        promotion = sentiment["promotion_rate"]
        lines = [
            _line(
                f"{pack['week']} 共 {len(pack['session_days'])} 个交易日有数据",
                ("情绪温度均值", temperature["avg"]),
                ("首日→末日", _arrow(temperature["first"], temperature["last"])),
                ("晋级率均值", _percent(promotion["avg"])),
                ("封板率均值", _percent(sentiment["seal_rate"]["avg"])),
            )
        ]
        breadth = pack["breadth"]
        lines.append(
            _line(
                "涨跌分布",
                ("上涨", breadth["up_count"] if breadth["available"] else None),
                ("下跌", breadth["down_count"] if breadth["available"] else None),
                ("平盘", breadth["flat_count"] if breadth["available"] else None),
                ("样本", breadth["samples"] if breadth["available"] else None),
            )
        )
        return lines if pack["available"] else [f"{missing}（{pack['reason']}）"]
    if name == "leader":
        ladder = pack["ladder"]
        top = "、".join(
            f"{stock['name'] or stock['code']}（{stock['boards']} 板，{stock['date']}）"
            for stock in ladder["leaders"]
        )
        return [
            _line(
                "连板高度",
                ("周初", ladder["height"]["first"]),
                ("周末", ladder["height"]["last"]),
                ("变化", ladder["height"]["change"]),
                ("峰值", ladder["peak_height"]),
                ("涨停家数合计", ladder["count_total"]),
                ("出现断层的天数", ladder["gap_days"]),
            ),
            f"本周最高板龙头：{top or '无（涨停池没有可解析的连板数据）'}",
        ]
    if name == "theme":
        themes = pack["themes"]
        if not themes["available"]:
            return [f"{missing}（{themes['reason']}）"]
        lines = [
            _line(
                f"主线题材（每日涨幅前 {themes['window']} 名为一个窗口）",
                ("观察题材数", themes["observed"]),
                ("入榜题材", "、".join(
                    f"{item['name']}（{item['streak_days']} 天连续 / {item['days_on_top']} 天入窗）"
                    for item in themes["themes"][:3]
                )),
            )
        ]
        return lines
    if name == "capital":
        capital = pack["capital"]
        if not capital["days_available"]:
            return [f"{missing}（fund_flow 本周没有缓存）"]
        return [
            _line(
                "资金流向",
                ("有数据天数", capital["days_available"]),
                ("记录合计", capital["records_total"]),
            )
        ]
    dragon = pack["dragon_tiger"]
    if not dragon["days_available"]:
        return [f"{missing}（dragon_tiger 本周没有缓存）"]
    return [
        _line(
            "龙虎榜与席位",
            ("有数据天数", dragon["days_available"]),
            ("榜单条目", dragon["board_rows_total"]),
            ("席位条目", dragon["seat_rows_total"]),
        )
    ]


def headline(pack: dict[str, Any]) -> list[str]:
    """周报抬头（程序生成的一行结论，与每日复盘 `_headline()` 同职责）。"""
    if not pack["available"]:
        return [f"数据缺失：{pack['reason']}"]
    sentiment = pack["sentiment"]
    ladder = pack["ladder"]
    lines = [
        f"{len(pack['session_days'])} 个交易日有数据；"
        f"情绪温度均值 {sentiment['temperature']['avg']}，"
        f"{sentiment['temperature']['first']} → {sentiment['temperature']['last']}"
        f"（{sentiment['temperature']['change']}）",
        f"连板高度 {ladder['height']['first']} → {ladder['height']['last']}，"
        f"峰值 {ladder['peak_height']} 板",
    ]
    validation = pack["validation"]
    if validation["available"]:
        lines.append(
            f"待验证项 {validation['conditions']} 条：已结算 {validation['settled']}，"
            f"通过 {validation['passed']}，未通过 {validation['failed']}，"
            f"待结算 {validation['pending']}"
        )
    if pack["missing_days"]:
        lines.append(
            "缺数据：" + "、".join(str(item["date"]) for item in pack["missing_days"])
        )
    return lines


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def render_markdown(report: dict[str, Any]) -> str:
    """人读件：版式与每日复盘同一条（抬头 → 事实包 → 五段叙述 → 口径 → 来源）。"""
    pack = report.get("facts") or {}
    lines: list[str] = [
        f"# {report.get('title') or report.get('week') or ''}",
        "",
        f"- 周期：{(pack.get('range') or {}).get('start')} ~ "
        f"{(pack.get('range') or {}).get('end')}",
        f"- 交易日：{'、'.join(pack.get('session_days') or []) or '无'}",
        f"- 状态：`{report.get('source_status')}` · freshness={report.get('freshness')}"
        f" · 模型={report.get('ai', {}).get('model') or '-'}",
    ]
    if pack.get("reason"):
        lines.append(f"- 提示：{pack['reason']}")
    lines += ["", "## 本周概览", ""]
    lines += [f"- {item}" for item in report.get("headline") or []]

    if pack.get("missing_days"):
        lines += ["", "## 缺数据的日子", ""]
        lines += [
            f"- {item['date']}：{item['reason']}（复盘产物"
            f"{'在' if item['has_report'] else '缺'}）"
            for item in pack["missing_days"]
        ]

    lines += _facts_tables(pack)

    lines += ["", "## 叙述（AI 只写文字，数字由程序计算）", ""]
    for section in report.get("sections") or []:
        name = str(section.get("name"))
        lines.append(
            f"### {section.get('title') or name} · `{section.get('status')}`"
            f" · mode={section.get('mode')}"
        )
        lines.append("")
        for paragraph in section.get("narrative") or []:
            lines.append(str(paragraph))
            lines.append("")
        notes = section.get("notes") or []
        if notes:
            lines.append("> " + "；".join(str(note) for note in notes))
            lines.append("")

    failures = report.get("ai_failures") or []
    if failures:
        lines += ["", "## AI 失败段落", ""]
        for failure in failures:
            lines.append(
                f"- {failure.get('section')}：{failure.get('kind')} — "
                f"{failure.get('message')}（{failure.get('action')}）"
            )

    lines += ["", "## 口径", ""]
    lines += [f"- {key}：{value}" for key, value in (pack.get("caliber") or {}).items()]
    lines += [
        "",
        "## 数据来源",
        "",
        f"- {pack.get('source_detail') or '（无）'}",
        f"- checkpoint：`{report.get('checkpoint')}`",
        "",
        f"> {report.get('footer') or FOOTER}",
        "",
    ]
    return "\n".join(lines)


def _facts_tables(pack: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", "## 情绪与天梯序列", ""]
    lines += [
        "| 日期 | 温度 | 晋级率 | 封板率 | 涨停 | 炸板 | 最高板 | 断层 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in (pack.get("sentiment") or {}).get("series") or []:
        ladder_row = _ladder_row(pack, str(row["date"]))
        lines.append(
            f"| {row['date']} | {_cell(row['temperature'])} | {_cell(row['promotion_rate'], '%')} "
            f"| {_cell(row['seal_rate'], '%')} | {_cell(row['limit_up_count'])} "
            f"| {_cell(row['broken_count'])} | {_cell(ladder_row.get('height'))} "
            f"| {_cell(ladder_row.get('gap_levels'), join='、')} | {row.get('source') or '-'} |"
        )

    breadth = pack.get("breadth") or {}
    lines += ["", "## 涨跌分布", ""]
    if breadth.get("available"):
        lines += ["| 区间 | 只次 | 占比 |", "| --- | --- | --- |"]
        lines += [
            f"| {item['band']} | {item['count']} | {_cell(item['share_pct'], '%')} |"
            for item in breadth.get("distribution") or []
        ]
    else:
        lines.append(f"（无读数：{breadth.get('reason')}）")

    themes = pack.get("themes") or {}
    lines += ["", "## 主线题材持续天数", ""]
    if themes.get("available"):
        lines += [
            "| 题材 | 连续天数 | 入窗天数 | 均涨幅 | 峰值 | 入窗日 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {item['name']} | {item['streak_days']} | {item['days_on_top']} "
            f"| {_cell(item['avg_change_pct'], '%')} | {_cell(item['best_change_pct'], '%')} "
            f"| {'、'.join(item['days'])} |"
            for item in themes.get("themes") or []
        ]
    else:
        lines.append(f"（无读数：{themes.get('reason')}）")

    validation = pack.get("validation") or {}
    lines += ["", "## 待验证项结算", ""]
    if validation.get("available"):
        lines += [
            f"- 条件 {validation['conditions']} 条：已结算 {validation['settled']}"
            f"（通过 {validation['passed']} / 未通过 {validation['failed']}"
            f" / 判定不出 {validation['unknown']}），待结算 {validation['pending']}，"
            f"通过率 {_cell(validation['pass_rate'], '%')}",
            "",
            "| 生成日 | 条件 | 目标 | 实际 | 结论 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in validation.get("items") or []:
            lines.append(
                f"| {item['generated_date']} | {item['text']} "
                f"| {item['operator']} {_cell(item['target'], join='、')} "
                f"| {_cell(item['actual'])} | {_verdict(item)} |"
            )
    else:
        lines.append(f"（无：{validation.get('reason')}）")

    coverage = pack.get("ai_coverage") or {}
    lines += ["", "## 本周每日复盘 AI 完成度", ""]
    if coverage.get("days"):
        lines += [
            f"- 有 checkpoint 的天数 {coverage['days_with_checkpoint']}，"
            f"段落完成 {coverage['sections_done']}/{coverage['sections_expected']}"
            f"（降级 {coverage['sections_degraded']}、失败 {coverage['sections_failed']}、"
            f"未跑 {coverage['sections_pending']}）"
        ]
    else:
        lines.append("- 本周没有 AI checkpoint（每日复盘尚未生成或全部段落未跑）")
    return lines


def _verdict(item: dict[str, Any]) -> str:
    if not item.get("settled"):
        return "未结算"
    if item.get("passed") is True:
        return "通过"
    if item.get("passed") is False:
        return "未通过"
    return "判定不出"


def _ladder_row(pack: dict[str, Any], day: str) -> dict[str, Any]:
    for row in (pack.get("ladder") or {}).get("series") or []:
        if row.get("date") == day:
            return row
    return {}


def _cell(value: Any, suffix: str = "", join: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        return join.join(str(item) for item in value) if value else "—"
    return f"{value}{suffix}"


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #

def write_report(report: dict[str, Any], week: str, root: Any = None) -> list[Path]:
    """原子写出 `<week>.json` + `<week>.md`；任一失败不影响另一件。"""
    from ladderstock.ai.checkpoint import atomic_write

    json_path = weekly_path(week, root)
    md_path = markdown_path(week, root)
    written: list[Path] = []
    try:
        atomic_write(
            json_path, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        )
        written.append(json_path)
    except OSError:
        pass
    try:
        atomic_write(md_path, render_markdown(report))
        written.append(md_path)
    except OSError:
        pass
    return written


__all__ = [
    "FOOTER",
    "FRESHNESS",
    "TEMPLATE_NOTE",
    "WEEKLY_PROMPT",
    "WEEKLY_SUBDIR",
    "WeeklyOrchestrator",
    "build_messages",
    "build_report",
    "generate",
    "headline",
    "latest_week",
    "load_report",
    "markdown_path",
    "render_markdown",
    "section_snapshots",
    "template_narrative",
    "weekly_path",
    "write_report",
]
