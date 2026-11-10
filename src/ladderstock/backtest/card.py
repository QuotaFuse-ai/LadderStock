"""运行卡：一次回测的完整证据包（QUOT-500 §5.1 / §5.4）。

三份产物，**同源**（同一份 ``run_card()`` 返回值），保证机器读和人读的结论不可能分叉：

- ``<run_id>.json``：机器读的完整卡片（页面只读这份，不在前端重算指标）；
- ``<run_id>.md``：人读的 Markdown；
- ``<run_id>.html``：自包含 HTML（无外链 CSS / JS / 字体），可单独转发。

**运行卡记录的是「本次实际生效值」**：成本、阈值、样本切分、成本档位都来自
``BacktestConfig.as_dict()``，并配 ``config_sources`` 说明每个字段来自 ``yaml`` /
``cli`` / ``fallback``——只记默认值会让「有人用 CLI 改了参数」不可复现。

**校验门不过 → 卡片与页面标红**：``gate.passed=false`` 时最顶部的 ``gate.status="fail"``
和 ``gate.red_reasons[]`` 都会出现在 JSON / Markdown / HTML 三份产物里，且 HTML 用红底横幅
置顶显示（不允许被主指标盖过去）。

卡片字段清单（与前端子单 QUOT-534 对齐，见 issue 评论里的契约）：

=====================  ==============================================================
顶层字段               含义
=====================  ==============================================================
``schema_version``     运行卡 JSON schema 版本（改字段必递增）
``run_id``             ``bt_`` + 8 位；同参数 + 同数据指纹必定相同
``generated_at``       生成时间（UTC ISO8601，**不参与 run_id**，只用于追溯）
``symbol`` / ``board`` / ``limit_pct`` / ``bars`` / ``adjust``
                       标的信息与可用 K 线根数、复权口径、涨跌停幅度
``strategy`` / ``strategy_params``
``period``             ``{start, end, trading_days}``
``data``               ``{fingerprint, first_date, last_date, bars, source, adjust}``
``assumptions``        执行假设（T+1、开盘成交、停牌口径等，写死并明示）
``config``             本次实际生效的成本 / 阈值 / 切分 / 成本档位
``config_sources``     每个 config 字段来源（yaml / cli / fallback）
``initial_cash`` / ``final_equity`` / ``open_position_pnl``
``metrics`` / ``metrics_display`` / ``metrics_calc_ids``
                       11 个决策指标的值、展示串、**每指标 calc_id**
``equity_curve``       ``[{date, equity, drawdown, shares}]`` 每日净值（供页面画图）
``benchmark``          ``{name, note, curve, cumulative_return}``
``trades``             完成回合明细
``gate``               ``{passed, status, red_reasons, warnings, checks, segments, cost_tiers}``
``calc_ids``           卡片整体计算口径 id：``{run_card, data, gate, benchmark}``
``artifacts``          ``{json, markdown, html}`` 三个文件名（相对运行卡目录）
=====================  ==============================================================
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from ladderstock.backtest.guard import FAIL, PASS
from ladderstock.backtest.score import Metrics

SCHEMA_VERSION: Final = "1"
RUN_ID_PREFIX: Final = "bt_"


class CardError(ValueError):
    """运行卡结构不合法（缺字段 / 字段类型不符）。"""


def _esc(text: Any) -> str:
    """HTML 转义（只做 4 个必要字符；HTML 卡片全部内联生成，无外部模板）。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f}%"


def _num(value: float | int | None, *, decimals: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{decimals}f}"


_METRIC_LABELS: Final[Mapping[str, str]] = {
    "cumulative_return": "累计收益",
    "annualized_return": "年化收益",
    "max_drawdown": "最大回撤",
    "sharpe": "夏普",
    "calmar": "卡玛",
    "win_rate": "胜率",
    "profit_loss_ratio": "盈亏比",
    "trade_count": "交易笔数",
    "avg_turnover": "平均换手",
    "avg_holding_days": "平均持仓天数",
    "exposure_ratio": "持仓时间占比",
}

