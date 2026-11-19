"""agent 运行时：让模型自己决定「查不查、查什么、查几轮」。

与现役一次性 `ask()` 的差别只在**决策权**：那条链路由代码决定拼什么 prompt、调一次
模型；这里把取数封成工具交出去，由模型在 `Runner` 的循环里自行决定调用与轮次。

三个硬约束（都来自项目红线，不是设计偏好）：

1. **数字只能来自工具**。工具的每次调用都落到现役 `run_fetch` / `fetch_live`，
   返回给模型的是上游原样结果；取不到就如实说取不到，不补 0、不估算。
2. **工具必须能在安装包里跑**。所以只用本地取数（peer registry / sqlite），不依赖
   任何需要用户额外部署的外部服务。
3. **失败要能兜底**。SDK 这一层抛错、或模型一轮都没调工具时，调用方可以回落到
   现役 `ask_stream`；本模块不吞异常，只把错误原样抛出。

事件形状是**给装配层看的**，不是给前端看的：`run_turn_stream` 负责把它翻成 SSE 帧。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from agents import Agent, FunctionTool, OpenAIChatCompletionsModel, Runner
from openai import AsyncOpenAI

_LOG = logging.getLogger(__name__)

#: 单轮最多几次"模型→工具→模型"。到顶就停，避免模型反复查同一个数把请求拖死。
DEFAULT_MAX_TURNS = 6

#: 本机缓存表的数据日滞后多少天就要在表头显式标注。
#: 3 天覆盖「周末 + 一个节假日」的正常休市；超过就不是正常休市能解释的了。
STALE_DAYS_WARN = 3

#: 事件名（装配层按这几个分支处理）
EVENT_TEXT = "text"  # (名称, {"text": 累积全文})
EVENT_TOOL = "tool"  # (名称, {"name", "label", "state"})
EVENT_FINAL = "final"  # (名称, {"text": 全文, "metrics": {...}, "sources": [...]})


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的静态描述 + 执行体。

    `label` 是流水账里给人看的中文（"正在查上证指数"），不要塞进 description——
    description 是给模型看的，两者受众不同。

    `invoke` 返回 `ToolResult`：`text` 给模型读，`metrics` / `sources` 回传给装配层。
    **这是红线 4 的落地方式**：模型读到的数字与页面 metric 块里的数字是同一份工具
    结果，不是模型转述——转述过的数字就对不上出处了。
    """

    name: str
    label: str
    description: str
    params_schema: dict[str, Any]
    invoke: Callable[[dict[str, Any]], ToolResult]


@dataclass
class ToolResult:
    """一次工具调用的产出：给模型看的文本 + 给装配层的数据。

    `metrics` 必须能过 `answer_blocks.assert_provenance`（每个标量都带出处）；
    `sources` 是 `ChatSource` 同形 dict（`name` / `fetched_at` / 可选 `symbol` / `as_of`）。
    取不到时两者留空，只给 `text` 说明原因——不编数。
    """

    text: str
    metrics: dict[str, Any] | None = None
    sources: list[dict[str, Any]] | None = None


def _index_label(code: str) -> str:
    return {
        "sh000001": "上证指数",
        "sh000300": "沪深300",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "DJI": "道琼斯",
        "IXIC": "纳斯达克",
        "HSI": "恒生指数",
        "HSTECH": "恒生科技",
    }.get(code, code)


