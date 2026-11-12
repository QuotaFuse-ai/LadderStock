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
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ladderstock.ai import answer_blocks, fallback, memory, module_memory
from ladderstock.ai.answer_blocks import AnswerBlockError, normalize_ai
from ladderstock.ai.answer_blocks import build_blocks as _build_blocks
from ladderstock.ai.cli_provider import CliUnavailable, build_chat_client
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.providers import ProviderError, UnconfiguredError
from ladderstock.cli.paths import data_dir
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.data import chat_store
from ladderstock.knowledge.recall import recall_wrapped
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


SYSTEM_PROMPT = (
    "你是 LadderStock「个股深挖」页的研究助手。只依据【页级上下文】里给出的数据回答，"
    "不要编造上下文里没有的数字、日期或结论。\n"
    "- 上下文缺失的行情 / 财报 / 事件，就直说「这一项页面没有取到」，不要用一般知识补。\n"
    "- 上下文里的数字只是页面已展示数据的摘要，不能当作新的行情结论；"
    "不要给出买卖建议，也不要预测价格。\n"
    "- 回答用简洁中文，必要时分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。"
)

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
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。"
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
    "- 上下文缺失的行情 / 新闻 / 财报 / 事件，就直说「本页这项没取到」，不要用一般知识补。\n"
    "- 这是页面内的轻量问答，不做单只股票的深度研究；需要深挖单个标的时，请回到个股研究页再问。\n"
    "- 上下文里的数字只是页面已展示数据的摘要，不能当作新的行情结论；"
    "不要给出买卖建议，也不要预测价格。\n"
    "- 回答用简洁中文，必要时分点；不要输出 JSON，不要使用 Markdown 标记"
    "（**、#、表格），用纯文本与短横线分点；不要复述整段上下文。"
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
) -> list[dict[str, Any]]:
    """成功路径装配 `blocks`。

    `metrics` 一律传 `{}`：现役三路由拿不到任何程序算出的数，`build_blocks` 于是生成
    `notice(missing_data)`——**不伪造 metrics**（红线 6 / 本单第 5 条）。真实 metrics
    接入是待办（见回报）。`sources=[]` 时由 `build_blocks` 生成「无来源」notice。
    """
    return _build_blocks({}, reply_to_structured(reply)["build_input"], sources)


