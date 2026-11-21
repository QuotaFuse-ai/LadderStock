"""提醒引擎单测：规则校验 / 存储 CRUD / 求值状态机 / 渠道投递 / run_once。

不碰真实行情与真实网络：
- 求值与状态机是纯函数，直接喂 `SymbolContext`；
- 渠道投递注入假 `poster` / 假 `smtp_send`，断言 payload 形状而不是远端回复；
- `run_once` 注入假 `price_fetch`，历史走临时数据根里手摆的 `daily_k` 分区
  ——太重，v1 直接不配均线/量能规则，只测价格与 pnl 路径。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ladderstock.alert import channels as channels_mod
from ladderstock.alert import evaluate as evaluator
from ladderstock.alert import rules as rules_mod
from ladderstock.alert import store
from ladderstock.errors import EXIT_USAGE, LadderStockError

# --------------------------------------------------------------------------- #
# rules.validate_rule
# --------------------------------------------------------------------------- #


def test_validate_rule_normalizes_and_rejects() -> None:
    clean = rules_mod.validate_rule("Price_Above", " 600000 ", 10.45)
    assert clean["kind"] == "price_above"
    assert clean["symbol"] == "600000"
    assert clean["threshold"] == 10.45
    assert clean["cooldown_min"] == 60

    with pytest.raises(LadderStockError) as err:
        rules_mod.validate_rule("nope", "600000", 1.0)
    assert err.value.code == EXIT_USAGE
    assert "未知规则类型" in err.value.message

    # 前缀形式 / 非 6 位代码都要拒
    for bad in ("sh600000", "60000", "6000006", "AAPL"):
        with pytest.raises(LadderStockError):
            rules_mod.validate_rule("price_above", bad, 1.0)

    # B 股与 core/portfolio 同口径拒收
    with pytest.raises(LadderStockError) as err:
        rules_mod.validate_rule("price_above", "900001", 1.0)
    assert "B 股" in err.value.message

    # 正阈值类型：倍数 / 百分比必须为正
    with pytest.raises(LadderStockError):
        rules_mod.validate_rule("volume_spike", "600000", -2)
    with pytest.raises(LadderStockError):
        rules_mod.validate_rule("pnl_drop", "600000", 0)

    # 均线类忽略 threshold，但 window 要在界内
    assert rules_mod.validate_rule("ma_break_down", "600000", 0.0)["window"] == 20
    with pytest.raises(LadderStockError):
        rules_mod.validate_rule("ma_break_down", "600000", 0.0, window=1)


def test_describe_rule_says_human_words() -> None:
    assert "跌破 10.45" in rules_mod.describe_rule(
        {"kind": "price_below", "symbol": "600000", "threshold": 10.45}
    )
    assert "MA20" in rules_mod.describe_rule(
        {"kind": "ma_break_down", "symbol": "600000", "window": 20}
    )
    assert "−5%" in rules_mod.describe_rule(
        {"kind": "pnl_drop", "symbol": "600000", "threshold": 5.0}
    )


# --------------------------------------------------------------------------- #
# store CRUD
# --------------------------------------------------------------------------- #


def test_rule_crud_roundtrip(tmp_path: Path) -> None:
    created = store.add_rule(
        "price_below", "600000", 10.2, channels=["console"], note="止损线", root=tmp_path
    )
    assert created["enabled"] is True
    assert created["channels"] == ["console"]

    listed = store.list_rules(root=tmp_path)
    assert [item["id"] for item in listed] == [created["id"]]

    updated = store.update_rule(created["id"], threshold=10.5, root=tmp_path)
    assert updated["threshold"] == 10.5
    # 改非法组合当场拒，且不留半改状态
    with pytest.raises(LadderStockError):
        store.update_rule(created["id"], threshold=-1, root=tmp_path)
    assert store.get_rule(created["id"], root=tmp_path)["threshold"] == 10.5

    toggled = store.toggle_rule(created["id"], False, root=tmp_path)
    assert toggled["enabled"] is False
    assert store.list_rules(root=tmp_path, enabled_only=True) == []

    removed = store.remove_rule(created["id"], root=tmp_path)
    assert removed["removed"] is True
    with pytest.raises(LadderStockError):
        store.get_rule(created["id"], root=tmp_path)


def test_state_upsert_and_history_cap(tmp_path: Path) -> None:
    rule = store.add_rule("price_above", "600000", 1.0, root=tmp_path)
    store.set_state(
        rule["id"],
        "cooldown",
        last_value=10.7,
        fired_at="2026-11-21T15:05:00",
        cooldown_until="2026-11-21T16:05:00",
        root=tmp_path,
    )
    state = store.get_state(rule["id"], root=tmp_path)
    assert state["status"] == "cooldown"
    assert state["last_value"] == 10.7

    # 幂等 upsert：再写一次不报重复
    store.set_state(
        rule["id"], "armed", last_value=10.7, fired_at=None, cooldown_until=None, root=tmp_path
    )
    assert store.get_state(rule["id"], root=tmp_path)["status"] == "armed"

    for index in range(store.HISTORY_CAP + 10):
        store.append_history(rule["id"], f"t{index}", "body", [], root=tmp_path)
    assert len(store.list_history(limit=500, root=tmp_path)) == store.HISTORY_CAP


def test_channel_crud_and_masking(tmp_path: Path) -> None:
    channel = store.add_channel(
        "ding-main",
        "dingtalk",
        name="钉钉主群",
        config={"url": "https://oapi.dingtalk.com/robot/send?access_token=SECRET_TOKEN"},
        root=tmp_path,
    )
    public = channels_mod.public_config(channel["kind"], channel["config"])
    assert "SECRET_TOKEN" not in str(public)
    assert public["url"].startswith("https://oapi.dingtalk.com/")
    assert public["url"].endswith("***")

    # 写面校验：webhook 家族必须有 http(s) url
    with pytest.raises(LadderStockError):
        store.add_channel("bad-1", "webhook", config={"url": "ftp://x"}, root=tmp_path)
    with pytest.raises(LadderStockError):
        store.add_channel("bad-2", "email", config={"to_addrs": []}, root=tmp_path)

    # id 非法字符当场拒（不进库）
    with pytest.raises(LadderStockError):
        store.add_channel("bad id!", "webhook", config={"url": "https://x"}, root=tmp_path)

    updated = store.update_channel(
        "ding-main",
        enabled=False,
        config={"url": "https://oapi.dingtalk.com/robot/send?access_token=TWO"},
        root=tmp_path,
    )
    assert updated["enabled"] is False
    assert updated["config"]["url"].endswith("TWO")

    duplicate = store.remove_channel("ding-main", root=tmp_path)
    assert duplicate["removed"] is True


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #


def _ctx(**overrides: object) -> evaluator.SymbolContext:
    base: dict = {
        "code": "600000",
        "latest_price": 10.0,
        "prev_close": 9.8,
        "closes": [9.0, 9.2, 9.4, 9.6, 9.8, 10.0],
        "volumes": [1000.0, 1200.0, 1100.0, 1300.0, 1000.0, 5000.0],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return evaluator.SymbolContext(**base)


def test_price_rules_respect_missing_data() -> None:
    rule = {"kind": "price_above", "threshold": 9.5}
    hit = evaluator.evaluate_rule(rule, _ctx(latest_price=10.0))
    assert hit.triggered is True and hit.status == "ok"

    miss = evaluator.evaluate_rule(rule, _ctx(latest_price=None, price_reason="上游限流"))
    assert miss.triggered is False
    assert miss.status == "no_data"
    assert "限流" in miss.detail


def test_ma_rules_need_full_window() -> None:
    rule = {"kind": "ma_break_down", "threshold": 0.0, "window": 20}
    outcome = evaluator.evaluate_rule(rule, _ctx())  # closes 只有 6 根
    assert outcome.status == "no_data"
    assert "不足" in outcome.detail


def test_volume_spike_ratio_and_noise_floor() -> None:
    rule = {"kind": "volume_spike", "threshold": 2.0, "window": 5}
    # 前 5 日均量 (1000+1200+1100+1300+1000)/5 = 1120，当日 5000 → 4.46 倍
    hit = evaluator.evaluate_rule(
        rule, _ctx(volumes=[1000.0, 1200.0, 1100.0, 1300.0, 1000.0, 5000.0])
    )
    assert hit.triggered is True
    assert hit.value is not None and hit.value > 4.4

    # 均量低于噪声下限：倍数无意义 → no_data 而不是触发
    quiet = evaluator.evaluate_rule(
        rule, _ctx(latest_price=10.0, volumes=[10.0, 12.0, 11.0, 13.0, 10.0, 500.0])
    )
    assert quiet.status == "no_data"
    assert "噪声" in quiet.detail


def test_pnl_rules_require_cost() -> None:
    rule = {"kind": "pnl_drop", "threshold": 5.0}
    no_cost = evaluator.evaluate_rule(rule, _ctx(unit_cost=None))
    assert no_cost.status == "no_data"
    assert "成本" in no_cost.detail

    # 成本 10.5、现价 10.0 → −4.76%，未过 5% 线：不触发
    near = evaluator.evaluate_rule(rule, _ctx(latest_price=10.0, unit_cost=10.5))
    assert near.triggered is False and near.value is not None
    assert near.value == pytest.approx(-4.761, rel=1e-2)

    # 现价 9.9、成本 10.5 → −5.71%：触发
    hit = evaluator.evaluate_rule(rule, _ctx(latest_price=9.9, unit_cost=10.5))
    assert hit.triggered is True


def test_should_fire_reports_once_per_crossing() -> None:
    rule = {"cooldown_min": 60}
    outcome_hit = evaluator.EvalOutcome(True, "ok", 10.5, "x")
    outcome_off = evaluator.EvalOutcome(False, "ok", 10.0, "x")
    now = datetime(2026, 11, 21, 15, 0, 0)

    # armed → 报并进冷却
    fire, status, until = evaluator.should_fire(rule, outcome_hit, {"status": "armed"}, now=now)
    assert fire is True and status == "cooldown" and until is not None

    # 冷却期内再触发：不报
    state = {"status": "cooldown", "cooldown_until": "2026-11-21T16:00:00"}
    fire, status, _ = evaluator.should_fire(rule, outcome_hit, state, now=now)
    assert fire is False

    # 冷却已过、条件仍成立：依然不报（等重臂）
    state = {"status": "cooldown", "cooldown_until": "2026-11-21T14:00:00"}
    fire, _, _ = evaluator.should_fire(rule, outcome_hit, state, now=now)
    assert fire is False

    # 条件离开 → 重臂回 armed
    fire, status, _ = evaluator.should_fire(rule, outcome_off, state, now=now)
    assert fire is False and status == "armed"

    # 重臂后再次越线 → 报（新事件）
    fire, _, _ = evaluator.should_fire(rule, outcome_hit, {"status": "armed"}, now=now)
    assert fire is True


# --------------------------------------------------------------------------- #
# channels.deliver（注入假出站，不碰网络）
# --------------------------------------------------------------------------- #


def test_deliver_dingtalk_checks_business_code() -> None:
    posted: list[tuple[str, dict]] = []

    def fake_post(url: str, payload: dict) -> tuple[int, dict]:
        posted.append((url, payload))
        return 200, {"errcode": 0, "errmsg": "ok"}

    channel = {
        "id": "ding-main",
        "kind": "dingtalk",
        "config": {"url": "https://oapi.dingtalk.com/robot/send?access_token=T"},
    }
    result = channels_mod.deliver(channel, "标题", "正文", poster=fake_post)
    assert result.ok is True
    assert posted[0][1]["msgtype"] == "text"
    assert "标题" in posted[0][1]["text"]["content"]

    # errcode != 0：HTTP 200 也不是送达
    def reject(url: str, payload: dict) -> tuple[int, dict]:
        return 200, {"errcode": 310000, "errmsg": "keyword not in content"}

    rejected = channels_mod.deliver(channel, "标题", "正文", poster=reject)
    assert rejected.ok is False
    assert rejected.degraded is True
    assert "310000" in rejected.detail


def test_deliver_webhook_generic_and_network_failure() -> None:
    channel = {"id": "hook", "kind": "webhook", "config": {"url": "https://example.com/hook"}}

    def ok_post(url: str, payload: dict) -> tuple[int, dict]:
        assert payload["title"] == "t"
        return 200, {}

    assert channels_mod.deliver(channel, "t", "b", poster=ok_post).ok is True

    def boom(url: str, payload: dict) -> tuple[int, dict]:
        raise OSError("connection refused")

    failed = channels_mod.deliver(channel, "t", "b", poster=boom)
    assert failed.ok is False and failed.degraded is True
    assert "connection refused" in failed.detail


def test_deliver_email_reads_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = {
        "id": "mail",
        "kind": "email",
        "config": {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "from_addr": "board@example.com",
            "to_addrs": ["me@example.com"],
            "use_starttls": False,
            "user": "board@example.com",
            "password_env": "LDST_TEST_SMTP_PASS",
        },
    }
    sent: list[tuple[str, int, object]] = []

    def fake_smtp(host: str, port: int, message, user, password) -> None:
        sent.append((host, port, message))
        assert user == "board@example.com"
        assert password == "s3cret"

    # 缺环境变量 → 降级并点名环境变量名，不猜
    monkeypatch.delenv("LDST_TEST_SMTP_PASS", raising=False)
    missing = channels_mod.deliver(channel, "t", "b", smtp_send=fake_smtp)
    assert missing.ok is False
    assert "LDST_TEST_SMTP_PASS" in missing.detail

    monkeypatch.setenv("LDST_TEST_SMTP_PASS", "s3cret")
    ok = channels_mod.deliver(channel, "t", "b", smtp_send=fake_smtp)
    assert ok.ok is True
    assert sent[0][0] == "smtp.example.com"
    assert sent[0][1] == 465


def test_deliver_console_delegates_to_local_notifier() -> None:
    channel = {"id": "console", "kind": "console", "config": {}}
    result = channels_mod.deliver(channel, "标题", "正文")
    assert result.ok is True
    assert result.channel == "console"
