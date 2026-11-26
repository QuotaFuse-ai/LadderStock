"""周期报告的 AI 叙述层（B 包第二阶段 / DEC-089 §4 收尾）。

与周报（`weekly/pipeline.py`）同一条纪律，但**不走编排器与 checkpoint**：
月/季/年报是低频重算的幂等产物，五段一次性生成即可，段级断点续跑没有收益。

- 快照只搬运：五段（概览 / 组合 / 归因 / 纪律 / 市场）的数字全部来自
  `period.build_report()` 的事实包，这里不重算、不新增指标；
- AI 只写叙述：模型输出按 `{narrative: [...]}` 解析，失败退回纯文本分行，
  再失败退回模板段落——事实包永远完整落盘；
- 未配置模型凭据时整份退回模板（`configured=False`、`degraded=False`）：
  这是 B 包第一阶段的既有契约，不因 AI 层存在而惩罚未配置的用户；
- 配置了凭据但段落失败 → `degraded=True`，CLI 按周报同口径给退出码 9。

本模块不发起任何除模型调用外的网络请求（DEC-006 红线不变）。
"""

from __future__ import annotations

import json
from typing import Any

from ladderstock.ai import fallback
from ladderstock.ai.structured_output import load_prompt

REPORT_PROMPT = "prompts/report.md"

SECTION_TITLES: dict[str, str] = {
    "overview": "期间一句话",
    "portfolio": "组合结果",
    "attribution": "按标的归因",
    "discipline": "买卖纪律与错题",
    "market": "市场情绪",
}

TEMPLATE_NOTE = "模板降级：本段 AI 叙述未生成，以下要点由程序从事实包直出。"

#: 模型链走复盘家族的默认链（周报同源）；段落不设角色，吃模块缺省
REPORT_MODULE = "senate"