_PERCENT_METRICS: Final[frozenset[str]] = frozenset(
    {
        "cumulative_return",
        "annualized_return",
        "max_drawdown",
        "win_rate",
        "avg_turnover",
        "exposure_ratio",
    }
)


def metric_cells(metrics: Metrics | Mapping[str, Any]) -> list[tuple[str, str, str | None]]:
    """统一的「指标 → 展示串」映射；JSON / MD / HTML 都从这里取，保证三份产物同值。"""
    if isinstance(metrics, Metrics):
        values: Mapping[str, Any] = metrics.values()
        displays: Mapping[str, Any] = metrics.display()
    else:
        values = metrics
        displays = {}
    out: list[tuple[str, str, str | None]] = []
    for key, label in _METRIC_LABELS.items():
        value = values.get(key)
        if key in displays and displays.get(key) is not None:
            shown = str(displays[key])
        elif value is None:
            shown = "—"
        elif key == "trade_count":
            shown = str(value)
        elif key in _PERCENT_METRICS:
            shown = _pct(float(value))
        else:
            shown = _num(float(value))
        out.append((key, label, None if value is None else shown))
    return out


def _red_banner(gate: Mapping[str, Any]) -> str:
    reasons = gate.get("red_reasons") or []
    if gate.get("passed"):
        return ""
    items = "".join(f"<li>{_esc(r)}</li>" for r in reasons)
    return (
        '<div class="red-banner" role="alert">'
        "<strong>校验门未通过（红灯）</strong>"
        f"<ul>{items}</ul></div>"
    )


def render_markdown(card: Mapping[str, Any]) -> str:
    gate = card["gate"]
    metrics = card["metrics"]
    lines: list[str] = [
        f"# 回测运行卡 · {card['symbol']} · {card['strategy']}",
        "",
        f"- run_id：`{card['run_id']}`",
        f"- 生成时间：{card['generated_at']}（UTC）",
        f"- 区间：{card['period']['start']} ~ {card['period']['end']}"
        f"（{card['data']['bars']} 根 K 线）",
        f"- 数据指纹：`{card['data']['fingerprint']}`",
        f"- 复权口径：{card['data']['adjust']}",
        "",
    ]
    if gate.get("passed"):
        lines += ["## 校验门：通过（绿）", ""]
    else:
        lines += [
            "## 校验门：未通过（红）",
            "",
            "> 结论未通过，以下收益数字只作对照，不应作为结论发布。",
            "",
        ]
        for reason in gate.get("red_reasons") or []:
            lines.append(f"- {reason}")
        lines.append("")
    warnings = gate.get("warnings") or []
    if warnings:
        lines += ["### 提醒（非阻塞）", ""] + [f"- {w}" for w in warnings] + [""]

    lines += [
        "## 核心指标",
        "",
        "| 指标 | 值 | calc_id |",
        "|---|---|---|",
    ]
    for key, label, shown in metric_cells(metrics):
        cid = card["metrics_calc_ids"].get(key) or "—"
        lines.append(f"| {label} (`{key}`) | {shown} | `{cid}` |")
    lines.append("")

    lines += [
        "## 成本敏感性（三档佣金率）",
        "",
        "滑点与印花税固定，只变佣金率；**每档都是一次独立撮合重跑**（非算术缩放）。",
        "",
        "| 佣金率 | 累计收益 | 年化 | 最大回撤 | 交易笔数 | 期末权益 |",
        "|---|---|---|---|---|---|",
    ]
    for tier in gate.get("cost_tiers") or []:
        lines.append(
            "| {rate}‱ | {cum} | {ann} | {dd} | {n} | {eq} |".format(
                rate=f"{tier['commission'] * 10000:g}",
                cum=_pct(tier["cumulative_return"]),
                ann=_pct(tier["annualized_return"]),
                dd=_pct(tier["max_drawdown"]),
                n=tier["trade_count"],
                eq=f"{tier['final_equity']:,.2f}",
            )
        )
    lines.append("")

    lines += [
        "## 样本内 / 样本外",
        "",
        "| 段 | 区间 | K 线 | 累计收益 | 交易笔数 |",
        "|---|---|---|---|---|",
    ]
    for seg in gate.get("segments") or []:
        metrics_seg = seg["metrics"]
        lines.append(
            f"| {seg['label']} | {seg['start']} ~ {seg['end']} | {seg['bars']} | "
            f"{_pct(metrics_seg.get('cumulative_return'))} | {metrics_seg.get('trade_count')} |"
        )
    lines.append("")

    lines += [
        "## 校验门明细",
        "",
        "| 闸 | 结论 | 原因 |",
        "|---|---|---|",
    ]
    for check in gate.get("checks") or []:
        verdict = "通过" if check["status"] == PASS else "**未通过**"
        reason = check["reason"] or "—"
        lines.append(f"| {check['label']} (`{check['key']}`) | {verdict} | {reason} |")
    lines.append("")

    lines += [
        "## 实际生效配置",
        "",
        "| 字段 | 值 | 来源 |",
        "|---|---|---|",
    ]
    for key, value in card["config"].items():
        lines.append(f"| `{key}` | `{value}` | {card['config_sources'].get(key, '—')} |")
    lines.append("")

    lines += [
        "## 假设（写死在代码里）",
        "",
    ] + [f"- {item}" for item in card["assumptions"]] + [""]

    lines += [
        "## 基准",
        "",
        f"- 口径：{card['benchmark']['name']}",
        f"- 说明：{card['benchmark']['note']}",
        f"- 累计收益：{_pct(card['benchmark']['cumulative_return'])}",
        "",
        "## 文件",
        "",
        f"- JSON（机器读）：`{card['artifacts']['json']}`",
        f"- Markdown（人读）：`{card['artifacts']['markdown']}`",
        f"- HTML（自包含）：`{card['artifacts']['html']}`",
        "",
    ]
    return "\n".join(lines)


