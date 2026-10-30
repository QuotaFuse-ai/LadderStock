"""个股研究六段编排（R1-1 骨架）。

`company → financial → forecast → valuation → risk → report` **顺序执行**，
每段完成即写产物 + 落 checkpoint（复用 `ai/checkpoint.py` 的状态机与原子写），
中断后重跑只补缺失段；单段失败不吞前序成果，整条管线以退出码 9 收尾。

本段的边界：

- **不联网取数**（联网检索属 R4，需人类批准）：`facts` 由调用方注入，默认空；
  六段（R1-2 前三段 + R1-3 后三段）都已接各自提示词与同一个三级降级阶梯；
- `report` 段只消费前五段已落盘的结构化产物，不重新取数；
- AI 只能追加叙述：产物写盘前过 `core/boundary.merge_narrative` 闸门，
  模型试图输出指标键即判该段 `failed`（DEC-006 闸门 5）；
- R2 起，结构化一级可以带**本段白名单内的受控工具**（`ai/tools.py` +
  `stages.ALLOWED_TOOLS`）：模型只能用列出来的那几个，越权调用第一次即判该段
  `failed`，全过程落 `tool_calls.jsonl`；客户端不支持 tool-calling 时走原路径；
- `ChatClient` 可注入假实现，验收不依赖真实模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ladderstock.ai import fallback, tools
from ladderstock.ai.error_taxonomy import AiFailure, classify_error
from ladderstock.ai.orchestrator import ChatClient, HttpChatClient, supports_tools
from ladderstock.core.boundary import BoundaryViolation, merge_narrative
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK
from ladderstock.research import artifacts, checkpoint, ladder, stages

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
                    "（配好凭据后由本段提示词 + 三级降级阶梯生成实际叙述）。"
                ],
                facts,
            )

        return self._laddered(stage, day, facts)

    def _laddered(self, stage: str, day: str, facts: dict[str, Any]) -> dict[str, Any]:
        """本段提示词 + 三级降级阶梯（`research/ladder.py`），产物结构与骨架一致。

        R2：客户端支持 tool-calling 时，结构化一级带上本段白名单里的受控工具；
        客户端不支持（五段复盘、既有假客户端）时，消息与调用序列与今天一致。
        """
        names = stages.allowed_tools(stage)
        use_tools = bool(names) and supports_tools(self.client)
        ctx = self._tool_context(stage, day, names) if use_tools else None
        tool_names = names if use_tools else None
        outcome = ladder.generate(
            self.client,
            self.config,
            stage,
            messages=stages.build_messages(
                self.code, stage, day, facts, tool_names=tool_names
            ),
            plain=stages.plain_messages(self.code, stage, day, facts),
            ctx=ctx,
        )
        notes = list(outcome["notes"])
        if ctx is not None and ctx.calls:
            notes.append(_tool_note(ctx))
        if outcome["status"] == "failed":
            title = checkpoint.STAGE_TITLES.get(stage, stage)
            return _failed(
                outcome["failure"],
                notes=[f"{title}段生成失败，其他段不受影响", *notes],
                facts=facts,
                attempts=outcome["attempts"],
            )
        ai_payload = {"narrative": outcome["narrative"], **outcome["extras"]}
        ai_payload["degraded"] = outcome["degraded"]
        if outcome["model"]:
            ai_payload["model"] = outcome["model"]
        try:
            merged = merge_narrative(facts, ai_payload)
        except BoundaryViolation as exc:
            return _failed(
                classify_error(exc),
                notes=[str(exc), *notes],
                facts=facts,
                attempts=outcome["attempts"],
            )
        return {
            "status": outcome["status"],
            "mode": outcome["mode"],
            "model": outcome["model"],
            "degraded": outcome["degraded"],
            "narrative": outcome["narrative"],
            "notes": notes,
            "failure": None,
            "attempts": outcome["attempts"],
            "facts": merged,
            "ai": merged["ai"],
        }

    def _tool_context(self, stage: str, day: str, names: tuple[str, ...]) -> tools.ToolContext:
        """本段的工具视野：只交出本次研究的落盘目录，别的根目录一概没有。"""
        run_dir = artifacts.artifact_dir(self.code, day, self.root)
        # 第一段跑起来时目录还不存在，而 `list_artifacts` 与审计日志都要有地方落
        run_dir.mkdir(parents=True, exist_ok=True)
        return tools.ToolContext(
            stage=stage,
            roots={tools.RUN_ROOT: run_dir},
            allowed=frozenset(names),
        )

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


def _tool_note(ctx: tools.ToolContext) -> str:
    """把本段的工具足迹写进 `notes`（产物的 15 键契约不变，DEC-021 §3）。"""
    counts: dict[str, int] = {}
    rejected = 0
    for call in ctx.calls:
        name = str(call.get("tool") or "?")
        counts[name] = counts.get(name, 0) + 1
        rejected += 1 if call.get("rejected") else 0
    detail = "、".join(f"{name}×{times}" for name, times in sorted(counts.items()))
    return (
        f"本段调用受控工具 {ctx.call_count} 次（{detail}，被拒 {rejected} 次），"
        "逐条审计见同目录 tool_calls.jsonl"
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
