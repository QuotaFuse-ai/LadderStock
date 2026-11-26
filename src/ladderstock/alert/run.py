"""提醒引擎入口：一次 `run` = 取数 → 求值 → 投递 → 落态（`alert_check` 同体）。

被三个位置共享：CLI `ldst alert run`、Web `POST /api/alert/run`、调度
`kind: alert_check`。所以本模块是**唯一的执行体**，三面只做参数透传与展示。

取数口径（全部复用现役链路，不新增第二条）：

- 现价：`core/watch_prices.fetch_prices()`（`spot_quote` → 同 peer 备源 → 不硬造）；
- 历史量价：`core/ladder.dataset_snapshot()` 读**本机缓存的 daily_k 分区**
  （走 Parquet 归档，不碰 DuckDB 写锁，`core/quotes.py` 同源）；
- 持仓成本：`journal/position.list_positions()`（本机隐私库，只读 `cost` 一个字段）。

缺数据的处理（平台红线的落地）：某规则的原料缺了 → 该规则记 `no_data` 并把原因
原样带回（`results[].reason`），**不触发、不报错、不中断其他规则**；一轮里出现
`no_data` 或投递降级时整体 `status="partial"`，其余为 `ok`。没有任何启用规则时
`status="skipped"`——「没配」和「没触发」是两种状态，不混。

触发即：收件箱落一条（`kind="alert"`，按 `规则id:交易日` 去重，当日重跑只刷新）+
渠道逐个投递（失败降级记录）+ 状态进冷却 + 触发历史入表。**inbox 是源头记录，
渠道只是投递**——外部渠道挂了，本机收件箱里仍然有这条提醒，修好渠道后下一轮
条件还在（未重臂）就不会再补发，这是「越线报一次」口径的既定代价，写入 DEC-087。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ladderstock.alert import evaluate as evaluator
from ladderstock.alert import rules as rule_model
from ladderstock.alert import store
from ladderstock.alert.channels import deliver as deliver_channel
from ladderstock.core.ladder import CODE_KEYS, as_float, dataset_snapshot, pick
from ladderstock.data import archive
from ladderstock.errors import LadderStockError
from ladderstock.journal import inbox as inbox_store
from ladderstock.journal import position as position_store
from ladderstock.watch.notifier import DeliveryResult

#: `daily_k` 记录里成交量字段的容忍键（上游命名不一，与 `watch_prices` 的做法同族）
VOLUME_KEYS: tuple[str, ...] = ("volume", "vol", "成交量", "trade_volume")
CLOSE_KEYS: tuple[str, ...] = ("close", "close_price", "收盘价", "closePrice")

#: 历史回看的最大自然日数（含周末；MA250 需要约 365 个自然日）
MAX_WALK_DAYS = 400

PriceFetch = Callable[[list[str]], dict[str, dict[str, Any]]]


def _registry(root: Path | None) -> Any:
    from ladderstock.data.provider_registry import ProviderRegistry

    return ProviderRegistry(root=root)


def _default_price_fetch(registry: Any) -> PriceFetch:
    from ladderstock.core.watch_prices import fetch_prices

    def _fetch(codes: list[str]) -> dict[str, dict[str, Any]]:
        strip = fetch_prices(registry, codes)
        return dict(strip.prices)

    return _fetch


def _cost_map(root: Path | None) -> dict[str, float]:
    """symbol → 持仓成本（journal/position 的 `cost`；读不动按没有持仓处理）。"""
    out: dict[str, float] = {}
    try:
        rows = position_store.list_positions(root=root)
    except Exception:  # noqa: BLE001 - 账本读不动不该拖垮整轮提醒
        return out
    for row in rows or []:
        code = str(row.get("code") or row.get("symbol") or "").strip().upper()
        cost = as_float(row.get("cost"))
        if code and cost is not None and cost > 0:
            out[code] = cost
    return out


def _history(
    symbols: set[str], lookback: int, *, root: Path | None
) -> dict[str, dict[str, list[float]]]:
    """从本机 `daily_k` 分区回看最多 `lookback` 根，攒出每只票的 closes / volumes。

    分区可能缺日（没同步 / 停牌）：只攒**真实有的分区**，不足就是不足（求值层
    会报 no_data），绝不拿隔周期的行情凑窗口。
    """
    out: dict[str, dict[str, list[float]]] = {
        code: {"closes": [], "volumes": []} for code in symbols
    }
    if not symbols or lookback <= 0:
        return out
    latest = archive.latest_day("daily_k", root=root)
    if latest is None:
        return out
    wanted = lookback
    cursor = latest
    first_ordinal = latest.toordinal()
    while wanted > 0 and (first_ordinal - cursor.toordinal()) < MAX_WALK_DAYS:
        if cursor.weekday() < 5 and archive.has_partition("daily_k", cursor, root=root):
            records, _backend = dataset_snapshot("daily_k", cursor, root=root)
            taken = False
            for record in records:
                code = str(pick(record, CODE_KEYS) or "").strip().upper()
                if code not in out:
                    continue
                close = as_float(pick(record, CLOSE_KEYS))
                volume = as_float(pick(record, VOLUME_KEYS))
                if close is not None and close > 0:
                    out[code]["closes"].append(close)
                    out[code]["volumes"].append(volume or 0.0)
                    taken = True
            if taken:
                wanted -= 1
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
    return out


def _build_contexts(
    symbols: list[str], rules: list[dict[str, Any]], *, root: Path | None, price_fetch: PriceFetch
) -> dict[str, evaluator.SymbolContext]:
    prices = price_fetch(symbols)
    costs = _cost_map(root)
    max_ma = max(
        (
            int(rule.get("window") or 0)
            for rule in rules
            if rule.get("kind") in ("ma_break_down", "ma_break_up")
        ),
        default=0,
    )
    has_volume = any(rule.get("kind") == "volume_spike" for rule in rules)
    max_vol_window = max(
        (int(rule.get("window") or 0) for rule in rules if rule.get("kind") == "volume_spike"),
        default=0,
    )
    lookback = max(max_ma, (max_vol_window + 1) if has_volume else 0)
    history = _history(set(symbols), lookback, root=root) if lookback > 0 else {}

    contexts: dict[str, evaluator.SymbolContext] = {}
    for code in symbols:
        record = prices.get(code) or {}
        latest = as_float(record.get("latest_price"))
        prev = as_float(record.get("prev_close"))
        history_rows = history.get(code) or {}
        contexts[code] = evaluator.SymbolContext(
            code=code,
            latest_price=latest,
            prev_close=prev,
            change_pct=as_float(record.get("change_pct")),
            price_reason=None
            if latest is not None
            else str(record.get("price_reason") or "现价未取到"),
            closes=list(reversed(history_rows.get("closes") or [])),
            volumes=list(reversed(history_rows.get("volumes") or [])),
            unit_cost=costs.get(code),
            as_of=record.get("price_as_of"),
        )
    return contexts


def _title_of(rule: dict[str, Any], outcome: evaluator.EvalOutcome) -> str:
    # describe_rule 已带 symbol（「600000 价格跌破 10.45」），标题不重复一遍代码。
    return f"提醒触发：{rule_model.describe_rule(rule)}"


def _body_of(
    rule: dict[str, Any],
    ctx: evaluator.SymbolContext,
    outcome: evaluator.EvalOutcome,
) -> list[str]:
    lines = [outcome.detail]
    if ctx.as_of:
        lines.append(f"行情时点：{ctx.as_of}")
    if ctx.unit_cost is not None:
        lines.append(f"持仓成本：{ctx.unit_cost:g}")
    if rule.get("note"):
        lines.append(f"备注：{rule['note']}")
    lines.append("（本条由提醒引擎生成，非投资建议）")
    return lines


def run_once(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    registry: Any = None,
    price_fetch: PriceFetch | None = None,
    deliverer: Callable[..., DeliveryResult] | None = None,
) -> dict[str, Any]:
    """跑一轮提醒。任何单点失败都折成结果字段，异常不向上抛（调度器要活着）。"""
    started = datetime.now().astimezone()
    moment = now or started
    rules = store.list_rules(root=root, enabled_only=True)
    base = {"ran_at": started.isoformat(timespec="seconds"), "checked": len(rules)}

    if not rules:
        return {
            **base,
            "status": "skipped",
            "detail": "没有启用的提醒规则（ldst alert rules add）",
            "fired": [],
            "results": [],
        }

    channels = {item["id"]: item for item in store.list_channels(root=root, include_disabled=False)}
    fetch = price_fetch or _default_price_fetch(registry or _registry(root))
    deliver = deliverer or deliver_channel

    symbols: list[str] = []
    for rule in rules:
        code = str(rule.get("symbol") or "").strip().upper()
        if code not in symbols:
            symbols.append(code)
    try:
        contexts = _build_contexts(symbols, rules, root=root, price_fetch=fetch)
    except LadderStockError as exc:
        return {
            **base,
            "status": "partial",
            "detail": f"取数失败：{exc.message}",
            "fired": [],
            "results": [],
        }

    fired: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    problems = 0
    for rule in rules:
        rule_id = str(rule.get("id"))
        ctx = contexts.get(str(rule.get("symbol") or "").strip().upper())
        if ctx is None:
            results.append(
                {
                    "rule_id": rule_id,
                    "symbol": rule.get("symbol"),
                    "triggered": False,
                    "reason": "上下文缺失",
                }
            )
            problems += 1
            continue
        outcome = evaluator.evaluate_rule(rule, ctx)
        state = store.get_state(rule_id, root=root)
        fire, new_status, cooldown_until = evaluator.should_fire(rule, outcome, state, now=moment)

        store.set_state(
            rule_id,
            new_status,
            last_value=outcome.value,
            fired_at=state.get("fired_at"),
            cooldown_until=cooldown_until,
            root=root,
        )
        results.append(
            {
                "rule_id": rule_id,
                "symbol": rule.get("symbol"),
                "kind": rule.get("kind"),
                "triggered": outcome.triggered,
                "status": outcome.status,
                "value": outcome.value,
                "detail": outcome.detail,
                "fired": fire,
                "state": new_status,
            }
        )
        if outcome.status == "no_data":
            problems += 1
        if not fire:
            continue

        title = _title_of(rule, outcome)
        body_lines = _body_of(rule, ctx, outcome)
        deliveries: list[dict[str, Any]] = []
        for channel_id in rule.get("channels") or []:
            channel = channels.get(channel_id)
            # console / desktop 是内置渠道：不预先登记配置也能用（watch/notifier 语义原样）
            if channel is None and channel_id in ("console", "desktop"):
                channel = {"id": channel_id, "kind": channel_id, "config": {}}
            if channel is None:
                deliveries.append(
                    {"channel": channel_id, "ok": False, "detail": "渠道不存在或未启用"}
                )
                problems += 1
                continue
            result = deliver(channel, title, "\n".join(body_lines))
            if not result.ok:
                problems += 1
            deliveries.append(result.as_dict())
        try:
            inbox_store.add_item(
                job=f"alert:{rule_id}",
                job_label="提醒规则",
                title=title,
                lines=body_lines,
                dedupe_key=f"{rule_id}:{moment.date().isoformat()}",
                kind="alert",
                root=root,
                now=moment,
            )
        except Exception:  # noqa: BLE001 - 收件箱写不进不影响渠道投递
            problems += 1
        store.set_state(
            rule_id,
            new_status,
            last_value=outcome.value,
            fired_at=moment.isoformat(timespec="seconds"),
            cooldown_until=cooldown_until,
            root=root,
        )
        store.append_history(rule_id, title, "\n".join(body_lines), deliveries, root=root)
        fired.append(
            {
                "rule_id": rule_id,
                "symbol": rule.get("symbol"),
                "kind": rule.get("kind"),
                "title": title,
                "value": outcome.value,
                "detail": outcome.detail,
                "deliveries": deliveries,
            }
        )

    missing_note = "" if problems == 0 else f"（{problems} 项缺数据或投递降级，详见 results）"
    status = "partial" if problems else "ok"
    detail = (
        f"检查 {len(rules)} 条：触发 {len(fired)} 条{missing_note}"
        if fired or problems
        else f"检查 {len(rules)} 条：未触发（条件未满足或数据未取到）"
    )
    return {**base, "status": status, "detail": detail, "fired": fired, "results": results}


__all__ = ["run_once"]
