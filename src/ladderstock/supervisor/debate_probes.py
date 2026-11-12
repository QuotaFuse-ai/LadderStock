"""辩论阶段探针（QUOT-563）：StockSenate 最近一次辩论 → 三阶段 StageSample。

三阶段（schema 唯一来源在 `core/stage_events.py`；module=debate、
module_class=cog、phase 1..3）：

- `debate_speak`：五个辩论角色（看多方 / 看空方 / 激进 / 中立 / 保守风控）
  发言完成情况——缺席或发言没有可用内容都如实点名；
- `debate_clash`：多空之间有没有真实反驳往来（rebuttals 计数）；
- `debate_converge`：辩论是否在轮次内收敛出结论（convergence_reason）。

数据源（只读）：StockSenate 落盘报告 `<senate_home>/reports/<run_id>/state.json`
（run 结束时一次写入），取 mtime 最新一份里的 `debate` 节。运行中的辩论进度
只存在 StockSenate WebUI 内存里且要鉴权，本探针**如实按「最近一次完成的辩论」
口径**观测——绝不触发辩论、绝不猜运行中状态、绝不编造进度（DEC-006）：

- 还没有任何辩论报告 / 报告里没有辩论记录 / 文件读不出来 → down；
- 角色缺席或空发言、跑满轮次未收敛、收敛检测被关、快照陈旧（> 7 天）→ warn；
- 只有证据齐备才 ok。任何读取异常都折叠成如实 down，绝不抛给观测线程。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_WARN,
    STAGE_DEBATE_CLASH,
    STAGE_DEBATE_CONVERGE,
    STAGE_DEBATE_SPEAK,
    StageSample,
)

#: StockSenate 的家目录环境变量（与 stock_senate.config.loader.home_dir 同名约定）
SENATE_HOME_ENV = "STOCKSENATE_HOME"

#: 快照陈旧阈值：最近一次辩论距今超过该天数 → ok 降 warn（与数据管线同口径）
DEBATE_STALE_DAYS = 7

#: 五个辩论角色：side → 白话称呼（reason 点名用，从不打印 argv / 路径 / 技术栈）
_DEBATE_ROLES: tuple[tuple[str, str], ...] = (
    ("bull", "看多方"),
    ("bear", "看空方"),
    ("aggressive", "激进风控"),
    ("neutral", "中立风控"),
    ("conservative", "保守风控"),
)

_BULL_BEAR_SIDES = ("bull", "bear")

#: 收敛状态 → (health, 白话模板)；未知的收敛状态一律如实 down，不猜
_CONVERGENCE: dict[str, tuple[str, str]] = {
    "converged": (HEALTH_OK, "辩论在{rounds}内收敛出结论"),
    "max_rounds": (HEALTH_WARN, "辩论跑满{rounds}仍未收敛，按轮次上限结束"),
    "convergence_disabled": (
        HEALTH_WARN,
        "收敛检测已关闭，辩论按固定轮次跑完，无法判断是否收敛",
    ),
}


def senate_home(env: Mapping[str, str] | None = None) -> Path:
    """StockSenate 家目录：`STOCKSENATE_HOME` 优先，缺省 `~/.stock_senate`。"""
    values = os.environ if env is None else env
    raw = (values.get(SENATE_HOME_ENV) or "").strip()
    return Path(raw) if raw else Path.home() / ".stock_senate"


def build_debate_probes(
    *,
    home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stale_days: int = DEBATE_STALE_DAYS,
) -> dict[str, Callable[[], StageSample]]:
    """三路只读辩论探针（QUOT-563）。

    `home` 显式给出时直接观测该目录（测试注入）；否则按 `senate_home(env)`
    解析。每次探测都重新读盘（辩论随时可能新增报告，不做缓存）；module /
    module_class / phase 由注册表统一补齐，本模块只产出 health + 白话 reason。
    """
    root = Path(home) if home is not None else senate_home(env)

    def _load() -> tuple[str, datetime | None, dict[str, Any]]:
        """读最新一份辩论报告：状态 ∈ missing / unreadable / ok + mtime + debate 节。"""
        reports = root / "reports"
        try:
            candidates = list(reports.glob("*/state.json"))
        except OSError:
            return "unreadable", None, {}
        latest: Path | None = None
        latest_stamp = -1.0
        for path in candidates:
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            if stamp >= latest_stamp:
                latest, latest_stamp = path, stamp
        if latest is None:
            return "missing", None, {}
        try:
            doc = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "unreadable", None, {}
        debate = doc.get("debate") if isinstance(doc, dict) else None
        mtime = datetime.fromtimestamp(latest_stamp).astimezone()
        return "ok", mtime, (debate if isinstance(debate, dict) else {})

    def _section(debate: dict[str, Any], key: str) -> dict[str, Any] | None:
        node = debate.get(key)
        return node if isinstance(node, dict) else None

    def _argument_of(section: dict[str, Any] | None, side: str) -> dict[str, Any] | None:
        if section is None:
            return None
        items = section.get("arguments")
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("side") or "") == side:
                return item
        return None

    def _spoke(argument: dict[str, Any] | None) -> bool | None:
        """角色发言三态：True=有可用内容；False=发了言但内容为空；None=缺席。"""
        if argument is None:
            return None
        points = argument.get("points") or []
        rebuttals = argument.get("rebuttals") or []
        return bool(points or rebuttals)

    def _rounds(section: dict[str, Any] | None) -> str:
        value = section.get("rounds_used") if section is not None else None
        return f"{value}轮" if isinstance(value, int) else "既定轮次"

    def _stale_line(mtime: datetime | None) -> str | None:
        if mtime is None:
            return None
        age = datetime.now().astimezone() - mtime
        if age <= timedelta(days=max(0, stale_days)):
            return None
        return f"最近一次辩论停在 {mtime.date().isoformat()}"

    def _finish(stage: str, health: str, reason: str, mtime: datetime | None) -> StageSample:
        """陈旧叠加：ok 降 warn；warn 保持 warn 但补陈旧说明；down 原样（未跑没有「旧」）。"""
        stale = _stale_line(mtime)
        if stale is None:
            return _sample(stage, health, reason)
        if health == HEALTH_OK:
            return _sample(stage, HEALTH_WARN, f"{stale}，{reason}")
        if health == HEALTH_WARN:
            return _sample(stage, HEALTH_WARN, f"{reason}（{stale}）")
        return _sample(stage, health, reason)

    def _probe_speak() -> StageSample:
        status, mtime, debate = _load()
        if status == "missing":
            return _sample(STAGE_DEBATE_SPEAK, HEALTH_DOWN, "还没有任何辩论报告（辩论从未运行）")
        if status == "unreadable":
            return _sample(
                STAGE_DEBATE_SPEAK,
                HEALTH_DOWN,
                "最近一次辩论报告读不出来（文件损坏或不可读）",
            )
        bull = _section(debate, "bull_bear")
        risk = _section(debate, "risk")
        if bull is None and risk is None:
            return _sample(STAGE_DEBATE_SPEAK, HEALTH_DOWN, "最近一次辩论报告里没有可读的辩论记录")
        spoken: list[str] = []
        absent: list[str] = []
        empty: list[str] = []
        for side, label in _DEBATE_ROLES:
            section = bull if side in _BULL_BEAR_SIDES else risk
            state = _spoke(_argument_of(section, side))
            if state is None:
                absent.append(label)
            elif state is False:
                empty.append(label)
            else:
                spoken.append(label)
        if absent or empty:
            parts: list[str] = []
            if absent:
                parts.append(f"{'、'.join(absent)}没有发言")
            if empty:
                parts.append(f"{'、'.join(empty)}的发言没有可用内容")
            reason = f"{len(spoken)}/5 个辩论角色完成发言：" + "；".join(parts)
            return _finish(STAGE_DEBATE_SPEAK, HEALTH_WARN, reason, mtime)
        reason = f"5 个辩论角色都完成了发言（多空辩论共{_rounds(bull)}）"
        return _finish(STAGE_DEBATE_SPEAK, HEALTH_OK, reason, mtime)

    def _rebuttal_count(section: dict[str, Any] | None, side: str) -> int | None:
        argument = _argument_of(section, side)
        if argument is None:
            return None
        rebuttals = argument.get("rebuttals") or []
        return len(rebuttals) if isinstance(rebuttals, list) else 0

    def _probe_clash() -> StageSample:
        status, mtime, debate = _load()
        if status == "missing":
            return _sample(STAGE_DEBATE_CLASH, HEALTH_DOWN, "还没有任何辩论报告（辩论从未运行）")
        if status == "unreadable":
            return _sample(
                STAGE_DEBATE_CLASH,
                HEALTH_DOWN,
                "最近一次辩论报告读不出来（文件损坏或不可读）",
            )
        bull = _section(debate, "bull_bear")
        if bull is None:
            return _sample(
                STAGE_DEBATE_CLASH,
                HEALTH_DOWN,
                "最近一次辩论报告里没有可读的多空辩论记录",
            )
        counts = [_rebuttal_count(bull, side) for side in _BULL_BEAR_SIDES]
        if any(count is None for count in counts):
            return _finish(
                STAGE_DEBATE_CLASH,
                HEALTH_WARN,
                "多空辩论记录不完整，看不到双方的完整发言，无法确认是否交锋",
                mtime,
            )
        total = sum(count or 0 for count in counts)
        if total > 0:
            return _finish(
                STAGE_DEBATE_CLASH,
                HEALTH_OK,
                f"多空之间出现了 {total} 条反驳，辩论有真实交锋",
                mtime,
            )
        return _finish(
            STAGE_DEBATE_CLASH,
            HEALTH_WARN,
            "多空双方都没有提出反驳，辩论没有产生交锋",
            mtime,
        )

    def _probe_converge() -> StageSample:
        status, mtime, debate = _load()
        if status == "missing":
            return _sample(STAGE_DEBATE_CONVERGE, HEALTH_DOWN, "还没有任何辩论报告（辩论从未运行）")
        if status == "unreadable":
            return _sample(
                STAGE_DEBATE_CONVERGE,
                HEALTH_DOWN,
                "最近一次辩论报告读不出来（文件损坏或不可读）",
            )
        bull = _section(debate, "bull_bear")
        if bull is None:
            return _sample(
                STAGE_DEBATE_CONVERGE,
                HEALTH_DOWN,
                "最近一次辩论报告里没有可读的多空辩论记录",
            )
        key = str(bull.get("convergence_reason") or "").strip()
        if key not in _CONVERGENCE:
            return _sample(STAGE_DEBATE_CONVERGE, HEALTH_DOWN, "辩论结论状态缺失，无法判断是否收敛")
        health, template = _CONVERGENCE[key]
        return _finish(STAGE_DEBATE_CONVERGE, health, template.format(rounds=_rounds(bull)), mtime)

    return {
        STAGE_DEBATE_SPEAK: _probe_speak,
        STAGE_DEBATE_CLASH: _probe_clash,
        STAGE_DEBATE_CONVERGE: _probe_converge,
    }


def _sample(stage: str, health: str, reason: str) -> StageSample:
    return StageSample.of(stage, health=health, reason=reason)


__all__ = [
    "DEBATE_STALE_DAYS",
    "SENATE_HOME_ENV",
    "build_debate_probes",
    "senate_home",
]
