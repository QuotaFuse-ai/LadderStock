"""公告候选前置筛：分母 = 代码池 ∪ 当日异动（池外），并集去重。

为什么要筛：`announcement_cninfo` / `announcement_szse` 上游要求**逐票**传 `code`，
每票一次 HTTP。池 85 只 = 85 次调用，池改 300 就是 300 次——这是最容易触发上游
限流/封 IP 的地方。按「当日异动」筛，请求数从 |池| 降到 |池 ∪ 异动|。

为什么分母是并集（而不是单选一个口径）：
- 只按池当分母 → 池外异动票等于没查，漏掉的公告不进任何统计；
- 只按异动数当分母 → 池内没查的票会被 100% 掩盖；
- 并集两头都如实：池规模仍是基准（池 85→300，分母跟着走），池外当日异动
  只补进**当天**的分母。

两条护栏（都很重要）：
1. 没有本机报价快照（或快照当天没有涨跌数据）→ 异动名单为空、`fallback=True`，
   采集侧必须**回退全池**，不能让前置筛把采集变成一只都不采；
2. 异动票**不进池**：池是手写资产（`sync.yaml` 的 `base_pool`），不因为今天涨停
   就自动长大；它只在当天分母里出现。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Sequence

#: 公告域的两个逐票源（只有它们吃前置筛；研报/个股新闻仍按池）
ANNOUNCEMENT_DATASETS: frozenset[str] = frozenset({"announcement_cninfo", "announcement_szse"})

#: 与 `watch_alert` 同一个阈值口径（±5%）
DEFAULT_THRESHOLD = 5.0

#: 行情快照最多能用几天：超过就认为"当日异动"失效，回退全池（宁可多打请求，
#: 也不拿上个月的价格假装今天的异动）。7 天覆盖周末与长假。
DEFAULT_MAX_AGE_DAYS = 7

CANDIDATE_DIRNAME = "news_candidates"


def candidates_path(data_root: Path, day: str) -> Path:
    return Path(data_root) / CANDIDATE_DIRNAME / f"{day}.json"


def read_candidates(data_root: Path, day: str) -> dict[str, Any] | None:
    """读当天筛过的候选名单；没有就返回 None（页面据此说明「还没跑过前置筛」）。"""
    path = candidates_path(data_root, day)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_candidates(data_root: Path, payload: dict[str, Any]) -> Path | None:
    """原子落盘：`data/news_candidates/<day>.json`。写失败不影响采集结果。"""
    day = str(payload.get("day") or "")
    if not day:
        return None
    path = candidates_path(data_root, day)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _focus_codes(pool: Sequence[str], data_root: Path) -> tuple[set[str], int, int]:
    """关注域 = 池 ∪ 自选 ∪ 持仓。自选/持仓读不到不算错（可能没建库）。"""
    codes = {str(code).strip() for code in pool if str(code).strip()}
    pool_size = len(codes)
    watch_size = position_size = 0
    try:
        from ladderstock.journal import position as position_store
        from ladderstock.journal import watchlist as watchlist_store

        watch = {
            str(row.get("code") or "").strip() for row in watchlist_store.list_watch(root=data_root)
        } - {""}
        positions = {
            str(row.get("code") or "").strip() for row in position_store.list_positions(root=data_root)
        } - {""}
        codes |= watch | positions
        watch_size, position_size = len(watch), len(positions)
    except Exception:  # noqa: BLE001 - 隐私库缺表/损坏都不该拖垮筛选
        pass
    return codes, pool_size, watch_size + position_size


def _change_pct(row: dict[str, Any]) -> float | None:
    try:
        price = float(row.get("price"))
        prev = float(row.get("prev_close"))
    except (TypeError, ValueError):
        return None
    if prev <= 0:
        return None
    return (price - prev) / prev * 100


def announcement_candidates(
    pool: Sequence[str],
    *,
    data_root: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """算出公告采集的当天候选名单（池 ∪ 池外异动）与拆解。

    返回字段（页面与采集侧共用同一份口径）：
    `pool_size` / `extra`（池外异动代码） / `union`（去重后的全名单） /
    `movers`（关注域内越过阈值的票，带名称与涨跌幅） / `snapshot_day`（行情日期） /
    `fallback`（是否回退全池） / `note`（一句话说清分母怎么来的）。
    """
    from ladderstock.cli.paths import data_dir

    root_dir = Path(data_root) if data_root is not None else data_dir()
    codes, pool_size, focus_size = _focus_codes(pool, root_dir)
    pool_set = {str(code).strip() for code in pool if str(code).strip()}
    base: dict[str, Any] = {
        "threshold": float(threshold),
        "pool_size": pool_size,
        "focus_size": focus_size,
        "snapshot_day": None,
        "movers": [],
        "movers_total": 0,
        "movers_in_pool": 0,
        "extra": [],
        "union": sorted(pool_set),
        "fallback": True,
        "fallback_reason": "",
    }

    try:
        from ladderstock.data import duckdb_store, symbol_universe

        day = symbol_universe.latest_trade_date(root=root_dir)
    except Exception:  # noqa: BLE001
        day = None
    if day is None:
        base["fallback_reason"] = "本机没有报价快照（symbol_basic 为空），异动名单算不出来"
        base["note"] = f"分母 = 池 {pool_size} 只（前置筛未生效，回退全池）"
        return base

    try:
        rows = duckdb_store.query_dataset("symbol_basic", day, root=root_dir)
    except Exception:  # noqa: BLE001
        base["snapshot_day"] = day.isoformat()
        base["fallback_reason"] = "symbol_basic 读取失败，异动名单算不出来"
        base["note"] = f"分母 = 池 {pool_size} 只（前置筛未生效，回退全池）"
        return base

    reference = today or date.today()
    age = (reference - day).days
    if age > int(max_age_days):
        base["snapshot_day"] = day.isoformat()
        base["fallback_reason"] = (
            f"行情快照过期（{day.isoformat()}，距今 {age} 天），不能当成当日异动"
        )
        base["note"] = f"分母 = 池 {pool_size} 只（前置筛未生效，回退全池）"
        return base

    movers: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if code not in codes:
            continue
        pct = _change_pct(row)
        if pct is None or abs(pct) < float(threshold):
            continue
        movers.append(
            {
                "code": code,
                "name": str(row.get("name") or ""),
                "pct": round(pct, 2),
                "in_pool": code in {str(c).strip() for c in pool},
            }
        )
    movers.sort(key=lambda item: abs(item["pct"]), reverse=True)

    extra = sorted(item["code"] for item in movers if not item["in_pool"])
    inside = len(movers) - len(extra)
    pool_set = {str(code).strip() for code in pool if str(code).strip()}
    # 分母只含「池 + 池外异动」：关注域里没异动的票（自选/持仓）不撑大分母，
    # 否则自选一多，覆盖率会被稀释成看不懂的数字。
    union = sorted(pool_set | set(extra))
    base.update(
        {
            "snapshot_day": day.isoformat(),
            "movers": movers,
            "movers_total": len(movers),
            "movers_in_pool": inside,
            "extra": extra,
            "union": union,
            "fallback": False,
        }
    )
    base["note"] = (
        f"分母 = 池 {pool_size} 只 ∪ 池外异动 {len(extra)} 只 = {len(union)} 只"
        f"（行情日 {day.isoformat()}（距今 {age} 天），阈值 ±{float(threshold):g}%）；"
        f"当天异动 {len(movers)} 只（池内 {inside} 只、池外 {len(extra)} 只），只请求这些"
    )
    return base
