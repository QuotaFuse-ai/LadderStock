"""`watch/bars.py`：分钟样本落盘的三条硬规矩。

1. 只写当日——旧交易日的价写进今天的文件，就是拿历史数据冒充当日信号；
2. 一分钟一条——30 秒采样会让同一分钟落两条，破坏「恰好 3 分钟前」的窗口匹配；
3. 拒收要有原因——「为什么没写进去」必须能看见。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ladderstock.watch import bars

DAY = "2026-11-17"


def _rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _append(rows, root: Path, day: str = DAY) -> dict:
    return bars.append_minute_bars(day, rows, root=root)


def test_writes_same_day_samples(tmp_path: Path) -> None:
    result = _append(
        [
            {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.01},
            {"ts": f"{DAY}T09:31:00", "code": "000001", "price": 8.2},
        ],
        tmp_path,
    )

    path = bars.bars_path(DAY, tmp_path)
    assert path.is_file()
    assert result["written"] == 2
    assert result["rejected"] == 0

    rows = _rows(path)
    assert {row["code"] for row in rows} == {"600000", "000001"}
    for row in rows:
        assert row["ts"].startswith(DAY)
        assert row["price"] > 0


def test_rejects_other_day_samples(tmp_path: Path) -> None:
    """上游给的是上一交易日的分钟线时，一条都不能写进去。"""
    result = _append(
        [
            {"ts": "2026-11-14T14:59:00", "code": "600000", "price": 10.01},
            {"ts": f"{DAY}T09:31:00", "code": "600001", "price": 9.9},
        ],
        tmp_path,
    )

    assert result["written"] == 1
    assert result["rejected"] == 1
    assert any("拒收旧数据" in item for item in result["rejected_detail"])

    rows = _rows(bars.bars_path(DAY, tmp_path))
    assert [row["code"] for row in rows] == ["600001"]


def test_same_minute_keeps_last_sample(tmp_path: Path) -> None:
    """30 秒一轮的两次采样落进同一分钟：只留最后一条，文件不重复。"""
    _append([{"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.00}], tmp_path)
    _append([{"ts": f"{DAY}T09:31:30", "code": "600000", "price": 10.12}], tmp_path)

    rows = _rows(bars.bars_path(DAY, tmp_path))
    assert len(rows) == 1
    assert rows[0]["price"] == pytest.approx(10.12)


def test_minutes_are_independent(tmp_path: Path) -> None:
    _append(
        [
            {"ts": f"{DAY}T09:31:20", "code": "600000", "price": 10.00},
            {"ts": f"{DAY}T09:32:10", "code": "600000", "price": 10.20},
        ],
        tmp_path,
    )

    rows = _rows(bars.bars_path(DAY, tmp_path))
    assert len(rows) == 2
    assert [row["price"] for row in rows] == [pytest.approx(10.0), pytest.approx(10.2)]


def test_rejects_bad_price_and_missing_code(tmp_path: Path) -> None:
    result = _append(
        [
            {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 0},
            {"ts": f"{DAY}T09:31:00", "code": "", "price": 10.0},
            {"ts": "not-a-time", "code": "600002", "price": 10.0},
        ],
        tmp_path,
    )

    assert result["written"] == 0
    assert result["rejected"] == 3
    assert not bars.bars_path(DAY, tmp_path).exists() or not _rows(bars.bars_path(DAY, tmp_path))


def test_limit_price_optional(tmp_path: Path) -> None:
    """涨停价取不到就不写：封板信号据此判不了，如实留空，不猜。"""
    _append(
        [
            {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.0},
            {"ts": f"{DAY}T09:31:00", "code": "600001", "price": 9.5, "limit_price": 10.45},
        ],
        tmp_path,
    )

    rows = {row["code"]: row for row in _rows(bars.bars_path(DAY, tmp_path))}
    assert "limit_price" not in rows["600000"]
    assert rows["600001"]["limit_price"] == pytest.approx(10.45)


def test_readable_by_signal_loader(tmp_path: Path) -> None:
    """落盘结果必须能被扫描器的 `load_bars()` 直接读成 MinuteBar（字段口径一致）。"""
    from ladderstock.watch.signal import load_bars

    _append([{"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.0}], tmp_path)
    parsed = load_bars(bars.bars_path(DAY, tmp_path))

    assert len(parsed) == 1
    assert parsed[0].code == "600000"
    assert parsed[0].price == pytest.approx(10.0)
    assert parsed[0].ts.date() == date(2026, 11, 17)


def test_name_flows_to_signal_events(tmp_path: Path) -> None:
    """名称一路透到信号事件：`/watch` 的信号行此前只有代码，名称列只能空着。

    名称只做显示，取不到就不写这个键（页面显示 `—`），绝不拿代码顶名称。
    """
    from ladderstock.watch.signal import WatchEvent, detect_signals, load_bars

    _append(
        [
            {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.00, "name": "浦发银行"},
            {"ts": f"{DAY}T09:34:00", "code": "600000", "price": 10.40, "name": "浦发银行"},
            {"ts": f"{DAY}T09:31:00", "code": "600001", "price": 9.50},
            {"ts": f"{DAY}T09:34:00", "code": "600001", "price": 9.90},
        ],
        tmp_path,
    )
    events = detect_signals(load_bars(bars.bars_path(DAY, tmp_path)), threshold=3.0)

    named = [event for event in events if event.code == "600000"]
    assert named and all(event.name == "浦发银行" for event in named)
    assert "name" in named[0].as_dict()
    # 没有名称的样本：事件里不带 name 键，读回来是空串而不是代码
    bare = next(event for event in events if event.code == "600001")
    assert "name" not in bare.as_dict()
    assert WatchEvent.from_dict(bare.as_dict()).name == ""
