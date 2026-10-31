"""盘中 6 时点快照（QUOT-286，照 vibe-astock 的实测口径）。

只做一件事：在**当天当场**把某一时刻的盘面读数存成一个 JSON，不补跑、不覆盖、
不用今天的价格给过去的日子写快照。

## 时点与窗口

| 时点 | 有效窗口（本地，左闭右开） | 说明 |
|---|---|---|
| `09:25` | `09:25:00` – `09:29:59` | 集合竞价成交价（开盘价）；进了连续竞价就没有竞价读数了 |
| `09:35` | `09:35:00` – `09:42:59` | 其余时点后 8 分钟内有效 |
| `10:00` | `10:00:00` – `10:07:59` | |
| `11:30` | `11:30:00` – `11:37:59` | 上午收盘 |
| `14:00` | `14:00:00` – `14:07:59` | |
| `15:00` | `15:00:00` – `15:07:59` | 收盘 |

窗口外一律 `skipped`，**不补跑**：用今天的价格给过去的日子写快照会永久污染数据，
而且从文件里看不出异样（vibe-astock `intraday.ts:15-16`）。

## 存储

`data/intraday/<YYYY-MM-DD>/<HHMM>.json` + 同目录 `status.json`。快照用
`O_CREAT|O_EXCL` **只创建不覆盖**：文件已存在即记 `already`，内容一个字节都不动。
快照**不进 sync 台账**（机器存的盘面读数 vs 用户自己记的东西分开）。

## 数据校验

- 行情行内 `timestamp`（Tencent 的 `YYYYMMDDHHMMSS`）的日期必须等于今天；
  节假日腾讯会回节前收盘价，数字看着完全合理，只有日期能拆穿；
- 一条新鲜报价都没有 → `status=missing`，不写快照；
- 抓取抛错 → `status=failed`，只写 `status.json`，不写快照。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import BOARD_KEYS, CODE_KEYS, NAME_KEYS, as_float, as_int, pick

SLOTS: tuple[str, ...] = ("09:25", "09:35", "10:00", "11:30", "14:00", "15:00")

#: 昨日涨停池里的**昨日**连板数。不能复用 `ladder.BOARD_KEYS`：该工具的
#: `limit_up_days` 是今天（含今日封板）的连板数，排在 `y_streak` 前面会被先取到，
#: 分板位与 1进2 都会整体错一档。上游没给 `y_streak` 就记未知，不拿别的字段顶替。
YESTERDAY_STREAK_KEYS: tuple[str, ...] = (
    "y_streak",
    "yesterday_streak",
    "yesterday_consecutive_boards",
)
AUCTION_SLOT = "09:25"
SENTIMENT_FROM = "09:35"
WINDOW_MINUTES = 8
AUCTION_WINDOW_END = time(9, 30)

DEFAULT_TIMEZONE = "Asia/Shanghai"
#: 单个快照目录名（`data/intraday/<date>/`）
SNAPSHOT_DIR = "intraday"
#: `tencent_quote_batch` 单次上限（工具默认 max_codes=50）
QUOTE_CHUNK = 50

#: status 取值：ok / degraded / already / skipped / missing / failed
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_ALREADY = "already"
STATUS_SKIPPED = "skipped"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"


class SnapshotUnavailable(Exception):
    """这一次时点没有可存的读数（日期不符 / 池子为空 / 上游给空）——不是崩溃。"""


# ---------------------------------------------------------------- 时点与窗口


def slot_window(slot: str) -> tuple[int, int]:
    """时点窗口 → `[start, end)`，单位是从零点起的秒数。未知时点抛 `ValueError`。"""
    if slot not in SLOTS:
        raise ValueError(f"未知快照时点 {slot!r}；可选：{'、'.join(SLOTS)}")
    hour, minute = (int(part) for part in slot.split(":", 1))
    start = hour * 3600 + minute * 60
    if slot == AUCTION_SLOT:
        end = AUCTION_WINDOW_END.hour * 3600 + AUCTION_WINDOW_END.minute * 60
    else:
        end = start + WINDOW_MINUTES * 60
    return start, end


def in_window(slot: str, moment: datetime) -> bool:
    """`moment` 是否落在该时点的有效窗口内（左闭右开）。"""
    start, end = slot_window(slot)
    current = moment.hour * 3600 + moment.minute * 60 + moment.second
    return start <= current < end


def window_text(slot: str) -> str:
    start, end = slot_window(slot)
    return f"{_clock(start)}–{_clock(end - 1)}"


def resolve_timezone(name: str | None) -> tzinfo | None:
    """调度表时区；`zoneinfo` 缺 tzdata 时对 Asia/Shanghai 退回固定 +08:00。

    中国没有夏令时，固定偏移与 `Asia/Shanghai` 等价；其它时区解析不了就返回
    None，由调用方退回本机时间（不假装知道时区）。
    """
    zone = name or DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(zone)
    except Exception:  # noqa: BLE001 - 缺 tzdata / 非法时区名都走这里
        if zone == DEFAULT_TIMEZONE:
            from datetime import timezone as _timezone

            return _timezone(timedelta(hours=8), DEFAULT_TIMEZONE)
        return None


def local_moment(now: datetime | None, timezone_name: str | None) -> datetime:
    """把「现在」换算到调度时区；无时区信息时按本机时间理解。"""
    zone = resolve_timezone(timezone_name)
    moment = now or datetime.now().astimezone()
    if zone is None:
        return moment
    if moment.tzinfo is None:
        return moment.replace(tzinfo=zone)
    return moment.astimezone(zone)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------- 存储


def snapshot_dir(day: date, root: Path | None = None) -> Path:
    from ladderstock.cli.paths import data_dir

    base = Path(root) if root is not None else data_dir()
    return base / SNAPSHOT_DIR / day.isoformat()


def snapshot_path(slot: str, day: date, root: Path | None = None) -> Path:
    return snapshot_dir(day, root) / f"{slot.replace(':', '')}.json"


def status_path(day: date, root: Path | None = None) -> Path:
    return snapshot_dir(day, root) / "status.json"


def write_new_json(path: Path, payload: Mapping[str, Any]) -> bool:
    """只创建不覆盖。返回 True=写入；False=文件已存在（一个字节都没动）。

    `O_CREAT|O_EXCL` 的语义与 vibe-astock 的临时文件 + `linkSync` 等价：两个进程
    同时写，只有一个能成功。写一半失败时删掉自己刚创建的文件，不留下半截成品。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return True


