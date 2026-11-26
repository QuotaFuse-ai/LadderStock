"""页级研究助手（QUOT-454）：把页面上下文 + 问题交给**既有** AI 通道。

本模块只做三件事：

1. 校验请求（问题 / 上下文长度），把页面上下文拼进同一条用户消息；
2. 按 `config/ai.yaml` + `ai.env` 选择客户端——仍然走
   `ai/cli_provider.build_chat_client()`（订阅版 CLI / OpenAI 兼容 / Anthropic），
   **不新起一套模型调用、不读也不回显密钥**；
3. 把失败翻译成可读、可重试的 HTTP 状态：未配 Key、断网、超时分开说。

AI 回答只是文本，不会回写页面上的行情 / 财务数据（页面数据仍以接口为准）。

QUOT-487 在同一条调用链上接了跨会话记忆：提问前先召回（`ai/memory.py`），把
结果作为独立分段注入系统提示；回复后按策略异步写入。记忆不可达时静默降级为
无记忆模式——回答照常出，只在日志留一条 `memory_unavailable`。命名空间跟随
模块（QUOT-498）：`agent_id` + `kb_scope` 两维同时到位，缺命名空间就不检索。

QUOT-566 在同一段系统提示里再接一路知识库检索：从问题 / 标的代码解析出
`market:symbol`，经 `knowledge/recall.py` 的 `recall_wrapped()` 取带 as_of 头与
不可信数据边界的档案正文，与记忆段并列成**独立分段**。无代码 / 无档案 / 读坏了
都静默跳过档案段（日志 `knowledge_unavailable`），绝不影响本来能答的问题。

QUOT-580（D3 拍板「能力迁进现役，暂不删 chat.py」）在现役链路旁边追加会话模式：
`run_turn()` 每轮把已到达的块 upsert 进 `data/chat_store.py`（独立 `chat.sqlite`，
D1），块由现役的 `reply_to_structured()` → `build_blocks()` 装配（不另起一套），
`/api/conversation` 只读 / 改名 / 续问；`chat.py` 本体保留不删、不挂载。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import socket
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ladderstock.ai import (
    answer_blocks,
    fallback,
    memory,
    module_memory,
    plans,
    preferences,
    reflection,
)
from ladderstock.ai.agent_runtime import (
    EVENT_FINAL as AGENT_FINAL,
)
from ladderstock.ai.agent_runtime import (
    EVENT_TEXT as AGENT_TEXT,
)
from ladderstock.ai.agent_runtime import (
    EVENT_TOOL as AGENT_TOOL,
)
from ladderstock.ai import backfill, ledger, number_audit, recall, research_plan
from ladderstock.research import debate
from ladderstock.ai.agent_runtime import (
    build_agent,
    build_tools,
    run_agent_stream,
)
from ladderstock.ai.answer_blocks import (
    CAPABILITY_BRIEF,
    AnswerBlockError,
    build_block,
    normalize_ai,
)
from ladderstock.ai.answer_blocks import build_blocks as _build_blocks
from ladderstock.ai.cli_provider import CliUnavailable, build_chat_client
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.intent import INTENT_DATA_QUERY, classify_intent
from ladderstock.ai.providers import ProviderError, UnconfiguredError
from ladderstock.cli.paths import data_dir
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.data import chat_store
from ladderstock.knowledge.recall import recall_wrapped
from ladderstock.web.qa_fetch import FetchPlan, build_plan, run_fetch
from ladderstock.web.research import _guard

logger = logging.getLogger("ladderstock.web.assistant")

ASSISTANT_PREFIX = "/api/research"
#: 单次问答的上限：问题够长，上下文只放摘要，避免把整页产物塞进 prompt
MAX_QUESTION_CHARS = 2000
MAX_CONTEXT_CHARS = 12000
#: 与 `ai/cli_provider` / `orchestrator` 的默认值一致；页面侧再给 90s 的请求超时
ASSISTANT_TIMEOUT_SEC = 60.0

#: 页面键（`pageBindings.ts` 的路由前缀）→ 子模块（QUOT-498）。
#: 未列出的页面落 `tickhub`：抽屉问答读的都是行情/自选这类 TickHub 数据。
PAGE_KEY_MODULES: dict[str, str] = {
    "/market": "tickhub",
    "/watch": "tickhub",
    "/intraday": "tickhub",
    "/my-stocks": "tickhub",
    "/review": "senate",
    "/weekly": "senate",
    "/pool": "senate",
    "/journal": "senate",
    "/validate": "senate",
    "/limit-up": "senate",
    "/research": "senate",
    "/industry": "tickhub",
    "/signals": "tickhub",
    "/news": "wire",
    "/calendar": "wire",
    "/inbox": "wire",
}
#: 没给页面键 / 页面键未知时的兜底子模块
DEFAULT_MODULE = "tickhub"


def module_for(page_key: str | None) -> str:
    """页面键 → 子模块；`/market?tab=x` 这类带查询串的键按路径部分匹配。"""
    if not page_key:
        return DEFAULT_MODULE
    path = page_key.split("?", 1)[0].rstrip("/") or "/"
    return PAGE_KEY_MODULES.get(path, DEFAULT_MODULE)


# 能力问题的口径（首页 / 深挖 / 页面抽屉共用这一段）：**只固定范围，不固定措辞**。
# 用户明确要求「同一个问题问两次可以稍有不同」，所以这里给的是可自由改写的清单，
# 不是逐字照抄的模板；重复问时换个说法讲同一组能力即可。
_CAPABILITY_RULE = (
    "\n【能力说明】用户问你会什么 / 你能做什么 / 你在这页能做什么（或类似问法）时：\n"
    "- 直接以「你可以…」这类第二人称句子回应，**不要**再用「我是 LadderStock 研究助手」开头"
    "做自我介绍（同一会话里已经说过就不必再说一遍）。\n"
    "- 只挑 3–4 条与本页、本轮问题最相关的能力展开，其余用一句「其他行情、情绪、资金、财报也能查」"
    "带过；**不要把能力清单逐条搬一遍**，整段回答控制在 6 行以内。\n"
    "- 每次换个切入角度：按「看什么」分类、给两三个具体场景例子、或按本页能干什么来讲都行，"
    "但不得超出下面这份范围。\n"
    "- 结尾一定落到一个可执行的下一步：反问要看哪只票 / 哪个板块，或直接给出一个建议动作。\n"
    "- 「不做的事」（不给买卖建议、不预测价格、不拿记忆补数字）只用一句话概括，不要展开。\n"
    "- 用户只是打招呼（你好 / 在吗 / 有人吗）时，同样直接给「你可以…」+ 一个下一步，"
    "**不要**报身份、也不要说「页面数据没取到」这类话——那会让打招呼看起来像故障通知。\n"
    "- 全篇不要以「我是 LadderStock 研究助手」开头；身份只在用户明确问「你是谁」时用半句带过。\n"
    "- 不要罗列「这一项没取到」。\n"
    "- 能力范围：" + CAPABILITY_BRIEF + "\n"
)

SYSTEM_PROMPT = (
    "你是 LadderStock「个股深挖」页的研究助手。只依据【页级上下文】里给出的数据回答，"
    "不要编造上下文里没有的数字、日期或结论。\n"
    "- 上下文缺失的行情 / 财报 / 事件，就直说「这一项页面没有取到」，不要用一般知识补。\n"
    "- 用户问「该查哪些数据 / 怎么核对」这类方法问题时，直接给一份可执行的查证清单"
    "（查什么、在哪个页面查、核对什么口径），不要回答成「页面没有取到」。\n"
    "- 用户问某家公司 / 某只股票、但没说是哪一家时，"
    "**先反问标的**：一句话说明你是 LadderStock 助手、"
    "能做什么，再请他给公司名或代码。不要罗列一串「这一项页面没有取到」，那是在答另一个问题。\n"
    "- 上下文里的数字只是页面已展示数据的摘要，不能当作新的行情结论；"
    "不要给出买卖建议，也不要预测价格。\n"
    "- 回答用简洁中文，必要时分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。\n"
    "- 用户让你「持续盯着某只票 / 某个板块」（盯住、跟踪、留意、每天看）时，"
    "**你已经能接**：这条目标会被自动写进【进行中的研究任务】，按它说的节奏"
    "（每天盘后 / 盘中间隔 / 有异动）自己推进，结论进「提醒」。所以直接答应，并说清"
    "「盯哪几只、什么节奏、比哪几项变化」；不要回答「我没有后台自动盯盘的能力」。"
) + _CAPABILITY_RULE

# QUOT-476：/skills 页没有个股深挖页的页面数据，预设跟着页面走——
# 技能助手只拿页键 / 绑定技能 / 对象三键上下文，口径改为「技能步骤引导 + 不编造」。
SKILL_SYSTEM_PROMPT = (
    "你是 LadderStock 研究助手，正在辅助一个页面技能包的研究流程。"
    "只依据【页级上下文】里给出的信息回答，不要编造其中没有的数字、日期或结论。\n"
    "- 上下文缺失的行情 / 财报 / 事件，就直说「这一项我还没拿到」，不要用一般知识补。\n"
    "- 用户的问题可能对应技能包的某一步，先顺着该步骤要研究什么来引导；"
    "上下文没给出技能步骤时，就按问题本身回答。\n"
    "- 不要给出买卖建议，也不要预测价格。\n"
    "- 回答用简洁中文，必要时分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。\n"
    "- 用户让你「持续盯着某只票 / 某个板块」（盯住、跟踪、留意、每天看）时，"
    "**你已经能接**：这条目标会被自动写进【进行中的研究任务】，按它说的节奏"
    "（每天盘后 / 盘中间隔 / 有异动）自己推进，结论进「提醒」。所以直接答应，并说清"
    "「盯哪几只、什么节奏、比哪几项变化」；不要回答「我没有后台自动盯盘的能力」。"
)

# QUOT-487：记忆段与档案段是系统提示里的独立分段，各自标注来源；
# 冲突时以档案（带 as_of）与页级上下文为准，不许静默采用记忆。
_MEMORY_CONFLICT_RULE = (
    "- 【对话记忆】只是过往对话留下的模糊线索，可能已经过时；"
    "【标的档案】带 as_of 与来源，优先级更高。"
    "若记忆与档案或页级上下文冲突，以档案与页级上下文为准，"
    "并在回答里说明「记忆里是旧说法」，不要静默采用记忆。"
)


# QUOT-493：/api/page/assistant 服务的是「任意页面」（watch / industry / calendar …）
# 的抽屉问答，预设跟着页面走：只认本页上下文、缺项直说没取到、且明确不做个股深挖——
# 深挖仍由 /api/research/{code}/assistant 承担，行为不变。
PAGE_SYSTEM_PROMPT = (
    "你是 LadderStock 页面助手，只回答当前页面（行情 / 产业 / 日历等）的问题。"
    "只依据【页级上下文】里给出的数据回答，不要编造其中没有的行情、新闻、财报数字、日期或结论。\n"
    "- 上下文缺失的行情 / 新闻 / 财报 / 事件，就直说「本页这项没取到」，"
    "不要用一般知识补。\n"
    "- 用户问某家公司 / 某只股票、但没说是哪一家时，**先反问标的**："
    "一句话说明你是 LadderStock 助手、"
    "能做什么，再请他给公司名或代码。不要罗列「这一项页面没有取到」，那是在答另一个问题。\n"
    "- 这是页面内的轻量问答，不做单只股票的深度研究；需要深挖单个标的时，请回到个股研究页再问。\n"
    "- 上下文里的数字只是页面已展示数据的摘要，不能当作新的行情结论；"
    "不要给出买卖建议，也不要预测价格。\n"
    "- 回答用简洁中文，必要时分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。\n"
    "- 用户让你「持续盯着某只票 / 某个板块」（盯住、跟踪、留意、每天看）时，"
    "**你已经能接**：这条目标会被自动写进【进行中的研究任务】，按它说的节奏"
    "（每天盘后 / 盘中间隔 / 有异动）自己推进，结论进「提醒」。所以直接答应，并说清"
    "「盯哪几只、什么节奏、比哪几项变化」；不要回答「我没有后台自动盯盘的能力」。"
) + _CAPABILITY_RULE

# agent 路径追加段：上面三套预设都是「只依据页级上下文」的问答口径，而 agent 本轮
# 能自己调工具取真数。不补这一段，「上下文里没有就不要说」会被读成「不许调工具」，
# 模型明明有 lookup_quote 也不用，退化成只会说「这一项没取到」。
AGENT_TOOL_RULE = (
    "\n【本轮可用工具】你可以在本轮内调用工具取真实数据，取到之后再回答。\n"
    "- 用户只给了名称或简称（「浦发」「茅台」）时，先 lookup_symbols 拿代码，"
    "再查行情；不要猜代码。\n"
    "- 问个股当前行情 → lookup_quote；问大盘指数 → lookup_index；问自选/持仓 → lookup_watchlist。\n"
    "- 问个股历史走势 → kline_daily；问涨停/炸板/连板梯队 → limit_ladder；"
    "问板块或概念强弱 → board_rank；问资金流向 → fund_flow；问龙虎榜 → dragon_tiger；"
    "问人气热股 → hot_rank；问消息与题材 → news_flash；问财务或业绩预告 → finance_fundamentals。\n"
    "- 这一批（除 lookup_quote / lookup_index 外）读的是**本机缓存的最新交易日**，不是实时行情；"
    "回答里要如实带上这个日期（工具结果里的 as_of），不要说成「今天实时」。\n"
    "- 工具返回「未取到」时如实说这一项没取到，不要用记忆、常识或推算补数字。\n"
    "- 用户问你是谁、能做什么（如「介绍下自己」）时，直接说明你是 LadderStock 研究助手、"
    "能做什么、不能做什么，不要当成数据查询，也不要罗列「这一项没取到」。\n"
    "- 用户问某公司但没说是哪家时，说明你能做什么，再请他给公司名或代码。\n"
    "- 同一轮里可以多次调工具：先查一只、看结果不够再查另一只，直到能直接回答。\n"
    "- **判断类回答必须给翻转条件**：问「值不值得买 / 趋势会不会延续 / 估值贵不贵 /"
    "多空怎么看」这类要给方向的问题时，正文最后单独一行写「翻转条件：…」，用**能观测到**"
    "的阈值或事件（价格位、毛利率、公告、政策落地），不要写「视情况而定」「需持续跟踪」"
    "这种谁都能说的话。纯事实查询（报价、名单、财务数字）不用写这一行。\n"
)


class ChatSource(BaseModel):
    """调用方的一次真实取数记录：来源名 + 取数时间（ISO 8601）+ 可选链接。

    QUOT-579（D3）：从 `web/chat.py` 迁入现役链路——`chat.py` 保留不删、不挂载，
    这里是现役三路由使用的同一份字段语义。`symbol` / `as_of` 为阶段 B 追加，
    缺失时如实留空，不补默认值。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    fetched_at: str
    url: str | None = None
    symbol: str | None = None
    as_of: str | None = None


