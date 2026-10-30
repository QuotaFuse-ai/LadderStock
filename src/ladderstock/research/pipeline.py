"""个股研究六段编排（R1-1 骨架）。

`company → financial → forecast → valuation → risk → report` **顺序执行**，
每段完成即写产物 + 落 checkpoint（复用 `ai/checkpoint.py` 的状态机与原子写），
中断后重跑只补缺失段；单段失败不吞前序成果，整条管线以退出码 9 收尾。

本段的边界：

- **不联网取数**（联网检索属 R4，需人类批准）：`facts` 由调用方注入，默认空；
  六段先用占位叙述跑通链路与契约，实际提示词与降级阶梯由 R1-2 接入；
- `report` 段只消费前五段已落盘的结构化产物，不重新取数；
- AI 只能追加叙述：产物写盘前过 `core/boundary.merge_narrative` 闸门，
  模型试图输出指标键即判该段 `failed`（DEC-006 闸门 5）；
- `ChatClient` 可注入假实现，验收不依赖真实模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ladderstock.ai import fallback, structured_output
from ladderstock.ai.error_taxonomy import AiFailure, classify_error
from ladderstock.ai.orchestrator import ChatClient, HttpChatClient
from ladderstock.core.boundary import BoundaryViolation, merge_narrative
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK
from ladderstock.research import artifacts, checkpoint

STAGES = checkpoint.STAGES


@dataclass
class ResearchRunResult:
    """一次研究管线运行的完整结果（CLI `--output json` 直接序列化）。"""

    code: str
    date: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    research_failures: list[dict[str, Any]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    degraded: bool = False
    failed: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    checkpoint_path: str | None = None
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    configured: bool = True

    @property
    def exit_code(self) -> int:
        return EXIT_AI_DEGRADED if (self.failed or self.degraded) else EXIT_OK

    def as_dict(self) -> dict[str, Any]:
        """`--output json` 契约：键集合稳定，下游（R1-2/3/8、页面）只依赖这里。"""
        return {
            "code": self.code,
            "date": self.date,
            "stage_order": list(STAGES),
            "configured": self.configured,
            "models": self.models,
            "degraded": self.degraded,
            "failed": self.failed,
            "generated": self.generated,
            "reused": self.reused,
            "stages": self.stages,
            "research_failures": self.research_failures,
            "checkpoint": self.checkpoint_path,
            "artifacts": self.artifacts,
            "exit_code": self.exit_code,
        }


class ResearchPipeline:
    def __init__(
        self,
        code: Any,
        *,
        config: fallback.AiConfig | None = None,
        client: ChatClient | None = None,
        root: Any = None,
        config_root: Any = None,
    ) -> None:
        self.code = checkpoint.normalize_code(code)
        self.config = config or fallback.load_config(config_root)
        self.client: ChatClient = client or HttpChatClient(self.config)
        self.root = root

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #

    def run(
        self,
        day: date | str,
        *,
        facts: dict[str, dict[str, Any]] | None = None,
        force: bool = False,
        retry_failed: bool = False,
        only: str | None = None,
    ) -> ResearchRunResult:
        day_text = str(day)
        facts_by_stage = dict(facts or {})
        document = checkpoint.load(self.code, day_text, self.root)
        targets = checkpoint.targets(
            document, force=force, retry_failed=retry_failed, only=only
        )

        for stage in targets:
            outcome = self._generate(stage, day_text, facts_by_stage.get(stage, {}))
            if outcome["status"] != "failed":
                outcome["artifact"] = artifacts.write_stage(
                    artifacts.build_document(
                        code=self.code,
                        day=day_text,
                        stage=stage,
                        status=outcome["status"],
                        mode=outcome["mode"],
                        model=outcome.get("model"),
                        degraded=outcome["degraded"],
                        narrative=outcome["narrative"],
                        notes=outcome["notes"],
                        facts=outcome["facts"],
                        ai=outcome["ai"],
                        failure=outcome.get("failure"),
                        attempts=outcome.get("attempts"),
                    ),
                    self.root,
                )
            checkpoint.record(
                document,
                stage,
                status=outcome["status"],
                mode=outcome["mode"],
                narrative=outcome["narrative"],
                model=outcome.get("model"),
                degraded=outcome["degraded"],
                notes=outcome["notes"],
                failure=outcome.get("failure"),
                attempts=outcome.get("attempts"),
            )
            checkpoint.save(document, self.root)

        return self._result(day_text, document, targets, facts_by_stage)

    # ------------------------------------------------------------------ #
    # 单段生成
    # ------------------------------------------------------------------ #

    def _generate(self, stage: str, day: str, facts: dict[str, Any]) -> dict[str, Any]:
        if stage == checkpoint.REPORT_STAGE:
            inputs, missing = self._report_inputs(day)
            if missing:
                hint = "、".join(f"--only {item}" for item in missing)
                failure = AiFailure(
                    kind="internal",
                    message="报告段缺少前五段产物：" + "、".join(missing),
                    action=f"先补齐缺失段（ldst research {self.code} {hint}），再重跑报告段",
                    retryable=True,
                    detail="report 段只消费前五段已落盘的结构化产物，不重新取数",
                )
                return _failed(
                    failure,
                    notes=[f"报告段缺少输入：{'、'.join(missing)}"],
                    facts={},
                )
            facts = {"upstream": inputs}

        if not fallback.is_configured(self.config):
            return _placeholder(
                stage,
                self.code,
                day,
                [
                    "未配置模型凭据（"
                    + "、".join(fallback.missing_env(self.config))
                    + "），本段用占位叙述落盘，仅用于验证顺序执行与断点续跑"
                    "（R1-2 起替换为实际叙述）。"
                ],
                facts,
            )

        models = fallback.model_chain(self.config)
        model = models[0] if models else None
        system, user = self._messages(stage, day, facts)
        attempts: list[dict[str, Any]] = []
        try:
            # 骨架阶段只用链首模型、单次调用：格式重试与「网络/额度才换模型」的
            # fallback 阶梯属 R1-2（三级降级阶梯与 prompts/ 一起接），届时这里换成
            # 与 orchestrator 同一条 attempt 循环，attempts 逐模型登记。
            text = self.client.complete(model or "", system, user, json_mode=False)
        except Exception as exc:  # noqa: BLE001 - 分类后按段隔离，不影响前序成果
            failure = classify_error(exc)
            attempts.append({"model": model, "mode": "text", "kind": failure.kind})
            return _failed(
                failure,
                notes=[f"{checkpoint.STAGE_TITLES.get(stage, stage)}段生成失败，其他段不受影响"],
                facts=facts,
                attempts=attempts,
            )
        payload = structured_output.plain_payload(text)
        if payload is None:
            failure = classify_error(ValueError("模型返回空内容"))
            attempts.append({"model": model, "mode": "text", "kind": failure.kind})
            return _failed(failure, notes=["模型返回空内容"], facts=facts, attempts=attempts)
        attempts.append({"model": model, "mode": "text", "kind": None})
        narrative = [str(item) for item in payload["narrative"]]
        ai_payload = {"narrative": narrative, "degraded": False, "model": model}
        try:
            merged = merge_narrative(facts, ai_payload)
        except BoundaryViolation as exc:
            return _failed(
                classify_error(exc),
                notes=[str(exc)],
                facts=facts,
                attempts=attempts,
            )
        return {
            "status": "ok",
            # 结构化输出阶梯（JSON + Schema → 文本补偿 → 纯文本）由 R1-2 接入，
            # 骨架阶段只走纯文本一级，因此不标 degraded。
            "mode": "text",
            "model": model,
            "degraded": False,
            "narrative": narrative,
            "notes": [],
            "failure": None,
            "attempts": attempts,
            "facts": merged,
            "ai": merged["ai"],
        }

    def _messages(self, stage: str, day: str, facts: dict[str, Any]) -> tuple[str, str]:
        title = checkpoint.STAGE_TITLES.get(stage, stage)
        system = (
            "你是 A 股个股研究助手。硬约束：\n"
            f"1. 只写「{title}」这一段的叙述，不写其他段；\n"
            "2. 不得输出或改写任何指标数字——数字由程序计算，你只能引用给定快照；\n"
            "3. 不联网、不编造来源；快照缺什么就明说缺什么；\n"
            "4. 中文纯文本输出，按段落分行。"
        )
        user = (
            f"证券代码：{self.code}\n"
            f"研究日期：{day}\n"
            # 段落标识单独一行且带机器名：报告段的快照里含前五段标题，
            # 只按标题匹配会把「报告合成」误认成「公司画像」（假客户端最容易踩到）
            f"段落标识：{stage}\n"
            f"段落：{title}\n"
            "程序事实快照（只读）：\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}\n\n"
            "请基于快照写本段叙述。"
        )
        return system, user

    def _report_inputs(self, day: str) -> tuple[dict[str, Any], list[str]]:
        """报告段的唯一输入 = 前五段已落盘的结构化产物（禁止重新取数）。"""
        inputs: dict[str, Any] = {}
        missing: list[str] = []
        for stage in checkpoint.REPORT_INPUTS:
            document = artifacts.read_stage(self.code, day, stage, self.root)
            if document is None:
                missing.append(stage)
                continue
            inputs[stage] = {
                "title": document.get("title"),
                "status": document.get("status"),
                "narrative": document.get("narrative") or [],
                "provenance": document.get("provenance") or {},
            }
        return inputs, missing

    # ------------------------------------------------------------------ #
    # 结果
    # ------------------------------------------------------------------ #

    def _result(
        self,
        day: str,
        document: dict[str, Any],
        targets: list[str],
        facts_by_stage: dict[str, dict[str, Any]],
    ) -> ResearchRunResult:
        stages: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        models: list[str] = []
        degraded = False
        for name in STAGES:
            state = checkpoint.stage_state(document, name)
            status = str(state.get("status") or "pending")
            if status == "pending":
                continue
            model = state.get("model")
            if model and model not in models:
                models.append(str(model))
            if bool(state.get("degraded")):
                degraded = True
            stages.append(
                {
                    "name": name,
                    "title": checkpoint.STAGE_TITLES.get(name, name),
                    "status": status,
                    "mode": state.get("mode"),
                    "model": model,
                    "degraded": bool(state.get("degraded")),
                    "narrative": [str(item) for item in state.get("narrative") or []],
                    "notes": [str(note) for note in state.get("notes") or []],
                    "facts": dict(facts_by_stage.get(name, {})),
                }
            )
            if status == "failed":
                failure = state.get("failure") or {}
                failures.append(
                    {
                        "stage": name,
                        "kind": failure.get("kind", "internal"),
                        "message": failure.get("message", "研究段生成失败"),
                        "action": failure.get("action", "稍后重试"),
                        "retryable": bool(failure.get("retryable")),
                    }
                )
        failed = [item["stage"] for item in failures]
        generated = [name for name in targets if name not in failed]
        reused = [
            name for name in checkpoint.done_stages(document) if name not in generated
        ]
        return ResearchRunResult(
            code=self.code,
            date=day,
            stages=stages,
            research_failures=failures,
            models=models,
            degraded=degraded,
            failed=failed,
            generated=generated,
            reused=reused,
            checkpoint_path=str(checkpoint.checkpoint_path(self.code, day, self.root)),
            artifacts=artifacts.artifact_paths(self.code, day, self.root),
            configured=fallback.is_configured(self.config),
        )


def _placeholder_text(stage: str, code: str, day: str) -> str:
    title = checkpoint.STAGE_TITLES.get(stage, stage)
    return f"【骨架占位】{code} · {day} · {title}（stage={stage}）尚未接入取数与实际提示词。"


def _placeholder(
    stage: str, code: str, day: str, notes: list[str], facts: dict[str, Any]
) -> dict[str, Any]:
    """未配置模型时的占位叙述：跑通链路与契约，如实标 degraded。"""
    narrative = [_placeholder_text(stage, code, day)]
    ai_payload = {"narrative": narrative, "degraded": True}
    merged = merge_narrative(facts, ai_payload)
    return {
        "status": "degraded",
        "mode": "placeholder",
        "model": None,
        "degraded": True,
        "narrative": narrative,
        "notes": notes,
        "failure": None,
        "attempts": [],
        "facts": merged,
        "ai": merged["ai"],
    }


def _failed(
    failure: AiFailure,
    *,
    notes: list[str],
    facts: dict[str, Any],
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "mode": "failed",
        "model": None,
        "degraded": False,
        "narrative": [],
        "notes": notes,
        "failure": failure.as_dict(),
        "attempts": list(attempts or []),
        "facts": dict(facts),
        "ai": {"narrative": [], "degraded": False},
    }


__all__ = ["ResearchPipeline", "ResearchRunResult", "STAGES"]
