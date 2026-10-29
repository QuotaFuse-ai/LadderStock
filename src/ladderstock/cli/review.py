"""`ldst review` / `validate` / `sentiment show` / `ladder show` 命令体（M3）。

main.py 只 import + 一行 `register(...)`；实现全部在这里。

契约（docs/CLI.md §A）：

| 命令 | 退出码 |
|---|---|
| `review --date` | 正常 0；AI 降级完成 9 |
| `review show --date` | 无结果 6 |
| `validate --date` | 无结果 6 |
| `validate reconcile --date` | 正常 0；部分数据缺失 8 |
| `sentiment show` / `ladder show` | 程序直出，0；数据不可用 6 |

所有读操作只读本地缓存/本地 JSON，不发起任何网络请求。
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core import validation as validation_module
from ladderstock.core.models import LadderEnvelope
from ladderstock.errors import (
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
)
from ladderstock.review import pipeline

# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def parse_date(value: str) -> str:
    """`--date` 必须是 YYYY-MM-DD；非法直接 2（参数错误）。"""
    try:
        date_type.fromisoformat(value)
    except ValueError:
        error(f"--date 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE) from None
    return value


def _metric_envelope(
    *,
    domain: str,
    topic: str,
    payload: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """给核心指标输出套 `LadderEnvelope`（METRICS.md §7，D-02）。

    溯源三件套 `data_as_of` / `cached_at` / `backend` 取 `CacheSnapshot` 的时点信息；
    `cached_at` / `backend` 不属于生态 envelope 契约字段（字段集由
    `tests/contract/test_ladder_envelope.py` 锁死），`data_as_of` 在契约里是必填
    `date`。缓存读不到时三者一律如实给 `null`——既不省略键，也不拿请求日期顶替，
    因此这里用 `model_construct`（值全部由本地代码产出，无需再校验）直接序列化。
    """
    envelope = LadderEnvelope.model_construct(
        domain=domain,
        topic=topic,
        event_id=None,
        data_as_of=meta.get("data_as_of"),
        payload=payload,
        source="ladderstock",
        source_detail=str(payload.get("source_detail") or ""),
        fetched_at=datetime.now(UTC),
        freshness="cached",
        confidence="high" if payload.get("available") else "low",
        source_status="ok" if payload.get("available") else "partial",
    )
    output = envelope.model_dump(mode="json")
    cached_at = meta.get("cached_at")
    output["cached_at"] = cached_at.isoformat() if isinstance(cached_at, datetime) else None
    output["backend"] = meta.get("backend")
    return output


# --------------------------------------------------------------------------- #
# review
# --------------------------------------------------------------------------- #

def review(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    force: Annotated[bool, typer.Option(help="跳过缓存重新生成")] = False,
    output: OutputOption = None,
) -> None:
    """生成指定日期的复盘（指标直出 + 五段 AI 叙述）。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    report, run = pipeline.generate(day, force=force)
    emit(
        {
            "date": day,
            "ok": not run.failed,
            "configured": run.configured,
            "degraded": run.degraded,
            "failed": run.failed,
            "models": run.models,
            "report_path": str(pipeline.review_path(day)),
            "mirror_path": str(pipeline.mirror_path(day)),
            "validation_path": str(validation_module.validation_path(day)),
            "checkpoint": run.checkpoint_path,
            "sections": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "mode": item.get("mode"),
                    "model": item.get("model"),
                    "degraded": bool(item.get("degraded")),
                }
                for item in report.get("sections") or []
            ],
            "ai_failures": run.ai_failures,
            "exit_code": run.exit_code,
        },
        output,
    )
    raise typer.Exit(run.exit_code)