class ContextHistoryTurn(BaseModel):
    """结构化上下文里的一轮历史（QUOT-579 / 581「停止后转上下文」落点 A）。

    只是**文本**：`blocks_seq` 记该轮已渲染块的序号，供前端回显，不参与取值；
    历史文本不进 metrics、不当来源（红线 4 / 6）。
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    text: str = ""
    blocks_seq: list[int] = Field(default_factory=list)


class AsyncStructuredContext(BaseModel):
    """结构化页级上下文：`{page, data, sources, history}`（对齐 `chat.py:111 ChatRequestContext`）。

    `data` 由调用方裁剪，服务端不主动补数据；`sources` 直接喂给 `build_blocks`；
    `history` 可选，缺省时行为与只有 `{page, data, sources}` 时完全一致。
    """

    model_config = ConfigDict(extra="forbid")

    page: str | None = None
    data: dict[str, Any] | None = None
    sources: list[ChatSource] = Field(default_factory=list)
    history: list[ContextHistoryTurn] = Field(default_factory=list)


class AssistantRequest(BaseModel):
    """页面发来的最小载荷：问题 + 上下文（旧字符串形态或结构化形态）。"""

    question: str = ""
    context: str | AsyncStructuredContext = ""
    date: str | None = None
    tab: str | None = None


# QUOT-476：/skills 页没有 code，抽屉把页面键 / 绑定技能 / 对象拼进 context 文本后
# 再带上这三个键，方便日志与服务端理解来源；三者均可缺省。
class SkillAssistantRequest(BaseModel):
    """技能页抽屉发来的载荷：问题 + 页面键上下文（与 `/research` 同一约定）。"""

    question: str = ""
    context: str | AsyncStructuredContext = ""
    page_key: str | None = None
    bound_skill: str | None = None


# QUOT-493/QUOT-477：page 通道与 research / skills 同构——上下文统一为前端序列化好的
# 文本（`AiDock` 的 serializedContext），本端不再做 JSON 解析，避免两侧类型各执一端。
class PageAssistantRequest(BaseModel):
    """任意页面抽屉发来的载荷：问题 + 页级上下文（文本或结构化，同一约定）。"""

    question: str = ""
    context: str | AsyncStructuredContext = ""
    page_key: str | None = None
    bound_skill: str | None = None


class AssistantFailure(RuntimeError):
    """可读的降级失败：`status` / `code` 供前端区分无 Key、断网与超时。"""

    def __init__(self, status: int, code: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.retryable = retryable


def _failure_response(exc: AssistantFailure) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "retryable": exc.retryable,
            "reply": "",
            "blocks": _failure_blocks(exc),
        },
    )


# --------------------------------------------------------------------------- #
# 答案块接缝（QUOT-579，阶段 B）
# --------------------------------------------------------------------------- #


#: 现役 `ask()` 不做结构化输出（`json_mode=False`），`reply` 是纯文本；本适配器把 reply
#: 按句切成 `narrative`，供 `build_blocks` 装配 conclusion。**不让模型产出块**（红线 2）。
_SENTENCE_RE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")
#: 分点列表的行首标记：`- ` / `* ` / `• ` / `1. ` / `1、` / `1)`。
#: 命中两行以上就按「列表」处理——整行成一条，不再按句切开，否则首句被抽去当
#: 结论、剩下的半句散进补充说明，用户看到的就是断成两截。
_LIST_MARK_RE = re.compile(r"^(?:[-*•·]|\d+[.、)])\s*")

#: 归类线索：只做**确定性**判定——句子以线索词**开头**才归类，不猜语义、不补内容。
#: 正文中间顺带提到「风险」不算风险句，宁可留在 narrative 也不误收（切不干净就留空）。
_RISK_HINTS: tuple[str, ...] = (
    "风险",
    "注意",
    "警惕",
    "慎防",
    "不确定性",
    "下行风险",
    "回撤风险",
)
_HIGHLIGHT_HINTS: tuple[str, ...] = (
    "亮点",
    "值得关注",
    "值得注意",
    "重点关注",
)


def _classify_sentence(sentence: str) -> str:
    """单句归类：`risk` / `highlight` / `narrative`。

    确定性规则（无模型、无随机）：去掉前导标点后以线索词开头才归类。
    """
    head = sentence.lstrip("，,、：: 　")
    for hint in _RISK_HINTS:
        if head.startswith(hint):
            return "risk"
    for hint in _HIGHLIGHT_HINTS:
        if head.startswith(hint):
            return "highlight"
    return "narrative"


def reply_to_structured(reply: str) -> dict[str, Any]:
    """把纯文本 `reply` 聚合成最小结构化 `ai` payload（不调模型、不改 `json_mode`）。

    **确定性推导**（本单追加交付）：同一输入永远得到同一输出，不含随机、时间戳或模型调用。

    - 按句切分（`。！？；`/换行/英文句读），保住原文顺序；
    - **首句固定进 `narrative`**（`build_blocks` 第 1 步只取 `narrative[0]` 作结论）；
    - 其余句子以风险 / 亮点线索开头的分别归入 `risks` / `highlights`，其余进 `narrative`；
    - `evidence` **恒为空数组**——纯文本里无法可靠地区分「取数记录」与正文，宁可留空，
      由 `sources` 承担出处（红线 6：来源只从 `context.sources` / 抓取结果来）。

    硬约束：不产生任何数字（数值只来自 `metrics`，红线 4 / 6）；切不干净就留空，
    **不编造**。数值剥离等后续红线由 `build_blocks` 统一执行。

    返回 `{"ai_structured": 四键数组, "build_input": normalize_ai(四键数组)}`：
    前者旁挂进响应（`ai_structured`），后者原样可喂 `build_blocks(metrics, ..., sources)`。
    """
    text = (reply or "").strip()
    # 分点式回答按**整行**切：一行就是一个要点。逐句切会把第一行的首句抽去当结论、
    # 其余半句散进「补充说明」，同一段话被劈成两处，用户读到的是断句。
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    is_list = sum(1 for line in lines if _LIST_MARK_RE.match(line)) >= 2
    if is_list:
        sentences = lines
    else:
        sentences = [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]
    if not sentences and text:
        sentences = [text]
    narrative: list[str] = []
    highlights: list[str] = []
    risks: list[str] = []
    for index, sentence in enumerate(sentences):
        bucket = "narrative" if index == 0 else _classify_sentence(sentence)
        if bucket == "risk":
            risks.append(sentence)
        elif bucket == "highlight":
            highlights.append(sentence)
        else:
            narrative.append(sentence)
    structured: dict[str, Any] = {
        "narrative": narrative,
        "highlights": highlights,
        "risks": risks,
        "evidence": [],
    }
    # 直通 `build_blocks` 的形状：句子级切分几乎过不了它的块数上限，此前直接把
    # `reply_to_structured(reply)` 交给 `build_blocks` 会在长回答上抛
    # `AnswerBlockError`（块数超限）；`normalize_ai` 归一会把 `narrative[1:]` 与
    # `highlights` 合并成一条「补充说明」、`risks` 取前两条，用它的归一结果喂
    # `build_blocks` 才稳。归一失败（理论不可达）退回原数组，不吞错。
    try:
        build_input = normalize_ai(structured)
    except AnswerBlockError:  # pragma: no cover - 入参全为 str 列表，走不到
        build_input = structured
    return {"ai_structured": structured, "build_input": build_input}


def _render_history(history: Sequence[ContextHistoryTurn]) -> str:
    """历史轮次 → 纯文本段落（用户/助手交替配对）；空文本轮次跳过。"""
    lines: list[str] = []
    pending_user: str | None = None
    for turn in history:
        text = (turn.text or "").strip()
        if turn.role == "user":
            pending_user = text
            continue
        if turn.role != "assistant":
            continue
        if pending_user is not None:
            lines.append(f"用户：{pending_user}\n助手：{text}")
            pending_user = None
        elif text:
            lines.append(f"助手：{text}")
    if pending_user:
        lines.append(f"用户：{pending_user}")
    return "\n\n".join(lines)


def _serialize_context(context: AsyncStructuredContext) -> str:
    """结构化上下文 → 喂给 `ask(context=...)` 的字符串（字段语义对齐 chat.py）。"""
    parts: list[str] = []
    if context.page:
        parts.append(f"页面：{context.page}")
    if context.data is not None:
        payload = json.dumps(context.data, ensure_ascii=False, sort_keys=True)
        parts.append("【页内数据】\n" + payload)
    history = _render_history(context.history)
    if history:
        parts.append("【上一轮已生成内容】\n" + history)
    if not parts:
        return ""
    return "\n".join(parts)


def _normalize_context(
    context: str | AsyncStructuredContext,
) -> tuple[str, list[ChatSource]]:
    """双形态上下文 → `(字符串上下文, sources)`。

    - 旧字符串形态：原样返回，`sources=[]`（`build_blocks` 于是出「无来源」notice）；
    - 结构化形态：`{page, data, sources, history}` 转字符串上下文 + `sources`。
      `history` 只进文本，**不进 sources**（红线 6）。
    """
    if isinstance(context, AsyncStructuredContext):
        return _serialize_context(context), list(context.sources)
    return context, []


def _blocks_for(
    reply: str,
    sources: Sequence[ChatSource],
    *,
    fetch_attempted: bool = False,
    metrics: Mapping[str, Any] | None = None,
    extra_notes: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """会话模式的成功路径装配 `blocks`。

    `metrics` 是 agent 这一轮工具调用的真实产出（带 `Provenance`）；没调工具时为空，
    metric 块的缺失提示由 `fetch_attempted` 决定——没查就不摆「没取到」，查了才摆。

    没调工具、没有 metrics、也没有来源的轮次（问候 / 能力说明 / 概念解释）走
    **正文块**：整段 reply 原样下发。这类回答没有「首句即结论」的结构，套研究卡
    只会得到「结论·中性」+「补充说明（正文 4 行折叠）」，把一条完整回答切成两截并
    丢掉超出 4 行的内容——用户看到的「闪一下变成骨架」就是这个。
    """
    text = (reply or "").strip()
    # F5：翻转条件从正文里摘出来挂到结论卡上，正文用「摘掉这一行」的版本，
    # 否则同一句话会在结论卡和正文块里各出现一次。
    falsifier, body = answer_blocks.extract_falsifier(text)
    if body and not fetch_attempted and not metrics and not sources:
        return [build_block(answer_blocks.BLOCK_NARRATIVE, {"text": body})]
    blocks = _build_blocks(
        dict(metrics or {}),
        reply_to_structured(body)["build_input"],
        sources,
        fetch_attempted=fetch_attempted,
        keep_numbers=bool(metrics),
        falsifier=falsifier,
    )
    # QUOT-627：取到数的轮次也要能看到整段回答。此前只有「没调工具」才走正文块，
    # 调过工具的轮次只剩「结论 + 指标/表格 + 补充说明（截 4 行、丢数字行）」，
    # 用户看到的就是先正常、再闪成一张写着「中性 · 本轮不提供置信度评级 · 页面没有取到」的骨架卡。
    # 正文块原样下发、不截断，插在 citation 之前（citation 必须仍是最后一块）。
    if body and len(body) >= NARRATIVE_MIN_CHARS:
        blocks = _with_narrative(blocks, body)
    blocks = _with_extra_notes(blocks, extra_notes)
    return _with_number_audit(blocks, body, metrics)


def _with_number_audit(
    blocks: list[dict[str, Any]],
    text: str,
    metrics: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """F6：正文里没对回本轮取数结果的小数 / 百分比，单独标一条提示。

    只标不改：删数字会留下「收盘价元」这种残句，比留一个被标记的数更难读。
    没有 metrics 的轮次直接跳过——那不是「数不对」，是「这一轮本来就没取数」。
    """
    if not text or not metrics:
        return blocks
    try:
        result = number_audit.audit(text, metrics)
    except Exception as exc:  # noqa: BLE001 - 自检失败不能影响回答本身
        logger.warning("number audit failed: %s", exc)
        return blocks
    note = number_audit.notice_text(result)
    if not note:
        return blocks
    block = build_block(
        answer_blocks.BLOCK_NOTICE,
        {"kind": answer_blocks.NOTICE_UNVERIFIED, "section": "数字核对", "message": note},
    )
    return _insert_before_citation(blocks, block)


def _with_extra_notes(
    blocks: list[dict[str, Any]], notes: Sequence[str]
) -> list[dict[str, Any]]:
    """把「数据缺口」这类服务端说明挂成一条 notice，放在来源之前。

    文案由调用方给（`ai/backfill.notice_for`），这里只负责摆放与去重：
    - 空串一律丢掉，不渲染空块；
    - 同一 section 的旧块先摘掉，避免同一句话说两遍；
    - 用 `_insert_before_citation`，仍在 6 块上限内腾位（citation 不动）。
    """
    texts = [str(note).strip() for note in notes if str(note).strip()]
    if not texts:
        return blocks
    section = "数据缺口"
    block = build_block(
        answer_blocks.BLOCK_NOTICE,
        {"kind": answer_blocks.NOTICE_NOTE, "section": section, "message": "\n".join(texts)},
    )
    trimmed = [
        item
        for item in blocks
        if not (
            item.get("type") == answer_blocks.BLOCK_NOTICE
            and (item.get("payload") or {}).get("section") == section
        )
    ]
    return _insert_before_citation(trimmed, block)


def _coverage_note(question: str, data_root: Any) -> str | None:
    """F3 的同步入口（串行路径用）：体检 + 决策 + 生成一句缺口说明。

    体检属于「锦上添花」，任何异常都只记日志返回 None——不能因为本地库临时不可用
    就把整轮回答变成 500。
    """
    try:
        return backfill.notice_for(backfill.plan_backfill(question, data_root=data_root))
    except Exception as exc:  # noqa: BLE001
        logger.warning("backfill plan failed: %s", exc)
        return None


#: 只有问题真问到多空/评审，才把 StockSenate 的结论附进主问答——不每轮都去读报告
_DEBATE_INTENT = re.compile(r"(多空|看多|看空|辩论|合议|评审|证伪|对手盘)", re.IGNORECASE)


def _debate_brief(question: str) -> list[dict[str, Any]]:
    """F7 接线：主问答里的多空评审块（0 或 1 块）。

    只读已落盘报告（`debate.latest_view`），**不在这一轮起辩论**——那是 `/review` 页
    的显式动作（一轮实测几分钟）。没有报告就返回空，不拿「还没有报告」占一块预算；
    有报告但质量门没过，给一条 `notice(degraded)` 说清原因——这是 debate 模块的红线：
    绝不出空对照表。

    读报告的任何异常都只记日志返回空：评审是附加信息，不能把整轮回答变成失败。
    """
    if not _DEBATE_INTENT.search(question or ""):
        return []
    target = resolve_target(question)
    if target is None:
        return []
    _market, symbol = target
    try:
        view = debate.latest_view(symbol)
    except Exception as exc:  # noqa: BLE001 - 读报告失败不能拖垮整轮回答
        logger.warning("debate latest_view failed: %s", exc)
        return []
    if view is None:
        logger.debug(
            "debate brief skipped: no report symbol=%s senate_home=%s",
            symbol,
            debate.senate_home(),
        )
        return []
    try:
        return [debate.summary_block(view)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("debate summary_block failed: %s", exc)
        return []


def _with_narrative(blocks: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """把整段回答作为正文块插到 citation 之前（不重算、不摘要、不截断）。

    插之前先摘掉「补充说明」notice：它的内容是 `narrative[1:] + highlights`，
    全部来自同一段 reply——正文块已经逐字说了一遍，再挂一条就等于同一句话出现两次。
    """
    if any(item.get("type") == answer_blocks.BLOCK_NARRATIVE for item in blocks):
        return blocks
    block = build_block(answer_blocks.BLOCK_NARRATIVE, {"text": text})
    blocks = [
        item
        for item in blocks
        if not (
            item.get("type") == answer_blocks.BLOCK_NOTICE
            and (item.get("payload") or {}).get("section") == "补充说明"
        )
    ]
    return _insert_before_citation(blocks, block)


#: 超限时为「正文块」让位的顺序：按信息价值从低到高。
_DROPPABLE: tuple[str, ...] = (
    answer_blocks.BLOCK_NOTICE,
    answer_blocks.BLOCK_CHART,
    answer_blocks.BLOCK_METRIC,
    answer_blocks.BLOCK_TABLE,
)


def _insert_before_citation(
    blocks: list[dict[str, Any]], block: dict[str, Any]
) -> list[dict[str, Any]]:
    """插一块到 citation 之前，并保证总数不破 `MAX_BLOCKS`。

    `_build_blocks` 已经按 `MAX_BLOCKS` 收过口，直接插会变成第 7 块，`check_ordering`
    就会拒。腾位顺序见 `_DROPPABLE`：先让重复的 `notice` 走，图次之，真实数据最后。
    `conclusion` / `citation` / 新插的正文块永远不动（§6.4-1 / §6.4-2）。
    """
    index = next(
        (i for i, item in enumerate(blocks) if item.get("type") == answer_blocks.BLOCK_CITATION),
        len(blocks),
    )
    merged = blocks[:index] + [block] + blocks[index:]
    keep = {id(item) for item in (block, *blocks[index:index + 1])}
    for kind in _DROPPABLE:
        while len(merged) > answer_blocks.MAX_BLOCKS:
            victim = next(
                (
                    i
                    for i, item in enumerate(merged)
                    if i > 0 and item.get("type") == kind and id(item) not in keep
                ),
                None,
            )
            if victim is None:
                break
            del merged[victim]
    return merged


#: 正文块的最小长度：短于此的回答（「已记下」「好的」这类回执）没必要单独铺一段。
NARRATIVE_MIN_CHARS = 80


# 首页研究台的对话要能刷新后回看：给它一个**固定的**会话 id，每轮成功回答落一行
# `chat_turn`。前缀 `desk:` 是刻意的——对话模式的会话列表按这个前缀把它过滤掉，
# 否则首页那几张问答会混进「我的会话」里。
DESK_SESSION_ID = "desk:home"
DESK_SESSION_PREFIX = "desk:"


def _persist_desk_turn(
    data_root: Any, session_id: str, question: str, blocks: list[dict[str, Any]]
) -> None:
    """把首页研究台的一轮问答写进 `chat_turn`（回看用）。

    落库只是"能回看"，不是回答成立的前提：写失败只记日志，绝不把一次成功回答
    变成 500——这与对话模式里 `_TurnWriter` 的口径一致。
    """
    try:
        with _TurnWriter(data_root, session_id, question) as writer:
            writer.start(blocks)
            writer.finish(chat_store.DONE, blocks)
    except Exception as exc:  # noqa: BLE001 - 回看能力失败不影响本轮回答
        logger.warning("desk_turn_persist_failed session=%s detail=%s", session_id, exc)


def _persist_record(
    data_root: Any,
    session_id: str | None,
    seq: int | None,
    question: str,
    blocks: Sequence[Mapping[str, Any]],
) -> None:
    """F9：把这一轮的方向判断落进观点账本，到期由回访判定对错。

    落账不是回答成立的前提：没有结论卡、方向中性/告警、拿不到基准价都直接跳过
    （`ledger.record_from_turn` 已判过）；写库失败只记日志——与
    `_persist_desk_turn` 同一口径，绝不让账本问题把一次成功回答变成 500。
    """
    if data_root is None:
        return
    try:
        target = resolve_target(question)
        record = ledger.record_from_turn(
            question,
            blocks,
            symbol=target[1] if target else None,
            session_id=session_id,
            seq=seq,
        )
        if record is None:
            return
        conn = chat_store.connect(data_root)
        if conn is None:
            return
        try:
            chat_store.insert_record(conn, **record)
        finally:
            chat_store.close(conn)
        logger.info(
            "research_record_added id=%s symbol=%s direction=%s due=%s",
            record["record_id"],
            record.get("symbol"),
            record["direction"],
            record["due_date"],
        )
    except Exception as exc:  # noqa: BLE001 - 账本写失败不影响本轮回答
        logger.warning("research_record_failed session=%s detail=%s", session_id, exc)


def _success_response(
    result: Mapping[str, Any],
    sources: Sequence[ChatSource],
    intent: str = INTENT_DATA_QUERY,
    *,
    plan: FetchPlan | None = None,
    fetched_metrics: Mapping[str, Any] | None = None,
    fetched_sources: Sequence[Mapping[str, Any]] | None = None,
    fetch_attempted: bool | None = None,
    persist: tuple[Any, str, str] | None = None,
) -> dict[str, Any]:
    """成功响应：既有四键原样透传，**只追加** `blocks` 与 `ai_structured`。

    `ai` 仍是原来的 `{model, source}` 回显（旧调用方可能在用，不得顶替）；结构化叙述
    旁挂在 `ai_structured`（`narrative / highlights / risks / evidence`），供 QUOT-581
    直接喂 `build_blocks`。字段名不与 581 的 `seq / last_seq / completed` 冲突。

    QUOT-616：`fetched_metrics` / `fetched_sources` 是问答取数接线的真实产出，
    非空时替换掉 `metrics={}` 的空契约——进 metric 块的每个值都带 Provenance 出处。
    """
    adapted = reply_to_structured(result["reply"])
    metrics = dict(fetched_metrics or {})
    all_sources: list[Any] = [*sources, *(fetched_sources or [])]
    # 本轮是否真的发起了取数：agent 路径由「模型有没有调工具」决定（`fetch_attempted`
    # 显式传入）；旧路径仍按 `plan.dataset is None` 判断——那是「没查」，不是
    # 「查了没取到」，装配层据此不再摆「指标 / 来源没取到」。
    if fetch_attempted is None:
        fetch_attempted = True if plan is None else plan.dataset is not None
    blocks = _build_blocks(
        metrics,
        adapted["build_input"],
        all_sources,
        intent=intent,
        # 取到数或点了名失败源时，本轮确实带了真实取数行为
        page_data=bool(metrics),
        fetch_attempted=fetch_attempted,
    )
    if plan is not None and plan.source_failures and not metrics:
        blocks = [
            b
            for b in blocks
            if not (
                b.get("type") == "notice"
                and b.get("payload", {}).get("kind") == "missing_data"
                and b.get("payload", {}).get("section") == "指标"
            )
        ]
        blocks.append(
            build_block(
                "notice",
                {
                    "kind": "missing_data",
                    "section": "指标",
                    # notice.message 不允许出现数字（answer_blocks 红线 4），
                    # 失败细节里往往带数字，只点名具体源，细节进服务端日志。
                    "message": "取数失败，"
                    + "、".join(failure.split(":")[0].strip() for failure in plan.source_failures)
                    + " 没有返回可用数据",
                },
            )
        )
    # QUOT-627：取到数的轮次也要能看到整段回答。此前 `_build_blocks` 只留
    # 「结论 + 指标/表格 + 补充说明（截 4 行、丢含数字的行）」，用户先看到正常回复，
    # 再闪成一张「中性 · 本轮不提供置信度评级 · 页面没有取到」的骨架卡。正文块
    # 原样下发、不截断，插在 citation 之前（citation 必须仍是最后一块）。
    reply_text = str(result.get("reply") or "").strip()
    if reply_text and len(reply_text) >= NARRATIVE_MIN_CHARS:
        blocks = _with_narrative(blocks, reply_text)
    # `persist=(data_root, session_id, question)`：本轮落库供刷新回看。
    # 只有首页研究台传这个参数；页面助手 / 技能助手是一次性问答，不落库。
    if persist is not None:
        _persist_desk_turn(persist[0], persist[1], persist[2], blocks)
        # F9：同一轮的判断落进观点账本（到期由回访判对错）
        _persist_record(persist[0], persist[1], None, persist[2], blocks)
    return {
        **result,
        "blocks": blocks,
        "ai_structured": adapted["ai_structured"],
    }


def _fetch_for_intent(
    question: str,
    intent: str,
    context_symbol: str | None = None,
    data_root: Any = None,
) -> FetchPlan:
    """按意图构建并执行取数（QUOT-616）。闲聊等非取数意图返回空计划，零 peer 调用。"""
    plan = build_plan(question, intent, context_symbol=context_symbol)
    if plan.dataset is None:
        return plan
    metrics, sources = run_fetch(plan, data_root=data_root)
    plan.fetched_metrics = metrics
    plan.fetched_sources = sources
    return plan


def _failure_blocks(exc: AssistantFailure) -> list[dict[str, Any]]:
    """失败路径装配 `blocks`：单条 `notice(missing_data)`，文案取既有可读 `detail`。"""
    return _build_blocks(
        {},
        None,
        [],
        failure={
            "kind": "missing_data",
            "message": exc.detail,
            "action": "" if exc.retryable else "请按提示修正后重试",
        },
    )


# --------------------------------------------------------------------------- #
# QUOT-580 / D3：chat.py → 现役链路的会话类型（chat.py 保留对照，不挂载）
# --------------------------------------------------------------------------- #

#: 单条问题的上限；与 `chat.py:MAX_MESSAGE_CHARS` 同值，落库时也按此截断
MAX_QUESTION_ASK_CHARS = 4000
#: 会话 ID 口径与 `chat.py` 一致：8–64 位安全字符
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ChatRequestContext(BaseModel):
    """页面已展示的只读上下文：字段由调用方裁剪，服务端不主动补数据。

    与 `AsyncStructuredContext` 同一份 `sources` 类型（`ChatSource`，QUOT-579 迁入），
    只少了 `history`：会话模式的历史由服务端从 `chat.sqlite` 读，不信任调用方塞。
    """

    model_config = ConfigDict(extra="forbid")

    page: str | None = None
    data: dict[str, Any] | None = None
    sources: list[ChatSource] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """会话模式的载荷：沿用 `chat.py` 的字段与 `extra="forbid"` 口径。"""

    model_config = ConfigDict(extra="forbid")

    message: str
    session_id: str | None = None
    context: ChatRequestContext | None = None
    timeout_sec: float | None = None
    #: F2：用户确认/编辑过的研究计划（标题列表）。缺省或空表 = 没有计划，与现状一致。
    plan: list[str] | None = None


class PlanRequest(BaseModel):
    """F2 计划草案的载荷：只要问题和页面上下文，不落库、不起模型。"""

    model_config = ConfigDict(extra="forbid")

    message: str
    context: ChatRequestContext | None = None


class ChatFailure(Exception):
    """一次问答的可读失败：`code` / `reason` / `kind` 供前端区分与配色。

    字段与 `chat.py:ChatFailure` 逐一对应；本模块的 `ask()` 仍抛语义更窄的
    `AssistantFailure`，两者在 `run_turn()` 的边界上互相翻译。
    """

    def __init__(
        self,
        status: int,
        *,
        code: str,
        reason: str,
        kind: str,
        message: str,
        action: str = "",
        retryable: bool = True,
        detail: str = "",
        channel_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.reason = reason
        self.kind = kind
        self.message = message
        self.action = action
        self.retryable = retryable
        self.detail = detail
        self.channel_state = channel_state

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "message": self.message,
            "action": self.action,
            "retryable": self.retryable,
            "detail": self.detail or self.message,
            "channel_state": self.channel_state,
        }


def _source_note(*, has_data: bool, sources: Any) -> str:
    """没有来源时补在正文尾部的服务端说明（文案在 `ai/answer_blocks.source_note`）。"""
    return answer_blocks.source_note(has_data=has_data, sources=sources)


# --------------------------------------------------------------------------- #
# 会话模式的回答块：装配仍走现役 `reply_to_structured()` → `build_blocks()`（QUOT-579），
# 本模块只做两件现役链路没有的事——会话侧上限 6（D2）、把块按序号逐块交出去（落库）
# --------------------------------------------------------------------------- #

MAX_ANSWER_BLOCKS = 6


def _conversation_blocks(
    *,
    reply: str,
    sources: Sequence[ChatSource],
    failure: ChatFailure | None = None,
    fetch_attempted: bool = False,
    metrics: Mapping[str, Any] | None = None,
    extra_notes: Sequence[str] = (),
    extra_blocks: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """一轮的块：失败只有一条 `notice`；成功复用现役装配链，再收到 D2 上限 6。

    `fetch_attempted` 由调用方给：只有 agent 这一轮**真的调了工具**才算查过。没查过
    就不摆「指标 / 来源没取到」——那是把「没查」说成「查了没取到」，等于用告警块回答
    一个没问的问题。块的顺序、文案、数字剥离仍全部由 `ai/answer_blocks.py` 负责。

    `extra_blocks` 是正文之外的附加块（F7 的多空评审那一块）：插在 citation 之前，
    腾位交给 `_insert_before_citation`（先丢重复 notice），最多插 1 块。
    """
    if failure is not None:
        return _failure_blocks(
            AssistantFailure(
                failure.status,
                failure.code,
                failure.message,
                retryable=failure.retryable,
            )
        )[:MAX_ANSWER_BLOCKS]
    base = _blocks_for(
        reply,
        sources,
        fetch_attempted=fetch_attempted,
        metrics=metrics,
        extra_notes=extra_notes,
    )[:MAX_ANSWER_BLOCKS]
    # 附加块（F7 多空评审）插在 citation 之前，并沿用 `_insert_before_citation` 的
    # 腾位规则——它保证 citation 仍在末尾、总数不破 6；最多插 1 块，正文不会被挤空。
    for block in list(extra_blocks)[:1]:
        base = _insert_before_citation(base, dict(block))
    return base


def _arrive_blocks(
    *,
    reply: str,
    sources: Sequence[ChatSource],
    on_block: Callable[[list[dict[str, Any]]], None],
    fetch_attempted: bool = False,
    metrics: Mapping[str, Any] | None = None,
    extra_notes: Sequence[str] = (),
    extra_blocks: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """同 `_conversation_blocks` 成功路径，但逐块交付：每多一块就 `on_block(当前全部块)`。

    块的装配仍走现役 `_blocks_for()`（`reply_to_structured()` → `build_blocks()`），
    不在这里重排；逐块交付只服务「边到达边 upsert」的落库时机（DEC-085）。返回值与
    `_conversation_blocks()` 完全一致，`on_block` 收到的最后一次快照就是返回值。
    """
    full = _conversation_blocks(
        reply=reply,
        sources=sources,
        fetch_attempted=fetch_attempted,
        metrics=metrics,
        extra_notes=extra_notes,
        extra_blocks=extra_blocks,
    )
    for count in range(1, len(full) + 1):
        on_block([dict(block) for block in full[:count]])
    return full


def resolve_session_id(raw: str | None) -> str:
    """`session_id` 缺省时新生成一个；给了就必须是 8–64 位安全字符。"""
    text = (raw or "").strip()
    if not text:
        return secrets.token_urlsafe(12)
    if not SESSION_ID_RE.match(text):
        raise ChatFailure(
            400,
            code="invalid_session",
            reason="invalid_request",
            kind="internal",
            message="session_id 需要 8–64 位字母 / 数字 / 下划线 / 连字符",
            retryable=False,
        )
    return text


_ASSISTANT_FAILURE_CODES: dict[str, tuple[str, str, str, bool]] = {
    # code → (reason, failure.kind, 可读动作, 是否可重试)
    "ai_not_configured": (
        "no_channel",
        "internal",
        "到「设置 → 接入 AI」配置后再试",
        False,
    ),
    "ai_cli_unavailable": ("no_channel", "internal", "检查本机 CLI 是否可用后重试", False),
    "ai_timeout": ("timeout", "network", "稍后重试；或调小上下文后重试", True),
    "ai_quota": ("quota", "quota", "等待额度恢复后重试", True),
    "ai_network": ("network", "network", "检查网络或代理后重试", True),
    "ai_upstream": ("upstream", "network", "稍后重试；持续失败请检查 AI 通道配置", True),
    "ai_empty": ("empty", "internal", "重试一次；仍为空时检查本机 CLI 是否正常", True),
}


def _as_chat_failure(exc: AssistantFailure) -> ChatFailure:
    reason, kind, action, retryable = _ASSISTANT_FAILURE_CODES.get(
        exc.code, ("internal", "internal", "稍后重试", exc.retryable)
    )
    return ChatFailure(
        exc.status,
        code=exc.code,
        reason=reason,
        kind=kind,
        message=exc.detail,
        action=action,
        retryable=retryable,
        detail=exc.detail,
    )


def _record_failure(session_id: str, err: ChatFailure) -> JSONResponse:
    return JSONResponse(
        status_code=err.status,
        content={
            "ok": False,
            "session_id": session_id,
            "reply": "",
            "sources": [],
            "pending_research": [],
            "code": err.code,
            "detail": err.detail or err.message,
            "retryable": err.retryable,
            "failure": err.as_dict(),
        },
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------- #
# 一轮问答（会话模式：答案 + 块 + 落库）
# --------------------------------------------------------------------------- #

RUN_TURN_MODULE = DEFAULT_MODULE


def _history(conn: sqlite3.Connection, session_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """最近几轮：只拿 `question` 与首块 `conclusion.text`，不把整串 blocks 塞进 prompt。"""
    rows = chat_store.session_turns(conn, session_id)[-limit:]
    history: list[dict[str, Any]] = []
    for row in rows:
        try:
            blocks = json.loads(row["blocks_json"])
        except ValueError:
            continue
        conclusion = next(
            (
                block.get("payload", {}).get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == answer_blocks.BLOCK_CONCLUSION
            ),
            "",
        )
        history.append({"question": row["question"], "answer": conclusion})
    return history


def _session_history(
    data_root: Any, session_id: str, *, limit: int = 6
) -> list[ContextHistoryTurn]:
    """本会话最近几轮 → 现役 `ContextHistoryTurn`（用户/助手交替，只写文本）。"""
    if data_root is None:
        return []
    try:
        conn = chat_store.connect(data_root, create=False)
    except (sqlite3.Error, OSError, BoundaryViolation) as exc:
        logger.warning("chat_store_unavailable detail=%s", exc)
        return []
    if conn is None:
        return []
    try:
        rows = _history(conn, session_id, limit=limit)
    finally:
        chat_store.close(conn)
    turns: list[ContextHistoryTurn] = []
    for row in rows:
        turns.append(ContextHistoryTurn(role="user", text=row["question"]))
        turns.append(ContextHistoryTurn(role="assistant", text=row["answer"]))
    return turns


class _TurnWriter:
    """一轮问答的落库句柄：同一轮只产生一行 `chat_turn`，`status` 在同一行上流转。

    写入时机（QUOT-580 第二轮拍板「落点 B：边到达边 upsert」）：

    - `start(blocks)`：首块到达时插入行，`status = streaming`；
    - `arrive(blocks)`：后续每块到达，更新**同一行**的 `blocks_json`；
    - `finish(status, blocks)`：`done` / `failed` 落终态；`aborted` 不带块——
      保留已到达内容，用户点停止后定格。

    任何一步写库失败都只记一条日志并把 `seq` 记成 `None`，**不影响答题主流程**；
    客户端断开（`aborted`）属于正常路径，不是错误。

    命名纪律：这里的 `seq` 是**轮次序号** `turn_seq`；块自身的 `seq` 是 `block_seq`，
    两者不共名、日志里也不混着打。
    """

    def __init__(self, data_root: Any, session_id: str, question: str) -> None:
        self._data_root = data_root
        self._session_id = session_id
        self._question = question[:MAX_QUESTION_ASK_CHARS]
        self._conn: sqlite3.Connection | None = None
        self._seq: int | None = None
        self._broken = False
        if data_root is not None:
            self._conn = self._open()

    def _open(self) -> sqlite3.Connection | None:
        try:
            return chat_store.connect(self._data_root)
        except (sqlite3.Error, OSError, BoundaryViolation) as exc:
            logger.warning("chat_store_unavailable detail=%s", exc)
            self._broken = True
            return None

    def _guard(self, op: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        conn = self._conn
        if conn is None or self._broken:
            return None
        try:
            return fn(conn)
        except sqlite3.Error as exc:
            logger.warning("chat_store_write_failed op=%s detail=%s", op, exc)
            self._broken = True
            return None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def turn_seq(self) -> int | None:
        return self._seq

    def start(self, blocks: list[dict[str, Any]]) -> None:
        if self._seq is not None:
            return
        recorded = self._guard(
            "begin",
            lambda conn: chat_store.begin_turn(
                conn,
                session_id=self._session_id,
                question=self._question,
                blocks=blocks,
            ),
        )
        if recorded is not None:
            self._seq = int(recorded["seq"])

    def arrive(self, blocks: list[dict[str, Any]]) -> None:
        if self._seq is None:
            self.start(blocks)
            return
        seq = self._seq
        self._guard(
            "update",
            lambda conn: chat_store.update_turn_blocks(
                conn, session_id=self._session_id, seq=seq, blocks=blocks
            ),
        )

    def finish(self, status: str, blocks: list[dict[str, Any]] | None = None) -> None:
        # 终态必须有行；`aborted` 可能在首块之前就发生，也要留下一行可回看的记录
        if self._seq is None and status != chat_store.DONE:
            self.start(blocks or [])
        if self._seq is None:
            return
        seq = self._seq
        self._guard(
            "finish",
            lambda conn: chat_store.finish_turn(
                conn,
                session_id=self._session_id,
                seq=seq,
                status=status,
                blocks=blocks,
            ),
        )

    def close(self) -> None:
        chat_store.close(self._conn)
        self._conn = None

    def __enter__(self) -> _TurnWriter:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def run_turn(
    *,
    message: str,
    session_id: str,
    context: ChatRequestContext | None = None,
    timeout_sec: float | None = None,
    config_root: Any = None,
    data_root: Any = None,
) -> dict[str, Any]:
    """会话模式的一轮问答：复用 `ask()` 的通道与记忆，另拼块并落库。

    成功时返回 `ok/session_id/reply/blocks/turn_seq/status/model/channel/sources/fetched_at`；
    `reply` 语义与抽屉完全一致（正文 + 服务端来源说明），块从同一份正文拼出。

    写入时机（落点 B，边到达边 upsert）：块是逐块「到达」的——`conclusion` → `citation`
    → `notice`。每拼出一块就经 `_TurnWriter` 写同一行 `chat_turn`：首块插
    `status=streaming` 的行，后续块更新该行的 `blocks_json`，收尾落
    `done` / `failed` / `aborted`。客户端断开（`GeneratorExit` / `CancelledError`）
    按 `aborted` 落库、保留已到达的块，后续 QUOT-581 的流式实现可直接复用这条路径。
    """
    text = (message or "").strip()
    if not text:
        raise ChatFailure(
            400,
            code="empty_message",
            reason="invalid_request",
            kind="internal",
            message="message 不能为空",
            retryable=False,
        )
    if len(text) > MAX_QUESTION_ASK_CHARS:
        raise ChatFailure(
            400,
            code="message_too_long",
            reason="invalid_request",
            kind="internal",
            message=f"message 过长（上限 {MAX_QUESTION_ASK_CHARS} 字）",
            retryable=False,
        )

    sources = list(context.sources) if context is not None else []
    # 历史由服务端从 `chat.sqlite` 读，走现役 `AsyncStructuredContext.history` 同一段渲染，
    # 不信任调用方在 `context` 里自带的历史。
    history = _session_history(data_root, session_id)
    structured = AsyncStructuredContext(
        page=context.page if context is not None else None,
        data=context.data if context is not None else None,
        sources=sources,
        history=history,
    )
    context_text, _ = _normalize_context(structured)

    with _TurnWriter(data_root, session_id, text) as writer:
        try:
            return _run_turn_logged(
                writer,
                session_id=session_id,
                text=text,
                context_text=context_text,
                context=context,
                sources=sources,
                data_root=data_root,
            )
        except ChatFailure as exc:
            # 服务端失败：落 `failed` 一行（块里只有失败说明），前端按 status 显示失败+重试
            writer.finish(
                chat_store.FAILED,
                _conversation_blocks(reply="", sources=sources, failure=exc),
            )
            raise
        except (GeneratorExit, asyncio.CancelledError):
            # 客户端断开 / 用户点停止：内容定格，已到达的块保留为 aborted
            writer.finish(chat_store.ABORTED)
            raise


#: 正文增量的最小间隔：每 0.12s 推一次累积全文，避免逐 token 刷爆事件循环
STREAM_TEXT_INTERVAL_SEC = 0.12


def _sse_frame(event: str, payload: Mapping[str, Any]) -> str:
    """一帧 SSE：`event:` + 单行 JSON。JSON 用 `ensure_ascii=False` 保留中文原文。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def wants_stream(request: Request) -> bool:
    """`Accept: text/event-stream` 才走流；其余（含不带 Accept 的调用方）原样走 JSON。"""
    return "text/event-stream" in (request.headers.get("accept") or "").lower()


