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
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from agents import Agent, FunctionTool, OpenAIChatCompletionsModel, Runner
from openai import AsyncOpenAI

#: 单轮最多几次"模型→工具→模型"。到顶就停，避免模型反复查同一个数把请求拖死。
DEFAULT_MAX_TURNS = 6

#: 事件名（装配层按这几个分支处理）
EVENT_TEXT = "text"      # (名称, {"text": 累积全文})
EVENT_TOOL = "tool"      # (名称, {"name", "label", "state"})
EVENT_FINAL = "final"    # (名称, {"text": 全文, "metrics": {...}, "sources": [...]})


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
    invoke: Callable[[dict[str, Any]], "ToolResult"]


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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
            "sh000001", "sh000300", "sz399001", "sz399006"
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
        for row in rows:
            # 上游时间戳是 `20261113161427` 这种紧凑串，切成 `2026-11-13` 给人看。
            ts = str(row.get("timestamp") or "")
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
        metrics = {
            "table": {
                "columns": ["指数", "最新点位", "涨跌幅(%)", "成交额(元)"],
                "rows": table_rows,
                "caption": "指数行情为同一交易日收盘口径",
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

    return [
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
                yield EVENT_TOOL, {
                    "name": tool_name,
                    "label": label_of.get(tool_name, tool_name),
                    "state": "running",
                }
            elif name == "tool_output":
                call_id = str(raw.get("call_id") or "") if isinstance(raw, dict) else ""
                tool_name = by_call_id.get(call_id, "")
                yield EVENT_TOOL, {
                    "name": tool_name,
                    "label": label_of.get(tool_name, tool_name),
                    "state": "done",
                }
            continue

    final = getattr(result, "final_output", None)
    final_text = str(final) if final is not None else text
    yield EVENT_FINAL, {
        "text": final_text,
        "metrics": dict(sink.get("metrics") or {}),
        "sources": list(sink.get("sources") or []),
    }
