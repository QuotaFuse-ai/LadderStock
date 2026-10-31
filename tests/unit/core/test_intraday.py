"""盘中 6 时点快照（QUOT-286）。

钉住四类行为：

1. **窗口**：6 个时点各自的左闭右开窗口（09:25 到 09:30 结束，其余 +8 分钟），
   窗口外只记 `skipped`、不落快照——不补跑，也不拿今天的价格回填过去；
2. **只创建不覆盖**：`O_CREAT|O_EXCL` 语义，已存在的文件一个字节都不动；
3. **日期校验**：行情 `timestamp` 的日期不是今天就 `missing`（节假日腾讯回节前
   收盘价，数字看着完全合理）；
4. **失败可观测**：取数抛错 → `failed`，`status.json` 有记录、没有快照。

外加 `spot_quote` 的边界拦截：它只走 `sources.yaml` 的实时链路，不进 sync 台账，
备源跨 peer 一律拒绝。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry
from fixtures.intraday_stubs import StubRegistry, quote_records

from ladderstock.core import intraday
from ladderstock.core.boundary import BoundaryViolation, owner_of
from ladderstock.data.provider_registry import FetchOutcome
from ladderstock.errors import EXIT_CONFIG, LadderStockError

DAY = date(2026, 10, 30)  # 周五
TZ = "Asia/Shanghai"


def moment(hour: int, minute: int, second: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second)


def snapshot_file(slot: str, root: Path) -> Path:
    return intraday.snapshot_path(slot, DAY, root)


# --------------------------------------------------------------------------- #
# 1. 窗口：6 个时点 × 接受/拒绝边界
# --------------------------------------------------------------------------- #

WINDOW_CASES = [
    ("09:25", (9, 25, 0), (9, 29, 59), (9, 24, 59), (9, 30, 0)),
    ("09:35", (9, 35, 0), (9, 42, 59), (9, 34, 59), (9, 43, 0)),
    ("10:00", (10, 0, 0), (10, 7, 59), (9, 59, 59), (10, 8, 0)),
    ("11:30", (11, 30, 0), (11, 37, 59), (11, 29, 59), (11, 38, 0)),
    ("14:00", (14, 0, 0), (14, 7, 59), (13, 59, 59), (14, 8, 0)),
    ("15:00", (15, 0, 0), (15, 7, 59), (14, 59, 59), (15, 8, 0)),
]


@pytest.mark.parametrize("slot,start,last,before,after", WINDOW_CASES)
def test_slot_window_is_left_closed_right_open(
    slot: str, start: tuple, last: tuple, before: tuple, after: tuple
) -> None:
    assert intraday.in_window(slot, moment(*start))
    assert intraday.in_window(slot, moment(*last))
    assert not intraday.in_window(slot, moment(*before))
    assert not intraday.in_window(slot, moment(*after))


def test_auction_window_rejects_continuous_trading() -> None:
    """09:29 仍是集合竞价读数；09:30 起连续竞价，竞价快照就没有意义了。"""
    assert intraday.in_window("09:25", moment(9, 29, 59))
    assert not intraday.in_window("09:25", moment(9, 30, 0))


def test_unknown_slot_is_rejected() -> None:
    with pytest.raises(ValueError):
        intraday.slot_window("09:20")
    result = intraday.run_slot("09:20", root=Path("unused"), registry=StubRegistry())
    assert result["status"] == "failed"
    assert "09:20" in result["detail"]


# --------------------------------------------------------------------------- #
# 2. 存储：只创建不覆盖
# --------------------------------------------------------------------------- #


def test_write_new_json_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "1000.json"

    assert intraday.write_new_json(target, {"first": 1}) is True
    assert intraday.write_new_json(target, {"second": 2}) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"first": 1}


def test_existing_snapshot_is_reported_as_already(tmp_path: Path) -> None:
    target = snapshot_file("10:00", tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text('{"sentinel": true}\n', encoding="utf-8")
    registry = StubRegistry()

    result = intraday.run_slot(
        "10:00", now=moment(10, 1), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "already"
    assert target.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert registry.calls == []  # 已存在就不该再打 peer
    status = intraday.read_status(DAY, tmp_path)
    assert status["slots"]["10:00"]["status"] == "already"


# --------------------------------------------------------------------------- #
# 3. 正常快照：聚合读数 + 情绪面
# --------------------------------------------------------------------------- #


def test_snapshot_payload_carries_market_readings(tmp_path: Path) -> None:
    result = intraday.run_slot(
        "10:00", now=moment(10, 0, 5), timezone=TZ, root=tmp_path, registry=StubRegistry()
    )

    assert result["status"] == "ok", result
    payload = json.loads(snapshot_file("10:00", tmp_path).read_text(encoding="utf-8"))
    assert payload["date"] == "2026-10-30"
    assert payload["slot"] == "10:00"
    assert payload["price_field"] == "price"
    assert payload["source"]["dataset"] == "spot_quote"

    readings = payload["yesterday_limit_up"]
    assert readings["count"] == 3
    assert readings["fresh"] == 3
    assert readings["overall"]["count"] == 3
    assert readings["overall"]["down"] == 1
    assert readings["overall"]["limit_up"] == 1  # 青岛食品封板
    assert [row["board"] for row in readings["by_board"]] == ["首板", "三板及以上"]
    assert readings["top_board"]["streak"] == 4
    assert [row["code"] for row in readings["top_board"]["stocks"]] == ["001219"]

    sentiment = payload["sentiment"]
    assert sentiment["available"] is True
    assert sentiment["limit_up"] == 47
    assert sentiment["broken"] == 31
    assert sentiment["limit_down"] == 82
    assert sentiment["max_streak"] == 5
    assert sentiment["broken_rate_pct"] == 39.7
    assert sentiment["one_to_two"]["available"] is True
    assert sentiment["one_to_two"]["yesterday_first_board"] == 2
    assert sentiment["one_to_two"]["today_second_board"] == 2
    assert sentiment["one_to_two"]["promoted"] == 0

    status = intraday.read_status(DAY, tmp_path)
    assert status["slots"]["10:00"]["status"] == "ok"
    assert status["slots"]["10:00"]["file"] == "1000.json"


def test_auction_slot_reads_open_price(tmp_path: Path) -> None:
    result = intraday.run_slot(
        "09:25", now=moment(9, 25, 10), timezone=TZ, root=tmp_path, registry=StubRegistry()
    )

    assert result["status"] == "ok", result
    payload = json.loads(snapshot_file("09:25", tmp_path).read_text(encoding="utf-8"))
    assert payload["price_field"] == "open"
    assert "sentiment" not in payload  # 09:25 不附情绪面
    by_code = {row["code"]: row for row in payload["yesterday_limit_up"]["stocks"]}
    assert by_code["001219"]["reading"] == 18.60
    assert by_code["001219"]["reading_field"] == "open"
    # 涨跌幅按开盘价重算：18.60 / 18.13 - 1 = +2.59%，不是报价里的最新价口径 9.98%
    assert by_code["001219"]["change_pct"] == 2.59


def test_sentiment_failure_degrades_but_still_writes_snapshot(tmp_path: Path) -> None:
    registry = StubRegistry(errors={"sentiment": RuntimeError("sentiment down")})

    result = intraday.run_slot(
        "09:35", now=moment(9, 35, 5), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "degraded"
    payload = json.loads(snapshot_file("09:35", tmp_path).read_text(encoding="utf-8"))
    assert payload["sentiment"]["available"] is False
    assert "sentiment down" in payload["sentiment"]["reason"]


# --------------------------------------------------------------------------- #
# 4. 日期不符 / 窗口外 / 抓取失败：都不落快照
# --------------------------------------------------------------------------- #


def test_quotes_from_another_day_are_not_stored(tmp_path: Path) -> None:
    """节假日腾讯回节前收盘价：日期不是今天就是 missing，数字再合理也不存。"""
    registry = StubRegistry()
    registry.payloads["spot_quote"] = quote_records(DAY - timedelta(days=1))

    result = intraday.run_slot(
        "10:00", now=moment(10, 0, 5), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "missing"
    assert not snapshot_file("10:00", tmp_path).exists()
    status = intraday.read_status(DAY, tmp_path)
    assert status["slots"]["10:00"]["status"] == "missing"


def test_partially_stale_quotes_still_snapshot_and_mark_missing(tmp_path: Path) -> None:
    registry = StubRegistry()
    rows = quote_records(DAY)
    rows[0]["timestamp"] = (DAY - timedelta(days=1)).strftime("%Y%m%d100003")
    registry.payloads["spot_quote"] = rows

    result = intraday.run_slot(
        "10:00", now=moment(10, 0, 5), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "ok", result
    payload = json.loads(snapshot_file("10:00", tmp_path).read_text(encoding="utf-8"))
    readings = payload["yesterday_limit_up"]
    assert readings["fresh"] == 2
    assert readings["stale"] == 1
    assert "000530" in readings["missing"]
    stale = next(row for row in readings["stocks"] if row["code"] == "000530")
    assert stale["reason"] == "stale"


@pytest.mark.parametrize(
    "slot,now",
    [
        ("09:25", moment(9, 30, 0)),
        ("10:00", moment(10, 8, 0)),
        ("15:00", moment(15, 30, 0)),  # 窗口外更久：同样不补跑
    ],
)
def test_outside_window_skips_without_snapshot(slot: str, now: datetime, tmp_path: Path) -> None:
    registry = StubRegistry()

    result = intraday.run_slot(slot, now=now, timezone=TZ, root=tmp_path, registry=registry)

    assert result["status"] == "skipped"
    assert "不补跑" in result["detail"]
    assert not snapshot_file(slot, tmp_path).exists()
    assert registry.calls == []
    status = intraday.read_status(DAY, tmp_path)
    assert status["slots"][slot]["status"] == "skipped"


def test_weekend_is_skipped(tmp_path: Path) -> None:
    saturday = date(2026, 10, 31)
    result = intraday.run_slot(
        "10:00",
        now=datetime(2026, 10, 31, 10, 0, 5),
        timezone=TZ,
        root=tmp_path,
        registry=StubRegistry(),
    )

    assert result["status"] == "skipped"
    assert "非交易日" in result["detail"]
    assert not intraday.snapshot_path("10:00", saturday, tmp_path).exists()


def test_fetch_failure_writes_status_but_no_snapshot(tmp_path: Path) -> None:
    registry = StubRegistry(errors={"spot_quote": RuntimeError("quote endpoint down")})

    result = intraday.run_slot(
        "10:00", now=moment(10, 0, 5), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "failed"
    assert "quote endpoint down" in result["detail"]
    assert not snapshot_file("10:00", tmp_path).exists()
    status = intraday.read_status(DAY, tmp_path)
    assert status["slots"]["10:00"]["status"] == "failed"
    assert "quote endpoint down" in status["slots"]["10:00"]["detail"]


def test_empty_yesterday_pool_is_missing(tmp_path: Path) -> None:
    registry = StubRegistry()
    registry.payloads["yesterday_limit_up"] = []

    result = intraday.run_slot(
        "10:00", now=moment(10, 0, 5), timezone=TZ, root=tmp_path, registry=registry
    )

    assert result["status"] == "missing"
    assert not snapshot_file("10:00", tmp_path).exists()


# --------------------------------------------------------------------------- #
# 5. 纯函数：分板位 / 未知板位 / 行情日
# --------------------------------------------------------------------------- #


def test_by_board_puts_missing_streak_in_unknown_bucket() -> None:
    records = [
        {"code": "000001", "y_streak": 1},
        {"code": "000002", "y_streak": 2},
        {"code": "000003", "y_streak": 4},
        {"code": "000004"},  # 上游没给 y_streak
    ]
    quotes = [
        {
            "code": code,
            "price": 10.5,
            "prev_close": 10.0,
            "change_pct": 5.0,
            "timestamp": "20261030100000",
        }
        for code in ("000001", "000002", "000003", "000004")
    ]

    readings = intraday.project_readings("10:00", records, quotes, DAY)

    boards = {row["board"]: row["count"] for row in readings["by_board"]}
    assert boards == {"首板": 1, "二板": 1, "三板及以上": 1, "未知": 1}
    unknown = next(row for row in readings["stocks"] if row["code"] == "000004")
    assert unknown["yesterday_streak"] is None
    assert unknown["fresh"] is True


def test_quote_day_parses_tencent_timestamp() -> None:
    assert intraday.quote_day({"timestamp": "20261030100003"}) == DAY
    assert intraday.quote_day({"timestamp": ""}) is None
    assert intraday.quote_day({}) is None


# --------------------------------------------------------------------------- #
# 6. spot_quote 边界：只走 sources.yaml 实时链路，不进 sync 台账
# --------------------------------------------------------------------------- #


def _spot_sources(via: str = "tickhub", dataset: str = "spot_quote") -> dict[str, Any]:
    sources = json.loads(json.dumps(SOURCES_DOC))
    sources["sources"].append(
        {
            "id": "tickhub_spot",
            "dataset": dataset,
            "via": via,
            "tool": "tencent_quote_batch",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    return sources


def test_spot_quote_is_owned_by_tickhub_and_stays_out_of_the_ledger() -> None:
    binding = owner_of("spot_quote")
    assert binding.peer == "tickhub"
    assert binding.tool == "tencent_quote_batch"
    assert binding.mode == "replace"

    registry = make_registry(FakeTransport(), sources=_spot_sources(), sync=SYNC_DOC)
    assert [spec.id for spec in registry.source_chain("spot_quote")] == ["tickhub_spot"]
    with pytest.raises(LadderStockError) as excinfo:
        registry.fetch("spot_quote", params={"codes": "000530"})
    assert excinfo.value.code == EXIT_CONFIG
    assert "sync.yaml" in str(excinfo.value)


def test_spot_quote_rejects_cross_peer_source() -> None:
    registry = make_registry(FakeTransport(), sources=_spot_sources(via="senate"), sync=SYNC_DOC)
    with pytest.raises(BoundaryViolation):
        registry.source_chain("spot_quote")


def test_spot_quote_rejects_source_bound_to_another_dataset() -> None:
    sources = _spot_sources()
    sources["sources"].append(
        {
            "id": "tickhub_other",
            "dataset": "limit_pool",
            "via": "tickhub",
            "tool": "limit_up_pool",
            "primary": False,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    spot = next(item for item in sources["sources"] if item["id"] == "tickhub_spot")
    spot["fallback"] = ["tickhub_other"]
    registry = make_registry(FakeTransport(), sources=sources, sync=SYNC_DOC)
    with pytest.raises(BoundaryViolation):
        registry.source_chain("spot_quote")


def test_fetch_live_returns_envelope_from_sources_chain() -> None:
    transport = FakeTransport(
        handlers={
            "tencent_quote_batch": lambda params: {
                "ok": True,
                "data": {"quotes": {"000530": {"code": "000530", "price": 7.92}}},
                "meta": {"source": "tencent", "fetched_at": "2026-10-30T10:00:03+08:00"},
            }
        }
    )
    registry = make_registry(transport, sources=_spot_sources(), sync=SYNC_DOC)

    outcome: FetchOutcome = registry.fetch_live("spot_quote", params={"codes": "000530"})

    assert outcome.envelope.payload["records"] == [{"code": "000530", "price": 7.92}]
    assert transport.tools_called == ["tencent_quote_batch"]
