"""`run_once` 端到端（注入假取数）：触发 → 收件箱 + 渠道 + 状态/历史落库。

不碰真实行情：`price_fetch` 直接回内存字典；渠道投递注入假 poster。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ladderstock.alert import store
from ladderstock.alert.channels import deliver as real_deliver
from ladderstock.alert.run import run_once
from ladderstock.journal import inbox as inbox_store


def _prices(codes, *, latest: float | None = 10.0):
    def fetch(wanted: list[str]) -> dict:
        return {
            code: {
                "available": latest is not None,
                "latest_price": latest,
                "change_pct": None,
                "price_as_of": "2026-11-21T15:00:00",
                "price_reason": None if latest is not None else "上游限流",
            }
            for code in wanted
        }

    return fetch


def test_run_once_skips_without_rules(tmp_path: Path) -> None:
    report = run_once(root=tmp_path, price_fetch=_prices([]))
    assert report["status"] == "skipped"
    assert report["checked"] == 0


def test_run_once_fires_and_records(tmp_path: Path) -> None:
    store.add_rule(
        "price_below", "600000", 10.5, channels=["console"], note="止损线", root=tmp_path
    )
    fired_deliveries: list[dict] = []

    def fake_deliver(channel, title, body, **kwargs):
        from ladderstock.watch.notifier import DeliveryResult

        fired_deliveries.append({"channel": channel.get("id"), "title": title, "body": body})
        return DeliveryResult(channel=str(channel.get("id")), ok=True, detail="ok", sent_at="t")

    first = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 0, 0),
        price_fetch=_prices(["600000"], latest=10.0),
        deliverer=fake_deliver,
    )
    # 600000 现价 10.0 < 10.5：触发
    assert first["status"] == "ok", first
    assert len(first["fired"]) == 1
    assert first["fired"][0]["value"] == 10.0
    assert fired_deliveries[0]["channel"] == "console"
    assert "止损线" in fired_deliveries[0]["body"]

    # 收件箱有留痕，kind=alert，按日去重
    items = inbox_store.list_items(limit=10, root=tmp_path)
    assert any(item.get("kind") == "alert" for item in items)

    # 状态进冷却；历史有记录
    rule_id = first["fired"][0]["rule_id"]
    assert store.get_state(rule_id, root=tmp_path)["status"] == "cooldown"
    assert len(store.list_history(root=tmp_path)) == 1

    # 同轮再跑：冷却期内不重报
    second = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 5, 0),
        price_fetch=_prices(["600000"], latest=10.0),
        deliverer=fake_deliver,
    )
    assert second["status"] == "ok"
    assert second["fired"] == []

    # 条件离开（价格回升）→ 重臂；再跌回 → 新事件再报一次
    run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 10, 0),
        price_fetch=_prices(["600000"], latest=11.0),
        deliverer=fake_deliver,
    )
    third = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 15, 0),
        price_fetch=_prices(["600000"], latest=10.0),
        deliverer=fake_deliver,
    )
    assert len(third["fired"]) == 1


def test_run_once_marks_partial_on_missing_data(tmp_path: Path) -> None:
    store.add_rule("price_above", "600000", 1.0, root=tmp_path)
    report = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 0, 0),
        price_fetch=_prices(["600000"], latest=None),
    )
    assert report["status"] == "partial"
    assert report["fired"] == []
    entry = report["results"][0]
    assert entry["status"] == "no_data"
    assert "限流" in entry["detail"]


def test_run_once_reports_missing_channel_as_degraded(tmp_path: Path) -> None:
    store.add_rule("price_above", "600000", 1.0, channels=["ghost"], root=tmp_path)
    report = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 0, 0),
        price_fetch=_prices(["600000"], latest=10.0),
        deliverer=real_deliver,  # 没有外部渠道，真实 deliver 只会收到 ghost → 走「渠道不存在」
    )
    assert report["status"] == "partial"
    fired = report["fired"][0]
    assert fired["deliveries"][0]["channel"] == "ghost"
    assert fired["deliveries"][0]["ok"] is False


def test_run_once_pnl_uses_position_cost(tmp_path: Path) -> None:
    from ladderstock.journal import position as position_store

    # 持仓走正规写入口（journal/position.add_position），隐私库与本轮求值同源。
    position_store.add_position("600000", 10.5, 1000, entry_date="2026-11-01", root=tmp_path)

    store.add_rule("pnl_drop", "600000", 5.0, root=tmp_path)
    report = run_once(
        root=tmp_path,
        now=datetime(2026, 11, 21, 14, 0, 0),
        price_fetch=_prices(["600000"], latest=9.9),  # −5.71%
    )
    assert len(report["fired"]) == 1
    assert report["fired"][0]["value"] is not None
    assert report["fired"][0]["value"] < -5.5
