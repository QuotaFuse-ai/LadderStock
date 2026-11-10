"""`ldst backfill`：A 股日频历史行情回补（QUOT-501）。

职责只有三件事，按顺序完成：

1. **基准先落库**：`index_daily` 取上证指数（`sh000001`）2000 根日线，既是基准，
   也是缺口报表的「预期交易日历」——指数每个交易日都有 bar，不依赖第三方日历。
2. **票池逐只回补**：按 `config/backfill.yaml` 的票池逐只取 `daily_k`（`period=daily`、
   `adjust=qfq`），单只失败不中断整批，连续失败到 `max_consecutive_failures` 才止损；
   所有行合并成**一个事务**写库，append 模式按 `(code, trade_date)` upsert。
3. **缺口报表**：写 `<data_dir>/backfill-reports/<source>-<时间戳>/gap-report.{md,csv}`，
   内容含每只票的首末日 / 缺口率 / 覆盖年数 / amount 缺失率、缺口率超阈值的单独列名
   与解释、失败清单，以及 `daily_k` 总量前后差（重跑去重证据）。

阶段切换只改 `config/backfill.yaml` 的 `source` 与 `stage`：

- `tencent`：现有 `tickhub_kline`（腾讯 qfq，单票上限约 641 根 ≈2.5 年）；
- `eastmoney`：阶段2 的 `tickhub_em_kline` / `tickhub_em_index`（东财 push2his
  `fqt=1`，2000 根）。source id 未登记时直接按配置错误退出，不会悄悄退回腾讯；
- `config`：完全按 `config/sync.yaml`，不做覆盖。

退出码：全部取数成功 0；有失败/空结果/止损未发起 8；基准取不到或一只都没取到 4；
配置错误 3；历史库被别的进程占着 10。**「覆盖率不足 5 年」不改变退出码**——那是
阶段事实，写进报表，不由退出码伪装成成功或失败。
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from jsonschema import Draft202012Validator

from ladderstock.cli.output import OutputOption, emit
from ladderstock.cli.paths import config_dir, data_dir
from ladderstock.cli.sync import clean_codes, envelope_records, result_source
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.data import backfill as gap
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.adapters.tickhub import tool_params
from ladderstock.data.db_lock import writer_lock
from ladderstock.data.provider_registry import (
    ProviderRegistry,
    load_sources_config,
    load_sync_config,
)
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_SOURCE,
    EXIT_USAGE,
    LadderStockError,
)

MODE = "backfill"

#: 阶段 → (dataset, primary, fallback)。`config` 不在表里：它表示「不覆盖」。
SOURCE_CHAINS: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "tencent": {
        "daily_k": ("tickhub_kline", ("tickhub_tdx_package",)),
        "index_daily": ("tickhub_index_daily", ()),
    },
    "eastmoney": {
        "daily_k": ("tickhub_em_kline", ()),
        "index_daily": ("tickhub_em_index", ()),
    },
}


def _log(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

def load_backfill_config(path: Path | None = None) -> dict[str, Any]:
    """读 `config/backfill.yaml` 并按同目录 schema 校验。"""
    target = Path(path) if path is not None else config_dir() / "backfill.yaml"
    if not target.is_file():
        raise LadderStockError(f"回补配置不存在：{target}", EXIT_CONFIG)
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LadderStockError(f"回补配置 YAML 解析失败：{target}（{exc}）", EXIT_CONFIG) from exc
    schema_path = target.parent / "schema" / "backfill.schema.json"
    if schema_path.is_file():
        import json

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema).iter_errors(data), key=lambda item: list(item.path)
        )
        if errors:
            detail = "；".join(
                f"/{'/'.join(map(str, item.path))}: {item.message}" for item in errors
            )
            raise LadderStockError(f"回补配置校验失败：{detail}", EXIT_CONFIG)
    block = data.get("backfill")
    if not isinstance(block, dict):
        raise LadderStockError("回补配置缺 backfill 段", EXIT_CONFIG)
    return block


def apply_source_policy(sync_doc: dict[str, Any], source: str) -> dict[str, Any]:
    """按阶段覆盖 `daily_k` / `index_daily` 的链路；其余数据集原样保留。"""
    if source == "config":
        return sync_doc
    chains = SOURCE_CHAINS.get(source)
    if chains is None:
        raise LadderStockError(
            f"未知回补源 {source!r}；可选：{', '.join(['config', *SOURCE_CHAINS])}", EXIT_USAGE
        )
    doc = copy.deepcopy(sync_doc)
    datasets = doc.setdefault("sync", {}).setdefault("datasets", {})
    for dataset, (primary, fallback) in chains.items():
        entry = datasets.setdefault(
            dataset, {"watermark_field": "trade_date", "mode": "append"}
        )
        entry["primary"] = primary
        entry["fallback"] = list(fallback)
        entry.setdefault("watermark_field", "trade_date")
        entry.setdefault("mode", "append")
    return doc


def ensure_sources_registered(source: str) -> None:
    """阶段源必须已在 sources.yaml 登记；缺失时给出阶段2的明确动作，不静默降级。"""
    if source == "config":
        return
    declared = {
        str(item.get("id") or "")
        for item in (load_sources_config().get("sources") or [])
        if isinstance(item, dict)
    }
    missing: list[str] = []
    for primary, fallback in SOURCE_CHAINS[source].values():
        for sid in (primary, *fallback):
            if sid not in declared and sid not in missing:
                missing.append(sid)
    if missing:
        raise LadderStockError(
            f"回补源 {source} 需要的 source 未登记：{', '.join(missing)}；"
            "阶段2 先在 TickHub-CN 接入对应工具并在 config/sources.yaml 登记，"
            "再切 config/backfill.yaml 的 source（不会自动退回腾讯源）",
            EXIT_CONFIG,
        )


def _resolve_universe(
    cfg: dict[str, Any], codes: list[str] | None
) -> list[dict[str, Any]]:
    configured = [dict(item) for item in cfg["universe"]]
    if not codes:
        return configured
    known = {item["code"]: item for item in configured}
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise LadderStockError(
            f"票池里没有这些代码：{', '.join(unknown)}；请先在 config/backfill.yaml 登记",
            EXIT_USAGE,
        )
    return [known[code] for code in codes]


def _bench_rows(root: Path | None, code: str) -> list[dict[str, Any]]:
    """库里的基准行：上游会把 `sh000001` 归一成 `000001`，两种拼写都要认。"""
    wanted = {code.strip().upper()}
    bare = code.strip().lower()
    for prefix in ("sh", "sz"):
        if bare.startswith(prefix):
            wanted.add(bare[len(prefix):].upper())
    return [
        row
        for row in duckdb_store.query_dataset("index_daily", root=root)
        if str(row.get("code") or "").strip().upper() in wanted
    ]


def _report_base(cfg: dict[str, Any], root: Path | None) -> Path:
    configured = Path(str(cfg["report_dir"]))
    if configured.is_absolute():
        return configured
    return (Path(root) if root is not None else data_dir()) / configured


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #

def _failure_payload(*, stage: str, source: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "exit_code": EXIT_SOURCE,
        "stage": stage,
        "source": source,
        "error": message,
        "reports": {},
    }


def _validate_chains(registry: ProviderRegistry) -> None:
    for dataset in ("daily_k", "index_daily"):
        try:
            registry.chain(dataset)
        except BoundaryViolation as exc:
            raise LadderStockError(f"回补链路越界：{exc}", EXIT_CONFIG) from exc
        except LadderStockError as exc:
            raise LadderStockError(
                f"回补链路不可用（{dataset}）：{exc}；"
                "阶段2 需先在 TickHub-CN 接入 em_kline 并在 config/sources.yaml 登记",
                EXIT_CONFIG,
            ) from exc


def run_backfill(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    cfg: dict[str, Any],
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    report_base: Path | None = None,
    stage: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """执行一次回补并产出报表；返回可直接 `emit` 的载荷（含 exit_code）。"""
    universe = _resolve_universe(cfg, codes)
    window_start = start or str(cfg["start"])
    window_end = end if end else (str(cfg.get("end") or "") or None)
    stage_text = stage or str(cfg["stage"])
    source_name = source or str(cfg["source"])
    target_years = float(cfg["target_years"])
    gap_threshold = float(cfg["gap_rate_threshold"])
    max_consecutive = int(cfg["max_consecutive_failures"])
    root = registry.root
    started = _now_iso()
    _validate_chains(registry)

    # ---- 1. 基准：先落 index_daily，同时作为预期交易日历 ----
    bench = cfg["benchmark"]
    bench_code = str(bench["code"])
    bench_params = dict(tool_params("index_daily", code=bench_code))
    bench_params["days"] = int(bench.get("days") or 800)
    _log(f"backfill 基准 {bench_code}（days={bench_params['days']}，source={source_name}）")
    index_before = duckdb_store.dataset_rows("index_daily", root=root)
    try:
        outcome = registry.fetch(
            "index_daily", params=bench_params, allow_cache=False, ignore_cooldown=True
        )
    except LadderStockError as exc:
        message = f"基准 {bench_code} 取不到：{_one_line(exc)}"
        _log(message)
        return _failure_payload(stage=stage_text, source=source_name, message=message)
    bench_records = envelope_records(outcome.envelope)
    if not bench_records:
        message = f"基准 {bench_code} 返回 0 行，无法计算预期交易日"
        _log(message)
        return _failure_payload(stage=stage_text, source=source_name, message=message)

    index_result = duckdb_store.write_dataset(
        "index_daily", bench_records, mode="append", source=outcome.source
    )
    bench_last = max(gap.trade_days(bench_records))
    index_wm_before = store.value("index_daily")
    index_entry = store.advance(
        "index_daily", bench_last, rows=index_result.rows, source=outcome.source, status="ok"
    )
    store.record_run(
        "index_daily",
        mode=MODE,
        status="ok",
        rows=index_result.rows,
        source=outcome.source,
        started_at=started,
        watermark_before=index_wm_before,
        watermark_after=index_entry.value,
        detail=f"回补基准 {bench_code}：{index_result.rows} 行（窗口外历史一并保留）",
    )
    index_dedupe = {
        "before_rows": index_before,
        "written": index_result.rows,
        "after_rows": index_result.total_rows,
        "new_rows": index_result.total_rows - index_before,
        "existing_rows": index_result.rows - (index_result.total_rows - index_before),
        "watermark_before": index_wm_before,
        "watermark_after": index_entry.value,
        "source": outcome.source,
    }

    bench_rows = _bench_rows(root, bench_code)
    expected = gap.clip_days(gap.trade_days(bench_rows), window_start, window_end)
    if not expected:
        message = (
            f"基准 {bench_code} 在窗口 {window_start} ~ {window_end or '最新'} 没有 bar，"
            "无法计算预期交易日"
        )
        _log(message)
        return _failure_payload(stage=stage_text, source=source_name, message=message)
    window_end_resolved = window_end or expected[-1]
    _log(
        f"backfill 窗口 {window_start} ~ {window_end_resolved}，"
        f"预期交易日 {len(expected)} 个，票池 {len(universe)} 只"
    )

    # ---- 2. 票池逐只回补（单只失败不中断整批）----
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    failures: list[str] = []
    not_attempted: list[str] = []
    empty: list[str] = []
    succeeded: list[str] = []
    consecutive = 0

    for position, item in enumerate(universe):
        if consecutive >= max_consecutive:
            not_attempted = [rest["code"] for rest in universe[position:]]
            _log(
                f"  连续 {max_consecutive} 只失败，止损；剩余 {len(not_attempted)} 只未发起调用"
            )
            break
        code = item["code"]
        try:
            fetched = registry.fetch(
                "daily_k",
                params=tool_params("daily_k", code=code, since=window_start),
                allow_cache=False,
                ignore_cooldown=True,
            )
        except LadderStockError as exc:
            consecutive += 1
            failures.append(f"{code}：{_one_line(exc)}")
            _log(f"  {code} 失败：{_one_line(exc)}")
            continue
        consecutive = 0
        records = envelope_records(fetched.envelope)
        if not records:
            empty.append(code)
            _log(f"  {code} 空结果")
            continue
        rows.extend(records)
        sources.append(fetched.source)
        succeeded.append(code)
        _log(f"  {code} {len(records)} 行（{fetched.source}）")

    # ---- 3. 一个事务落库 + 水位 ----
    before_rows = duckdb_store.dataset_rows("daily_k", root=root)
    daily_wm_before = store.value("daily_k")
    written = 0
    after_rows = before_rows
    status_wm = "ok" if not (failures or empty or not_attempted) else "degraded"
    if rows:
        result = duckdb_store.write_dataset(
            "daily_k", rows, mode="append", source=result_source(sources)
        )
        written = result.rows
        after_rows = result.total_rows
        last_day = max(gap.trade_days(rows))
        entry = store.advance(
            "daily_k",
            last_day,
            rows=written,
            source=result_source(sources),
            status=status_wm,
        )
        daily_wm_after = entry.value
        store.record_run(
            "daily_k",
            mode=MODE,
            status=status_wm,
            rows=written,
            source=result_source(sources),
            started_at=started,
            watermark_before=daily_wm_before,
            watermark_after=daily_wm_after,
            detail=(
                f"回补窗口 {window_start} ~ {window_end_resolved}；"
                f"票池 {len(universe)} 只，成功 {len(succeeded)}、空 {len(empty)}、"
                f"失败 {len(failures)}、止损未发起 {len(not_attempted)}"
            ),
        )
    else:
        daily_wm_after = daily_wm_before

    dedupe = {
        "before_rows": before_rows,
        "written": written,
        "after_rows": after_rows,
        "new_rows": after_rows - before_rows,
        "existing_rows": written - (after_rows - before_rows),
        "watermark_before": daily_wm_before,
        "watermark_after": daily_wm_after,
        "source": result_source(sources) if rows else "-",
    }
    _log(
        f"daily_k 落库：提交 {written} 行，总行数 {before_rows} → {after_rows}"
        f"（新增 {dedupe['new_rows']}，重复 {dedupe['existing_rows']}）"
    )

    # ---- 4. 从库里读回统计（真机落库口径，不用内存里的返回）----
    failure_text = {item.split("：", 1)[0]: item for item in failures}
    code_rows: list[dict[str, Any]] = []
    for item in universe:
        code = item["code"]
        if code in failure_text:
            status, note = "failed", failure_text[code]
        elif code in not_attempted:
            status, note = "not_attempted", "连续失败止损后未发起调用"
        elif code in empty:
            status, note = "empty", "上游返回 0 行"
        else:
            status, note = "ok", ""
        stats = gap.code_stats(
            code=code,
            name=str(item.get("name") or ""),
            board=str(item.get("board") or ""),
            rows=duckdb_store.query_dataset("daily_k", code=code, root=root),
            expected=expected,
            start=window_start,
            end=window_end_resolved,
            target_years=target_years,
            gap_threshold=gap_threshold,
        )
        stats.update(status=status, error=note)
        code_rows.append(stats)

    benchmark_stats = gap.code_stats(
        code=bench_code,
        name=str(bench.get("name") or ""),
        board="指数",
        rows=bench_rows,
        expected=expected,
        start=window_start,
        end=window_end_resolved,
        target_years=target_years,
        gap_threshold=gap_threshold,
    )

    generated_at = _now_iso()
    out_dir = (
        Path(report_base) if report_base is not None else _report_base(cfg, root)
    ) / f"{source_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    report = gap.build_report(
        stage=stage_text,
        source=source_name,
        source_detail=outcome.envelope.source_detail,
        start=window_start,
        end=window_end,
        target_years=target_years,
        gap_threshold=gap_threshold,
        expected_days=len(expected),
        window_end=window_end_resolved,
        benchmark=benchmark_stats,
        codes=code_rows,
        failures=failures,
        not_attempted=not_attempted,
        dedupe=dedupe,
        index_dedupe=index_dedupe,
        generated_at=generated_at,
        report_dir=str(out_dir),
    )
    paths = gap.write_reports(report, out_dir)
    _log(f"缺口报表：{paths['markdown']}；{paths['csv']}")

    if not rows:
        exit_code = EXIT_SOURCE
    elif failures or empty or not_attempted:
        exit_code = EXIT_PARTIAL
    else:
        exit_code = EXIT_OK

    return {
        "ok": exit_code == EXIT_OK,
        "exit_code": exit_code,
        "stage": stage_text,
        "source": source_name,
        "source_detail": outcome.envelope.source_detail,
        "window": report["window"],
        "summary": report["summary"],
        "daily_k": dedupe,
        "index_daily": index_dedupe,
        "codes": code_rows,
        "failures": failures,
        "not_attempted": not_attempted,
        "reports": paths,
    }


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #

def backfill_command(
    codes: Annotated[
        list[str] | None,
        typer.Option("--codes", help="只回补票池里的这些代码（逗号分隔或重复传）"),
    ] = None,
    start: Annotated[str | None, typer.Option("--start", help="窗口起点 YYYY-MM-DD")] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="窗口终点 YYYY-MM-DD，缺省取基准最新日")
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", help="回补源：config / tencent / eastmoney（缺省取 backfill.yaml）"
        ),
    ] = None,
    report_dir: Annotated[
        str | None, typer.Option("--report-dir", help="报表输出目录（缺省取 backfill.yaml）")
    ] = None,
    output: OutputOption = None,
) -> None:
    cfg = load_backfill_config()
    chosen = source or str(cfg["source"])
    ensure_sources_registered(chosen)
    registry = ProviderRegistry(
        sync=apply_source_policy(load_sync_config(), chosen),
        peer_min_interval={"tickhub": max(0.0, int(cfg["throttle_ms"]) / 1000.0)},
    )
    store = watermark.WatermarkStore(root=registry.root)
    with writer_lock(holder="ldst backfill"):
        payload = run_backfill(
            registry,
            store,
            cfg,
            codes=clean_codes(codes),
            start=start,
            end=end,
            report_base=Path(report_dir) if report_dir else None,
            source=chosen,
        )
    emit(payload, output)
    raise typer.Exit(int(payload["exit_code"]))


def register(app: typer.Typer) -> None:
    """挂到 `main.py` 的顶层 app 上（叶子命令，不建子命令组）。"""
    app.command(
        "backfill",
        help="A 股日频历史行情回补（qfq）+ 缺口报表（md/csv）",
    )(backfill_command)
