"""`GET /api/symbols/search`：标的字典只读检索（QUOT-405）。

参考库（vibe-astock `symbolSearch`）用专门的标的字典工具出代码 + 名称；本仓生态里
TickHub-CN 盘点后**没有**代码表 / 标的检索类工具（`stock_profile` /
`baostock_stock_basic` 都要传具体代码且走活源，`iwencai_search` 是需要 API key 的
外部自然语言检索），所以数据源落在本机缓存反查：把带名称字段的几个数据集
（`fund_flow` / `limit_pool` / `hot_rank` / `broken_board` / `yesterday_limit_up` /
`dragon_tiger`）里出现过的 `code → name` 并起来。

三条硬口径：

- **不伪造**：查无此代码就是 `matches: []`，不编名称、不用代码顶替名称；
- **只读、只 GET**：不触发 sync、不写库，信封照 data 面的 `LadderEnvelope` 形状
  （`payload.data_source_status` / 顶层 `data_as_of`）；
- **回环独占 + `Host` 白名单**：闸门照 `/api/research/*` 的 `_guard` 形状
  （回环来源 + Host 校验），本机缓存里出现过哪些标的属于「这台机器在看什么」的信号。

拼音：不引重依赖——pyproject 没有 pypinyin 时 `pinyin` 字段如实返回 `null`
（拼音匹配待后续单接入），绝不手写大映射表。
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ladderstock.core.ladder import CODE_KEYS, NAME_KEYS, pick
from ladderstock.core.models import LadderEnvelope
from ladderstock.data import duckdb_store
from ladderstock.web import control as control_plane

SYMBOLS_PREFIX = "/api/symbols"

#: 带名称字段的数据集（本机缓存反查的唯一来源）。都在 `DATASET_CATALOG` 登记，
#: 读取走 `duckdb_store.query_dataset`，不自拼 SQL、不碰未登记的表。
#: 表内无名称的数据集（如 `daily_k`）不进这一列。
NAME_DATASETS: tuple[str, ...] = (
    "fund_flow",
    "limit_pool",
    "hot_rank",
    "broken_board",
    "yesterday_limit_up",
    "dragon_tiger",
)

#: 单个数据集反查的行数上限：本机缓存体量远小于它，只是给失控增长上一道闸
MAX_DATASET_ROWS = 20000

#: 检索词上限：防御超长输入，超过即 422
MAX_QUERY_LEN = 64

#: 市场别名集合：payload 里只有这三种取值才直接采信，其余按代码前缀推导
_MARKET_ALIASES = frozenset({"SH", "SZ", "BJ"})

#: 检索词归一化（与前端 `searchIntent.ts` 同一规则）：全角/空白/连接符折叠、
#: 去交易所前后缀，剩下的才是查询本体
_CODE_PUNCTUATION = re.compile(r"[\s._\-/]+")
_MARKET_PREFIX = re.compile(r"^(SH|SZ|BJ)", re.IGNORECASE)
_MARKET_SUFFIX = re.compile(r"(SH|SZ|BJ)$", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_query(raw: str) -> str:
    """`600000.SH` / ` 600 000 ` / `sh600000` → `600000`；其余折叠空白原样返回。"""
    text = _CODE_PUNCTUATION.sub("", (raw or "").strip())
    text = _MARKET_PREFIX.sub("", text, count=1)
    return _MARKET_SUFFIX.sub("", text, count=1)


def board_of(code: str) -> str:
    """按代码段推导板块（事实推导，不是编数据）。"""
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    if code.startswith(("43", "83", "87", "92")):
        return "北交所"
    return "主板"


def market_of(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    return "BJ"


def _pinyin_of(name: str) -> str | None:
    """名称 → 首字母拼音（gzmt）；pypinyin 不在依赖里就 `None`，不手写映射表。"""
    try:
        from pypinyin import (  # type: ignore[import-not-found]  # noqa: PLC0415
            Style,
            lazy_pinyin,
        )
    except ImportError:
        return None
    letters = lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore")
    return "".join(letters).lower() or None


class SymbolPlane:
    """标的字典检索的实现；数据根可注入，单测写假缓存即可跑。"""

    def __init__(self, *, data_root: Path) -> None:
        self.data_root = Path(data_root)

    # ---------------------------------------------------------------- 读取

    def _collect(self) -> tuple[dict[str, dict[str, Any]], date | None]:
        """反查本机缓存：`code → {name, market, pinyin}` 与缓存里最新的交易日。

        后写覆盖先写：各数据集按 `trade_date` 升序读回，同一代码取**最新带名称**的
        那条；没有名称的行不许把已有名称冲掉。读不出（库不存在 / 表还没建 /
        缓存损坏）按空处理——检索是只读兜底，不该因一个坏表整条 500。
        """
        symbols: dict[str, dict[str, Any]] = {}
        as_of: date | None = None
        path = duckdb_store.db_path(self.data_root)
        if not path.is_file():
            return symbols, as_of
        conn = duckdb_store.connect(path, read_only=True)
        try:
            for dataset in NAME_DATASETS:
                if not duckdb_store.table_exists(conn, dataset):
                    continue
                try:
                    records = duckdb_store.query_dataset(
                        dataset, None, limit=MAX_DATASET_ROWS, conn=conn, root=self.data_root
                    )
                except Exception:  # noqa: BLE001 - 单个坏表不拖垮整个字典
                    continue
                for record in records:
                    code = str(pick(record, CODE_KEYS) or "").strip()
                    if not code:
                        continue
                    name = str(pick(record, NAME_KEYS) or "").strip()
                    existing = symbols.get(code)
                    if not name and existing is not None:
                        continue
                    day_text = str(record.get("trade_date") or "")[:10]
                    try:
                        day = date.fromisoformat(day_text)
                    except ValueError:
                        day = None
                    if day is not None and (as_of is None or day > as_of):
                        as_of = day
                    if existing is None or (day is not None):
                        symbols[code] = {
                            "name": name or (existing or {}).get("name"),
                            "market": self._market(record, code),
                        }
        finally:
            duckdb_store.close(conn)

        known = {code: row for code, row in symbols.items() if row.get("name")}
        for row in known.values():
            row["pinyin"] = _pinyin_of(str(row["name"]))
        return known, as_of

    @staticmethod
    def _market(record: dict[str, Any], code: str) -> str:
        raw = str(record.get("market") or "").strip().upper()
        return raw if raw in _MARKET_ALIASES else market_of(code)

    # ---------------------------------------------------------------- 检索

    def search(self, raw_query: Any, raw_limit: Any) -> dict[str, Any]:
        """`q` 归一化后按「代码精确 + 名称包含」匹配，命中按分排序截 `limit`。"""
        text = str(raw_query or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="缺少 q（代码或中文名称）")
        if len(text) > MAX_QUERY_LEN:
            raise HTTPException(status_code=422, detail=f"q 过长（≤ {MAX_QUERY_LEN} 字符）")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        query = normalize_query(text)
        term = query.lower()
        symbols, as_of = self._collect()
        has_source = bool(symbols)

        matches: list[dict[str, Any]] = []
        for code, row in sorted(symbols.items()):
            name = str(row.get("name") or "")
            score = self._score(query, term, code, name, row)
            if not score:
                continue
            matches.append(
                {
                    "symbol": code,
                    "name": name,
                    "market": row.get("market"),
                    "kind": "stock",
                    "board": board_of(code),
                    "pinyin": row.get("pinyin"),
                    "score": score,
                }
            )
        matches.sort(key=lambda item: (-int(item["score"]), item["symbol"]))
        matches = matches[:limit]
        for item in matches:
            del item["score"]  # 内部排序用，不进契约

        payload: dict[str, Any] = {
            "query": text,
            "limit": limit,
            "count": len(matches),
            "matches": matches,
            "data_source_status": "ok" if has_source else "missing",
        }
        envelope = LadderEnvelope(
            domain="sentiment",
            topic="symbol_search",
            data_as_of=as_of if has_source else None,
            payload=payload,
            source="ladderstock",
            source_detail=(
                f"local symbol cache: {'、'.join(NAME_DATASETS)}"
                f"（{len(symbols)} 只带名称的标的）"
            ),
            fetched_at=_now(),
            freshness="cached",
            confidence="medium" if has_source else "low",
            source_status="ok" if has_source else "partial",
        )
        return envelope.model_dump(mode="json")

    @staticmethod
    def _score(query: str, term: str, code: str, name: str, row: dict[str, Any]) -> int:
        """代码精确 100 / 代码前缀 70 / 名称相等 90 / 名称前缀 80 / 名称包含 60。"""
        if term and code == term:
            return 100
        if term.isdigit() and len(term) < 6 and code.startswith(term):
            return 70
        lowered = name.lower()
        if term and lowered == term:
            return 90
        if term and lowered.startswith(term):
            return 80
        if term and term in lowered:
            return 60
        pinyin = row.get("pinyin")
        if (
            isinstance(pinyin, str)
            and pinyin
            and (term.startswith(pinyin) or pinyin.startswith(term))
        ):
            return 75
        return 0


# --------------------------------------------------------------------------- #
# 闸门与路由
# --------------------------------------------------------------------------- #

async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单（照 `/api/research/*` 的形状，只读面不看写证据）。"""
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="标的字典只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )


def build_router(plane: SymbolPlane) -> APIRouter:
    """构造 `/api/symbols/search` 一个只读路由。"""
    router = APIRouter(prefix=SYMBOLS_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/search")
    def symbols_search(
        q: Annotated[str | None, Query(description="代码或中文名称")] = None,
        limit: Annotated[int | None, Query(description="返回条数，默认 10，钳制 1–50")] = None,
    ) -> dict[str, Any]:
        return plane.search(q, limit)

    return router


__all__ = [
    "NAME_DATASETS",
    "SYMBOLS_PREFIX",
    "SymbolPlane",
    "board_of",
    "build_router",
    "market_of",
    "normalize_query",
]
