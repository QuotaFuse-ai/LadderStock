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
"""

from __future__ import annotations

import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ladderstock.ai import fallback, memory, module_memory
from ladderstock.ai.cli_provider import CliUnavailable, build_chat_client
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.providers import ProviderError, UnconfiguredError
from ladderstock.core.boundary import BoundaryViolation
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


class AssistantRequest(BaseModel):
    """页面发来的最小载荷：问题 + 已脱敏的上下文文本。"""

    question: str = ""
    context: str = ""
    date: str | None = None
    tab: str | None = None


# QUOT-476：/skills 页没有 code，抽屉把页面键 / 绑定技能 / 对象拼进 context 文本后
# 再带上这三个键，方便日志与服务端理解来源；三者均可缺省。
class SkillAssistantRequest(BaseModel):
    """技能页抽屉发来的载荷：问题 + 页面键上下文（与 `/research` 同一文本约定）。"""

    question: str = ""
    context: str = ""
    page_key: str | None = None
    bound_skill: str | None = None


# QUOT-493/QUOT-477：page 通道与 research / skills 同构——上下文统一为前端序列化好的
# 文本（`AiDock` 的 serializedContext），本端不再做 JSON 解析，避免两侧类型各执一端。
class PageAssistantRequest(BaseModel):
    """任意页面抽屉发来的载荷：问题 + 页级上下文文本（与另两条通道同一约定）。"""

    question: str = ""
    context: str = ""
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
        },
    )


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
) -> dict[str, Any]:
    """跑一轮问答。成功返回 `{reply, model, ai, fetched_at}`；失败抛 `AssistantFailure`。

    QUOT-487：同一条链上先召回跨会话记忆并注入系统提示（独立分段，见
    `ai/memory.render_memory_block`），回复完成后异步按策略写入记忆——
    写入失败只记日志，绝不影响已经完成的回答。测试可用 `sync_memory=True`
    同步等写入结束。

    QUOT-498：记忆检索/写入走模块命名空间（`memory_scope_for`），`agent_id` 与
    `kb_scope` 两维同时到位；配置读不出命名空间时跳过记忆，回答照常。
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
    block = memory.render_memory_block(hits)
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


def _question_payload_error(question: str, context: str) -> JSONResponse | None:
    """问题 / 上下文长度校验，`/research` 与 `/skills` 两个端点共用。"""
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
    if len(context) > MAX_CONTEXT_CHARS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (f"页级上下文过长（上限 {MAX_CONTEXT_CHARS} 字），请缩小页面数据后重试"),
                "code": "context_too_long",
                "retryable": False,
            },
        )
    return None


def build_router() -> APIRouter:
    """`POST /api/research/{code}/assistant`：页级研究助手一问一答。"""
    router = APIRouter(prefix=ASSISTANT_PREFIX, dependencies=[Depends(_guard)])

    @router.post("/{code}/assistant")
    def research_assistant(code: str, body: AssistantRequest) -> Any:
        question = body.question.strip()
        error = _question_payload_error(question, body.context)
        if error is not None:
            return error
        try:
            return ask(
                question=question,
                context=body.context,
                day=body.date,
                tab=body.tab,
                module="senate",
                symbol=code,
                page="research",
            )
        except AssistantFailure as exc:
            return _failure_response(exc)

    return router


SKILLS_PREFIX = "/api/skills"


def build_skills_router() -> APIRouter:
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
        try:
            return ask(
                question=question,
                context=body.context,
                tab=body.page_key,
                page_label=page_label,
                system_prompt=SKILL_SYSTEM_PROMPT,
                module=module_for(body.page_key),
                page="skills",
            )
        except AssistantFailure as exc:
            return _failure_response(exc)

    return router


PAGE_PREFIX = "/api/page"


def build_page_router() -> APIRouter:
    """`POST /api/page/assistant`（QUOT-493/QUOT-477）：任意页面抽屉的一问一答。

    与 `/api/research/{code}/assistant`、`/api/skills/assistant` 同一套客户端、闸门与
    降级口径；差别有两点：system prompt 换成不含个股深挖预设的 `PAGE_SYSTEM_PROMPT`，
    meta 行只写页面键与绑定技能。
    """
    router = APIRouter(prefix=PAGE_PREFIX, dependencies=[Depends(_guard)])

    @router.post("/assistant")
    def page_assistant(body: PageAssistantRequest) -> Any:
        question = body.question.strip()
        context = body.context
        error = _question_payload_error(question, context)
        if error is not None:
            return error
        page_label = "页面助手"
        if body.page_key:
            page_label += f" · {body.page_key}"
        if body.bound_skill:
            page_label += f" · {body.bound_skill}"
        try:
            return ask(
                question=question,
                context=context,
                tab=body.page_key,
                page_label=page_label,
                system_prompt=PAGE_SYSTEM_PROMPT,
                module=module_for(body.page_key),
            )
        except AssistantFailure as exc:
            return _failure_response(exc)

    return router


__all__ = [
    "ASSISTANT_PREFIX",
    "ASSISTANT_TIMEOUT_SEC",
    "DEFAULT_MODULE",
    "MAX_CONTEXT_CHARS",
    "MAX_QUESTION_CHARS",
    "PAGE_KEY_MODULES",
    "PAGE_PREFIX",
    "PAGE_SYSTEM_PROMPT",
    "SKILLS_PREFIX",
    "SKILL_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "AssistantFailure",
    "AssistantRequest",
    "PageAssistantRequest",
    "SkillAssistantRequest",
    "ask",
    "build_page_router",
    "build_router",
    "build_skills_router",
    "memory_scope_for",
    "module_for",
]
