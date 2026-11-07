"""页级研究助手（QUOT-454）：把页面上下文 + 问题交给**既有** AI 通道。

本模块只做三件事：

1. 校验请求（问题 / 上下文长度），把页面上下文拼进同一条用户消息；
2. 按 `config/ai.yaml` + `ai.env` 选择客户端——仍然走
   `ai/cli_provider.build_chat_client()`（订阅版 CLI / OpenAI 兼容 / Anthropic），
   **不新起一套模型调用、不读也不回显密钥**；
3. 把失败翻译成可读、可重试的 HTTP 状态：未配 Key、断网、超时分开说。

AI 回答只是文本，不会回写页面上的行情 / 财务数据（页面数据仍以接口为准）。
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ladderstock.ai import fallback
from ladderstock.ai.cli_provider import CliUnavailable, build_chat_client
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.providers import ProviderError, UnconfiguredError
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web.research import _guard

ASSISTANT_PREFIX = "/api/research"
#: 单次问答的上限：问题够长，上下文只放摘要，避免把整页产物塞进 prompt
MAX_QUESTION_CHARS = 2000
MAX_CONTEXT_CHARS = 12000
#: 与 `ai/cli_provider` / `orchestrator` 的默认值一致；页面侧再给 90s 的请求超时
ASSISTANT_TIMEOUT_SEC = 60.0

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


def _load_client() -> tuple[Any, str]:
    """读配置并构造既有通道的客户端；配置不可用时抛 `AssistantFailure`。"""
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
    return client, config.default_model


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, httpx.TimeoutException)):
        return True
    return "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()


def ask(
    *,
    question: str,
    context: str,
    day: str | None = None,
    tab: str | None = None,
    page_label: str = "个股深挖",
    system_prompt: str = SYSTEM_PROMPT,
) -> dict[str, Any]:
    """跑一轮问答。成功返回 `{reply, model, fetched_at}`；失败抛 `AssistantFailure`。"""
    client, model = _load_client()
    header = "【页级上下文】\n" + (context.strip() or "（这一页的数据还没取到。）")
    meta = f"页面：{page_label} · 视图 {tab or '未标注'} · 日期 {day or '未标注'}"
    user = f"{meta}\n{header}\n\n【问题】\n{question.strip()}"
    try:
        reply = client.complete(model, system_prompt, user, json_mode=False)
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
    return {
        "reply": text,
        "model": model,
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
                "detail": (
                    f"页级上下文过长（上限 {MAX_CONTEXT_CHARS} 字），"
                    "请缩小页面数据后重试"
                ),
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
            return ask(question=question, context=body.context, day=body.date, tab=body.tab)
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
            )
        except AssistantFailure as exc:
            return _failure_response(exc)

    return router


__all__ = [
    "ASSISTANT_PREFIX",
    "ASSISTANT_TIMEOUT_SEC",
    "MAX_CONTEXT_CHARS",
    "MAX_QUESTION_CHARS",
    "SKILLS_PREFIX",
    "SKILL_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "AssistantFailure",
    "AssistantRequest",
    "SkillAssistantRequest",
    "ask",
    "build_router",
    "build_skills_router",
]
