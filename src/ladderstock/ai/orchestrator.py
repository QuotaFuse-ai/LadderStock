"""AI 五段编排状态机（M3 §6.3）。

顺序：情绪 → 资金 → 题材 → 龙虎榜 → 龙头。每段独立生成、独立落 checkpoint，
某段失败不影响前序成果；`retry_failed` 只重跑失败段。

每段的降级阶梯：

1. 结构化输出（JSON + Schema 校验）；
2. 文本解析补偿（从同一段返回文本里提取叙述）；
3. 纯文本（标注 `degraded`），是否走这一级由 `ai.yaml: on_all_failed` 决定。

换模型只在**网络 / 额度**错误时发生；格式错误先同模型重试一次。
所有层级都失败时该段记 `failed`，整条复盘退出码为 9（`EXIT_AI_DEGRADED`）。

边界：AI 只能写叙述（`core/boundary.py:merge_narrative`），指标原样保留。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import httpx

from ladderstock.ai import checkpoint, fallback, structured_output
from ladderstock.ai.error_taxonomy import AiFailure, classify_error
from ladderstock.core.boundary import merge_narrative
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK


class UnconfiguredError(RuntimeError):
    """缺少模型地址或密钥：属于本地配置问题，不是网络故障。"""


class ChatClient(Protocol):
    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        """返回模型正文；失败时抛异常，由 `classify_error` 归类。"""


class HttpChatClient:
    """OpenAI 兼容的 `/chat/completions` 客户端；密钥只从环境变量读。"""

    def __init__(
        self,
        config: fallback.AiConfig,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.transport = transport

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        base_url = os.environ.get(self.config.base_url_env, "").strip()
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not base_url or not api_key:
            raise UnconfiguredError(
                f"未配置模型凭据（{self.config.base_url_env} / {self.config.api_key_env}）"
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
            data = response.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ValueError("模型响应缺少 choices[0].message.content") from None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("模型返回空内容")
        return text


@dataclass
class RunResult:
    """一次编排的完整结果（CLI 直接序列化）。"""

    date: str
    sections: list[dict[str, Any]] = field(default_factory=list)
    ai_failures: list[dict[str, Any]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    degraded: bool = False
    failed: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    checkpoint_path: str | None = None
    configured: bool = True

    @property
    def exit_code(self) -> int:
        return EXIT_AI_DEGRADED if (self.failed or self.degraded) else EXIT_OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "configured": self.configured,
            "models": self.models,
            "degraded": self.degraded,
            "failed": self.failed,
            "generated": self.generated,
            "reused": self.reused,
            "sections": [
                {
                    "name": item["name"],
                    "status": item["status"],
                    "mode": item["mode"],
                    "model": item.get("model"),
                    "degraded": item.get("degraded", False),
                }
                for item in self.sections
            ],
            "ai_failures": self.ai_failures,
            "checkpoint": self.checkpoint_path,
        }


class Orchestrator:
    def __init__(
        self,
        *,
        config: fallback.AiConfig | None = None,
        client: ChatClient | None = None,
        root: Any = None,
        config_root: Any = None,
    ) -> None:
        self.config = config or fallback.load_config(config_root)
        self.client: ChatClient = client or HttpChatClient(self.config)
        self.root = root
        self._prompts: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #

    def run(
        self,
        day: date | str,
        metrics_by_section: dict[str, dict[str, Any]],
        *,
        force: bool = False,
        retry_failed: bool = False,
        only: str | None = None,
    ) -> RunResult:
        document = checkpoint.load(day, self.root)
        targets = self._targets(document, force=force, retry_failed=retry_failed, only=only)

        for name in targets:
            metrics = metrics_by_section.get(name, {})
            record = self._generate(name, day, metrics)
            checkpoint.record(
                document,
                name,
                status=record["status"],
                mode=record["mode"],
                narrative=record["narrative"],
                model=record.get("model"),
                degraded=record["degraded"],
                notes=record["notes"],
                failure=record.get("failure"),
                attempts=record.get("attempts"),
            )
            checkpoint.save(document, self.root)

        return self._result(day, document, targets, metrics_by_section)

    # ------------------------------------------------------------------ #
    # 段落选择
    # ------------------------------------------------------------------ #

    def _targets(
        self,
        document: dict[str, Any],
        *,
        force: bool,
        retry_failed: bool,
        only: str | None,
    ) -> list[str]:
        if only is not None and only not in checkpoint.SECTIONS:
            raise ValueError(f"未知段落：{only}（可选：{', '.join(checkpoint.SECTIONS)}）")
        done = set(checkpoint.done_sections(document))
        targets: list[str] = []
        for name in checkpoint.SECTIONS:
            if only is not None and name != only:
                continue
            if force:
                targets.append(name)
            elif retry_failed:
                # 只补失败 / 未跑过的段落，成功段落一律不重跑
                if name not in done:
                    targets.append(name)
            elif name not in done:
                targets.append(name)
        return targets

    # ------------------------------------------------------------------ #
    # 单段生成
    # ------------------------------------------------------------------ #

    def _generate(self, name: str, day: date | str, metrics: dict[str, Any]) -> dict[str, Any]:
        if not fallback.is_configured(self.config):
            missing = "、".join(fallback.missing_env(self.config))
            text = (
                f"AI 未配置（缺少环境变量 {missing}），本段未生成叙事，"
                "仅保留程序计算的指标。"
            )
            return {
                "status": "degraded",
                "mode": "text",
                "degraded": True,
                "model": None,
                "narrative": [text],
                "notes": ["纯文本降级：未配置模型凭据，不发起任何请求"],
                "failure": None,
                "attempts": [],
            }

        system, user = self._messages(name, day, metrics)
        schema = structured_output.schema_for(
            name, fallback.section_config(self.config, name).output_schema
        )
        models = fallback.model_chain(self.config)
        attempts: list[dict[str, Any]] = []
        last_failure: AiFailure | None = None

        if self.config.structured_output != "off":
            payload, failure = self._structured_chain(name, schema, models, system, user, attempts)
            if payload is not None:
                return _payload(payload, models, attempts)
            last_failure = failure

        if self.config.on_all_failed == "degrade_to_text":
            payload, failure = self._plain_chain(models, system, user, attempts)
            if payload is not None:
                return {
                    "status": "degraded",
                    "mode": "text",
                    "degraded": True,
                    "model": attempts[-1]["model"] if attempts else None,
                    "narrative": payload["narrative"],
                    "notes": ["纯文本降级：结构化输出失败后改用纯文本生成"],
                    "failure": (failure.as_dict() if failure else None),
                    "attempts": attempts,
                }
            last_failure = failure or last_failure

        failure_dict = last_failure.as_dict() if last_failure else None
        return {
            "status": "failed",
            "mode": "failed",
            "degraded": False,
            "model": None,
            "narrative": [],
            "notes": ["该段 AI 生成失败，其他段落结果不受影响；可用 ldst ai retry 重试"],
            "failure": failure_dict,
            "attempts": attempts,
        }

    def _structured_chain(
        self,
        name: str,
        schema: dict[str, Any],
        models: list[str],
        system: str,
        user: str,
        attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, AiFailure | None]:
        last: AiFailure | None = None
        for model in models:
            payload, raw, failure = self._structured(model, name, schema, system, user, attempts)
            if payload is not None:
                return payload, None
            if raw is not None:
                compensated = structured_output.compensate(raw)
                if compensated is not None:
                    try:
                        payload = structured_output.validate_payload(name, compensated)
                    except structured_output.StructuredOutputError:
                        payload = None
                    if payload is not None:
                        attempts.append({"model": model, "mode": "parsed", "kind": None})
                        return payload, None
            if failure is not None:
                last = failure
                # 只有网络 / 额度错误才换模型；其余直接进入纯文本降级
                if not fallback.should_switch(failure):
                    break
        return None, last

    def _structured(
        self,
        model: str,
        name: str,
        schema: dict[str, Any],
        system: str,
        user: str,
        attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None, AiFailure | None]:
        """结构化调用；格式错误同模型重试一次（issue R3）。"""
        failure: AiFailure | None = None
        text = ""
        for attempt in (1, 2):
            try:
                text = self.client.complete(model, system, user, json_mode=True)
            except Exception as exc:  # noqa: BLE001 - 分类后统一处理
                failure = classify_error(exc)
                attempts.append(_attempt(model, "structured", failure))
                return None, None, failure
            try:
                payload = structured_output.parse_json(text)
                payload = structured_output.validate_payload(name, payload)
            except structured_output.StructuredOutputError as exc:
                failure = classify_error(exc, schema=schema)
                attempts.append(_attempt(model, "structured", failure))
                if attempt == 1:
                    continue
                return None, text, failure
            attempts.append({"model": model, "mode": "structured", "kind": None})
            return payload, text, None
        return None, text, failure

    def _plain_chain(
        self,
        models: list[str],
        system: str,
        user: str,
        attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, AiFailure | None]:
        last: AiFailure | None = None
        for model in models:
            try:
                text = self.client.complete(model, system, user, json_mode=False)
            except Exception as exc:  # noqa: BLE001
                failure = classify_error(exc)
                attempts.append(_attempt(model, "text", failure))
                last = failure
                if not fallback.should_switch(failure):
                    break
                continue
            payload = structured_output.plain_payload(text)
            if payload is not None:
                attempts.append({"model": model, "mode": "text", "kind": None})
                return payload, None
        return None, last

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #

    def _messages(
        self, name: str, day: date | str, metrics: dict[str, Any]
    ) -> tuple[str, str]:
        prompt = self._prompt(name)
        return structured_output.build_messages(name, str(day), metrics, prompt)

    def _prompt(self, name: str) -> str:
        if name not in self._prompts:
            section = fallback.section_config(self.config, name)
            self._prompts[name] = structured_output.load_prompt(section.prompt_file)
        return self._prompts[name]

    def _result(
        self,
        day: date | str,
        document: dict[str, Any],
        targets: list[str],
        metrics_by_section: dict[str, dict[str, Any]],
    ) -> RunResult:
        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        models: list[str] = []
        degraded = False
        for name in checkpoint.SECTIONS:
            state = checkpoint.section_state(document, name)
            status = str(state.get("status") or "pending")
            if status == "pending":
                continue
            model = state.get("model")
            if model and model not in models:
                models.append(str(model))
            narrative = [str(item) for item in state.get("narrative") or []]
            ai_payload: dict[str, Any] = {
                "narrative": narrative,
                "degraded": bool(state.get("degraded")),
            }
            if model:
                ai_payload["model"] = str(model)
            metrics = metrics_by_section.get(name, {})
            # 边界闸门：AI 只能追加叙述，命中指标键即 BoundaryViolation
            merged = merge_narrative(metrics, ai_payload)
            if ai_payload["degraded"]:
                degraded = True
            section = {
                "name": name,
                "title": structured_output.SECTION_TITLES.get(name, name),
                "status": status,
                "mode": state.get("mode"),
                "model": model,
                "degraded": bool(state.get("degraded")),
                "narrative": narrative,
                "notes": [str(note) for note in state.get("notes") or []],
                "metrics": dict(metrics),
                "ai": merged["ai"],
            }
            sections.append(section)
            if status == "failed":
                failure = state.get("failure") or {}
                failures.append(
                    {
                        "section": name,
                        "kind": failure.get("kind", "internal"),
                        "message": failure.get("message", "AI 生成失败"),
                        "action": failure.get("action", "稍后重试"),
                        "retryable": bool(failure.get("retryable")),
                    }
                )
        failed = [item["section"] for item in failures]
        generated = [name for name in targets if name not in failed]
        reused = [
            name
            for name in checkpoint.done_sections(document)
            if name not in generated
        ]
        return RunResult(
            date=str(day),
            sections=sections,
            ai_failures=failures,
            models=models,
            degraded=degraded,
            failed=failed,
            generated=generated,
            reused=reused,
            checkpoint_path=str(checkpoint.checkpoint_path(day, self.root)),
            configured=fallback.is_configured(self.config),
        )


def _attempt(model: str, mode: str, failure: AiFailure) -> dict[str, Any]:
    return {
        "model": model,
        "mode": mode,
        "kind": failure.kind,
        "message": failure.message,
    }


def _payload(
    payload: dict[str, Any], models: list[str], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    mode = "structured"
    if attempts and attempts[-1].get("mode") == "parsed":
        mode = "parsed"
    return {
        "status": "ok",
        "mode": mode,
        "degraded": False,
        "model": attempts[-1]["model"] if attempts else (models[0] if models else None),
        "narrative": payload["narrative"],
        "notes": [],
        "failure": None,
        # 成功段也要保留尝试轨迹（换了几档、格式错重试几次），
        # 否则 checkpoint 里 attempts 恒为空，事后无法解释用了哪个模型。
        "attempts": list(attempts),
    }


__all__ = [
    "ChatClient",
    "HttpChatClient",
    "Orchestrator",
    "RunResult",
    "UnconfiguredError",
]