def review_show(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    output: OutputOption = None,
) -> None:
    """只读取已生成的复盘，不重算。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    report = pipeline.load_report(day)
    if report is None:
        error(
            f"没有 {day} 的复盘：{pipeline.review_path(day)}"
            f"（先运行 ldst review --date {day}）"
        )
        raise typer.Exit(EXIT_NOT_FOUND)
    emit(report, output)
    raise typer.Exit(EXIT_OK)


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

def validate(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    output: OutputOption = None,
) -> None:
    """查看当日复盘生成的明日验证条件与已有对账结果。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    document = validation_module.load_validation(day)
    if document is None:
        error(
            f"没有 {day} 的验证条件：{validation_module.validation_path(day)}"
            f"（先运行 ldst review --date {day}）"
        )
        raise typer.Exit(EXIT_NOT_FOUND)

    conditions = [item for item in document.get("conditions") or [] if isinstance(item, dict)]
    if not conditions:
        error(f"{day} 的验证文档里没有可用条件")
        raise typer.Exit(EXIT_NOT_FOUND)

    emit(
        {
            "date": str(document.get("date") or day),
            "generated_at": document.get("generated_at"),
            "conditions": conditions,
            "reconcile": document.get("reconcile"),
            "source": str(validation_module.validation_path(day)),
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def validate_reconcile(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    output: OutputOption = None,
) -> None:
    """拿下一交易日实际指标逐条判定当日条件；缺数据时 8。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    try:
        result = validation_module.reconcile(day)
    except FileNotFoundError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_NOT_FOUND) from None

    emit(result, output)
    raise typer.Exit(EXIT_PARTIAL if result.get("status") == "partial" else EXIT_OK)


# --------------------------------------------------------------------------- #
# 核心指标
# --------------------------------------------------------------------------- #

def sentiment_show(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    output: OutputOption = None,
) -> None:
    """情绪温度、晋级率、封板率与梯队断层（程序直出，不含 AI 文本）。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    snapshot = sentiment_module.compute_sentiment(day)
    meta = ladder_module.cache_meta(
        ["limit_pool", "broken_board", "yesterday_limit_up"], day
    )
    emit(
        _metric_envelope(
            domain="sentiment",
            topic="shortline_sentiment",
            payload=snapshot,
            meta=meta,
        ),
        output,
    )
    if not snapshot.get("available"):
        # D-03：缺数据不再是 0——DEC-003 里 6 = 无结果，脚本据此可判失败
        error(f"{day} 情绪指标不可用：{snapshot.get('reason')}")
        raise typer.Exit(EXIT_NOT_FOUND)
    raise typer.Exit(EXIT_OK)


def ladder_show(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD")] = None,
    output: OutputOption = None,
) -> None:
    """连板天梯、梯队高度与断层（程序直出）。"""
    if ctx.invoked_subcommand:
        return
    if not date:
        error("缺少 --date")
        raise typer.Exit(EXIT_USAGE)
    day = parse_date(date)

    ladder = ladder_module.compute_ladder(day)
    meta = ladder_module.cache_meta(["limit_pool"], day)
    emit(
        _metric_envelope(
            domain="sentiment",
            topic="shortline_ladder",
            payload=ladder,
            meta=meta,
        ),
        output,
    )
    if not ladder.get("available"):
        error(f"{day} 连板天梯不可用：{ladder.get('reason')}")
        raise typer.Exit(EXIT_NOT_FOUND)
    raise typer.Exit(EXIT_OK)


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

def register(
    review_app: typer.Typer,
    validate_app: typer.Typer,
    sentiment_app: typer.Typer,
    ladder_app: typer.Typer,
) -> None:
    """把复盘 / 验证 / 指标命令挂到 main.py 已建好的 Typer 组上。"""
    review_app.callback()(review)
    review_app.command("show", help="只读取已生成复盘，不重算")(review_show)

    validate_app.callback()(validate)
    validate_app.command("reconcile", help="触发收盘对账（15:00 后）")(validate_reconcile)

    sentiment_app.command("show", help="情绪温度、晋级率、梯队断层（程序直出）")(sentiment_show)
    ladder_app.command("show", help="连板天梯与梯队断层")(ladder_show)


__all__ = [
    "ladder_show",
    "parse_date",
    "register",
    "review",
    "review_show",
    "sentiment_show",
    "validate",
    "validate_reconcile",
]
