"""可选的模型叙述（R6 要求 2 的「模型可选、默认关闭」与要求 4 的隐私边界）。

三条硬规矩，写代码时就定死，不靠调用方自觉：

1. **默认关闭**：只有 CLI/MCP 显式带 `--ai` / `ai=true` 才会走到这里，
   `AI_ENRICH_ENABLED_BY_DEFAULT = False` 是这个决定的唯一出处，测试反向钉住。
2. **模型只写叙述**：入口只接受「已经由规则抽出的事件」，产出只允许落进
   `ai_note` 一个字段。模型不许新增事件、不许改类型/日期/代码——
   这是 DEC-006 闸门 5「AI 只写叙述」在资讯面的落地，复用
   `core.boundary.assert_narrative_only` 做键名把关。
3. **只送最小视图**：prompt 里的内容是 `NewsItem.ai_payload()`——
   标题 + ≤200 字摘要 + url，正文全文、`related_codes`、`provenance` 一律不进。
   送之前再跑一遍 `private_keys_present`，出现隐私键名直接拒绝发送。

失败不装成功：模型没配、超时、返回空，都只把这条事件原样留着，
在结果里记 `failed` / `skipped_reason`，绝不因为 AI 抖动而丢事件。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ladderstock.core.boundary import assert_narrative_only
from ladderstock.core.privacy import private_keys_present
from ladderstock.news.events import NewsEvent
from ladderstock.news.models import NewsItem

if TYPE_CHECKING:  # 只在类型检查期引用，避免与 stream 形成运行期循环
    from ladderstock.news.stream import StreamResult

#: 默认关闭（issue 要求 2）：任何调用方都要显式打开，不存在「顺手就调了模型」
AI_ENRICH_ENABLED_BY_DEFAULT = False

#: 单次最多给多少条事件补叙述：一条一句，成本与耗时都要有上限
MAX_ENRICH_EVENTS = 20
DEFAULT_TIMEOUT = 20.0
NOTE_LIMIT = 200

SYSTEM_PROMPT = (
    "你是 A 股复盘助手。下面给你一条已经由规则抽取出的公告/快讯事实，"
    "包含原文标题、不超过 200 字的摘要和原文链接。"
    "请只输出一句中文叙述，说明这条事实对短线复盘的意义，"
    "不要输出日期、代码、评级或任何数字，不要补充事实之外的信息。"
    "如果资料不足以判断，只回答「资料不足」。"
)


class PromptPrivacyViolation(RuntimeError):
    """prompt 里出现了不该出现的字段——不发出去，直接停。"""


@dataclass
class EnrichResult:
    events: list[NewsEvent] = field(default_factory=list)
    attempted: int = 0
    enriched: int = 0
    failed: list[dict[str, Any]] = field(default_factory=list)
    skipped_reason: str = ""
    model: str = ""
    degraded: bool = False

    @property
    def all_failed(self) -> bool:
        return self.attempted > 0 and self.enriched == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": not self.skipped_reason,
            "skipped_reason": self.skipped_reason,
            "model": self.model,
            "attempted": self.attempted,
            "enriched": self.enriched,
            "failed": list(self.failed),
            "degraded": self.degraded,
        }


@dataclass
class ModelChat:
    """薄封装：让「用哪个模型」这件事在结果里可见，不藏在 client 对象里。"""

    client: Any
    model_label: str

    def complete(
        self, model: str, system: str, user: str, *, json_mode: bool
    ) -> str:
        return self.client.complete(model, system, user, json_mode=json_mode)


def prompt_payload(event: NewsEvent, item: NewsItem) -> dict[str, Any]:
    """送模型的最小视图：标题 + ≤200 字摘要 + url + 命中关键词（证据片段本身不外发全文）。"""
    payload = item.ai_payload()
    payload["label"] = event.label
    payload["matched"] = str(event.evidence.get("matched") or "")
    leaks = private_keys_present([payload])
    if leaks:
        raise PromptPrivacyViolation(f"prompt 含隐私字段：{', '.join(leaks)}，已拒绝发送")
    return payload


def render_user_prompt(event: NewsEvent, item: NewsItem) -> str:
    payload = prompt_payload(event, item)
    lines = [
        f"标题：{payload['title']}",
        f"摘要：{payload['summary'] or '（无）'}",
        f"链接：{payload['url']}",
        f"事件类型：{payload['label']}",
        f"命中关键词：{payload['matched'] or '（无）'}",
    ]
    return "\n".join(lines)


def enrich_events(
    events: Sequence[NewsEvent],
    items: Sequence[NewsItem],
    *,
    client: Any | None = None,
    model: str | None = None,
    limit: int = MAX_ENRICH_EVENTS,
) -> EnrichResult:
    """给规则抽出的事件补一句叙述。返回的新列表**顺序、条数、id 都不变**。"""
    result = EnrichResult(events=list(events))
    if not events:
        return result
    capped = max(0, min(int(limit), MAX_ENRICH_EVENTS))
    if capped == 0:
        result.skipped_reason = "未请求叙述补全（limit=0）"
        return result

    by_id: dict[str, NewsItem] = {item.id: item for item in items}
    try:
        chat: Any = client if client is not None else _default_chat()
    except Exception as exc:  # noqa: BLE001 - 配置缺失不是资讯流的失败
        result.skipped_reason = f"模型不可用：{exc}"[:200]
        result.degraded = True
        return result

    targets = [event for event in events if event.item_id in by_id][:capped]
    result.model = str(model or getattr(chat, "model_label", "") or "")
    position = {event.id: index for index, event in enumerate(result.events)}
    for event in targets:
        item = by_id[event.item_id]
        result.attempted += 1
        try:
            note = _call(chat, event, item, model=model)
        except Exception as exc:  # noqa: BLE001 - 单条失败只影响这一条的叙述
            result.failed.append({"event_id": event.id, "error": str(exc)[:200]})
            continue
        if not note:
            result.failed.append({"event_id": event.id, "error": "模型返回空叙述"})
            continue
        narrative_keys_ok(note, event)
        result.events[position[event.id]] = event.with_ai_note(note)
        result.enriched += 1
    if len(events) > len(targets):
        result.failed.append(
            {
                "event_id": "*",
                "error": f"超出单次上限 {capped} 条，未处理 {len(events) - len(targets)} 条",
            }
        )
    if result.attempted and result.enriched == 0:
        result.degraded = True
    return result


def _call(chat: Any, event: NewsEvent, item: NewsItem, *, model: str | None) -> str:
    user = render_user_prompt(event, item)
    text = chat.complete(
        str(model or getattr(chat, "model_label", "") or ""),
        SYSTEM_PROMPT,
        user,
        json_mode=False,
    )
    return _normalize_note(text)


def _normalize_note(text: Any) -> str:
    note = str(text or "").strip().strip('"').strip()
    if not note or note == "资料不足":
        return ""
    return note[:NOTE_LIMIT]


def _default_chat() -> ModelChat:
    """延迟构造 HTTP 客户端：没配模型也不该在导入期炸掉。"""
    from ladderstock.ai import fallback
    from ladderstock.ai.orchestrator import HttpChatClient

    config = fallback.load_config()
    if not fallback.is_configured(config):
        missing = ", ".join(fallback.missing_env(config)) or "ai.yaml 未配置模型链"
        raise RuntimeError(f"未配置模型（{missing}）；资讯叙述默认关闭，需要时加 --ai 并先配置")
    return ModelChat(HttpChatClient(config), str(config.default_model))


def narrative_keys_ok(note: str, event: NewsEvent) -> bool:
    """把「AI 只写叙述」这条闸门显式跑一遍：ai_note 不是指标键，越界就报错。"""
    metrics: Mapping[str, Any] = {
        key: value
        for key, value in event.as_dict().items()
        if key != "ai_note"
    }
    assert_narrative_only({"narrative": note}, metrics)
    return True


def apply_to_stream(stream_result: StreamResult, *, enabled: bool) -> dict[str, Any]:
    """CLI 与 MCP 共用的模型叙述入口——**默认关闭**，开了也只补叙述。

    返回一段可以直接塞进 `payload["ai"]` 的账本：没开就只有一句理由，
    不存在「没加开关也悄悄调了模型」这条路。
    """
    if not enabled:
        return {
            "requested": False,
            "enabled": False,
            "skipped_reason": "默认关闭：需要模型叙述请加 --ai / ai=true",
        }
    enrich = enrich_events(stream_result.event_objects, stream_result.items)
    if stream_result.persisted and enrich.enriched:
        from ladderstock.news.stream import persist_events

        persist_events(enrich.events)
    payload = enrich.as_dict()
    payload["requested"] = True
    payload["enabled"] = True
    return payload


__all__ = [
    "AI_ENRICH_ENABLED_BY_DEFAULT",
    "EnrichResult",
    "ModelChat",
    "PromptPrivacyViolation",
    "SYSTEM_PROMPT",
    "apply_to_stream",
    "enrich_events",
    "narrative_keys_ok",
    "prompt_payload",
    "render_user_prompt",
]