def _success_response(
    result: Mapping[str, Any],
    sources: Sequence[ChatSource],
) -> dict[str, Any]:
    """成功响应：既有四键原样透传，**只追加** `blocks` 与 `ai_structured`。

    `ai` 仍是原来的 `{model, source}` 回显（旧调用方可能在用，不得顶替）；结构化叙述
    旁挂在 `ai_structured`（`narrative / highlights / risks / evidence`），供 QUOT-581
    直接喂 `build_blocks`。字段名不与 581 的 `seq / last_seq / completed` 冲突。
    """
    adapted = reply_to_structured(result["reply"])
    return {
        **result,
        "blocks": _build_blocks({}, adapted["build_input"], sources),
        "ai_structured": adapted["ai_structured"],
    }


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
    *, reply: str, sources: Sequence[ChatSource], failure: ChatFailure | None = None
) -> list[dict[str, Any]]:
    """一轮的块：失败只有一条 `notice`；成功复用现役装配链，再收到 D2 上限 6。

    `metrics` 一律 `{}`（与 `_blocks_for` 同口径：会话模式同样拿不到程序算出的数，
    由 `build_blocks` 出 `notice(missing_data)`，不伪造）。块的顺序、文案、数字剥离
    全部由 `ai/answer_blocks.py` 负责，这里不另拼。
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
    return _blocks_for(reply, sources)[:MAX_ANSWER_BLOCKS]


def _arrive_blocks(
    *,
    reply: str,
    sources: Sequence[ChatSource],
    on_block: Callable[[list[dict[str, Any]]], None],
) -> list[dict[str, Any]]:
    """同 `_conversation_blocks` 成功路径，但逐块交付：每多一块就 `on_block(当前全部块)`。

    块的装配仍走现役 `_blocks_for()`（`reply_to_structured()` → `build_blocks()`），
    不在这里重排；逐块交付只服务「边到达边 upsert」的落库时机（DEC-085）。返回值与
    `_conversation_blocks()` 完全一致，`on_block` 收到的最后一次快照就是返回值。
    """
    full = _conversation_blocks(reply=reply, sources=sources)
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
        result = ask(
            question=text,
            context=context_text,
            page_label="对话模式",
            module=RUN_TURN_MODULE,
            page=(
                context.page
                if context is not None and context.page
                else PAGE_PREFIX.strip("/")
            ),
            data_root=data_root,
        )
    except AssistantFailure as exc:
        raise _as_chat_failure(exc) from None

    reply = result["reply"]
    blocks = _arrive_blocks(reply=reply, sources=sources, on_block=writer.arrive)
    writer.finish(chat_store.DONE, blocks)
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
    knowledge_block = _recall_knowledge(
        question,
        symbol=symbol,
        data_root=data_root if data_root is not None else data_dir(),
    )
    block = memory.render_memory_block(hits, knowledge=knowledge_block)
    if block and scope_info is not None:
        block = f"{scope_info['label']}\n{block}"
    if block:
        system = f"{system_prompt}\n\n{block}\n\n{_MEMORY_CONFLICT_RULE}"
    else:
        system = system_prompt
    header = "【页级上下文】\n" + (context.strip() or "（这一页的数据还没取到。）")
    meta = f"页面：{page_label} · 视图 {tab or '未标注'} · 日期 {day or '未标注'}"
    user = f"{meta}\n{header}\n\n【问题】\n{question.strip()}"
    try:
        reply = client.complete(model, system, user, json_mode=False)
    except (UnconfiguredError, BoundaryViolation) as exc:
        raise AssistantFailure(503, "ai_not_configured", f"未配置 AI 通道：{exc}") from None
    except CliUnavailable as exc:
        raise AssistantFailure(503, "ai_cli_unavailable", f"订阅版 CLI 不可用：{exc}") from None
    except (ProviderError, httpx.HTTPError, OSError) as exc:
        failure = classify_error(exc)
        if _is_timeout(exc):
            raise AssistantFailure(
                504, "ai_timeout", f"AI 响应超时（{int(ASSISTANT_TIMEOUT_SEC)}s）：{failure.detail}"
            ) from None
        if failure.kind == "quota":
            raise AssistantFailure(429, "ai_quota", failure.message) from None
        if failure.kind == "network":
            raise AssistantFailure(
                502, "ai_network", f"AI 请求未送达（网络/代理）：{failure.detail}"
            ) from None
        raise AssistantFailure(502, "ai_upstream", failure.message) from None
    except Exception as exc:  # noqa: BLE001 - 兜底也要给可读原因，绝不静默失败
        failure = classify_error(exc)
        if _is_timeout(exc):
            raise AssistantFailure(
                504, "ai_timeout", f"AI 响应超时（{int(ASSISTANT_TIMEOUT_SEC)}s）：{failure.detail}"
            ) from None
        raise AssistantFailure(502, "ai_upstream", failure.message) from None

    text = (reply or "").strip()
    if not text:
        raise AssistantFailure(502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。")
    if mem_client is not None and scope is not None:
        # QUOT-487 写入：回答已经成立，异步落库；失败只记日志，不影响本次响应。
        # 命名空间两维随载荷同行（QUOT-498），无命名空间时不落库
        memory.dispatch_store(
            question,
            symbol=symbol,
            page=page,
            run_id=uuid.uuid4().hex,
            settings=settings,
            client=mem_client,
            sync=sync_memory,
            scope=scope,
        )
    return {
        "reply": text,
        "model": model,
        "ai": echo,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


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
    def research_assistant(code: str, body: AssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        context, sources = _normalize_context(body.context)
        try:
            result = ask(
                question=question,
                context=context,
                day=body.date,
                tab=body.tab,
                module="senate",
                symbol=code,
                page="research",
                data_root=data_root,
            )
        except AssistantFailure as exc:
            return _failure_response(exc)
        return _success_response(result, sources)

    return router


CONVERSATION_PREFIX = "/api/conversation"


def _blocks_for_view(row: dict[str, Any]) -> list[Any]:
    try:
        return json.loads(row["blocks_json"])
    except ValueError:
        return []


def build_conversation_router(
    *, data_root: Any = None, config_root: Any = None
) -> APIRouter:
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
            return {"sessions": chat_store.list_sessions(conn)}
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

    @router.post("/turns")
    def post_turn(body: ChatRequest) -> Any:
        try:
            session_id = resolve_session_id(body.session_id)
            return run_turn(
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
    def skill_assistant(body: SkillAssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        page_label = "研究助手 · 技能页"
        if body.bound_skill:
            page_label += f" · {body.bound_skill}"
        context, sources = _normalize_context(body.context)
        try:
            result = ask(
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
        return _success_response(result, sources)

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
    def page_assistant(body: PageAssistantRequest) -> Any:
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
        try:
            result = ask(
                question=question,
                context=context,
                tab=body.page_key,
                page_label=page_label,
                system_prompt=PAGE_SYSTEM_PROMPT,
                module=module_for(body.page_key),
                data_root=data_root,
            )
        except AssistantFailure as exc:
            return _failure_response(exc)
        return _success_response(result, sources)

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
