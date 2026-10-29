"""`ldst ai` 命令体：status / retry / chain test（M3 §6.3）。

- `ai status`：模型链、密钥是否配置（只看环境变量名与布尔值，**绝不回显密钥**），
  最近一次 checkpoint 的段落状态；未配置时明确提示「仅能纯文本降级」，不崩溃。
- `ai retry`：只重跑失败 / 未完成的段落，成功段落不重跑；`--section` 限定单段。
- `ai chain test`：按 `ai.yaml` 的模型链逐一发一次最小请求，报告每档健康度。

密钥只从 `ai.yaml` 指向的环境变量读取；本模块不打印任何凭据原文。
"""

from __future__ import annotations

import json
from datetime import date as date_type
from typing import Annotated, Any

import typer

from ladderstock.ai import checkpoint, fallback
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.orchestrator import HttpChatClient
from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.errors import (
    EXIT_AI_DEGRADED,
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_USAGE,
)
from ladderstock.review import pipeline

PROBE_SYSTEM = "你是一个连通性探测器，只回复 ok。"
PROBE_USER = "ping"


def _load_config() -> fallback.AiConfig:
    try:
        return fallback.load_config()
    except BoundaryViolation as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None


def _parse_date(value: str) -> str:
    try:
        date_type.fromisoformat(value)
    except ValueError:
        error(f"--date 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE) from None
    return value


# --------------------------------------------------------------------------- #
# ai status
# --------------------------------------------------------------------------- #

def ai_status(output: OutputOption = None) -> None:
    """模型链与最近调用状态（未配置时也能正常输出）。"""
    config = _load_config()
    document = fallback.status_document(config)
    document["recent_checkpoint"] = _recent_checkpoint()
    emit(document, output)
    raise typer.Exit(EXIT_OK)


def _recent_checkpoint() -> dict[str, Any] | None:
    """最近一份 checkpoint 的段落状态摘要（只读，不触发任何生成）。"""
    base = checkpoint.checkpoint_dir()
    files = sorted(base.glob("*.json"), reverse=True) if base.is_dir() else []
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        sections = document.get("sections") or {}
        if not isinstance(sections, dict):
            continue
        return {
            "date": document.get("date") or path.stem,
            "updated_at": document.get("updated_at"),
            "sections": {
                str(name): (state or {}).get("status")
                for name, state in sections.items()
                if isinstance(state, dict)
            },
            "path": str(path),
        }
    return None


# --------------------------------------------------------------------------- #
# ai retry
# --------------------------------------------------------------------------- #

def ai_retry(
    date: Annotated[str, typer.Option(help="交易日 YYYY-MM-DD")],
    section: Annotated[str | None, typer.Option(help="只重试指定段落")] = None,
    output: OutputOption = None,
) -> None:
    """重试指定日期失败段落（成功段落不重跑）。"""
    day = _parse_date(date)
    if section is not None and section not in checkpoint.SECTIONS:
        error(f"未知段落 {section}；可选：{', '.join(checkpoint.SECTIONS)}")
        raise typer.Exit(EXIT_USAGE)

    report, run = pipeline.generate(day, retry_failed=True, only=section)
    emit(
        {
            "date": day,
            "retried": run.generated,
            "reused": run.reused,
            "failed": run.failed,
            "degraded": run.degraded,
            "configured": run.configured,
            "report_path": str(pipeline.review_path(day)),
            "checkpoint": run.checkpoint_path,
            "sections": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "mode": item.get("mode"),
                    "model": item.get("model"),
                }
                for item in report.get("sections") or []
            ],
            "ai_failures": run.ai_failures,
            "exit_code": run.exit_code,
        },
        output,
    )
    raise typer.Exit(run.exit_code)


# --------------------------------------------------------------------------- #
# ai chain test
# --------------------------------------------------------------------------- #

def ai_chain_test(
    model: Annotated[str | None, typer.Option(help="只测指定模型")] = None,
    output: OutputOption = None,
) -> None:
    """按模型链逐档探测；未配置时明确报「未配置」并以 3 退出。"""
    config = _load_config()
    chain = fallback.model_chain(config)
    if model:
        if model not in chain:
            chain = [model, *chain]
        else:
            chain = [model]

    if not fallback.is_configured(config):
        missing = "、".join(fallback.missing_env(config))
        emit(
            {
                "configured": False,
                "missing_env": fallback.missing_env(config),
                "models": chain,
                "results": [],
                "hint": f"未配置 {missing}，无法探测；补齐环境变量后重试",
            },
            output,
        )
        raise typer.Exit(EXIT_CONFIG)

    client = HttpChatClient(config)
    results: list[dict[str, Any]] = []
    ok = 0
    for name in chain:
        try:
            text = client.complete(name, PROBE_SYSTEM, PROBE_USER, json_mode=False)
        except Exception as exc:  # noqa: BLE001 - 失败分类后统一上报
            failure = classify_error(exc)
            results.append(
                {
                    "model": name,
                    "ok": False,
                    "kind": failure.kind,
                    "message": failure.message,
                    "retryable": failure.retryable,
                }
            )
            continue
        ok += 1
        results.append({"model": name, "ok": True, "chars": len(text)})

    emit(
        {
            "configured": True,
            "models": chain,
            "ok": ok,
            "failed": len(chain) - ok,
            "results": results,
        },
        output,
    )
    raise typer.Exit(EXIT_OK if ok == len(chain) else EXIT_AI_DEGRADED)


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

def register(ai_app: typer.Typer, chain_app: typer.Typer) -> None:
    """把 AI 子命令挂到 main.py 已建好的 Typer 组上。"""
    ai_app.command("status", help="模型链与最近调用状态")(ai_status)
    ai_app.command("retry", help="重试指定日期失败段落（成功段落不重跑）")(ai_retry)
    chain_app.command("test", help="多后端兼容矩阵体检")(ai_chain_test)


__all__ = ["ai_chain_test", "ai_retry", "ai_status", "register"]
