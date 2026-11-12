"""辩论阶段探针（QUOT-563）：`build_debate_probes` 三路只读观测的真实口径。

钉住：

| 场景 | 期望 |
|---|---|
| schema | 三探针 module=debate / class=cog / phase 1..3，与 `core/stage_events` 注册表一致 |
| debate_speak | 五角色全发言 → ok；缺席 / 空发言 → warn 点名；无报告 / 读不出 / 无辩论记录 → down |
| debate_clash | rebuttals > 0 → ok；双方零反驳 → warn；记录不完整 → warn；无报告 → down |
| debate_converge | converged → ok；max_rounds / convergence_disabled → warn；未知状态 → down |
| 陈旧 | 快照 > 7 天：ok 降 warn、warn 补括注、down 原样（没跑过谈不上「旧」） |
| DEC-006 | 探针前后报告目录字节不变（只读，绝不触发辩论 / 不写盘） |
| home | `STOCKSENATE_HOME` env 优先；缺省 `~/.stock_senate`；测试显式注入 |

真实报告落 tmp 目录；不联网、不起进程。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_WARN,
    MODULE_CLASS_COG,
    STAGE_DEBATE_CLASH,
    STAGE_DEBATE_CONVERGE,
    STAGE_DEBATE_SPEAK,
    STAGE_REGISTRY,
)
from ladderstock.supervisor.debate_probes import (
    DEBATE_STALE_DAYS,
    SENATE_HOME_ENV,
    build_debate_probes,
    senate_home,
)

STALE_PAST_DAYS = DEBATE_STALE_DAYS + 3


def _argument(side: str, *, points: int = 1, rebuttals: int = 0) -> dict[str, Any]:
    return {
        "side": side,
        "points": [f"论点{i}" for i in range(points)],
        "rebuttals": [f"反驳{i}" for i in range(rebuttals)],
        "partial": False,
    }


def _debate_doc(
    *,
    bull: dict[str, Any] | None = None,
    bear: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    rounds_used: int = 2,
    convergence_reason: str = "converged",
) -> dict[str, Any]:
    bull_bear: dict[str, Any] | None = None
    if bull is not None or bear is not None:
        bull_bear = {
            "rounds_used": rounds_used,
            "convergence_reason": convergence_reason,
            "arguments": [arg for arg in (bull, bear) if arg is not None],
        }
    doc: dict[str, Any] = {}
    if bull_bear is not None:
        doc["bull_bear"] = bull_bear
    if risk is not None:
        doc["risk"] = risk
    return {"debate": doc} if doc else {}


def _full_risk() -> dict[str, Any]:
    return {
        "rounds_used": 2,
        "arguments": [
            _argument("aggressive"),
            _argument("neutral"),
            _argument("conservative"),
        ],
    }


def _write_report(home: Path, doc: dict[str, Any], *, run_id: str = "r1") -> None:
    run_dir = home / "reports" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(doc), encoding="utf-8")


def _age_report(home: Path, *, days: int) -> None:
    stamp = time.time() - days * 86400
    for path in (home / "reports").glob("*/state.json"):
        os.utime(path, (stamp, stamp))


def _probes(home: Path) -> dict[str, Any]:
    return build_debate_probes(home=home)


# -------------------------------------------------------------------- schema


def test_three_probes_follow_registry_module_class_phase() -> None:
    home = Path("__never_written__")
    probes = _probes(home)

    assert set(probes) == {STAGE_DEBATE_SPEAK, STAGE_DEBATE_CLASH, STAGE_DEBATE_CONVERGE}
    for stage, probe in probes.items():
        sample = probe()
        spec = STAGE_REGISTRY[stage]
        assert sample.stage == stage
        assert sample.module == spec.module == "debate"
        assert sample.module_class == spec.module_class == MODULE_CLASS_COG
        assert sample.phase == spec.phase
        assert sample.reason  # 兜底白话永不为空


def test_senate_home_env_and_default(tmp_path: Path) -> None:
    assert senate_home({SENATE_HOME_ENV: str(tmp_path)}) == tmp_path
    assert senate_home({}) == Path.home() / ".stock_senate"
    assert senate_home({SENATE_HOME_ENV: "  "}) == Path.home() / ".stock_senate"

    env_home = tmp_path / "via-env"
    env_home.mkdir()
    samples = {k: v() for k, v in build_debate_probes(env={SENATE_HOME_ENV: str(env_home)}).items()}
    assert all(item.health == HEALTH_DOWN and "从未" in item.reason for item in samples.values())


# ---------------------------------------------------------------- debate_speak


def test_speak_ok_when_all_five_roles_spoke(tmp_path: Path) -> None:
    home = tmp_path / "speak-ok"
    _write_report(
        home,
        _debate_doc(
            bull=_argument("bull", rebuttals=3),
            bear=_argument("bear", rebuttals=1),
            risk=_full_risk(),
        ),
    )

    sample = _probes(home)[STAGE_DEBATE_SPEAK]()

    assert sample.health == HEALTH_OK
    assert "5 个辩论角色都完成了发言" in sample.reason
    assert "2轮" in sample.reason


def test_speak_warns_and_names_missing_or_empty_roles(tmp_path: Path) -> None:
    absent = tmp_path / "speak-absent"
    _write_report(absent, _debate_doc(bull=_argument("bull"), risk=_full_risk()))

    sample = _probes(absent)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_WARN
    assert "4/5" in sample.reason and "看空方没有发言" in sample.reason

    empty = tmp_path / "speak-empty"
    _write_report(
        empty,
        _debate_doc(bull=_argument("bull"), bear=_argument("bear", points=0), risk=_full_risk()),
    )
    sample = _probes(empty)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_WARN
    assert "4/5" in sample.reason and "看空方的发言没有可用内容" in sample.reason

    missing_risk = tmp_path / "speak-risk"
    _write_report(missing_risk, _debate_doc(bull=_argument("bull"), bear=_argument("bear")))
    sample = _probes(missing_risk)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_WARN
    for label in ("激进风控", "中立风控", "保守风控"):
        assert label in sample.reason


def test_speak_down_without_report_or_debate_record(tmp_path: Path) -> None:
    home = tmp_path / "speak-missing"
    sample = _probes(home)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_DOWN
    assert "还没有任何辩论报告" in sample.reason

    unreadable = tmp_path / "speak-unreadable"
    _write_report(unreadable, {})
    (unreadable / "reports" / "r1" / "state.json").write_text("{broken", encoding="utf-8")
    sample = _probes(unreadable)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_DOWN
    assert "读不出来" in sample.reason

    no_debate = tmp_path / "speak-no-debate"
    _write_report(no_debate, {"other": 1})
    sample = _probes(no_debate)[STAGE_DEBATE_SPEAK]()
    assert sample.health == HEALTH_DOWN
    assert "没有可读的辩论记录" in sample.reason


# ---------------------------------------------------------------- debate_clash


def test_clash_ok_when_rebuttals_exist(tmp_path: Path) -> None:
    home = tmp_path / "clash-ok"
    _write_report(
        home,
        _debate_doc(
            bull=_argument("bull", rebuttals=9),
            bear=_argument("bear", rebuttals=5),
            risk=_full_risk(),
        ),
    )

    sample = _probes(home)[STAGE_DEBATE_CLASH]()

    assert sample.health == HEALTH_OK
    assert "14 条反驳" in sample.reason


def test_clash_warn_zero_rebuttals_or_incomplete(tmp_path: Path) -> None:
    quiet = tmp_path / "clash-quiet"
    _write_report(
        quiet,
        _debate_doc(bull=_argument("bull"), bear=_argument("bear"), risk=_full_risk()),
    )
    sample = _probes(quiet)[STAGE_DEBATE_CLASH]()
    assert sample.health == HEALTH_WARN
    assert "没有产生交锋" in sample.reason

    incomplete = tmp_path / "clash-incomplete"
    _write_report(incomplete, _debate_doc(bull=_argument("bull"), risk=_full_risk()))
    sample = _probes(incomplete)[STAGE_DEBATE_CLASH]()
    assert sample.health == HEALTH_WARN
    assert "看不到双方的完整发言" in sample.reason


def test_clash_down_without_bull_bear_record(tmp_path: Path) -> None:
    home = tmp_path / "clash-missing"
    assert _probes(home)[STAGE_DEBATE_CLASH]().health == HEALTH_DOWN

    no_bull = tmp_path / "clash-no-bull"
    _write_report(no_bull, {"debate": {"risk": _full_risk()}})
    sample = _probes(no_bull)[STAGE_DEBATE_CLASH]()
    assert sample.health == HEALTH_DOWN
    assert "没有可读的多空辩论记录" in sample.reason


# ------------------------------------------------------------ debate_converge


def test_converge_follows_convergence_reason(tmp_path: Path) -> None:
    def probe_with(reason: str, *, rounds: int = 2) -> Any:
        home = tmp_path / f"converge-{reason or 'none'}"
        _write_report(
            home,
            _debate_doc(
                bull=_argument("bull", rebuttals=2),
                bear=_argument("bear"),
                risk=_full_risk(),
                rounds_used=rounds,
                convergence_reason=reason,
            ),
        )
        return _probes(home)[STAGE_DEBATE_CONVERGE]()

    ok = probe_with("converged")
    assert ok.health == HEALTH_OK
    assert "收敛出结论" in ok.reason and "2轮" in ok.reason

    max_rounds = probe_with("max_rounds")
    assert max_rounds.health == HEALTH_WARN
    assert "仍未收敛" in max_rounds.reason

    disabled = probe_with("convergence_disabled")
    assert disabled.health == HEALTH_WARN
    assert "收敛检测已关闭" in disabled.reason

    unknown = probe_with("something_new")
    assert unknown.health == HEALTH_DOWN
    assert "无法判断" in unknown.reason

    assert probe_with("").health == HEALTH_DOWN


# -------------------------------------------------------------------- 陈旧


def test_staleness_downgrades_ok_but_keeps_down(tmp_path: Path) -> None:
    home = tmp_path / "stale"
    _write_report(
        home,
        _debate_doc(bull=_argument("bull", rebuttals=3), bear=_argument("bear"), risk=_full_risk()),
    )
    _age_report(home, days=STALE_PAST_DAYS)
    probes = _probes(home)

    speak = probes[STAGE_DEBATE_SPEAK]()
    assert speak.health == HEALTH_WARN, "陈旧把 ok 降为 warn"
    stale_date = (datetime.now().astimezone() - timedelta(days=STALE_PAST_DAYS)).date().isoformat()
    assert f"最近一次辩论停在 {stale_date}" in speak.reason

    clash = probes[STAGE_DEBATE_CLASH]()
    assert clash.health == HEALTH_WARN, "有交锋但陈旧：ok 同样降为 warn"
    assert "真实交锋" in clash.reason and clash.reason.startswith("最近一次辩论停在")

    home_warn = tmp_path / "stale-warn"
    _write_report(
        home_warn,
        _debate_doc(
            bull=_argument("bull", rebuttals=1),
            bear=_argument("bear"),
            risk=_full_risk(),
            convergence_reason="max_rounds",
        ),
    )
    _age_report(home_warn, days=STALE_PAST_DAYS)
    converge = _probes(home_warn)[STAGE_DEBATE_CONVERGE]()
    assert converge.health == HEALTH_WARN, "本来就是 warn：保持 warn 只补陈旧括注"
    assert "仍未收敛" in converge.reason and "（最近一次辩论停在" in converge.reason

    fresh = tmp_path / "fresh"
    _write_report(
        fresh,
        _debate_doc(bull=_argument("bull", rebuttals=3), bear=_argument("bear"), risk=_full_risk()),
    )
    assert _probes(fresh)[STAGE_DEBATE_SPEAK]().health == HEALTH_OK


# ------------------------------------------------------------------- DEC-006


def test_probes_are_readonly_never_write(tmp_path: Path) -> None:
    home = tmp_path / "readonly"
    _write_report(
        home,
        _debate_doc(bull=_argument("bull", rebuttals=3), bear=_argument("bear"), risk=_full_risk()),
    )

    def fingerprint(data: Path) -> dict[str, str]:
        return {
            str(item.relative_to(data)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(data.rglob("*"))
            if item.is_file()
        }

    before = fingerprint(home)
    for probe in _probes(home).values():
        probe()

    assert fingerprint(home) == before, "辩论探针在报告目录留下了写痕（违反 DEC-006）"