def _precheck_turn(body: ChatRequest) -> tuple[str, str]:
    """流式路径的入参校验，与 `run_turn` 首段同口径；返回 `(session_id, message)`。"""
    text = (body.message or "").strip()
    if not text:
        raise ChatFailure(
            400,
            code="empty_message",
            reason="invalid_request",
            kind="internal",
            message="message 不能为空",
            retryable=False,
        )
    if len(text) > MAX_QUESTION_ASK_CHARS:
        raise ChatFailure(
            400,
            code="message_too_long",
            reason="invalid_request",
            kind="internal",
            message=f"message 过长（上限 {MAX_QUESTION_ASK_CHARS} 字）",
            retryable=False,
        )
    return resolve_session_id(body.session_id), text


async def _run_agent_turn(
    *,
    question: str,
    context: str,
    page_label: str,
    module: str,
    page: str,
    data_root: Any,
    view_tab: str | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    day: str | None = None,
    symbol: str | None = None,
    prepared: _PreparedCall | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """agent 路径的一轮：与 `ask_stream` 同形（`text` / `final`），额外多一个 `tool`。

    与 `ask_stream` 的差别只在**决策权**：这里把取数当工具交出去，由模型自己决定查不查、
    查几个、查几轮；prompt 仍是 `_prepare_call()` 拼的那份（记忆 / 知识库 / 页级上下文
    一并保留），两条路径的内容不会漂移。

    通道不满足条件时抛 `AssistantFailure`，由调用方决定是否回落——本函数不自己吞。
    """
    try:
        config = fallback.load_config()
        target = config.resolve_for(module)
    except BoundaryViolation as exc:
        raise AssistantFailure(503, "ai_not_configured", f"未配置 AI 通道：{exc}") from None
    # SDK 走 /chat/completions，只有 openai 型后端能用；CLI 订阅通道与 anthropic 不在此列
    if target.backend.kind != "openai":
        raise AssistantFailure(
            503,
            "ai_channel_not_supported",
            f"当前通道（{target.backend.kind}）不支持工具调用，需要 OpenAI 兼容后端",
        )
    base_url = target.backend.base_url
    api_key = target.backend.api_key
    if not (base_url and api_key):
        raise AssistantFailure(503, "ai_not_configured", "AI 通道缺少 base_url / api_key")

    # 非流式入口（`ask_agent`）已经 prepare 过一次：同一份 prompt 不再重复召回
    # 记忆与档案（那是两次 IO），也让它事后能用同一个 prepared 写记忆。
    if prepared is None:
        prepared = _prepare_call(
            question=question,
            context=context,
            day=day,
            page_label=page_label,
            system_prompt=system_prompt,
            module=module,
            symbol=symbol,
            tab=view_tab or page,
            data_root=data_root,
        )
    specs = build_tools(data_root)
    labels = {spec.name: spec.label for spec in specs}
    # 工具跑出来的 metrics / sources 收在这里，final 时一并交给装配层：
    # 模型写的正文会被块协议剥数字（红线 4），数字只能从这条通道进 metric 块。
    collected: dict[str, Any] = {"metrics": {}, "sources": []}
    agent, client = build_agent(
        model=target.model,
        base_url=base_url,
        api_key=api_key,
        instructions=prepared.system + AGENT_TOOL_RULE,
        tool_specs=specs,
        collector=collected,
    )
    try:
        async for kind, payload in run_agent_stream(
            agent, prepared.user, labels=labels, collector=collected
        ):
            if kind == AGENT_TOOL:
                yield "tool", payload
            elif kind == AGENT_TEXT:
                yield "text", payload.get("text", "")
            elif kind == AGENT_FINAL:
                yield "final", payload.get("text", "")
                if payload.get("metrics") or payload.get("sources"):
                    yield (
                        "data",
                        {
                            "metrics": payload.get("metrics") or {},
                            "sources": payload.get("sources") or [],
                        },
                    )
    finally:
        await client.close()


async def ask_agent(
    *,
    question: str,
    context: str,
    day: str | None = None,
    tab: str | None = None,
    page_label: str = "个股深挖",
    system_prompt: str = SYSTEM_PROMPT,
    module: str = DEFAULT_MODULE,
    symbol: str | None = None,
    page: str = "research",
    data_root: Any = None,
    sync_memory: bool = False,
    remember_plan: bool = True,
) -> dict[str, Any]:
    """非流式跑一轮 **agent**：返回值与 `ask()` 同形，外加工具产出的 metrics / sources。

    存在的理由：`/api/research/{code}/assistant`、`/api/page/assistant`、
    `/api/skills/assistant`、以及 `/api/conversation/turns` 的 JSON 路径此前都
    走 `ask()`——先调一次模型、再由代码按意图补取数。那是"问答"，不是 agent：
    查不查由关键词决定，模型看不到查回来的东西。统一到这里后，四条入口与流式
    路径共用同一份决策（模型自己选工具、定轮次），只有交付方式不同。

    `remember_plan=False` 给「任务推进」用：推进时问的那句本身带「盯住 600000」，
    若还让它建任务，每推进一次就繁殖一条新任务，五天就把上限打满。
    """
    prepared = _prepare_call(
        question=question,
        context=context,
        day=day,
        tab=tab,
        page_label=page_label,
        system_prompt=system_prompt,
        module=module,
        symbol=symbol,
        data_root=data_root,
    )
    reply = ""
    fetched: dict[str, Any] = {}
    used_tools = False
    async for kind, payload in _run_agent_turn(
        question=question,
        context=context,
        page_label=page_label,
        module=module,
        page=page,
        data_root=data_root,
        system_prompt=system_prompt,
        day=day,
        symbol=symbol,
        view_tab=tab,
        prepared=prepared,
    ):
        if kind == "tool":
            used_tools = True
        elif kind == "text":
            reply = payload
        elif kind == "final":
            reply = payload
        elif kind == "data":
            fetched = payload

    text = (reply or "").strip()
    if not text:
        raise AssistantFailure(502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。")

    # 自我反思：模型这一轮一次工具都没调，却给了价格/涨跌幅这类结论——那不是数据，
    # 是编的。补查一轮是唯一正确的动作（`ai/reflection.py` 只反思一次，不递归）。
    decision = reflection.decide(
        question=question,
        reply=text,
        used_tools=used_tools,
        metrics=fetched.get("metrics") or {},
    )
    if decision.needed:
        logger.info("self_reflect triggered reason=%s head=%s", decision.reason, question[:40])
        followup_text = ""
        followup_fetched: dict[str, Any] = {}
        followup_tools = False
        async for kind, payload in _run_agent_turn(
            question=reflection.followup(question, reason=decision.reason),
            context=context,
            page_label=page_label,
            module=module,
            page=page,
            data_root=data_root,
            system_prompt=system_prompt,
            day=day,
            symbol=symbol,
            view_tab=tab,
        ):
            if kind == "tool":
                followup_tools = True
            elif kind == "text":
                followup_text = payload
            elif kind == "final":
                followup_text = payload
            elif kind == "data":
                followup_fetched = payload
        if reflection.should_replace(text, followup_text):
            text = followup_text.strip()
            if followup_fetched.get("metrics"):
                fetched = followup_fetched
            used_tools = used_tools or followup_tools
            logger.info("self_reflect replaced used_tools=%s", used_tools)

    _remember(
        prepared,
        question=question,
        symbol=symbol,
        page=page,
        sync_memory=sync_memory,
        remember_plan=remember_plan,
    )
    return {
        "reply": text,
        "model": prepared.model,
        "ai": prepared.echo,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "metrics": fetched.get("metrics") or {},
        "sources": fetched.get("sources") or [],
        "used_tools": used_tools,
    }


async def run_turn_stream(
    *,
    message: str,
    session_id: str,
    context: ChatRequestContext | None = None,
    data_root: Any = None,
    plan: Sequence[str] | None = None,
) -> AsyncIterator[str]:
    """会话模式的一轮问答，以 SSE 帧逐段交付。

    帧契约（与前端 `conversation.ts` 的 `parseSseFrame` 对齐）：

    - `status`：`{action, item, state, item_id?, elapsed_ms?}`，只服务等待态的流水账；
    - `text`：`{text}`，**累积全文**（前端整段替换，不做拼接）；
    - `done`：与 JSON 路径的响应同形（含 `blocks` / `turn_seq` / `status`）。

    与 `run_turn` 的另一处差别是**正文直接从流式通道拿**，不走 `ask()` 的一次性返回；
    但 prompt、记忆写入、块装配、落库时机全部复用同一条现役链路，最终 `blocks`
    与串行路径逐字段一致。
    """
    started = time.monotonic()

    def _status(item: str, state: str, *, item_id: str | None = None, action: str = "run") -> str:
        payload: dict[str, Any] = {
            "action": action,
            "item": item,
            "state": state,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        if item_id is not None:
            payload["item_id"] = item_id
        return _sse_frame("status", payload)

    sources = list(context.sources) if context is not None else []
    history = _session_history(data_root, session_id)
    structured = AsyncStructuredContext(
        page=context.page if context is not None else None,
        data=context.data if context is not None else None,
        sources=sources,
        history=history,
    )
    context_text, _ = _normalize_context(structured)
    # 这一轮的页面归属（`_prepare_call` 的 tab 与记忆 page 都取它）
    turn_page = context.page if context is not None and context.page else PAGE_PREFIX.strip("/")

    yield _status("读取会话历史", "done", item_id="history", action="history")

    # 建库连接在锁竞争时要等待（5950 / 5951 共用同一份 `chat.sqlite`）：先把这一步
    # 亮出来，再把构造丢进线程，界面不会只剩上一行字干等。
    yield _status("正在准备对话记录", "running", item_id="session_store", action="store")
    writer = await asyncio.to_thread(_TurnWriter, data_root, session_id, message)
    yield _status("对话记录就绪", "done", item_id="session_store", action="store")

    with writer:
        # F3 缺口自补：开跑前先把本地库体检一遍。缺的那一类不该等模型答完才暴露——
        # 权限档允许就直接起后台补数任务（不占首屏），不允许就把缺口写成一条 notice；
        # 两种情况都要在流水账上留一步，否则「补数」是黑箱，跟没做一样。
        cover: dict[str, Any] = {}
        cover_note: str | None = None
        yield _status("数据体检（本地库）", "running", item_id="coverage", action="coverage")
        try:
            cover = await asyncio.to_thread(backfill.plan_backfill, message, data_root=data_root)
            cover_note = backfill.notice_for(cover)
        except Exception as exc:  # noqa: BLE001 - 体检失败不能拖垮整轮回答
            logger.warning("backfill plan failed: %s", exc)
        if cover.get("action") == backfill.ACTION_READY:
            yield _status(
                f"数据体检：{'、'.join(cover.get('kinds') or [])} 本地齐全",
                "done",
                item_id="coverage",
                action="coverage",
            )
        elif cover.get("action") == backfill.ACTION_AUTO:
            yield _status(
                f"数据体检：缺 {'、'.join(cover.get('missing') or [])}，已起补数任务",
                "done",
                item_id="coverage",
                action="coverage",
            )
        elif cover.get("action") == backfill.ACTION_CONFIRM:
            yield _status(
                f"数据体检：缺 {'、'.join(cover.get('missing') or [])}（需确认后才能补）",
                "done",
                item_id="coverage",
                action="coverage",
            )
        else:
            yield _status("数据体检：这一轮不需要补数", "done", item_id="coverage", action="coverage")

        yield _status("正在调用模型", "running", item_id="model", action="model")
        try:
            accumulated = ""
            last_push = 0.0
            reply = ""
            used_tools = False
            fetched: dict[str, Any] = {}
            # 与 `ask_agent` 同构：先把 prompt / 记忆 / 知识库准备好，再交给 agent。
            # 这份 prepared 事后还要用在同一处写记忆与长期偏好——流式此前没这一步，
            # 页面对话的记忆与偏好一直是空的（只有 JSON 路径写）。
            steps = research_plan.normalize_steps(plan)
            if steps:
                yield _status(
                    f"按计划执行（{len(steps)} 步）", "done", item_id="plan", action="plan"
                )
                draft = research_plan.draft_steps(
                    message,
                    symbol=backfill.detect_symbol(message),
                    kinds=backfill.kinds_for_question(message),
                )
                detail = research_plan.note_for(
                    research_plan.validate_edits(base=draft, edited=steps)
                )
                if detail:
                    yield _status(
                        f"计划已调整：{detail}", "done", item_id="plan_edit", action="plan"
                    )
            prepared_turn = _prepare_call(
                question=message,
                context=context_text,
                page_label="对话模式",
                module=RUN_TURN_MODULE,
                tab=turn_page,
                data_root=data_root,
                turn_plan=steps or None,
            )
            # 首选 agent 路径：模型自主选工具、定轮次。它解决的是"该不该查"——查过就
            # 有真实数据可装配，没查就说明缺什么，不再靠猜意图决定要不要打「没取到」。
            async for kind, payload in _run_agent_turn(
                question=message,
                context=context_text,
                page_label="对话模式",
                module=RUN_TURN_MODULE,
                page=turn_page,
                data_root=data_root,
                prepared=prepared_turn,
            ):
                if kind == "tool":
                    used_tools = True
                    label = str(payload.get("label") or payload.get("name") or "工具")
                    state = "done" if payload.get("state") == "done" else "running"
                    # 查的是什么 / 回来多少：流水账要能看出这一步的实际内容
                    detail = str(payload.get("detail") or "").strip()
                    suffix = f"（{detail}）" if detail else ""
                    yield _status(
                        f"{label} {'完成' if state == 'done' else '查询中'}{suffix}",
                        state,
                        item_id=f"tool:{payload.get('name')}",
                        action="tool",
                    )
                elif kind == "text":
                    accumulated = payload
                    now = time.monotonic()
                    if now - last_push >= STREAM_TEXT_INTERVAL_SEC:
                        last_push = now
                        yield _sse_frame("text", {"text": accumulated})
                elif kind == "final":
                    reply = payload
                elif kind == "data":
                    fetched = payload
            if accumulated and accumulated != reply:
                yield _sse_frame("text", {"text": accumulated})
            elif reply and not accumulated:
                yield _sse_frame("text", {"text": reply})

            # 正文已落地，模型这一步要收口：否则「正在调用模型」会一直转，
            # 正文都开始出了界面还在转圈。
            yield _status("模型返回完成", "done", item_id="model", action="model")

            # 自我反思（与 `ask_agent` 同一判据）：这一轮没调工具却给了价格/涨跌幅，
            # 就补查一轮再覆盖正文。页面能看见「补查证据」这一步，不偷偷替换。
            decision = reflection.decide(
                question=message,
                reply=reply,
                used_tools=used_tools,
                metrics=fetched.get("metrics") or {},
            )
            if decision.needed:
                yield _status(
                    "正在补查证据（这一轮没有取到数）",
                    "running",
                    item_id="reflect",
                    action="reflect",
                )
                logger.info(
                    "self_reflect triggered reason=%s head=%s", decision.reason, message[:40]
                )
                followup_text = ""
                followup_fetched: dict[str, Any] = {}
                followup_tools = False
                async for kind, payload in _run_agent_turn(
                    question=reflection.followup(message, reason=decision.reason),
                    context=context_text,
                    page_label="对话模式",
                    module=RUN_TURN_MODULE,
                    page=turn_page,
                    data_root=data_root,
                ):
                    if kind == "tool":
                        followup_tools = True
                        label = str(payload.get("label") or payload.get("name") or "工具")
                        state = "done" if payload.get("state") == "done" else "running"
                        detail = str(payload.get("detail") or "").strip()
                        suffix = f"（{detail}）" if detail else ""
                        yield _status(
                            f"{label} {'完成' if state == 'done' else '查询中'}{suffix}",
                            state,
                            item_id=f"tool:{payload.get('name')}:followup",
                            action="tool",
                        )
                    elif kind in ("text", "final"):
                        followup_text = payload
                        if kind == "text" and payload:
                            yield _sse_frame("text", {"text": payload})
                    elif kind == "data":
                        followup_fetched = payload
                if reflection.should_replace(reply, followup_text):
                    reply = followup_text.strip()
                    accumulated = reply
                    if followup_fetched.get("metrics"):
                        fetched = followup_fetched
                    used_tools = used_tools or followup_tools
                    yield _sse_frame("text", {"text": reply})
                    logger.info("self_reflect replaced used_tools=%s", used_tools)
                yield _status("补查完成", "done", item_id="reflect", action="reflect")
        except (AssistantFailure, asyncio.CancelledError, GeneratorExit) as exc:
            if isinstance(exc, AssistantFailure):
                chat_failure = _as_chat_failure(exc)
                writer.finish(
                    chat_store.FAILED,
                    _conversation_blocks(reply="", sources=sources, failure=chat_failure),
                )
                yield _sse_frame(
                    "done",
                    {
                        "ok": False,
                        "code": chat_failure.code,
                        "reason": chat_failure.reason,
                        "retryable": chat_failure.retryable,
                        "detail": chat_failure.message,
                    },
                )
                return
            writer.finish(chat_store.ABORTED)
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要变成终态帧
            # 以前只接 `AssistantFailure`，模型侧抛出的 `openai.APIConnectionError`
            # 会直接穿出生成器：SSE 就地断掉，前端等不到 `done` 帧，页面表现就是
            # 「这一轮没答出来」而没有任何原因。这里统一收成 ai_upstream 再落终态。
            logger.exception("run_turn_stream 出现未分类异常，按 ai_upstream 收尾")
            failure = AssistantFailure(502, "ai_upstream", f"{type(exc).__name__}: {exc}")
            chat_failure = _as_chat_failure(failure)
            writer.finish(
                chat_store.FAILED,
                _conversation_blocks(reply="", sources=sources, failure=chat_failure),
            )
            yield _sse_frame(
                "done",
                {
                    "ok": False,
                    "code": chat_failure.code,
                    "reason": chat_failure.reason,
                    "retryable": chat_failure.retryable,
                    "detail": chat_failure.message,
                },
            )
            return

        # 工具调用是「这一轮真的发起取数」的唯一证据：用它决定要不要出取数类告警，
        # 而不是靠问题里有没有某些词。模型一次没查，就不该摆「指标没取到」。
        # 同时把工具产出的 metrics / sources 交给装配层——那才是页面上能看到的数。
        tool_sources = [
            ChatSource(**item)
            for item in (fetched.get("sources") or [])
            if isinstance(item, Mapping) and item.get("name")
        ]
        # 回答成立后与 JSON 路径走同一份记账：记忆（mem0）+ 长期偏好（本地文件）。
        _remember(prepared_turn, question=message, symbol=None, page=turn_page, sync_memory=False)
        blocks = _arrive_blocks(
            reply=reply,
            sources=[*sources, *tool_sources],
            on_block=writer.arrive,
            fetch_attempted=used_tools,
            metrics=fetched.get("metrics") or {},
            extra_notes=[cover_note] if cover_note else (),
            extra_blocks=_debate_brief(message),
        )
        writer.finish(chat_store.DONE, blocks)
        # F9：流式路径同样落账——首页研究台与对话模式走的是同一份判定口径
        _persist_record(data_root, session_id, writer.turn_seq, message, blocks)
        yield _status("model", "done", item_id="model")
        yield _sse_frame(
            "done",
            {
                "ok": True,
                "session_id": session_id,
                "reply": reply,
                "blocks": blocks,
                "turn_seq": writer.turn_seq,
                "status": chat_store.DONE,
            },
        )


def _run_turn_logged(
    writer: _TurnWriter,
    *,
    session_id: str,
    text: str,
    context_text: str,
    context: ChatRequestContext | None,
    sources: Sequence[ChatSource],
    data_root: Any,
) -> dict[str, Any]:
    """真正跑一轮；每拼出一块就经 `writer` upsert 一次（同一行）。

    块的装配与串行路径共用 `_conversation_blocks()`（现役 `reply_to_structured()` →
    `build_blocks()`，再收到 6）；逐块交付只是把同一份结果的**前缀**依次交给
    `writer.arrive`，落库内容与返回值最终一致。
    """
    try:
        # JSON 路径与流式路径共用同一个 agent：此前这里调 `ask()`，同一句问题
        # 流式与不流式会有两种决策方式（一个模型自己选工具、一个代码按意图猜）。
        # 本函数跑在线程池里（`run_in_threadpool`），没有运行中的事件循环，可用 asyncio.run。
        result = asyncio.run(
            ask_agent(
                question=text,
                context=context_text,
                page_label="对话模式",
                module=RUN_TURN_MODULE,
                page=(
                    context.page if context is not None and context.page else PAGE_PREFIX.strip("/")
                ),
                data_root=data_root,
            )
        )
    except AssistantFailure as exc:
        raise _as_chat_failure(exc) from None

    reply = result["reply"]
    tool_sources = [
        ChatSource(**item)
        for item in (result["sources"] or [])
        if isinstance(item, Mapping) and item.get("name")
    ]
    # F3：串行路径同样先体检再装配（与流式路径同一套判定，不改权限口径）
    cover_note = _coverage_note(text, data_root)
    blocks = _arrive_blocks(
        reply=reply,
        sources=[*sources, *tool_sources],
        on_block=writer.arrive,
        fetch_attempted=result["used_tools"],
        metrics=result["metrics"],
        extra_notes=[cover_note] if cover_note else (),
        extra_blocks=_debate_brief(text),
    )
    writer.finish(chat_store.DONE, blocks)
    _persist_record(data_root, session_id, writer.turn_seq, text, blocks)
    return {
        "ok": True,
        "session_id": session_id,
        "reply": reply,
        "blocks": blocks,
        "model": result["model"],
        "channel": result["ai"]["source"],
        "sources": [item.model_dump(exclude_none=True) for item in sources],
        "turn_seq": writer.turn_seq,
        "status": chat_store.DONE,
        "fetched_at": result["fetched_at"],
    }


def _load_client(module: str = DEFAULT_MODULE) -> tuple[Any, str]:
    """读配置并构造既有通道的客户端；模型按 `module` 三级解析（QUOT-498）。"""
    try:
        config = fallback.load_config()
    except BoundaryViolation as exc:
        raise AssistantFailure(503, "ai_not_configured", f"未配置 AI 通道：{exc}") from None
    if not fallback.is_configured(config):
        missing = "、".join(fallback.missing_env(config)) or "LDST_LLM_BASE_URL / LDST_LLM_API_KEY"
        raise AssistantFailure(
            503,
            "ai_not_configured",
            f"未配置 AI 通道：缺少 {missing}。请到「设置 → 接入 AI」配置后再试。",
        )
    client = build_chat_client(config)
    # 客户端构造没有暴露 timeout 参数；这里统一到单轮问答的上限。
    client.timeout = ASSISTANT_TIMEOUT_SEC
    return client, config.resolve_for(module).label


def _ai_echo(module: str, model: str) -> dict[str, str]:
    """响应信封的 `ai: {model, source}`；配置读不出来时来源只报 `global`。"""
    try:
        config = fallback.load_config()
    except BoundaryViolation:
        return {"model": model, "source": "global"}
    return {"model": config.resolve_for(module).label, "source": config.source_for(module)}


def memory_scope_for(module: str) -> dict[str, str] | None:
    """该模块的记忆命名空间（QUOT-498）：`agent_id` + `kb_scope`，读不出来就没有。

    记忆检索/写入统一从这里取名空间——**不另起通道**（QUOT-487 收口）。
    """
    try:
        scope = fallback.load_config().memory_scope(module)
    except BoundaryViolation:
        return None
    return {
        "agent_id": scope.agent_id,
        "kb_scope": scope.kb_scope,
        "label": module_memory.scope_label(scope),
    }


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, httpx.TimeoutException)):
        return True
    return "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()


# 问题里的标的写法：`600000` / `600000.SH` / `sh600000`（大小写宽容）。两侧都不许紧贴
# 字母数字，也不许前接小数点/顿号——「10.600000 元」「8,350000」这类数字不该被当成代码。
_SYMBOL_IN_TEXT = re.compile(
    r"(?<![0-9A-Za-z.,、])(?:(SH|SZ|BJ)\.?([0-9]{6})|([0-9]{6})(?:\.(SH|SZ|BJ))?)"
    r"(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def _market_for_prefix(symbol: str) -> str:
    """无显式市场时按 6 位代码首位推断交易所（与交易所号段一致）。"""
    if symbol[0] == "6":
        return "SH"
    if symbol[0] in "03":
        return "SZ"
    return "BJ"


def resolve_target(question: str, symbol: str | None = None) -> tuple[str, str] | None:
    """解析 `(market, symbol)`：调用方给的 `symbol` 优先，否则从问题里取第一个代码。

    `symbol` 可带 `.SH` / `SH` 前缀；解析不出返回 None（不猜）。
    """
    candidates = [symbol] if symbol else []
    candidates.extend(match.group(0) for match in _SYMBOL_IN_TEXT.finditer(question or ""))
    for raw in candidates:
        text = str(raw or "").strip().upper()
        if not text:
            continue
        market: str | None = None
        if text[:2] in ("SH", "SZ", "BJ") and len(text) == 8:
            market, text = text[:2], text[2:]
        elif text.endswith((".SH", ".SZ", ".BJ")):
            market, text = text[-2:], text[:-3]
        if len(text) != 6 or not text.isdigit():
            continue
        return (market or _market_for_prefix(text)), text
    return None


def _recall_knowledge(
    question: str,
    *,
    symbol: str | None,
    data_root: Any,
) -> str | None:
    """召回标的档案（带 as_of 头 + 边界）；解析不出标的 / 无档案 / 读失败都返回 None。"""
    target = resolve_target(question, symbol)
    if target is None:
        return None
    market, code = target
    try:
        return recall_wrapped(data_root, market, code)
    except Exception as exc:  # noqa: BLE001 - 知识库故障绝不拖垮问答（姊妹分支 memory_unavailable）
        logger.debug(
            "knowledge_unavailable stage=recall market=%s symbol=%s detail=%s",
            market,
            code,
            exc,
        )
        return None


def _recall_memories(
    question: str,
    *,
    symbol: str | None,
    settings: memory.MemorySettings,
    client: memory.MemoryClient | None,
    scope: fallback.MemoryScope,
) -> list[memory.MemoryHit]:
    """召回跨会话记忆（模块命名空间）；mem0 不可达时静默降级（日志 `memory_unavailable`）。"""
    if client is None:
        return []
    try:
        return memory.recall(question, symbol=symbol, settings=settings, client=client, scope=scope)
    except memory.MemoryUnavailable as exc:
        logger.warning("memory_unavailable stage=search detail=%s", exc)
        return []


@dataclass
class _PreparedCall:
    """一次问答的完整准备结果（`ask` 与 `ask_stream` 共用，保证 prompt 同源）。"""

    client: Any
    model: str
    echo: dict[str, str]
    system: str
    user: str
    settings: memory.MemorySettings
    mem_client: memory.MemoryClient | None
    scope: fallback.MemoryScope | None
    data_root: Any = None


def _prepare_call(
    *,
    question: str,
    context: str,
    day: str | None = None,
    tab: str | None = None,
    page_label: str = "个股深挖",
    system_prompt: str = SYSTEM_PROMPT,
    module: str = DEFAULT_MODULE,
    symbol: str | None = None,
    memory_settings: memory.MemorySettings | None = None,
    memory_client: memory.MemoryClient | None = None,
    data_root: Any = None,
    turn_plan: Sequence[str] | None = None,
) -> _PreparedCall:
    """通道 + 记忆召回 + 知识库 + 拼 system/user。

    抽出来的唯一目的是让 `ask()`（一次性）与 `ask_stream()`（逐段）走**同一份**
    prompt 与同一段召回顺序：流式只换交付方式，不换内容。任何一侧改 prompt
    都必须改到这里，否则两条路径会漂移。
    """
    client, model = _load_client(module)
    echo = _ai_echo(module, model)
    scope_info = memory_scope_for(module)
    scope = (
        fallback.MemoryScope(scope_info["agent_id"], scope_info["kb_scope"])
        if scope_info is not None
        else None
    )
    settings = memory_settings if memory_settings is not None else memory.load_settings()
    mem_client = memory_client if memory_client is not None else memory.shared_client(settings)
    hits = (
        _recall_memories(question, symbol=symbol, settings=settings, client=mem_client, scope=scope)
        if scope is not None
        else []
    )
    root = data_root if data_root is not None else data_dir()
    knowledge_block = _recall_knowledge(
        question,
        symbol=symbol,
        data_root=root,
    )
    block = memory.render_memory_block(hits, knowledge=knowledge_block)
    if block and scope_info is not None:
        block = f"{scope_info['label']}\n{block}"
    # 长期偏好段紧跟角色设定：它讲的是「你是谁、要什么」这类全局约定，优先于
    # 本轮召回的记忆与档案。空表时不留空段（普通人首次使用不该看到「偏好」字样）。
    # **必须显式传 root**：不传就回落到默认数据盘，注入路径换盘（测试盘 / 便携盘）时
    # 会读到另一台机器的偏好，测试与生产都会错。
    preference_block = preferences.render(preferences.load(root))
    # 进行中的研究任务排在偏好之后：偏好是长期不变的，任务是「这周让我盯的」。
    plan_block = plans.render(plans.active(root))
    # F2 本轮计划（用户确认过的那份）紧跟在长期任务之后：它是"这一次要查什么"，
    # 比长期任务更具体，所以排在后面、离问题更近。
    turn_plan_block = research_plan.render(research_plan.normalize_steps(turn_plan))
    sections = [system_prompt]
    if preference_block:
        sections.append(preference_block)
    if plan_block:
        sections.append(plan_block)
    if turn_plan_block:
        sections.append(turn_plan_block)
    if block:
        sections.extend((block, _MEMORY_CONFLICT_RULE))
    system = "\n\n".join(sections)
    # 空 context 的占位句必须写成「给模型看的指令」而不是「像事实的句子」：
    # 写成「这一页的数据还没取到」时，模型会原样复述给用户（问候轮尤其明显）。
    header = "【页级上下文】\n" + (
        context.strip() or "（本轮未附带页面数据；此句只给你看，禁止复述给用户。）"
    )
    meta = f"页面：{page_label} · 视图 {tab or '未标注'} · 日期 {day or '未标注'}"
    user = f"{meta}\n{header}\n\n【问题】\n{question.strip()}"
    return _PreparedCall(
        client=client,
        model=model,
        echo=echo,
        system=system,
        user=user,
        settings=settings,
        mem_client=mem_client,
        scope=scope,
        data_root=data_root,
    )


def _as_assistant_failure(exc: BaseException) -> AssistantFailure:
    """把通道异常统一归类成 `AssistantFailure`（`ask` / `ask_stream` 共用一份口径）。"""
    if isinstance(exc, (UnconfiguredError, BoundaryViolation)):
        return AssistantFailure(503, "ai_not_configured", f"未配置 AI 通道：{exc}")
    if isinstance(exc, CliUnavailable):
        return AssistantFailure(503, "ai_cli_unavailable", f"订阅版 CLI 不可用：{exc}")
    if isinstance(exc, (ProviderError, httpx.HTTPError, OSError)):
        failure = classify_error(exc)
        if _is_timeout(exc):
            return AssistantFailure(
                504, "ai_timeout", f"AI 响应超时（{int(ASSISTANT_TIMEOUT_SEC)}s）：{failure.detail}"
            )
        if failure.kind == "quota":
            return AssistantFailure(429, "ai_quota", failure.message)
        if failure.kind == "network":
            return AssistantFailure(
                502, "ai_network", f"AI 请求未送达（网络/代理）：{failure.detail}"
            )
        return AssistantFailure(502, "ai_upstream", failure.message)
    failure = classify_error(exc)
    if _is_timeout(exc):
        return AssistantFailure(
            504, "ai_timeout", f"AI 响应超时（{int(ASSISTANT_TIMEOUT_SEC)}s）：{failure.detail}"
        )
    return AssistantFailure(502, "ai_upstream", failure.message)


def _name_resolver(data_root: Any) -> Callable[[str], str | None]:
    """中文名 → 代码，供「盯住浦发银行」这类说法解析标的。

    只认本机标的字典（`web/symbols.SymbolPlane`），查不到就返回 None——绝不猜。
    解析失败的代价是「这条任务没建成」，猜错的代价是「盯错了票」，后者严重得多。
    """

    def resolve(name: str) -> str | None:
        text = (name or "").strip()
        if not text:
            return None
        try:
            from ladderstock.web.symbols import SymbolPlane

            payload = SymbolPlane(data_root=Path(data_root) if data_root else data_dir()).search(
                text, 3
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败就当解析不出
            logger.warning("symbol_resolver_failed name=%s detail=%s", text, exc)
            return None
        matches = ((payload or {}).get("payload") or {}).get("matches") or []
        for item in matches:
            matched_name = str(item.get("name") or "")
            if (
                matched_name == text
                or matched_name.startswith(text)
                or text.startswith(matched_name)
            ):
                return str(item.get("symbol") or "") or None
        return None

    return resolve


def _remember(
    prepared: _PreparedCall,
    *,
    question: str,
    symbol: str | None,
    page: str,
    sync_memory: bool,
    remember_plan: bool = True,
) -> None:
    """回答成立后写记忆、长期偏好与跨轮任务；失败只记日志（绝不影响回答）。

    偏好沉淀**不依赖** mem0：没有命名空间/客户端时也照抽照存，因为它是本地文件，
    与记忆通道的可用性无关（普通人装上就能用，不该先要求他跑通向量库）。
    跨轮任务同理：一句话就能建一条「盯它一周」的任务，不需要用户先去配调度。
    """
    try:
        preferences.remember(question, data_root=prepared.data_root)
    except Exception as exc:  # noqa: BLE001 - 偏好是增强，任何异常都不能影响回答
        logger.warning("preferences_store_failed detail=%s", exc)
    try:
        if remember_plan:
            plan = plans.maybe_create(
                question,
                data_root=prepared.data_root,
                resolver=_name_resolver(prepared.data_root),
            )
            if plan is not None:
                logger.info("plan_created_from_chat id=%s symbols=%s", plan.id, plan.symbols)
    except Exception as exc:  # noqa: BLE001 - 建任务失败同样不能影响回答
        logger.warning("plans_create_failed detail=%s", exc)
    if prepared.mem_client is None or prepared.scope is None:
        return
    memory.dispatch_store(
        question,
        symbol=symbol,
        page=page,
        run_id=uuid.uuid4().hex,
        settings=prepared.settings,
        client=prepared.mem_client,
        sync=sync_memory,
        scope=prepared.scope,
    )


def ask(
    *,
    question: str,
    context: str,
    day: str | None = None,
    tab: str | None = None,
    page_label: str = "个股深挖",
    system_prompt: str = SYSTEM_PROMPT,
    module: str = DEFAULT_MODULE,
    symbol: str | None = None,
    page: str = "research",
    memory_settings: memory.MemorySettings | None = None,
    memory_client: memory.MemoryClient | None = None,
    sync_memory: bool = False,
    data_root: Any = None,
) -> dict[str, Any]:
    """跑一轮问答。成功返回 `{reply, model, ai, fetched_at}`；失败抛 `AssistantFailure`。

    QUOT-487：同一条链上先召回跨会话记忆并注入系统提示（独立分段，见
    `ai/memory.render_memory_block`），回复完成后异步按策略写入记忆——
    写入失败只记日志，绝不影响已经完成的回答。测试可用 `sync_memory=True`
    同步等写入结束。

    QUOT-498：记忆检索/写入走模块命名空间（`memory_scope_for`），`agent_id` 与
    `kb_scope` 两维同时到位；配置读不出命名空间时跳过记忆，回答照常。

    QUOT-566：`data_root` 指档案数据目录（`<data_root>/knowledge/`）；不传时按
    `cli.paths.data_dir()` 解析（与知识库 REST 出口同一口径）。记忆段固定排在
    档案段之前（`render_memory_block` 的分段顺序）。
    """
    prepared = _prepare_call(
        question=question,
        context=context,
        day=day,
        tab=tab,
        page_label=page_label,
        system_prompt=system_prompt,
        module=module,
        symbol=symbol,
        memory_settings=memory_settings,
        memory_client=memory_client,
        data_root=data_root,
    )
    try:
        reply = prepared.client.complete(
            prepared.model, prepared.system, prepared.user, json_mode=False
        )
    except Exception as exc:  # noqa: BLE001 - 兜底也要给可读原因，绝不静默失败
        raise _as_assistant_failure(exc) from None

    text = (reply or "").strip()
    if not text:
        raise AssistantFailure(502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。")
    # QUOT-487 写入：回答已经成立，异步落库；失败只记日志，不影响本次响应。
    _remember(prepared, question=question, symbol=symbol, page=page, sync_memory=sync_memory)
    return {
        "reply": text,
        "model": prepared.model,
        "ai": prepared.echo,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


async def ask_stream(
    *,
    question: str,
    context: str,
    day: str | None = None,
    tab: str | None = None,
    page_label: str = "个股深挖",
    system_prompt: str = SYSTEM_PROMPT,
    module: str = DEFAULT_MODULE,
    symbol: str | None = None,
    page: str = "research",
    memory_settings: memory.MemorySettings | None = None,
    memory_client: memory.MemoryClient | None = None,
    sync_memory: bool = False,
    data_root: Any = None,
) -> AsyncIterator[tuple[str, str]]:
    """逐段产出 `(kind, payload)`；`kind='text'` 是**累积全文**。

    与 `ask()` 共用 `_prepare_call()`——prompt、记忆、知识库的顺序一字不差。
    通道没有 token 增量（CLI 一次性子进程 / 假客户端）时回落到一次 `complete`，
    仍然只发一帧 `text`，由调用方按「不是逐字」如实展示，不假装流式。
    """
    prepared = _prepare_call(
        question=question,
        context=context,
        day=day,
        tab=tab,
        page_label=page_label,
        system_prompt=system_prompt,
        module=module,
        symbol=symbol,
        memory_settings=memory_settings,
        memory_client=memory_client,
        data_root=data_root,
    )
    streamer = getattr(prepared.client, "aiter_text", None)
    accumulated = ""
    try:
        if streamer is not None:
            async for chunk in streamer(prepared.model, prepared.system, prepared.user):
                accumulated += chunk
                yield ("text", accumulated)
        else:
            reply = prepared.client.complete(
                prepared.model, prepared.system, prepared.user, json_mode=False
            )
            accumulated = reply or ""
            if accumulated:
                yield ("text", accumulated)
    except NotImplementedError:
        # CLI 通道没有 token 级增量：退回一次性补全，正文照旧完整
        reply = prepared.client.complete(
            prepared.model, prepared.system, prepared.user, json_mode=False
        )
        accumulated = reply or ""
        if accumulated:
            yield ("text", accumulated)
    except Exception as exc:  # noqa: BLE001
        raise _as_assistant_failure(exc) from None

    text = accumulated.strip()
    if not text:
        raise AssistantFailure(502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。")
    _remember(prepared, question=question, symbol=symbol, page=page, sync_memory=sync_memory)
    yield ("final", text)


def _question_payload_error(
    question: str, context: str | AsyncStructuredContext
) -> JSONResponse | None:
    """问题 / 上下文长度校验，`/research` 与 `/skills` 两个端点共用。

    结构化上下文按序列化后的文本长度判定，与旧字符串形态同一把尺子。
    """
    if not question:
        return JSONResponse(
            status_code=422,
            content={"detail": "问题不能为空", "code": "empty_question", "retryable": False},
        )
    if len(question) > MAX_QUESTION_CHARS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"问题过长（上限 {MAX_QUESTION_CHARS} 字）",
                "code": "question_too_long",
                "retryable": False,
            },
        )
    context_text, _ = _normalize_context(context)
    if len(context_text) > MAX_CONTEXT_CHARS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (f"页级上下文过长（上限 {MAX_CONTEXT_CHARS} 字），请缩小页面数据后重试"),
                "code": "context_too_long",
                "retryable": False,
            },
        )
    return None


def build_router(data_root: Any = None) -> APIRouter:
    """`POST /api/research/{code}/assistant`：页级研究助手一问一答。

    `data_root` 是标的档案的数据目录（QUOT-566），不传时 `ask()` 按 `data_dir()` 解析。
    """
    router = APIRouter(prefix=ASSISTANT_PREFIX, dependencies=[Depends(_guard)])

    @router.post("/{code}/assistant")
    async def research_assistant(code: str, body: AssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        context, sources = _normalize_context(body.context)
        intent = classify_intent(question, context)
        try:
            result = await ask_agent(
                question=question,
                context=context,
                day=body.date,
                tab=body.tab,
                page_label="个股深挖",
                module="senate",
                symbol=code,
                page="research",
                data_root=data_root,
            )
        except AssistantFailure as exc:
            return _failure_response(exc)
        return _success_response(
            result,
            sources,
            intent,
            fetched_metrics=result["metrics"],
            fetched_sources=result["sources"],
            fetch_attempted=result["used_tools"],
            # 首页研究台固定会话：刷新 / 重开页面时由 `GET /api/research/desk/history` 读回。
            # 只对首页（code 占位 `home`）落库；个股深挖页的问答属于单页一次性问答，
            # 混进同一个会话会让「首页回看」里冒出别页的问题。
            persist=((data_root, DESK_SESSION_ID, question) if code == "home" else None),
        )

    @router.get("/desk/history")
    def desk_history(limit: int = 20) -> Any:
        """首页研究台的最近若干轮（刷新回看）。

        只读：库还没建（从没问过）时返回空列表，不现建一个空库。
        """
        capped = max(1, min(int(limit), 50))
        conn = chat_store.connect(data_root, create=False)
        if conn is None:
            return {"turns": []}
        try:
            rows = chat_store.session_turns(conn, DESK_SESSION_ID)
            turns = [
                {
                    "seq": row["seq"],
                    "question": row["question"],
                    "blocks": _blocks_for_view(row),
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in rows[-capped:]
            ]
            return {"turns": turns}
        except Exception as exc:  # noqa: BLE001 - 回看失败按空处理，不挡首页
            logger.warning("desk_history_read_failed detail=%s", exc)
            return {"turns": []}
        finally:
            chat_store.close(conn)

    @router.post("/desk/history/clear")
    def clear_desk_history() -> Any:
        """「清空」按钮的服务端口径：把首页研究台那一条会话真删掉。

        只删 `desk:home` 这一条，对话模式的会话不受影响；库不存在时按「本来就空」
        返回，不现建库。
        """
        conn = chat_store.connect(data_root, create=False)
        if conn is None:
            return {"deleted": False}
        try:
            deleted = chat_store.delete_session(conn, DESK_SESSION_ID)
            return {"deleted": bool(deleted)}
        finally:
            chat_store.close(conn)

    return router


CONVERSATION_PREFIX = "/api/conversation"


def _blocks_for_view(row: dict[str, Any]) -> list[Any]:
    try:
        return json.loads(row["blocks_json"])
    except ValueError:
        return []


def build_conversation_router(*, data_root: Any = None, config_root: Any = None) -> APIRouter:
    """`/api/conversation/*`（QUOT-580）：会话模式的一轮问答 + 会话列表读取。

    阶段 F 的 `SessionList` 只消费这里：`GET /api/conversation/sessions` 列会话，
    `GET /api/conversation/sessions/{id}/turns` 取一个会话的全部轮次。
    读写都在 `chat.sqlite`（本机独立库，D1），不碰 `market.duckdb` / `journal.sqlite`。
    """
    router = APIRouter(prefix=CONVERSATION_PREFIX, dependencies=[Depends(_guard)])

    def _open_read() -> sqlite3.Connection | None:
        return chat_store.connect(data_root, create=False)

    def _not_found() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "会话不存在", "code": "not_found"})

    @router.get("/sessions")
    def list_sessions() -> Any:
        conn = _open_read()
        if conn is None:
            return {"sessions": []}
        try:
            rows = chat_store.list_sessions(conn)
            # 首页研究台的 `desk:home` 会话是为了刷新回看而落的，不是用户在对话模式里
            # 建的会话——留在列表里会多出一个点不开的条目，这里按前缀剔掉。
            return {
                "sessions": [
                    item
                    for item in rows
                    if not str(item.get("session_id") or "").startswith(DESK_SESSION_PREFIX)
                ]
            }
        finally:
            chat_store.close(conn)

    @router.get("/sessions/{session_id}/turns")
    def list_turns(session_id: str) -> Any:
        conn = _open_read()
        if conn is None:
            return _not_found()
        try:
            session = chat_store.get_session(conn, session_id)
            if session is None:
                return _not_found()
            turns = [
                {
                    "seq": row["seq"],
                    "question": row["question"],
                    "blocks": _blocks_for_view(row),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in chat_store.session_turns(conn, session_id)
            ]
            return {"session": session, "turns": turns}
        finally:
            chat_store.close(conn)

    @router.patch("/sessions/{session_id}")
    def rename_session(session_id: str, body: RenameRequest) -> Any:
        conn = chat_store.connect(data_root)
        if conn is None:
            return _not_found()
        try:
            title = chat_store.rename_session(conn, session_id, body.title)
        finally:
            chat_store.close(conn)
        if title is None:
            return _not_found()
        return {"session_id": session_id, "title": title}

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> Any:
        """删除一条会话（QUOT-602）：真删 `chat_session` 行，轮次随级联一起走。

        幂等：重复删返回 200 `deleted: false`，不报 5xx——前端删完可能因为并发重取再删一次。
        库还没建（从没问过）同样按「本来就没有」返回，不现建一个空库。
        """
        conn = _open_read()
        if conn is None:
            return {"session_id": session_id, "deleted": False}
        try:
            deleted = chat_store.delete_session(conn, session_id)
        finally:
            chat_store.close(conn)
        return {"session_id": session_id, "deleted": deleted}

    @router.post("/plan")
    async def post_plan(body: PlanRequest) -> Any:
        """F2：先出一条可编辑的计划草案。纯规则，不调模型、不落库、不占延迟。"""
        question = (body.message or "").strip()
        if not question:
            return {"ok": False, "error": "message_required"}
        symbol = backfill.detect_symbol(question)
        steps = research_plan.draft_steps(
            question,
            symbol=symbol,
            kinds=backfill.kinds_for_question(question),
        )
        return {
            "ok": True,
            "symbol": symbol,
            "max_steps": research_plan.MAX_STEPS,
            "steps": [step.as_dict() for step in steps],
        }

    @router.get("/ledger")
    def get_ledger(
        limit: int = 50,
        symbol: str | None = None,
        status: str | None = None,
    ) -> Any:
        """F9：观点账本 —— 已落账的判断 + 命中统计。

        `hit_rate` 只算已判的（hit / miss / flipped）；还没到期的 `open` 不进分母，
        否则「准确率」会被自己的等待期稀释。
        """
        conn = _open_read()
        empty = {
            "total": 0,
            "open": 0,
            "hit": 0,
            "miss": 0,
            "flipped": 0,
            "undecided": 0,
            "judged": 0,
            "hit_rate": None,
        }
        if conn is None:
            return {"stats": empty, "records": []}
        try:
            return {
                "stats": chat_store.record_stats(conn),
                "records": chat_store.list_records(
                    conn, limit=max(1, min(int(limit), 200)), symbol=symbol, status=status
                ),
            }
        finally:
            chat_store.close(conn)

    @router.post("/recall")
    def post_recall() -> Any:
        """F9：手动跑一次回访（到期记录拉日线判对错）。

        调度器还没统一之前，这个端点既是手动触发口，也是定时任务将来调用的同一个入口。
        """
        conn = chat_store.connect(data_root)
        if conn is None:
            return {"ok": False, "error": "chat_store_unavailable"}
        try:
            stats = recall.run_recall(conn, data_root=data_root)
            return {"ok": True, **stats}
        finally:
            chat_store.close(conn)

    @router.post("/turns")
    async def post_turn(request: Request, body: ChatRequest) -> Any:
        if wants_stream(request):
            try:
                session_id, text = _precheck_turn(body)
            except ChatFailure as exc:
                return _record_failure(body.session_id or "", exc)
            return StreamingResponse(
                run_turn_stream(
                    message=text,
                    session_id=session_id,
                    context=body.context,
                    data_root=data_root,
                    plan=body.plan,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            session_id = resolve_session_id(body.session_id)
            return await run_in_threadpool(
                run_turn,
                message=body.message,
                session_id=session_id,
                context=body.context,
                timeout_sec=body.timeout_sec,
                config_root=config_root,
                data_root=data_root,
            )
        except ChatFailure as exc:
            # 校验类失败（空消息 / 超长 / session_id 非法）还没起一轮，不落库；
            # 服务端失败（模型/数据）由 `run_turn` 落 `failed` 行，前端按 status 重试。
            return _record_failure(body.session_id or "", exc)

    return router


class RenameRequest(BaseModel):
    """重命名载荷：标题按首问同一口径截到 40 码点。"""

    model_config = ConfigDict(extra="forbid")

    title: str


SKILLS_PREFIX = "/api/skills"


def build_skills_router(data_root: Any = None) -> APIRouter:
    """`POST /api/skills/assistant`（QUOT-476）：/skills 页抽屉的一问一答。

    与 `/api/research/{code}/assistant` 同一套客户端与降级口径，差别只有两点：
    system prompt 不含个股深挖预设（`SKILL_SYSTEM_PROMPT`）；meta 行写页面键与
    绑定技能，而不是个股深挖的视图 / 日期。
    """
    router = APIRouter(prefix=SKILLS_PREFIX, dependencies=[Depends(_guard)])

    @router.post("/assistant")
    async def skill_assistant(body: SkillAssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        page_label = "研究助手 · 技能页"
        if body.bound_skill:
            page_label += f" · {body.bound_skill}"
        context, sources = _normalize_context(body.context)
        intent = classify_intent(question, context)
        try:
            result = await ask_agent(
                question=question,
                context=context,
                tab=body.page_key,
                page_label=page_label,
                system_prompt=SKILL_SYSTEM_PROMPT,
                module=module_for(body.page_key),
                page="skills",
                data_root=data_root,
            )
        except AssistantFailure as exc:
            return _failure_response(exc)
        return _success_response(
            result,
            sources,
            intent,
            fetched_metrics=result["metrics"],
            fetched_sources=result["sources"],
            fetch_attempted=result["used_tools"],
        )

    return router


PAGE_PREFIX = "/api/page"


def build_page_router(data_root: Any = None) -> APIRouter:
    """`POST /api/page/assistant`（QUOT-493/QUOT-477）：任意页面抽屉的一问一答。

    与 `/api/research/{code}/assistant`、`/api/skills/assistant` 同一套客户端、闸门与
    降级口径；差别有两点：system prompt 换成不含个股深挖预设的 `PAGE_SYSTEM_PROMPT`，
    meta 行只写页面键与绑定技能。
    """
    router = APIRouter(prefix=PAGE_PREFIX, dependencies=[Depends(_guard)])

    @router.post("/assistant")
    async def page_assistant(body: PageAssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        context, sources = _normalize_context(body.context)
        page_label = "页面助手"
        if body.page_key:
            page_label += f" · {body.page_key}"
        if body.bound_skill:
            page_label += f" · {body.bound_skill}"
        intent = classify_intent(question, context)
        # 能力问题**不走固定文案**：口径由 `PAGE_SYSTEM_PROMPT` 末尾的 `_CAPABILITY_RULE`
        # 约束（范围固定、措辞自由），同一个问题问两次允许说得不一样。
        try:
            result = await ask_agent(
                question=question,
                context=context,
                tab=body.page_key,
                page_label=page_label,
                system_prompt=PAGE_SYSTEM_PROMPT,
                module=module_for(body.page_key),
                page="page",
                data_root=data_root,
            )
        except AssistantFailure as exc:
            return _failure_response(exc)
        return _success_response(
            result,
            sources,
            intent,
            fetched_metrics=result["metrics"],
            fetched_sources=result["sources"],
            fetch_attempted=result["used_tools"],
        )

    return router


__all__ = [
    "ASSISTANT_PREFIX",
    "ASSISTANT_TIMEOUT_SEC",
    "CONVERSATION_PREFIX",
    "DEFAULT_MODULE",
    "MAX_ANSWER_BLOCKS",
    "MAX_CONTEXT_CHARS",
    "MAX_QUESTION_ASK_CHARS",
    "MAX_QUESTION_CHARS",
    "PAGE_KEY_MODULES",
    "PAGE_PREFIX",
    "PAGE_SYSTEM_PROMPT",
    "RUN_TURN_MODULE",
    "SESSION_ID_RE",
    "SKILLS_PREFIX",
    "SKILL_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "AssistantFailure",
    "AssistantRequest",
    "AsyncStructuredContext",
    "ChatFailure",
    "ChatRequest",
    "ChatRequestContext",
    "ChatSource",
    "ContextHistoryTurn",
    "PageAssistantRequest",
    "RenameRequest",
    "SkillAssistantRequest",
    "ask",
    "build_conversation_router",
    "build_page_router",
    "build_router",
    "build_skills_router",
    "memory_scope_for",
    "module_for",
    "reply_to_structured",
    "resolve_session_id",
    "run_turn",
]