def _fnum(value: Any) -> float | None:
    """表格单元格只接受字符串 / 数字 / None；上游给的字符串数字在这里统一转 float。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def build_tools(data_root: Any) -> list[ToolSpec]:
    """构造首批工具。全部走现役取数链路，取不到返回"未取到"，不编数。"""

    def lookup_quote(args: dict[str, Any]) -> ToolResult:
        from ladderstock.web.qa_fetch import FetchPlan, _prefixed_code, run_fetch

        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            return ToolResult(text="缺少 symbol 参数，请给出股票代码（如 600000）")
        # 直接点名 `spot_quote`，不走 `build_plan` 的关键词判断：工具的参数语义是确定的
        # （"给我这只票的实时报价"），交给词表去猜只会被判成日线 K 线。
        plan = FetchPlan(
            symbol=symbol.partition(".")[0],
            dataset="spot_quote",
            params={"codes": _prefixed_code(symbol), "max_codes": 1},
        )
        metrics, sources = run_fetch(plan, data_root=data_root)
        if not metrics:
            reason = "；".join(plan.source_failures) or "上游没有返回可用记录"
            return ToolResult(text=f"{symbol} 未取到行情（{reason}）")
        payload = {"symbol": symbol, "metrics": metrics, "sources": sources}
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False),
            metrics=metrics,
            sources=sources,
        )

    def lookup_index(args: dict[str, Any]) -> ToolResult:
        from ladderstock.data.provider_registry import ProviderRegistry

        code = str(args.get("code") or "").strip()
        codes = [c.strip() for c in code.split(",") if c.strip()] or [
            "sh000001",
            "sh000300",
            "sz399001",
            "sz399006",
        ]
        try:
            registry = ProviderRegistry(root=data_root)
            outcome = registry.fetch_live(
                "index_quote",
                params={"codes": ",".join(codes)},
                allow_cache=False,
                timeout=8.0,
            )
        except Exception as exc:  # noqa: BLE001 - 取数失败是缺口，不是异常出口
            detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
            return ToolResult(text=f"指数未取到（{detail}）")
        envelope = getattr(outcome, "envelope", None)
        payload = getattr(envelope, "payload", {}) or {}
        records = payload.get("records") if isinstance(payload, dict) else None
        rows = [row for row in (records or []) if isinstance(row, dict)]
        if not rows:
            return ToolResult(text="指数未取到（上游返回 0 行）")
        # 多只指数共用同一批字段（close / change_pct …），标量键会互相覆盖，所以走表格：
        # 一行一只，四个数各自完整，不会被 `MAX_METRIC_ITEMS` 截断。
        table_rows: list[list[Any]] = []
        date_part = ""
        latest_ts = ""
        for row in rows:
            # 上游时间戳是 `20261113161427` 这种紧凑串，切成 `2026-11-13` 给人看。
            ts = str(row.get("timestamp") or "")
            if ts:
                latest_ts = ts
            if not date_part and len(ts) >= 8:
                date_part = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            table_rows.append(
                [
                    str(row.get("name") or row.get("code") or ""),
                    _fnum(row.get("price")),
                    _fnum(row.get("change_pct")),
                    _fnum(row.get("amount")),
                ]
            )
        # 口径随快照时钟变，不能一律写「收盘」：盘中问到的就是实时快照。
        clock = latest_ts[8:12] if len(latest_ts) >= 12 else ""
        caption = (
            "指数行情为当日收盘口径"
            if clock and clock >= "1500"
            else "指数行情为盘中实时快照（当日尚未收盘）"
        )
        metrics = {
            "table": {
                # 上游 `amount` 的单位是**万元**（tickhub index_quote），标「元」会把
                # 3461 亿写成 3461 万，差 10000 倍。
                "columns": ["指数", "最新点位", "涨跌幅(%)", "成交额(万元)"],
                "rows": table_rows,
                "caption": caption,
            }
        }
        sources = [
            {
                "name": "tickhub · index_quote",
                "fetched_at": _now_iso(),
                "as_of": date_part or None,
            }
        ]
        return ToolResult(
            text=json.dumps({"rows": rows}, ensure_ascii=False, default=str),
            metrics=metrics,
            sources=sources,
        )

    def lookup_watchlist(_args: dict[str, Any]) -> ToolResult:
        from pathlib import Path

        from ladderstock.journal.watchlist import list_watch

        try:
            rows = list_watch(root=Path(data_root)) if data_root else list_watch()
        except Exception as exc:  # noqa: BLE001
            detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
            return ToolResult(text=f"自选未取到（{detail}）")
        if not rows:
            return ToolResult(text="自选列表为空（用户还没有添加自选）")
        return ToolResult(text=json.dumps({"items": rows}, ensure_ascii=False, default=str))

    def lookup_symbols(args: dict[str, Any]) -> ToolResult:
        """名称 / 简称 / 拼音首字母 → 代码。

        用户往往只给「浦发」「茅台」这种口语称呼，行情工具要的是代码。没有这一步，
        模型只能反问「请给代码」，把一次可取数的问答降级成一次追问。
        """
        from pathlib import Path

        from ladderstock.cli.paths import data_dir
        from ladderstock.web.symbols import SymbolPlane

        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(text="缺少 query 参数，请给出股票名称、简称、拼音首字母或代码片段")
        try:
            limit = int(args.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(limit, 20))
        root = Path(data_root) if data_root else data_dir()
        try:
            # `SymbolPlane.search` 返回的是完整 envelope：标的在 `payload.matches` 下，
            # 不在顶层——按顶层取会永远拿到空列表，表现成「字典里没有匹配」。
            envelope = SymbolPlane(data_root=root).search(query, limit)
        except Exception as exc:  # noqa: BLE001 - 字典读不出是缺口，不是异常出口
            detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
            return ToolResult(text=f"标的字典未取到（{detail}）")
        payload = envelope.get("payload") if isinstance(envelope, dict) else {}
        matches = (payload or {}).get("matches") or []
        if not matches:
            return ToolResult(
                text=f"本机标的字典里没有匹配「{query}」；不要猜代码，请让用户补充公司全名或代码"
            )
        return ToolResult(text=json.dumps({"query": query, "matches": matches}, ensure_ascii=False))

    # ------------------------------------------------------------ 本机缓存批量数据集（A 批）
    # 这一批不联网：数据已经在 `sync` / 盘中快照里落进本机 DuckDB，工具只做「读最新一天 +
    # 排成表格」。口径与 `lookup_quote` 的现取现回不同——这里回答的是「盘面是什样」，
    # 不是「此刻这一只多少钱」，所以 as_of 如实写缓存里那一天，不假装是实时。

    def _root() -> Any:
        from pathlib import Path

        return Path(data_root) if data_root else None

    def _read(
        dataset: str, *, code: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        from ladderstock.data import duckdb_store

        try:
            return duckdb_store.query_dataset(dataset, code=code, limit=limit, root=_root())
        except Exception:  # noqa: BLE001 - 单个坏表按「没有这张表」处理
            return []

    def _latest_day(records: list[dict[str, Any]]) -> str | None:
        days = [str(row.get("trade_date") or "")[:10] for row in records]
        days = [day for day in days if day]
        return max(days) if days else None

    def _normalize_code(raw: Any) -> str | None:
        """把用户/模型给的代码归一成 6 位数字；取不到就返回 None。

        接受 `600519` / `600519.SH` / `sh600519` 三种形态。**不用 lstrip 剥前缀**
        ——那会按字符集吃掉代码本身的数字（`lstrip("shSHzszjbjBJ")` 的老写法有此坑）。
        """
        text = str(raw or "").strip().upper()
        if not text:
            return None
        match = re.search(r"(\d{6})", text)
        return match.group(1) if match else None

    def _stale_days(as_of: str | None) -> int | None:
        """数据日距今天（Asia/Shanghai）几天；无法判定返回 None。"""
        if not as_of:
            return None
        try:
            day = date.fromisoformat(as_of)
        except ValueError:
            return None
        today = datetime.now(timezone(timedelta(hours=8))).date()
        return max(0, (today - day).days)

    def _cell(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)[:180]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if value is None:
            return None
        return str(value)[:200]

    _name_planes: dict[str, Any] = {}

    def _names_from_basic(root: Any) -> dict[str, str]:
        """兜底：直接从 `symbol_basic` 只读表取 `code → name`。

        为什么不只靠 `SymbolPlane`：它要遍历全部数据集、给 5000+ 标的算首字母，
        冷路径下可能被别的进程（正式服 5950 与测试服 5951 共用同一份 data）拖住
        或抛异常；而异常原先被静默吞掉，页面就只剩「名称」整列空。这张表只读，
        不参与任何写锁，拿得到就先补上。
        """
        from ladderstock.data import archive

        try:
            snap = archive.snapshot("symbol_basic", root=root)
        except Exception as exc:  # noqa: BLE001 - 兜底也读不出才是真缺口
            _LOG.warning("symbol_basic 兜底读名失败：%s", exc)
            return {}
        names: dict[str, str] = {}
        for row in snap.records:
            code = _normalize_code(row.get("code"))
            name = str(row.get("name") or "").strip()
            if code and name:
                names[code] = name
        return names

    def _symbol_names() -> dict[str, str]:
        """`code → 名称`，先取本机标的字典（`SymbolPlane`），失败再回落 `symbol_basic`。

        为什么需要：`fund_flow` / `dragon_tiger` 这类上游数据集的 `name` 字段是空的
        （2026-11-19 实测 600519 的 name 为 null），直接展示会让表格「名称」整列空。
        两条都读不出才返回空表——宁可留空，也不拿别处的名字硬凑。
        """
        from ladderstock.cli.paths import data_dir
        from ladderstock.web.symbols import SymbolPlane

        root = _root() or data_dir()
        key = str(root)
        plane = _name_planes.get(key)
        if plane is None:
            plane = SymbolPlane(data_root=root)
            _name_planes[key] = plane
        try:
            # 复用同一个 SymbolPlane 实例：`_collect_cached` 只在字典文件变化时重算。
            # 每次新建实例会把 6000+ 标的的首字母重算一遍（历史实测把 /symbols 拖过 5s）。
            index, _ = plane._collect_cached()
        except Exception as exc:  # noqa: BLE001 - 字典读不出要能看见并回落
            _LOG.warning("symbol names 取自 SymbolPlane 失败，回落 symbol_basic：%s", exc)
            return _names_from_basic(root)
        names = {code: str(row.get("name") or "") for code, row in index.items()}
        if names:
            return names
        return _names_from_basic(root)

    def _cached_tool(
        *,
        name: str,
        label: str,
        description: str,
        datasets: dict[str, str],
        fields: list[str],
        columns: list[str],
        default_limit: int,
        caption: str,
        kind_hint: str | None = None,
        code_kinds: tuple[str, ...] | None = None,
    ) -> ToolSpec:
        """读一张（或按 `kind` 分几张）本机缓存表，排成表格块。

        `fields` 是 payload 里的键，`columns` 是对应的人读表头——两列必须等长，
        这里不猜字段别名：上游没给的值就留空，不补 0。

        `code_kinds` 声明哪些 kind 支持「按个股代码取数」（None = 该表不是个股维度）。
        给了 code 就**只回这只票的行、并按这只票自己的最新交易日**取；查不到就明说
        查不到，**绝不回退展示别的股票**——那正是「问茅台资金流却返回 000002」的成因：
        旧实现不传 code，且用整张表的最新日过滤，别的票更新了这只没更新就一行不剩。
        """
        first = next(iter(datasets.values()))

        def invoke(args: dict[str, Any]) -> ToolResult:
            kind = str(args.get("kind") or "").strip()
            dataset = datasets.get(kind) or first
            try:
                limit = int(args.get("limit") or default_limit)
            except (TypeError, ValueError):
                limit = default_limit
            limit = max(1, min(limit, 50))

            # 只有声明了个股维度的 kind 才接受 code；行业/概念表按代码过滤没有意义。
            effective_kind = kind or next(iter(datasets))
            accepts_code = code_kinds is not None and effective_kind in code_kinds
            code = _normalize_code(args.get("code")) if accepts_code else None
            if accepts_code and str(args.get("code") or "").strip() and code is None:
                return ToolResult(
                    text=f"{label}：代码 {args.get('code')!r} 不是合法的 6 位证券代码，未取数"
                )

            records = _read(dataset, code=code, limit=max(limit * 6, 200))
            if not records:
                if code:
                    return ToolResult(
                        text=(
                            f"{label}未取到（本机缓存里没有 {code} 的 {dataset} 记录，"
                            f"可能这只票还没同步过）"
                        )
                    )
                return ToolResult(
                    text=f"{label}未取到（本机缓存里没有 {dataset} 的记录，可能还没同步过）"
                )
            latest = _latest_day(records)
            rows = [
                row
                for row in records
                if latest is None or str(row.get("trade_date") or "")[:10] == latest
            ][:limit]
            if not rows:
                # 理论上到不了（latest 取自 records 自身），留作兜底：宁可说没有，
                # 也不把别的交易日的行当成最新日展示。
                return ToolResult(text=f"{label}未取到（{dataset} 没有可展示的记录）")
            names: dict[str, str] = {}
            # 上游 name 整列为空时才去查字典：查一次够用，也别为本来有名字的表白跑一趟。
            if "name" in fields and any(not str(row.get("name") or "").strip() for row in rows):
                names = _symbol_names()
            table_rows: list[list[Any]] = []
            for row in rows:
                cells: list[Any] = []
                for field in fields:
                    value = row.get(field)
                    if field == "name" and not str(value or "").strip():
                        value = names.get(str(row.get("code") or "").strip()) or None
                    cells.append(_cell(value))
                table_rows.append(cells)
            behind = _stale_days(latest)
            title = f"{caption}（{latest or '交易日未知'} 本机缓存）"
            if code:
                title = f"{caption} · {code}（{latest or '交易日未知'} 本机缓存）"
            if behind is not None and behind > STALE_DAYS_WARN:
                title += f"｜数据已滞后 {behind} 天，非当日口径"
            metrics = {"table": {"columns": columns, "rows": table_rows, "caption": title}}
            sources = [
                {
                    "name": f"本机缓存 · {dataset}",
                    "fetched_at": _now_iso(),
                    "as_of": latest,
                }
            ]
            payload = {
                "dataset": dataset,
                "as_of": latest,
                "count": len(rows),
                "rows": rows,
            }
            if code:
                payload["code"] = code
            if behind is not None:
                payload["stale_days"] = behind
            return ToolResult(
                text=json.dumps(payload, ensure_ascii=False, default=str),
                metrics=metrics,
                sources=sources,
            )

        properties: dict[str, Any] = {
            "limit": {"type": "integer", "description": f"返回条数上限，默认 {default_limit}"}
        }
        if kind_hint:
            properties["kind"] = {
                "type": "string",
                "enum": list(datasets),
                "description": kind_hint,
            }
        if code_kinds:
            properties["code"] = {
                "type": "string",
                "description": (
                    "个股 6 位代码（如 600519）。问某一只票时必须传，"
                    "否则返回的是全市场排行的前几行、不是这只票。"
                ),
            }
        return ToolSpec(
            name=name,
            label=label,
            description=description,
            params_schema={
                "type": "object",
                "properties": properties,
                "required": [],
                "additionalProperties": False,
            },
            invoke=invoke,
        )

    def kline_daily(args: dict[str, Any]) -> ToolResult:
        """个股日线（本机 `daily_k`，前复权收盘口径）。

        `daily_k` 的 payload 只有 OHLC 与量，没有 `change_pct`：涨跌幅是派生值，
        要算就得拿前一交易日收盘当分母。这里如实只给收盘与成交量，把「涨跌几个点」
        留给调用方（或 `lookup_quote` 的实时口径），不在这里造一个派生百分比。
        """
        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            return ToolResult(text="缺少 symbol 参数，请给出股票代码（如 600000）")
        code = symbol.partition(".")[0].lstrip("shSHzszjbjBJ")[-6:]  # noqa: B005
        try:
            days = int(args.get("days") or 10)
        except (TypeError, ValueError):
            days = 10
        days = max(3, min(days, 40))
        records = _read("daily_k", code=code)
        picked_live = False
        if not records:
            # 本机日线只回补过自选相关的那一小批票（实测 77 只）。用户问到别的票时
            # 现取一次，别把「没有缓存」说成「查不到这只票」——现取口径与 `daily_k`
            # 相同（tickhub · tencent_kline，日线前复权），只是数据源标注要改。
            from ladderstock.data.provider_registry import ProviderRegistry

            try:
                outcome = ProviderRegistry(root=data_root).fetch_live(
                    "kline_chart",
                    params={
                        "code": code,
                        "period": "daily",
                        "adjust": "qfq",
                        "limit": max(days, 20),
                    },
                    allow_cache=False,
                    timeout=10.0,
                )
                payload = getattr(getattr(outcome, "envelope", None), "payload", {}) or {}
                records = [row for row in (payload.get("records") or []) if isinstance(row, dict)]
                picked_live = bool(records)
            except Exception as exc:  # noqa: BLE001 - 现取失败是缺口，不是异常出口
                detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
                return ToolResult(text=f"{code} 日线未取到（本机无缓存，现取也失败：{detail}）")
            if not records:
                return ToolResult(text=f"{code} 日线未取到（本机无缓存，现取返回 0 行）")
        records = sorted(records, key=lambda row: str(row.get("trade_date") or ""))
        rows = records[-days:]
        table_rows = [
            [
                str(row.get("trade_date") or "")[:10],
                _fnum(row.get("open")),
                _fnum(row.get("close")),
                _fnum(row.get("high")),
                _fnum(row.get("low")),
                _fnum(row.get("volume")),
            ]
            for row in rows
        ]
        latest = str(rows[-1].get("trade_date") or "")[:10] or None
        source_name = "现取 · tickhub tencent_kline" if picked_live else "本机缓存 · daily_k"
        origin = "现取（tickhub · tencent_kline，日线前复权）" if picked_live else "本机日线缓存"
        metrics = {
            "table": {
                "columns": ["交易日", "开盘", "收盘", "最高", "最低", "成交量"],
                "rows": table_rows,
                "caption": f"{code} 最近 {len(table_rows)} 个交易日（前复权，{origin}）",
            }
        }
        sources = [{"name": source_name, "fetched_at": _now_iso(), "as_of": latest}]
        return ToolResult(
            text=json.dumps(
                {"code": code, "days": len(table_rows), "rows": rows},
                ensure_ascii=False,
                default=str,
            ),
            metrics=metrics,
            sources=sources,
        )

    extra = [
        ToolSpec(
            name="kline_daily",
            label="个股日线",
            description=(
                "查一只股票最近若干交易日的日线（开高低收、成交量）。"
                "用户问「最近走势」「这几天怎么走的」「历史价格」时调用。"
                "这是本机缓存的日线，不是实时报价；要现价用 lookup_quote。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "days": {
                        "type": "integer",
                        "description": "最近多少个交易日，默认 10，上限 40",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            invoke=kline_daily,
        ),
        _cached_tool(
            name="limit_ladder",
            label="涨停梯队",
            description=(
                "查当日涨停池 / 炸板 / 昨日涨停的个股名单。"
                "kind=limit_pool 当日涨停、kind=broken_board 当日炸板、"
                "kind=yesterday_limit_up 昨日涨停今日表现。"
                "用户问「今天哪些涨停」「谁炸板了」「连板梯队」时调用。"
            ),
            datasets={
                "limit_pool": "limit_pool",
                "broken_board": "broken_board",
                "yesterday_limit_up": "yesterday_limit_up",
            },
            fields=["name", "code", "change_pct", "amount", "streak", "turnover_rate"],
            columns=["名称", "代码", "涨跌幅(%)", "成交额(万元)", "连板", "换手率(%)"],
            default_limit=20,
            caption="涨停梯队",
            kind_hint="limit_pool 当日涨停 / broken_board 炸板 / yesterday_limit_up 昨日涨停",
        ),
        _cached_tool(
            name="board_rank",
            label="板块与概念",
            description=(
                "查板块（行业）或概念的涨跌幅、主力净流入、领涨股。"
                "kind=industry 行业板块、kind=concept 概念板块。"
                "用户问「今天什么板块强」「哪个概念在涨」时调用。"
            ),
            datasets={"industry": "industry_board", "concept": "concept_board"},
            fields=[
                "board_name",
                "change_pct",
                "main_net_inflow",
                "leader_name",
                "leader_change_pct",
            ],
            columns=["板块", "涨跌幅(%)", "主力净流入(元)", "领涨股", "领涨股涨幅(%)"],
            default_limit=15,
            caption="板块与概念排行",
            kind_hint="industry 行业板块 / concept 概念板块，默认 industry",
        ),
        _cached_tool(
            name="fund_flow",
            label="资金流",
            description=(
                "查资金流向。kind=stock 个股资金流、kind=industry 行业资金流排行。"
                "问某一只票（如「茅台资金流」）时必须传 code=该股 6 位代码，"
                "否则拿到的是全市场排行前几行、不是这只票。"
                "用户问「资金在往哪流」「主力净流入多少」时调用。"
            ),
            datasets={"stock": "fund_flow", "industry": "industry_fund_flow"},
            fields=["code", "name", "main_net", "main_pct", "change_pct"],
            columns=["代码/板块", "名称", "主力净额(元)", "主力净占比(%)", "涨跌幅(%)"],
            default_limit=15,
            caption="资金流向",
            kind_hint="stock 个股 / industry 行业，默认 stock",
            code_kinds=("stock",),
        ),
        _cached_tool(
            name="dragon_tiger",
            label="龙虎榜",
            description=(
                "查最近交易日的龙虎榜：成交额、买入卖出净额、净占比。"
                "用户问「龙虎榜」「谁在上榜」「游资动向」时调用。"
            ),
            datasets={"dragon_tiger": "dragon_tiger"},
            fields=["name", "code", "close", "change_pct", "net_amount", "net_ratio"],
            columns=["名称", "代码", "收盘", "涨跌幅(%)", "净买入(元)", "净占比(%)"],
            default_limit=15,
            caption="龙虎榜",
            code_kinds=("dragon_tiger",),
        ),
        _cached_tool(
            name="hot_rank",
            label="热度榜",
            description=(
                "查个股人气热度榜（热股榜）：排名、热度值、涨跌幅、概念标签。"
                "用户问「人气最高」「讨论最多」「热股」时调用。"
            ),
            datasets={"hot_rank": "hot_rank"},
            fields=["rank", "name", "code", "heat", "change_pct", "tag"],
            columns=["排名", "名称", "代码", "热度", "涨跌幅(%)", "标签"],
            default_limit=15,
            caption="个股热度榜",
            code_kinds=("hot_rank",),
        ),
        _cached_tool(
            name="news_flash",
            label="快讯与题材",
            description=(
                "查快讯 / 题材资讯。kind=flash 财联社等快讯标题摘要，"
                "kind=theme 赛道题材纪要（按 topic 归并）。"
                "用户问「有什么消息」「今天新闻」「题材面」时调用。"
            ),
            datasets={"flash": "flash_cls", "theme": "theme_news"},
            fields=["title", "content", "summary", "date"],
            columns=["标题", "正文摘要", "题材摘要", "日期"],
            default_limit=10,
            caption="快讯与题材资讯",
            kind_hint="flash 快讯 / theme 题材纪要，默认 flash",
        ),
        _cached_tool(
            name="finance_fundamentals",
            label="财务与业绩",
            description=(
                "查财务指标或业绩预告。kind=indicators 财务指标（EPS/ROE/营收/净利/增速），"
                "kind=earnings 业绩预告（预增预减、区间与幅度）。"
                "用户问「财报怎么样」「业绩预告」「ROE 多少」时调用。"
            ),
            datasets={"indicators": "finance_indicators", "earnings": "earnings_preview"},
            fields=[
                "code",
                "report_name",
                "eps",
                "roe",
                "revenue",
                "net_profit",
                "forecast_type",
                "change_pct_lower",
                "change_pct_upper",
            ],
            columns=[
                "代码",
                "报告期",
                "EPS",
                "ROE(%)",
                "营收(元)",
                "净利(元)",
                "预告类型",
                "增幅下限(%)",
                "增幅上限(%)",
            ],
            default_limit=15,
            caption="财务与业绩预告",
            kind_hint="indicators 财务指标 / earnings 业绩预告，默认 indicators",
        ),
    ]

    def query_tool(args: dict[str, Any]) -> ToolResult:
        """按「工具名 / 数据集名」取一次真实数据（DATASET_CATALOG 里登记的全部可用）。

        为什么要有这个：`/skills` 里 70 条方向的白名单写的是各 peer 的 MCP 工具名
        （`th_us_get_fundamentals` / `wire_get_digest` / `fund_flow` …）。不把入口打通，
        页面上的方向就只能"读步骤、不能执行"。本工具把它们统一接回现役取数链路：
        数据集必须在 `DATASET_CATALOG` 登记（归属 peer 与工具），未登记一律拒答。
        """
        from ladderstock.core.boundary import DATASET_CATALOG
        from ladderstock.data.provider_registry import ProviderRegistry

        target = str(args.get("tool") or args.get("dataset") or "").strip()
        if not target:
            return ToolResult(text="缺少 tool 或 dataset 参数")
        raw_params = args.get("params")
        params = dict(raw_params) if isinstance(raw_params, dict) else {}
        peer_key = str(args.get("peer") or "").strip()
        if peer_key:
            # 直通分支：技能白名单写的是各 peer 的原始 MCP 工具名（98 个里有一半不在
            # DATASET_CATALOG 登记），走这一支就不必为每个工具补一条数据集。
            registry = ProviderRegistry(root=data_root)
            peer = registry.peers.get(peer_key)
            if peer is None:
                known = ", ".join(sorted(registry.peers))
                return ToolResult(text=f"未知 peer「{peer_key}」；可用：{known}")
            if peer_key == "tickhub_us":
                params.setdefault("commercial_use", False)
            try:
                payload = registry.transport.call(peer, target, params, timeout=30.0)
            except Exception as exc:  # noqa: BLE001
                detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
                return ToolResult(text=f"{peer_key}/{target} 未取到（{detail[:180]}）")
            body = json.dumps(payload, ensure_ascii=False, default=str)
            if not body or body in ("{}", "null", "[]"):
                return ToolResult(text=f"{peer_key}/{target} 返回空（上游没有数据）")
            return ToolResult(text=f"{peer_key}/{target} 返回：\n{body[:6000]}")
        dataset = target if target in DATASET_CATALOG else None
        if dataset is None:
            dataset = next(
                (name for name, binding in DATASET_CATALOG.items() if binding.tool == target),
                None,
            )
        if dataset is None:
            return ToolResult(text=f"未登记的工具或数据集「{target}」；可用清单见本工具说明。")
        binding = DATASET_CATALOG[dataset]
        if binding.peer == "tickhub_us":
            # TickHub-US 每个工具都强制要合规声明（缺了直接 E_CONFIG / 校验失败）
            params.setdefault("commercial_use", False)
        try:
            outcome = ProviderRegistry(root=data_root).fetch_live(
                dataset, params=params, allow_cache=False, timeout=30.0
            )
        except Exception as exc:  # noqa: BLE001 - 取不到是缺口，不编数
            detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
            return ToolResult(text=f"{target} 未取到（{detail[:180]}）")
        envelope = getattr(outcome, "envelope", None)
        payload = getattr(envelope, "payload", {}) or {}
        records = payload.get("records") if isinstance(payload, dict) else None
        rows = [row for row in (records or []) if isinstance(row, dict)]
        if not rows:
            return ToolResult(text=f"{target} 未取到（上游返回 0 行）")
        shown = rows[:20]
        body = json.dumps(shown, ensure_ascii=False, default=str)
        head = (
            f"{target}（数据集 {dataset} @ {binding.peer}）"
            f"取到 {len(rows)} 行，前 {len(shown)} 行："
        )
        return ToolResult(text=f"{head}\n{body[:6000]}")

    def senate_debate(args: dict[str, Any]) -> ToolResult:
        """发起一次真实合议（分钟级），立刻返回 task_id，进度去 `/skills/53` 看。"""
        from ladderstock.web import senate_actions

        code = str(args.get("code") or "").strip()
        if not (len(code) == 6 and code.isdigit()):
            return ToolResult(text="code 需要 6 位 A 股代码，例如 600000")
        try:
            task = senate_actions.start_task(
                "analysis",
                "stock_senate_analysis",
                {"code": code, "hitl": False},
                senate_actions.ANALYSIS_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(text=f"发起失败：{type(exc).__name__}: {exc}")
        return ToolResult(
            text=(
                f"已发起 {code} 的合议，任务 {task.id}。整条链路分钟级（实测 8-9 分钟）；"
                f"进度与结论看 /skills/53，或 GET /api/senate/analysis/{task.id}。"
                "现在不要向用户复述评级——结果还没出来。"
            )
        )

    def _fc_call(tool: str, params: dict[str, Any]) -> ToolResult:
        """FinanceCoffer MCP 直调：#68 历史财报对比 / #69 公告语义检索 / #70 风险事件回溯。

        为什么单列三个工具、不只靠 query_tool 的 peer 分支：模型遇到「近三年营收」会先去挑
        TickHub 的 finance_indicators（那条链路的上游当前不可用），不会主动想到
        `peer=financecoffer`。给独立名字和明确说明，命中率才稳。
        """
        from ladderstock.data.provider_registry import ProviderRegistry

        clean = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        registry = ProviderRegistry(root=data_root)
        peer = registry.peers.get("financecoffer")
        if peer is None:
            return ToolResult(text="FinanceCoffer 未注册到取数通道（sources.yaml 缺 peer 段）")
        try:
            payload = registry.transport.call(peer, tool, clean, timeout=40.0)
        except Exception as exc:  # noqa: BLE001 - 取不到是缺口，不编数
            detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
            return ToolResult(text=f"financecoffer/{tool} 未取到（{detail[:200]}）")
        body = json.dumps(payload, ensure_ascii=False, default=str)
        return ToolResult(text=f"financecoffer/{tool} 返回：\n{body[:6000]}")

    def _announcement_search(params: dict[str, Any]) -> ToolResult:
        """公告语义检索：形态 2 下先查 LS 自己的 qdrant 知识库，兜底才回 FC MCP。

        为什么先 qdrant：FC 只留 ODS/DWD/DWS 结构化事实，公告正文由
        `ladderstock_knowledge` 承载（512 维 fastembed，与标的档案同一向量空间），
        一次检索能同时覆盖档案与公告。FC 的 pgvector 路留作兜底：库没投影、
        qdrant 挂了、或确实一条都没命中，都退回 FC 的 MCP 工具。
        """

        from ladderstock.knowledge import announcements as ann_module

        symbol = str(params.get("symbol") or "").strip()
        query = str(params.get("query_text") or "").strip()
        if not query:
            return ToolResult(text="query_text 不能为空")
        try:
            hits = ann_module.search(query, symbol=symbol or None, top_k=5)
        except Exception as exc:  # noqa: BLE001 - 知识库是可选依赖，失败不该让整轮挂掉
            # 兜底可以静默，但不能瞎：把本地知识库的失败原因带上，否则
            # 用户只看到「FC embedding 不可用」，无法判断是 qdrant 没起、
            # 模型没加载，还是这条数据还没投影。
            fallback = _fc_call("search_announcements_semantically", params)
            return ToolResult(
                text=(
                    f"{fallback.text}\n（本地知识库本次没检索："
                    f"{type(exc).__name__}: {str(exc)[:160]}）"
                )
            )
        if not hits:
            return _fc_call("search_announcements_semantically", params)
        lines = [f"公告语义检索：qdrant 命中 {len(hits)} 段（collection ladderstock_knowledge）"]
        for hit in hits:
            lines.append(
                f"- [{hit.score:.4f}] {hit.symbol} {hit.ann_date} 《{hit.title}》"
                f"\n  {hit.text[:400].strip()}"
            )
        return ToolResult(text="\n".join(lines))

    def _catalog_hint() -> str:
        """把 DATASET_CATALOG 里可用的数据集按 peer 分组列出来，交给模型选。"""
        from ladderstock.core.boundary import DATASET_CATALOG

        groups: dict[str, list[str]] = {}
        for name, binding in DATASET_CATALOG.items():
            groups.setdefault(binding.peer, []).append(name)
        return "\n".join(
            f"{peer}：{', '.join(sorted(names))}" for peer, names in sorted(groups.items())
        )

    return [
        ToolSpec(
            name="lookup_symbols",
            label="标的检索",
            description=(
                "把股票名称 / 简称 / 拼音首字母 / 代码片段解析成股票代码。"
                "用户只给了公司名（如「浦发」「茅台」）而没有代码时，先调用本工具拿代码，"
                "再用 lookup_quote 查行情；不要凭记忆猜代码。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "股票名称、简称、拼音首字母或代码片段",
                    },
                    "limit": {"type": "integer", "description": "返回条数上限，默认 8"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            invoke=lookup_symbols,
        ),
        ToolSpec(
            name="lookup_quote",
            label="个股行情",
            description=(
                "查一只股票的实时行情（现价、涨跌幅、成交额等）。"
                "用户问到某只股票当前价格或涨跌时必须调用，不得凭记忆回答。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600000 或 sh600000"},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            invoke=lookup_quote,
        ),
        ToolSpec(
            name="lookup_index",
            label="大盘指数",
            description=(
                "查大盘指数点位。默认返回上证指数、沪深300、深证成指、创业板指四格。"
                "用户问到大盘或指数涨跌时必须调用。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "指数代码，逗号分隔。可选 sh000001 / sh000300 / sz399001 / sz399006；"
                            "留空默认四格全查。"
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            invoke=lookup_index,
        ),
        ToolSpec(
            name="lookup_watchlist",
            label="自选列表",
            description=(
                "查用户的自选股列表与持仓。用户说到「我的自选」「持仓」「关注的那些票」时调用。"
            ),
            params_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            invoke=lookup_watchlist,
        ),
        ToolSpec(
            name="query_tool",
            label="通用取数",
            description=(
                "按工具名或数据集名从现役链路取一次真实数据，覆盖所有已登记数据集"
                "（A股行情/K线/财务/资金/公告/研报、美股、宏观、资讯等）。"
                "用户问到具体数据时必须用它取，不得凭记忆回答；取不到就如实说未取到。\n"
                'params 传上游工具自己的入参（如 {"symbol": "600000"} / {"symbol": "AAPL"}）；'
                "美股工具会自动补 commercial_use=false。\n"
                "可用数据集（peer：数据集名）：\n" + _catalog_hint()
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": "上游工具名，如 th_us_get_fundamentals、wire_get_digest",
                    },
                    "dataset": {
                        "type": "string",
                        "description": "或直接给数据集名，如 us_fundamentals、spot_quote",
                    },
                    "params": {
                        "type": "object",
                        "description": "透传给上游工具的参数对象",
                    },
                    "peer": {
                        "type": "string",
                        "description": (
                            "可选。给 peer 就直通该 peer 的 MCP 工具（技能白名单里的原始工具名，"
                            "如 tickhub / tickhub_us / wire / senate / financecoffer 都能调）；"
                            "不给则按已登记数据集取数。"
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            invoke=query_tool,
        ),
        ToolSpec(
            name="finance_statements",
            label="历史财报对比",
            description=(
                "按财年取同一只股票的连续财报（营收、净利润、ROE、PE、PB），用于「近三年营收」"
                "「连续五期净利润」「历年财报横向对比」这类问题。数据来自 FinanceCoffer 结构化仓"
                "（已经落库的年报口径）。返回里某年或某指标为 null 表示库里没有，如实说明缺口，"
                "不要用记忆补数，也不要用别的接口凑一个近似值。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6 位 A 股代码，如 600519"},
                    "year_range": {"type": "string", "description": "财年区间，如 2022-2026"},
                    "metric": {
                        "type": "string",
                        "description": (
                            "可选。只取某一指标（如 revenue / net_profit）；留空返回全指标"
                        ),
                    },
                    "valuation_basis": {
                        "type": "string",
                        "description": "可选。估值口径（如 pe / pb）；留空用库内默认",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            invoke=lambda args: _fc_call("query_structured_data", args),
        ),
        ToolSpec(
            name="announcement_search",
            label="公告语义检索",
            description=(
                "在一只股票的公告库做语义检索，例如「有没有提过分红调整」「募集资金用途变更怎么说」。"
                "关键词式提问（公告里有没有提到 X）用它，返回命中的公告片段；"
                "公告尚未入库或 embedding 服务不可用时会明确报依赖不可用，不要编造公告内容。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6 位 A 股代码，如 600519"},
                    "query_text": {"type": "string", "description": "要检索的语义问题或关键词"},
                    "year_range": {
                        "type": "string",
                        "description": "可选。公告年份区间，如 2024-2026",
                    },
                },
                "required": ["symbol", "query_text"],
                "additionalProperties": False,
            },
            invoke=_announcement_search,
        ),
        ToolSpec(
            name="risk_history",
            label="风险事件回溯",
            description=(
                "取一只股票的历史风险事件序列（诉讼、处罚、股权质押、业绩预警等）。"
                "用户问「有没有出过事」「历史上被处罚过吗」「风险事件回溯」时用它。"
                "返回 count=0 表示库里没有该股事件，是真实缺口，不要说成「没有风险」。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6 位 A 股代码，如 600519"},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            invoke=lambda args: _fc_call("get_risk_history", args),
        ),
        ToolSpec(
            name="senate_debate",
            label="发起多空辩论",
            description=(
                "对一只 A 股发起 StockSenate 完整合议（7 分析师 → 多空辩论 → 研究经理 → "
                "交易员 → 风控 → 终审）。分钟级，本工具只返回任务号；"
                "不要在回答里编造评级或结论，等结果出来再说。"
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "6 位 A 股代码，如 600000"},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            invoke=senate_debate,
        ),
        *extra,
    ]


def _to_function_tool(spec: ToolSpec, collector: dict[str, Any] | None = None) -> FunctionTool:
    """把 `ToolSpec` 包成 SDK 认的 `FunctionTool`。

    用显式构造而不是 `@function_tool` 装饰器：工具是运行时按 `data_root` 拼出来的，
    闭包拿不到稳定的 docstring 与类型注解，`function_tool` 的自动 schema 推导在那里
    不可靠。

    `collector` 收到每次工具调用的 `metrics` / `sources`。**工具结果必须回到装配层**：
    模型读了数字写进正文，正文里的数字随后会被块协议剥掉（红线 4），如果工具结果
    不同时交给 `build_blocks`，页面上就只剩「指标没取到」——取到过，但没人接住。
    """

    async def _invoke(_context: Any, args_json: str) -> str:
        try:
            args = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            return f"参数不是合法 JSON：{args_json[:120]}"
        # 取数是一次阻塞调用（httpx / sqlite），放线程里跑，别占住事件循环。
        result = await asyncio.to_thread(spec.invoke, args)
        if isinstance(result, ToolResult):
            if collector is not None:
                if result.metrics:
                    collector.setdefault("metrics", {}).update(result.metrics)
                if result.sources:
                    collector.setdefault("sources", []).extend(result.sources)
            return result.text
        return str(result)

    return FunctionTool(
        name=spec.name,
        description=spec.description,
        params_json_schema=spec.params_schema,
        on_invoke_tool=_invoke,
        strict_json_schema=False,
    )


def build_agent(
    *,
    model: str,
    base_url: str,
    api_key: str,
    instructions: str,
    tool_specs: list[ToolSpec],
    name: str = "LadderStock 研究助手",
    timeout_sec: float = 60.0,
    collector: dict[str, Any] | None = None,
) -> tuple[Agent[Any], AsyncOpenAI]:
    """构造 agent。返回客户端是因为调用方要负责 close，别在事件循环里漏连接。

    `collector` 与 `run_agent_stream(..., collector=...)` 传同一个 dict，工具跑出来的
    `metrics` / `sources` 才会被 `final` 事件带出去。
    """
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_sec, max_retries=1)
    chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
    agent: Agent[Any] = Agent(
        name=name,
        instructions=instructions,
        tools=[_to_function_tool(spec, collector) for spec in tool_specs],
        model=chat_model,
    )
    return agent, client


async def run_agent_stream(
    agent: Agent[Any],
    prompt: str,
    *,
    labels: dict[str, str] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    collector: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """跑一轮 agent，边跑边产出事件。

    产出三类：

    - `("tool", {"name","label","state"})`：`state` 是 `running` 或 `done`；
    - `("text", {"text": 累积全文})`：正文增量，**累积**，前端整段替换；
    - `("final", {"text", "metrics", "sources"})`：收尾。正文以 `final_output` 为准
      （tool 轮次里模型可能先说一句再调工具，那段不算最终答案）；`metrics` /
      `sources` 是这一轮所有工具调用的真实产出，交给装配层进 metric / citation 块。

    `labels` 是 `工具名 → 中文标签`（来自 `build_tools` 的 `ToolSpec.label`）；不传就退回
    工具名本身，流水账那里会显示英文。
    """
    label_of = dict(labels or {})
    by_call_id: dict[str, str] = {}
    sink: dict[str, Any] = collector if collector is not None else {}

    result = Runner.run_streamed(agent, prompt, max_turns=max_turns)
    text = ""
    async for event in result.stream_events():
        kind = getattr(event, "type", "")
        if kind == "raw_response_event":
            data = getattr(event, "data", None)
            if getattr(data, "type", "") == "response.output_text.delta":
                text += getattr(data, "delta", "") or ""
                yield EVENT_TEXT, {"text": text}
            continue
        if kind == "run_item_stream_event":
            name = getattr(event, "name", "")
            item = getattr(event, "item", None)
            raw = getattr(item, "raw_item", None)
            if name == "tool_called":
                call_id = getattr(raw, "call_id", "") or ""
                tool_name = getattr(raw, "name", "") or ""
                by_call_id[call_id] = tool_name
                yield (
                    EVENT_TOOL,
                    {
                        "name": tool_name,
                        "label": label_of.get(tool_name, tool_name),
                        "state": "running",
                    },
                )
            elif name == "tool_output":
                call_id = str(raw.get("call_id") or "") if isinstance(raw, dict) else ""
                tool_name = by_call_id.get(call_id, "")
                yield (
                    EVENT_TOOL,
                    {
                        "name": tool_name,
                        "label": label_of.get(tool_name, tool_name),
                        "state": "done",
                    },
                )
            continue

    final = getattr(result, "final_output", None)
    final_text = str(final) if final is not None else text
    yield (
        EVENT_FINAL,
        {
            "text": final_text,
            "metrics": dict(sink.get("metrics") or {}),
            "sources": list(sink.get("sources") or []),
        },
    )
