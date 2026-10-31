"""`ldst industry` 命令体：申万一级行业五维画像（R5，QUOT-143）。

实现与注册分离：`cli/main.py` 只 import + 一行 `register_industry(industry_app)`。

契约（docs/CLI.md §D‴）：

- `industry list`：清单 + 每个行业的可用性摘要（配置本身永远可读，指标缺就显式 null）；
- `industry show <行业>`：单行业五维快照（景气 / 资金 / 估值 / 动量 / 拥挤度）；
- `show` 的输出外层是 `LadderEnvelope`，与 `ldst pool` 同一条装配路径；
- **只读本机缓存**，零外呼；核心指标 `available=false` → stdout 仍给完整结构化结果，
  退出码 **6**（DEC-012）；行业名不在清单里同样 6，并给出候选名称；
- `config/industry.yaml` 读不动 → 3（`EXIT_CONFIG`），不静默兜底成空清单。
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.review import _metric_envelope
from ladderstock.core import ladder as ladder_module
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_OK, LadderStockError
from ladderstock.industry import snapshot as snapshot_module

#: 信封 `data_as_of` 的主依据数据集（常量在 `industry/snapshot.py`，与 MCP 出口同一份）
META_PRIMARY_DATASET = snapshot_module.META_PRIMARY_DATASET


def _emit_snapshot(payload: dict[str, Any], output: OutputOption) -> None:
    """装配信封、输出、按 DEC-012 决定退出码。

    `cache_meta()` 的 `day` 决定它去哪一天取时点；行业快照读的是全量历史，
    所以传「归属数据自己的最新日期」，读不到缓存时才回落今天（那时点本来就不存在）。
    """
    as_of = payload["datasets"][META_PRIMARY_DATASET]["as_of"]
    day = str(as_of) if as_of else date_type.today().isoformat()
    meta = ladder_module.cache_meta(list(snapshot_module.SNAPSHOT_DATASETS), day)
    emit(
        _metric_envelope(
            domain="sentiment",
            topic="industry_snapshot",
            payload=payload,
            meta=meta,
        ),
        output,
    )
    if not payload.get("available"):
        error(f"行业指标不可用：{payload.get('reason')}")
        raise typer.Exit(EXIT_NOT_FOUND)
    raise typer.Exit(EXIT_OK)


def industry_list(output: OutputOption = None) -> None:
    """行业清单：申万一级（`config/industry.yaml` 可裁剪）+ 每个行业的缓存可用性。"""
    try:
        result = snapshot_module.industry_list()
    except LadderStockError as exc:
        error(exc.message)
        raise typer.Exit(exc.code) from exc
    # 清单是配置视图，不是指标结果：即使一个行业都没数也照常 0 退出
    emit(result, output)
    raise typer.Exit(EXIT_OK)


def industry_show(
    industry: Annotated[str, typer.Argument(help="申万一级行业名（见 `ldst industry list`）")],
    output: OutputOption = None,
) -> None:
    """单行业五维快照：景气 / 资金 / 估值 / 动量 / 拥挤度。"""
    try:
        result = snapshot_module.industry_snapshot(industry)
    except LadderStockError as exc:
        error(exc.message)
        raise typer.Exit(exc.code) from exc
    _emit_snapshot(result, output)


def register(app: typer.Typer) -> None:
    """把 `industry list|show` 挂到给定的 typer 组上。"""
    app.command("list", help="行业清单与缓存可用性")(industry_list)
    app.command("show", help="单行业五维画像（景气/资金/估值/动量/拥挤度）")(industry_show)


__all__ = ["META_PRIMARY_DATASET", "industry_list", "industry_show", "register"]