def read_status(day: date, root: Path | None = None) -> dict[str, Any]:
    path = status_path(day, root)
    if not path.is_file():
        return {"date": day.isoformat(), "slots": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"date": day.isoformat(), "slots": {}}
    if not isinstance(data, dict):
        return {"date": day.isoformat(), "slots": {}}
    if not isinstance(data.get("slots"), dict):
        data["slots"] = {}
    return data


def record_status(
    day: date,
    slot: str,
    status: str,
    *,
    detail: str | None = None,
    root: Path | None = None,
    at: datetime | None = None,
    **fields: Any,
) -> Path:
    """把这一次时点的结果写进 `status.json`（原子替换；快照本身仍是只创建）。"""
    moment = at or datetime.now().astimezone()
    path = status_path(day, root)
    data = read_status(day, root)
    slots = data.setdefault("slots", {})
    entry: dict[str, Any] = {
        "status": status,
        "at": moment.isoformat(timespec="seconds"),
        "detail": detail,
    }
    entry.update({key: value for key, value in fields.items() if value is not None})
    slots[slot] = entry
    data["date"] = day.isoformat()
    data["updated_at"] = moment.isoformat(timespec="seconds")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 读数（纯函数）


def quote_day(row: Mapping[str, Any]) -> date | None:
    """行内行情时间（Tencent `timestamp`: `YYYYMMDDHHMMSS`）属于哪一天。"""
    text = str(row.get("timestamp") or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def project_readings(
    slot: str, records: Iterable[dict[str, Any]], quotes: Iterable[dict[str, Any]], day: date
) -> dict[str, Any]:
    """昨日涨停股 × 此刻报价 → 市场层面聚合 + 逐只客观读数（不排序、不评分）。

    `09:25` 读开盘价（集合竞价成交价），其余时点读最新价。`at_limit_up` 只在
    报价里同时有 `limit_up` 时才是布尔值，否则为 `null`——不拿涨幅阈值猜。
    """
    by_code = {
        str(row.get("code") or "").strip(): row
        for row in quotes
        if str(row.get("code") or "").strip()
    }
    stocks: list[dict[str, Any]] = []
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        quote = by_code.get(code)
        entry: dict[str, Any] = {
            "code": code,
            "name": str(pick(record, NAME_KEYS) or "").strip() or None,
            "yesterday_streak": as_int(pick(record, YESTERDAY_STREAK_KEYS)),
            "reading_field": "open" if slot == AUCTION_SLOT else "price",
            "reading": None,
            "open": None,
            "price": None,
            "prev_close": None,
            "change_pct": None,
            "limit_up": None,
            "at_limit_up": None,
            "fresh": False,
            "reason": None,
        }
        if quote is None:
            entry["reason"] = "no_quote"
            stocks.append(entry)
            continue
        open_price = _positive(quote.get("open"))
        price = _positive(quote.get("price"))
        prev_close = _positive(quote.get("prev_close"))
        limit_up = _positive(quote.get("limit_up"))
        entry.update(open=open_price, price=price, prev_close=prev_close, limit_up=limit_up)

        reading = open_price if slot == AUCTION_SLOT else price
        if quote_day(quote) != day:
            entry["reason"] = "stale"
            stocks.append(entry)
            continue
        if reading is None:
            entry["reason"] = "no_open" if slot == AUCTION_SLOT else "no_reading"
            stocks.append(entry)
            continue

        change = as_float(quote.get("change_pct"))
        if slot == AUCTION_SLOT:
            # 竞价时读开盘价，涨跌幅也必须对开盘价算：报价里的 change_pct 是最新价口径。
            change = round((reading - prev_close) / prev_close * 100, 2) if prev_close else None
        elif change is None and prev_close:
            change = round((reading - prev_close) / prev_close * 100, 2)

        entry.update(
            reading=reading,
            change_pct=change,
            at_limit_up=(reading >= limit_up - 0.005) if limit_up else None,
            fresh=True,
        )
        stocks.append(entry)

    fresh = [entry for entry in stocks if entry["fresh"]]
    return {
        "count": len(stocks),
        "fresh": len(fresh),
        "stale": sum(1 for entry in stocks if entry["reason"] == "stale"),
        "missing": [entry["code"] for entry in stocks if not entry["fresh"]],
        "overall": _aggregate(fresh),
        "by_board": _by_board(fresh),
        "top_board": _top_board(stocks),
        "stocks": stocks,
    }


def _positive(value: Any) -> float | None:
    number = as_float(value)
    return number if number is not None and number > 0 else None


def _aggregate(stocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    changes = [row["change_pct"] for row in stocks if row["change_pct"] is not None]
    return {
        "count": len(stocks),
        "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else None,
        "up": sum(1 for value in changes if value > 0),
        "flat": sum(1 for value in changes if value == 0),
        "down": sum(1 for value in changes if value < 0),
        "limit_up": sum(1 for row in stocks if row["at_limit_up"] is True),
    }


BOARD_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("首板", 1, 1),
    ("二板", 2, 2),
    ("三板及以上", 3, None),
)
BOARD_UNKNOWN = "未知"


def _bucket(streak: int | None) -> str:
    if streak is None:
        return BOARD_UNKNOWN
    for label, low, high in BOARD_BUCKETS:
        if streak >= low and (high is None or streak <= high):
            return label
    return BOARD_UNKNOWN


def _by_board(stocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """分板位强弱：按**昨日**连板数分组，只给聚合，不给个股排名。

    空分组不输出（没有首板就别编一行 0）；昨日连板数缺失的股票单独进「未知」，
    不并进任何一档。
    """
    rows: list[dict[str, Any]] = []
    for label in [*[name for name, _, _ in BOARD_BUCKETS], BOARD_UNKNOWN]:
        members = [row for row in stocks if _bucket(row.get("yesterday_streak")) == label]
        if not members:
            continue
        rows.append({"board": label, **_aggregate(members)})
    return rows


def _top_board(stocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    streaks = [row["yesterday_streak"] for row in stocks if row["yesterday_streak"] is not None]
    if not streaks:
        return {"streak": None, "stocks": []}
    top = max(streaks)
    return {
        "streak": top,
        "stocks": [
            {
                "code": row["code"],
                "name": row["name"],
                "reading": row["reading"],
                "change_pct": row["change_pct"],
                "fresh": row["fresh"],
            }
            for row in stocks
            if row.get("yesterday_streak") == top
        ],
    }


# ---------------------------------------------------------------- 抓取


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _records(envelope: Any) -> list[dict[str, Any]]:
    payload = getattr(envelope, "payload", {}) or {}
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [dict(row) for row in records if isinstance(row, Mapping)]


def _quote_codes(records: Iterable[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _fetch_quotes(
    registry: Any, codes: list[str]
) -> tuple[list[dict[str, Any]], list[str], str, bool]:
    rows: list[dict[str, Any]] = []
    trail: list[str] = []
    detail = ""
    degraded = False
    for chunk in _chunks(codes, QUOTE_CHUNK):
        outcome = registry.fetch_live(
            "spot_quote",
            params={"codes": ",".join(chunk), "max_codes": len(chunk)},
        )
        trail.extend(outcome.trail)
        detail = outcome.envelope.source_detail
        degraded = degraded or outcome.used_fallback or outcome.envelope.source_status != "ok"
        rows.extend(_records(outcome.envelope))
    return rows, trail, detail, degraded


def _one_to_two(registry: Any, day: date, yesterday: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """1进2：昨日首板（`y_streak == 1`）里今天已经二板（`streak == 2`）的占比。"""
    first = {
        str(pick(row, CODE_KEYS) or "").strip()
        for row in yesterday
        if as_int(pick(row, YESTERDAY_STREAK_KEYS)) == 1
    }
    first.discard("")
    if not first:
        return {
            "available": False,
            "reason": "昨日涨停池没有首板（上游未给 y_streak / 全为高位板）",
        }
    try:
        outcome = registry.fetch("limit_pool", params={"date": day.isoformat()})
    except Exception as exc:  # noqa: BLE001 - 单项缺失只降级这一块
        return {"available": False, "reason": f"limit_pool 取数失败：{_one_line(exc)}"}
    if outcome.envelope.data_as_of != day:
        return {
            "available": False,
            "reason": f"limit_pool 数据日 {outcome.envelope.data_as_of} 不是今天 {day}",
        }
    second = {
        str(pick(row, CODE_KEYS) or "").strip()
        for row in _records(outcome.envelope)
        if as_int(pick(row, BOARD_KEYS)) == 2
    }
    promoted = first & second
    return {
        "available": True,
        "yesterday_first_board": len(first),
        "today_second_board": len(second),
        "promoted": len(promoted),
        "rate_pct": round(len(promoted) / len(first) * 100, 1),
    }


def _sentiment_block(
    registry: Any, day: date, yesterday: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """09:35 起附的情绪面：涨停 / 炸板 / 跌停 / 最高板 / 炸板率 / 1进2。"""
    try:
        outcome = registry.fetch("sentiment", params={"date": day.isoformat()})
    except Exception as exc:  # noqa: BLE001 - 情绪缺失不拖垮快照本身
        return {"available": False, "reason": f"sentiment 取数失败：{_one_line(exc)}"}
    body = (outcome.envelope.payload or {}).get("records")
    if not isinstance(body, Mapping) or body.get("limit_up") is None:
        return {"available": False, "reason": "sentiment 返回为空（上游未发布）"}
    if outcome.envelope.data_as_of != day:
        return {
            "available": False,
            "reason": f"sentiment 数据日 {outcome.envelope.data_as_of} 不是今天 {day}",
        }

    def percent(key: str) -> float | None:
        value = as_float(body.get(key))
        return round(value * 100, 1) if value is not None else None

    leaders = body.get("leaders")
    return {
        "available": True,
        "date": body.get("date") or day.strftime("%Y%m%d"),
        "limit_up": as_int(body.get("limit_up")),
        "broken": as_int(body.get("broken")),
        "limit_down": as_int(body.get("limit_down")),
        "max_streak": as_int(body.get("max_streak")),
        "seal_rate_pct": percent("seal_rate"),
        "broken_rate_pct": percent("broken_rate"),
        "leaders": [dict(row) for row in leaders if isinstance(row, Mapping)]
        if isinstance(leaders, list)
        else [],
        "one_to_two": _one_to_two(registry, day, yesterday),
        "source_detail": outcome.envelope.source_detail,
    }


def capture(slot: str, day: date, moment: datetime, registry: Any) -> tuple[dict[str, Any], bool]:
    """抓一次时点快照；返回 (载荷, 是否降级)。拿不到可存读数抛 `SnapshotUnavailable`。"""
    try:
        yesterday_outcome = registry.fetch("yesterday_limit_up", params={"date": day.isoformat()})
    except Exception as exc:  # noqa: BLE001 - 取不到池子就没什么可快照的
        raise SnapshotUnavailable(f"昨日涨停池取数失败：{_one_line(exc)}") from exc
    if yesterday_outcome.envelope.data_as_of != day:
        raise SnapshotUnavailable(
            f"昨日涨停池数据日 {yesterday_outcome.envelope.data_as_of} 不是今天 {day}"
        )
    yesterday = _records(yesterday_outcome.envelope)
    if not yesterday:
        raise SnapshotUnavailable("昨日涨停池为空（非交易日或上游未发布）")

    codes = _quote_codes(yesterday)
    quotes, trail, detail, degraded = _fetch_quotes(registry, codes)
    readings = project_readings(slot, yesterday, quotes, day)
    if not readings["fresh"]:
        raise SnapshotUnavailable(
            f"{len(codes)} 只昨日涨停股没有一条今天（{day}）的报价："
            "行情日期不是今天（节假日回节前收盘价）或上游未更新"
        )

    payload: dict[str, Any] = {
        "date": day.isoformat(),
        "slot": slot,
        "captured_at": moment.isoformat(timespec="seconds"),
        "price_field": "open" if slot == AUCTION_SLOT else "price",
        "source": {
            "dataset": "spot_quote",
            "tool": "tencent_quote_batch",
            "source_detail": detail,
            "yesterday_limit_up": yesterday_outcome.envelope.source_detail,
            "trail": trail,
        },
        "yesterday_limit_up": {
            key: readings[key]
            for key in (
                "count",
                "fresh",
                "stale",
                "missing",
                "overall",
                "by_board",
                "top_board",
                "stocks",
            )
        },
    }
    if slot >= SENTIMENT_FROM:
        payload["sentiment"] = _sentiment_block(registry, day, yesterday)
        if not payload["sentiment"].get("available"):
            degraded = True
    return payload, degraded


# ---------------------------------------------------------------- 入口


def run_slot(
    slot: str,
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """跑一个时点：窗口判定 → 抓取 → 只创建写入 → 记 status。

    返回 `{status, detail, date, slot, file, ...}`，与 `schedule.run_job` 的结果字段同名，
    供调度器直接合并。任何失败都折成状态字段，不向上抛（调度器要活着）。
    """
    if slot not in SLOTS:
        return {
            "status": STATUS_FAILED,
            "detail": f"未知快照时点 {slot!r}；可选：{'、'.join(SLOTS)}",
            "slot": slot,
        }
    moment = local_moment(now, timezone)
    day = moment.date()
    base = {"date": day.isoformat(), "slot": slot}

    if day.weekday() >= 5:
        detail = "非交易日（周末），不跑"
        record_status(day, slot, STATUS_SKIPPED, detail=detail, root=root, at=moment)
        return {**base, "status": STATUS_SKIPPED, "detail": detail}

    if not in_window(slot, moment):
        detail = f"不在 {slot} 的有效窗口 {window_text(slot)} 内，不补跑"
        record_status(day, slot, STATUS_SKIPPED, detail=detail, root=root, at=moment)
        return {**base, "status": STATUS_SKIPPED, "detail": detail}

    target = snapshot_path(slot, day, root)
    if target.is_file():
        detail = f"快照已存在，不覆盖：{target.name}"
        record_status(
            day, slot, STATUS_ALREADY, detail=detail, root=root, at=moment, file=target.name
        )
        return {**base, "status": STATUS_ALREADY, "detail": detail, "file": target.name}

    try:
        if registry is None:
            from ladderstock.data.provider_registry import ProviderRegistry

            registry = ProviderRegistry(root=root)
        payload, degraded = capture(slot, day, moment, registry)
    except SnapshotUnavailable as exc:
        detail = str(exc)
        record_status(day, slot, STATUS_MISSING, detail=detail, root=root, at=moment)
        return {**base, "status": STATUS_MISSING, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - 单点时点失败不拖垮调度器
        detail = f"快照抓取失败：{_one_line(exc)}"
        record_status(day, slot, STATUS_FAILED, detail=detail, root=root, at=moment)
        return {**base, "status": STATUS_FAILED, "detail": detail}

    if not write_new_json(target, payload):
        detail = f"快照已存在（并发写入），不覆盖：{target.name}"
        record_status(
            day, slot, STATUS_ALREADY, detail=detail, root=root, at=moment, file=target.name
        )
        return {**base, "status": STATUS_ALREADY, "detail": detail, "file": target.name}

    status = STATUS_DEGRADED if degraded else STATUS_OK
    fresh = payload["yesterday_limit_up"]["fresh"]
    detail = (
        f"{fresh}/{payload['yesterday_limit_up']['count']} 只昨日涨停股有今天读数，"
        f"写入 {target.name}"
    )
    record_status(
        day,
        slot,
        status,
        detail=detail,
        root=root,
        at=moment,
        file=target.name,
        price_field=payload["price_field"],
        stocks=payload["yesterday_limit_up"]["count"],
        fresh=fresh,
    )
    return {**base, "status": status, "detail": detail, "file": target.name}


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


__all__ = [
    "AUCTION_SLOT",
    "DEFAULT_TIMEZONE",
    "SENTIMENT_FROM",
    "SLOTS",
    "SnapshotUnavailable",
    "capture",
    "in_window",
    "local_moment",
    "project_readings",
    "quote_day",
    "read_status",
    "record_status",
    "resolve_timezone",
    "run_slot",
    "slot_window",
    "snapshot_dir",
    "snapshot_path",
    "status_path",
    "window_text",
    "write_new_json",
]