def section_snapshots(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """事实包 → 五段快照（只搬运、不再算；null 原样保留）。"""
    portfolio = report.get("portfolio") or {}
    market = report.get("market") or {}
    behavior = report.get("behavior") or {}
    meta = {
        "period": report.get("period"),
        "label": report.get("label"),
        "start": report.get("start"),
        "end": report.get("end"),
        "generated_at": report.get("generated_at"),
        "trades_available": report.get("trades_available"),
    }
    snapshots: dict[str, dict[str, Any]] = {
        "overview": {
            **meta,
            "realized_count": portfolio.get("realized_count"),
            "realized_total": portfolio.get("realized_total"),
            "win_rate": portfolio.get("win_rate"),
            "reason": portfolio.get("reason"),
        },
        "portfolio": {
            **meta,
            "portfolio": portfolio,
            "notes": report.get("notes"),
        },
        "attribution": {
            **meta,
            "rows": report.get("attribution") or [],
        },
        "discipline": {
            **meta,
            "buckets": behavior.get("buckets") or [],
            "behavior_reason": behavior.get("reason"),
            "mistakes": report.get("mistakes") or [],
        },
        "market": {
            **meta,
            "market": market,
        },
    }
    return snapshots


def build_messages(
    section: str, label: str, metrics: dict[str, Any], prompt_text: str
) -> tuple[str, str]:
    """周期报告的 system / user 消息（与周报同构，只换时间粒度与段落集）。"""
    title = SECTION_TITLES.get(section, section)
    system = (
        f"{prompt_text}\n\n"
        "硬约束：\n"
        "1. 只输出一个 JSON 对象，字段仅限 narrative；\n"
        "2. 不得输出任何指标数字键（realized_total、win_rate 等），数字由程序计算；\n"
        "3. 不得编造或新算指标；引用数字时必须与给定快照逐字一致；\n"
        "4. 快照里为 null 的字段就直说「没有读数」，不要填空；\n"
        "5. 期间是已结束的历史区间（不是进行中），没有数据就是本机缺产物；\n"
        "6. 中文输出，narrative 为字符串数组。"
    )
    period_kind = str(metrics.get("period") or "")
    period_label = {"month": "月报", "quarter": "季报"}.get(period_kind, "年报")
    user = (
        f"周期：{label}（{period_label}，不是某一个交易日）\n"
        f"段落：{title}\n"
        "程序统计快照（只读）：\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"请基于快照写「{title}」这一段的期间叙述，输出 JSON。"
    )
    return system, user


def _template_lines(section: str, report: dict[str, Any]) -> list[str]:
    """五段各自的模板要点（复述事实包，不写新数字）。"""
    from ladderstock.analytics.period import template_section_lines

    return template_section_lines(section, report)


def _parse_narrative(raw: str) -> list[str] | None:
    """模型回包 → narrative 行；JSON 解析失败退回按行拆的纯文本。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines or None
    if isinstance(payload, dict):
        narrative = payload.get("narrative")
        if isinstance(narrative, list):
            lines = [str(item).strip() for item in narrative if str(item).strip()]
            return lines or None
        if isinstance(narrative, str) and narrative.strip():
            return [narrative.strip()]
    return None


def narrate(
    report: dict[str, Any],
    *,
    root: Any = None,
    config_root: Any = None,
    config: fallback.AiConfig | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """五段叙述：配置了凭据走模型链，失败退模板；未配置整份模板。

    返回结构：

    - ``sections``：五段 `{name,title,mode,model,degraded,narrative,notes,failure}`；
    - ``configured`` / ``degraded`` / ``failed`` / ``models`` / ``ai_failures``。
    """
    from ladderstock.core.boundary import BoundaryViolation
    from ladderstock.errors import LadderStockError

    try:
        cfg = config or fallback.load_config(config_root)
    except (LadderStockError, BoundaryViolation):
        # ai.yaml 缺失 / 损坏（BoundaryViolation 是 RuntimeError 族，不是
        # LadderStockError）：按「未配置」兑底（B 包第一阶段契约），不把报告炸掉
        cfg = None
    configured = cfg is not None and fallback.is_configured(cfg)
    chain: list[str] = []
    if configured and cfg is not None:
        chain = [target.label for target in cfg.chain_for(REPORT_MODULE, None)]

    snapshots = section_snapshots(report)
    prompt_text = load_prompt(REPORT_PROMPT)

    client_instance = client
    if client_instance is None and configured:
        from ladderstock.ai.cli_provider import build_chat_client

        client_instance = build_chat_client(cfg)

    sections: list[dict[str, Any]] = []
    ai_failures: list[dict[str, Any]] = []
    used_models: list[str] = []
    degraded = False
    failed = False

    for name in SECTION_TITLES:
        narrative: list[str] | None = None
        model_used: str | None = None
        failure: dict[str, Any] | None = None

        if configured and client_instance is not None:
            snapshot = snapshots[name]
            system, user = build_messages(name, str(report.get("label")), snapshot, prompt_text)
            last_error: Exception | None = None
            for model in chain:
                try:
                    raw = client_instance.complete(model, system, user, json_mode=True)
                except Exception as exc:  # noqa: BLE001 - 与编排器同口径：分类后继续
                    last_error = exc
                    failure = {"model": model, "kind": type(exc).__name__, "message": str(exc)}
                    ai_failures.append({"section": name, **failure})
                    continue
                narrative = _parse_narrative(raw)
                if narrative is None:
                    failure = {
                        "model": model,
                        "kind": "schema",
                        "message": "回包不是可用的 narrative JSON",
                    }
                    ai_failures.append({"section": name, **failure})
                    continue
                model_used = model
                if model not in used_models:
                    used_models.append(model)
                failure = None
                break
            if narrative is None and last_error is not None:
                failed = True

        if narrative is None:
            narrative = _template_lines(name, report)
            section_degraded = False
            notes = [TEMPLATE_NOTE]
            if failure is not None and configured:
                degraded = True
                chain_label = " / ".join(chain) or "无可用模型"
                notes.append(f"模型失败：{failure.get('kind')}（{chain_label}）")
                section_degraded = True
            elif not configured:
                notes.append("未配置模型凭据，本报告由模板直出（配置后重跑即可获得 AI 叙述）。")
            else:
                section_degraded = False
            sections.append(
                {
                    "name": name,
                    "title": SECTION_TITLES[name],
                    "mode": "template",
                    "model": None,
                    "degraded": section_degraded,
                    "narrative": narrative,
                    "notes": notes,
                    "failure": failure,
                }
            )
        else:
            sections.append(
                {
                    "name": name,
                    "title": SECTION_TITLES[name],
                    "mode": "ai",
                    "model": model_used,
                    "degraded": False,
                    "narrative": narrative,
                    "notes": [],
                    "failure": None,
                }
            )

    return {
        "sections": sections,
        "configured": configured,
        "degraded": degraded,
        "failed": failed,
        "models": used_models,
        "chain": chain,
        "ai_failures": ai_failures,
    }


__all__ = [
    "REPORT_MODULE",
    "REPORT_PROMPT",
    "SECTION_TITLES",
    "build_messages",
    "narrate",
    "section_snapshots",
]