def render_html(card: Mapping[str, Any]) -> str:
    gate = card["gate"]
    passed = bool(gate.get("passed"))
    banner = _red_banner(gate)
    status_class = "pass" if passed else "fail"
    metric_rows = "".join(
        f'<tr><td>{_esc(label)}</td><td class="num">{_esc(shown or "—")}</td>'
        f'<td class="cid">{_esc(card["metrics_calc_ids"].get(key) or "—")}</td></tr>'
        for key, label, shown in metric_cells(card["metrics"])
    )
    tier_rows = "".join(
        f'<tr><td class="num">{tier["commission"] * 10000:g}‱</td>'
        f'<td class="num {"neg" if tier["cumulative_return"] < 0 else ""}">'
        f'{_esc(_pct(tier["cumulative_return"]))}</td>'
        f'<td class="num">{_esc(_pct(tier["annualized_return"]))}</td>'
        f'<td class="num">{_esc(_pct(tier["max_drawdown"]))}</td>'
        f'<td class="num">{tier["trade_count"]}</td>'
        f'<td class="num">{tier["final_equity"]:,.2f}</td></tr>'
        for tier in gate.get("cost_tiers") or []
    )
    seg_rows = "".join(
        f'<tr><td>{_esc(seg["label"])}</td><td>{_esc(seg["start"])} ~ {_esc(seg["end"])}</td>'
        f'<td class="num">{seg["bars"]}</td>'
        f'<td class="num">{_esc(_pct(seg["metrics"].get("cumulative_return")))}</td>'
        f'<td class="num">{seg["metrics"].get("trade_count")}</td></tr>'
        for seg in gate.get("segments") or []
    )
    check_rows = "".join(
        f'<tr class="{"ok" if c["status"] == PASS else "bad"}">'
        f'<td>{_esc(c["label"])}</td>'
        f'<td>{"通过" if c["status"] == PASS else "未通过"}</td>'
        f'<td>{_esc(c["reason"] or "—")}</td></tr>'
        for c in gate.get("checks") or []
    )
    config_rows = "".join(
        f'<tr><td><code>{_esc(key)}</code></td><td>{_esc(value)}</td>'
        f'<td>{_esc(card["config_sources"].get(key, "—"))}</td></tr>'
        for key, value in card["config"].items()
    )
    assumption_items = "".join(f"<li>{_esc(item)}</li>" for item in card["assumptions"])
    warn_items = "".join(f"<li>{_esc(w)}</li>" for w in gate.get("warnings") or [])
    warn_block = (
        f'<div class="warn"><strong>提醒（非阻塞）</strong><ul>{warn_items}</ul></div>'
        if warn_items
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>回测运行卡 · {_esc(card["symbol"])} · {_esc(card["strategy"])}
  · {_esc(card["run_id"])}</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #1f2937; background: #f5f6f8; }}
main {{ max-width: 1080px; margin: 24px auto; padding: 0 16px 48px; }}
header {{ background: {'#16a34a' if passed else '#dc2626'}; color: #fff;
  padding: 16px 20px; border-radius: 8px; }}
header h1 {{ margin: 0 0 4px; font-size: 20px; }}
header .meta {{ font-size: 12px; opacity: .92; word-break: break-all; }}
.red-banner {{ margin: 16px 0; border: 2px solid #dc2626; background: #fee2e2;
  color: #7f1d1d; padding: 12px 16px; border-radius: 8px; }}
.red-banner ul {{ margin: 8px 0 0 20px; padding: 0; }}
.warn {{ margin: 16px 0; border-left: 4px solid #d97706; background: #fffbeb;
  padding: 10px 14px; border-radius: 4px; }}
section {{ background: #fff; margin: 16px 0; padding: 16px 20px; border-radius: 8px;
  box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
section h2 {{ font-size: 16px; margin: 0 0 12px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #e5e7eb; padding: 6px 8px;
  text-align: left; vertical-align: top; }}
th {{ background: #f9fafb; font-weight: 600; font-size: 12px; color: #4b5563; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.cid {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #6b7280; }}
td.neg {{ color: #dc2626; }}
tr.ok td:nth-child(2) {{ color: #16a34a; font-weight: 600; }}
tr.bad td:nth-child(2) {{ color: #dc2626; font-weight: 600; }}
code {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; }}
ul {{ margin: 0; padding-left: 20px; }}
footer {{ text-align: center; color: #9ca3af; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<main>
<header class="{'pass' if passed else 'fail'}">
  <h1>回测运行卡 · {_esc(card["symbol"])} · {_esc(card["strategy"])}</h1>
  <div class="meta">
    run_id <strong>{_esc(card["run_id"])}</strong>
    · 区间 {_esc(card["period"]["start"])} ~ {_esc(card["period"]["end"])}
      （{card["data"]["bars"]} 根）
    · 数据指纹 <strong>{_esc(card["data"]["fingerprint"])}</strong>
    · 复权 {_esc(card["data"]["adjust"])}
    · 生成于 {_esc(card["generated_at"])}（UTC）
  </div>
</header>
<div class="status {status_class}">
  校验门结论：<strong>{'通过（绿）' if passed else '未通过（红）'}</strong>
</div>
{banner}
{warn_block}
<section>
  <h2>核心指标</h2>
  <table><thead><tr><th>指标</th><th style="text-align:right">值</th><th>calc_id</th></tr></thead>
  <tbody>{metric_rows}</tbody></table>
</section>
<section>
  <h2>成本敏感性（三档佣金率；滑点 / 印花税固定，每档独立撮合重跑）</h2>
  <table><thead><tr><th>佣金率</th><th style="text-align:right">累计收益</th>
  <th style="text-align:right">年化</th><th style="text-align:right">最大回撤</th>
  <th style="text-align:right">交易笔数</th><th style="text-align:right">期末权益</th></tr></thead>
  <tbody>{tier_rows}</tbody></table>
</section>
<section>
  <h2>样本内 / 样本外</h2>
  <table><thead><tr><th>段</th><th>区间</th><th style="text-align:right">K 线</th>
  <th style="text-align:right">累计收益</th><th style="text-align:right">交易笔数</th></tr></thead>
  <tbody>{seg_rows}</tbody></table>
</section>
<section>
  <h2>校验门明细</h2>
  <table><thead><tr><th>闸</th><th>结论</th><th>原因</th></tr></thead>
  <tbody>{check_rows}</tbody></table>
</section>
<section>
  <h2>实际生效配置</h2>
  <table><thead><tr><th>字段</th><th>值</th><th>来源</th></tr></thead>
  <tbody>{config_rows}</tbody></table>
</section>
<section>
  <h2>执行假设（写死在代码里）</h2>
  <ul>{assumption_items}</ul>
</section>
<section>
  <h2>基准</h2>
  <p>{_esc(card["benchmark"]["name"])}</p>
  <p style="color:#6b7280;font-size:12px">{_esc(card["benchmark"]["note"])}</p>
  <p>累计收益：<strong>{_esc(_pct(card["benchmark"]["cumulative_return"]))}</strong></p>
</section>
<footer>
  运行卡 schema v{_esc(card["schema_version"])} · 同参数 + 同数据指纹 → 同 run_id；
  本卡所有指标均由后端计算结果直接写入，前端不得重算。
</footer>
</main>
</body>
</html>
"""


def dump_json(card: Mapping[str, Any]) -> str:
    """严格 JSON（``allow_nan=False``）：任何 inf / NaN 都直接抛，不静默写出坏文件。"""
    return json.dumps(card, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False)


def write_artifacts(card: Mapping[str, Any], directory: Path) -> dict[str, Path]:
    """把三份产物写到 ``directory``，返回 ``{json, markdown, html}`` 的绝对路径。"""
    if not card.get("run_id"):
        raise CardError("运行卡缺 run_id")
    for required in (
        "symbol",
        "strategy",
        "period",
        "data",
        "metrics",
        "gate",
        "config",
        "assumptions",
        "artifacts",
    ):
        if required not in card:
            raise CardError(f"运行卡缺字段 {required!r}")
    directory.mkdir(parents=True, exist_ok=True)
    files = card["artifacts"]
    paths = {
        "json": directory / files["json"],
        "markdown": directory / files["markdown"],
        "html": directory / files["html"],
    }
    paths["json"].write_text(dump_json(card), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(card), encoding="utf-8")
    paths["html"].write_text(render_html(card), encoding="utf-8")
    return paths


def artifact_names(run_id: str) -> dict[str, str]:
    return {
        "json": f"{run_id}.json",
        "markdown": f"{run_id}.md",
        "html": f"{run_id}.html",
    }


def summary_lines(card: Mapping[str, Any]) -> list[str]:
    """终端人读摘要（CLI 用）；与运行卡同源，不另算。"""
    gate = card["gate"]
    top = [
        f"run_id     {card['run_id']}",
        f"symbol     {card['symbol']}  ({card['period']['start']} ~ {card['period']['end']}, "
        f"{card['data']['bars']} bars)",
        f"strategy   {card['strategy']} {card['strategy_params']}",
        f"data_fp    {card['data']['fingerprint']}",
        f"gate       {'PASS' if gate['passed'] else 'FAIL'}",
    ]
    if not gate["passed"]:
        for reason in gate.get("red_reasons") or []:
            top.append(f"  - {reason}")
    return top


__all__ = [
    "CardError",
    "RUN_ID_PREFIX",
    "SCHEMA_VERSION",
    "FAIL",
    "PASS",
    "artifact_names",
    "dump_json",
    "metric_cells",
    "render_html",
    "render_markdown",
    "summary_lines",
    "write_artifacts",
]
