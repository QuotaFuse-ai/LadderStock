"""`ldst schedule list|run`：定时任务表（APScheduler）与单轮 tick。

调度表落 `config/schedule.yaml`（JSON Schema 校验，`ldst config validate` 一并检查），
默认 **07:30 题材资讯采集**（wire_news，QUOT-554）、**09:05 盘前摘要**（digest）、
**盘中 6 时点快照**（snapshot，QUOT-286）、**15:05 收盘对账**（reconcile）、
**15:35 收盘全量同步**（sync_close，QUOT-347）、**15:40 收盘摘要**与 **16:15 自选变化**
（digest，DEC-058）。

`trigger` 支持两种写法：`HH:MM` 定点（cron）与 `Ns` 间隔（如 `30s` / `60s`）。
盘中高频（QUOT-352 / DEC-056）就是两条间隔任务：`snapshot_fast`（关键盘面快照）与
`sync_batch`（批量数据集，目前是 `fund_flow_realtime`），二者都只在连续竞价时段出网。

边界（DEC-006 / DEC-004）：

- 任务只跑本机动作（发本机通知、对账本地已落盘数据、复用 sync 链路按治理取 peer 读数、
  触发 Wire peer 自己的采集（`qfw run`，QUOT-554）、生成当日复盘、生成三类本机摘要）；
- 单个任务失败只记 `degraded` / `skipped`，不拖垮整轮、不中断调度器；
- 摘要任务（`kind: digest`）先写 `inbox` 行再投递通知：通知失败只把 `delivery` 记为
  `degraded`，收件箱里那条**仍然在**；同一 `dedupe_key` 重跑只更新同一条。
- `schedule run --once` 跑一轮就退出（冒烟、手动补跑都靠它）；不带 `--once` 是长驻
  调度器，按 `scheduler.timezone` 的本地时间触发，Ctrl+C 优雅退出。
- snapshot 的时点窗口在 `core.intraday` 判定：窗口外只记 `skipped`，**不补跑**。
- sync_close 与 15:05 reconcile 是两个独立触发点，天然解耦：同步失败只折进自己的
  状态字段，绝不把对账那一轮带崩；落库幂等由 `duckdb_store.write_dataset` 保证。
"""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import bundle_config_dir, config_dir
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    LadderStockError,
)
from ladderstock.watch import notifier

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MISFIRE_GRACE_SECONDS = 300

#: 任务类型白名单。`snapshot_fast` / `sync_batch` 是盘中高频（QUOT-352）：
#: 前者按秒级间隔抓「昨日涨停股 × 此刻报价」，后者按间隔刷一条批量数据集。
#: `digest` 是三类本机摘要（DEC-058）：生成正文 + 写 `inbox` + 投递通知。
#: `wire_news` 是题材资讯每日采集（QUOT-554）：先触发 Wire 自己的 `qfw run`
#: （采集+提炼），再走既有 `theme_news` 同步链路落库。
JOB_KINDS: tuple[str, ...] = (
    "notify",
    "reconcile",
    "snapshot",
    "sync_close",
    "snapshot_fast",
    "sync_batch",
    "digest",
    "wire_news",
    "knowledge_refresh",
)

#: 单次 `qfw run`（全赛道采集 + LLM 提炼）的时长上限：正常 1–3 分钟，超上限即终止
#: 记 failed，再按 Wire 实际已落库的数据同步。cron 任务 max_instances=1，不会重叠。
WIRE_RUN_TIMEOUT_SEC = 900


def trigger_interval_seconds(trigger: Any) -> int | None:
    """`"30s"` → 30；`"HH:MM"` 或其它写法 → None（定点触发，走 cron）。"""
    text = str(trigger or "").strip()
    if len(text) > 1 and text.endswith("s") and text[:-1].isdigit():
        seconds = int(text[:-1])
        if seconds > 0:
            return seconds
    return None


#: 15:35 收盘全量同步的数据集顺序（QUOT-347）。复用 `cli/sync.py` 已有的取数链路，
#: 一切取数走 TickHub 缓存、不直打上游（DEC-006 边界）；`fund_flow` 是 code 必填的
#: 候选池数据集，代码池由 `sync.py` 按 `--codes > sync.yaml > 本机自选` 解析。
CLOSE_SYNC_DATASETS: tuple[str, ...] = (
    "limit_pool",
    "fund_flow",
    "concept_board",
    # QUOT-446：财报前瞻 / 事件回看的事件类数据集（semi_static），随 15:35 收盘一起
    # 按日落 Parquet。排在候选池之后——当日池子先落，后续若要从池子取代码才拿得到。
    "consensus_forecast",
    "holder_trades",
    "institution_survey",
    "share_buyback",
    "index_daily",
)

# 内置默认表：与 config/schedule.yaml 同口径。配置缺失时按此运行并给 warning，
# 这样「第一次装上就能跑」和「配置坏了不静默」两件事都不牺牲——坏了的那份会
# 解析失败并按退出码 3 报出来。
DEFAULT_JOBS: tuple[dict[str, Any], ...] = (
    {
        # QUOT-554：题材/资讯每日自动新鲜——先触发 Wire 本机 `qfw run`（采集+提炼，
        # QF_WIRE_API_KEY 由 Wire 从自己的 .env 加载，不经 LadderStock），再走既有
        # theme_news 同步链路落库推水位。每天跑（含周末）；部分源不可用只记 degraded。
        "id": "wire_news_daily",
        "name": "07:30 题材资讯采集",
        "kind": "wire_news",
        "trigger": "07:30",
        "enabled": True,
    },
    {
        # QUOT-432 / DEC-058：`premarket_reminder`（静态口播文案）退役，换成
        # `premarket_digest`——同一触发时刻（09:05）改为按真实行情生成摘要并写收件箱。
        # 旧 id 不再出现在默认表里；用户自己的 schedule.yaml 里若还留着
        # `kind: notify` 的 `premarket_reminder`，照常生效（`notify` 类型没有删除）。
        "id": "premarket_digest",
        "name": "09:05 盘前摘要",
        "kind": "digest",
        "summary": "premarket",
        "trigger": "09:05",
        "enabled": True,
        "channel": notifier.CHANNEL_CONSOLE,
    },
    {
        "id": "snapshot_0925",
        "name": "09:25 竞价快照",
        "kind": "snapshot",
        "trigger": "09:25",
        "enabled": True,
    },
    {
        "id": "snapshot_0935",
        "name": "09:35 开盘快照",
        "kind": "snapshot",
        "trigger": "09:35",
        "enabled": True,
    },
    {
        "id": "snapshot_1000",
        "name": "10:00 盘中快照",
        "kind": "snapshot",
        "trigger": "10:00",
        "enabled": True,
    },
    {
        "id": "snapshot_1130",
        "name": "11:30 午盘快照",
        "kind": "snapshot",
        "trigger": "11:30",
        "enabled": True,
    },
    {
        "id": "snapshot_1400",
        "name": "14:00 午后快照",
        "kind": "snapshot",
        "trigger": "14:00",
        "enabled": True,
    },
    {
        "id": "snapshot_1500",
        "name": "15:00 收盘快照",
        "kind": "snapshot",
        "trigger": "15:00",
        "enabled": True,
    },
    {
        "id": "intraday_fast",
        "name": "盘中 30s 关键快照",
        "kind": "snapshot_fast",
        "trigger": "30s",
        "enabled": True,
    },
    {
        "id": "fund_flow_fast",
        "name": "盘中 60s 资金流批量",
        "kind": "sync_batch",
        "trigger": "60s",
        "enabled": True,
        "dataset": "fund_flow_realtime",
    },
    {
        "id": "close_reconcile",
        "name": "15:05 收盘对账",
        "kind": "reconcile",
        "trigger": "15:05",
        "enabled": True,
    },
    {
        "id": "close_sync",
        "name": "15:35 收盘全量同步",
        "kind": "sync_close",
        "trigger": "15:35",
        "enabled": True,
    },
    {
        "id": "close_digest",
        "name": "15:40 收盘摘要",
        "kind": "digest",
        "summary": "close",
        "trigger": "15:40",
        "enabled": True,
        "channel": notifier.CHANNEL_CONSOLE,
    },
    {
        "id": "watch_changes",
        "name": "16:15 自选变化",
        "kind": "digest",
        "summary": "watch",
        "trigger": "16:15",
        "enabled": True,
        "channel": notifier.CHANNEL_CONSOLE,
    },
    {
        # QUOT-568：收盘数据齐了再刷新档案（15:35 同步之后），每只票跑一次研究并
        # 经既有 `store.archive_run()` 落 `data/knowledge/`。跑挂的票记原因、不编档案。
        "id": "knowledge_refresh",
        "name": "16:30 自选/持仓档案刷新",
        "kind": "knowledge_refresh",
        "trigger": "16:30",
        "enabled": True,
    },
)


# ---------------------------------------------------------------- 配置装载


def schedule_config_path() -> Path:
    return config_dir() / "schedule.yaml"


def _schema_path() -> Path:
    """调度表的 schema：配置目录优先，否则退回随包模板的 `config/schema/`。

    自定义 `LDST_CONFIG_DIR` 往往只有一份 schedule.yaml；此时若找不到 schema 就
    退化成"什么都能过"，等于没有校验。所以宁可读随包那份，也别静默放宽
    （打包后没有工程根，必须走 `bundle_config_dir()`，QUOT-525）。
    """
    local = config_dir() / "schema" / "schedule.schema.json"
    if local.is_file():
        return local
    return bundle_config_dir() / "schema" / "schedule.schema.json"


def _digest_summaries() -> tuple[str, ...]:
    """摘要类型取值（懒导入：`watch.digest` 会拉行情缓存模块，配置校验不该为它付费）。"""
    from ladderstock.watch.digest import SUMMARIES

    return SUMMARIES


def _structural_problems(data: Mapping[str, Any]) -> list[str]:
    """schema 缺失时的兜底自检：只覆盖会让调度器起不来的硬伤。"""
    problems: list[str] = []
    scheduler = data.get("scheduler")
    if not isinstance(scheduler, Mapping):
        problems.append("/scheduler: 缺失或不是对象")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        problems.append("/jobs: 至少要有一条任务")
        return problems
    for index, job in enumerate(jobs):
        where = f"/jobs/{index}"
        if not isinstance(job, Mapping):
            problems.append(f"{where}: 不是对象")
            continue
        for field in ("id", "name", "kind", "trigger", "enabled"):
            if field not in job:
                problems.append(f"{where}/{field}: 缺少必填字段")
        trigger = job.get("trigger")
        if isinstance(trigger, str) and len(trigger) == 5 and trigger[2] == ":":
            hour, minute = trigger[:2], trigger[3:]
            if not (hour.isdigit() and minute.isdigit()):
                problems.append(f"{where}/trigger: 需要 HH:MM 或 Ns")
        elif trigger_interval_seconds(trigger) is None and "trigger" in job:
            problems.append(f"{where}/trigger: 需要 HH:MM（定点）或 Ns（间隔）")
        kind = job.get("kind")
        if kind not in JOB_KINDS:
            problems.append(f"{where}/kind: 只支持 {' / '.join(JOB_KINDS)}")
        if kind == "sync_batch" and not job.get("dataset"):
            problems.append(f"{where}/dataset: sync_batch 必须声明数据集")
        limit = job.get("daily_limit")
        if kind == "knowledge_refresh" and limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                problems.append(f"{where}/daily_limit: 必须是正整数（每轮最多刷几只）")
        if kind == "digest":
            summary = job.get("summary")
            if summary not in _digest_summaries():
                problems.append(f"{where}/summary: digest 必须是 {' / '.join(_digest_summaries())}")
    return problems


def _default_table() -> dict[str, Any]:
    return {
        "scheduler": {
            "timezone": DEFAULT_TIMEZONE,
            "misfire_grace_seconds": DEFAULT_MISFIRE_GRACE_SECONDS,
        },
        "jobs": [copy.deepcopy(job) for job in DEFAULT_JOBS],
    }


def validate_table(data: Mapping[str, Any]) -> None:
    """按 schema 校验；schema 缺失时退到结构自检（宁可严一点，也别静默放过）。"""
    schema_file = _schema_path()
    problems: list[str] = []
    if schema_file.is_file():
        from jsonschema import Draft202012Validator

        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        problems = [
            f"/{'/'.join(map(str, err.path))}: {err.message}"
            for err in sorted(
                Draft202012Validator(schema).iter_errors(dict(data)),
                key=lambda err: list(err.path),
            )
        ]
    else:
        problems = _structural_problems(data)
    if not problems:
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            problems.append("/jobs: 至少要有一条任务")
    if problems:
        raise LadderStockError("schedule.yaml: " + "；".join(problems), EXIT_CONFIG)


def load_schedule(
    path: Path | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    """读调度表：返回 (表, 警告, 来源说明)。解析/校验失败抛 `LadderStockError(3)`。"""
    target = path or schedule_config_path()
    if not target.is_file():
        return (
            _default_table(),
            [f"配置缺失：{target}（使用内置默认表：09:05 盘前提醒、15:05 收盘对账）"],
            "builtin",
        )
    import yaml

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - 原样把原因交给用户
        raise LadderStockError(f"schedule.yaml 解析失败：{exc}", EXIT_CONFIG) from exc
    if not isinstance(raw, dict):
        raise LadderStockError("schedule.yaml 顶层必须是对象", EXIT_CONFIG)
    validate_table(raw)

    scheduler = raw.get("scheduler") or {}
    jobs = [job for job in raw.get("jobs") or [] if isinstance(job, dict)]
    table: dict[str, Any] = {
        "scheduler": {
            "timezone": str(scheduler.get("timezone") or DEFAULT_TIMEZONE),
            "misfire_grace_seconds": int(
                scheduler.get("misfire_grace_seconds", DEFAULT_MISFIRE_GRACE_SECONDS)
            ),
        },
        "jobs": jobs,
    }
    return table, [], str(target)


def jobs_view(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs = [job for job in table.get("jobs") or [] if isinstance(job, Mapping)]
    ordered = sorted(jobs, key=lambda job: str(job.get("trigger") or "99:99"))
    return [
        {
            "id": job.get("id"),
            "name": job.get("name"),
            "kind": job.get("kind"),
            "trigger": job.get("trigger"),
            "enabled": bool(job.get("enabled")),
            "channel": job.get("channel") or "-",
        }
        for job in ordered
    ]


def _fail(message: str, code: int) -> NoReturn:
    error(message)
    raise typer.Exit(code)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 任务执行


def _run_reconcile(day: date | None = None) -> dict[str, Any]:
    """收盘对账：只读本地已落盘的验证条件与次日数据，缺就先跳过（不伪造结论）。"""
    from ladderstock.core import validation as validation_module

    target = day or date.today()
    try:
        outcome = validation_module.reconcile(target)
    except FileNotFoundError as exc:
        return {"status": "skipped", "detail": str(exc), "date": target.isoformat()}
    status = str(outcome.get("status") or "unknown")
    return {
        "status": "ok" if status == "ok" else "partial",
        "detail": str(outcome.get("reason") or f"对账状态 {status}"),
        "date": target.isoformat(),
    }


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


def _sync_close_row_text(row: Mapping[str, Any]) -> str:
    """一行一个数据集：`dataset=状态(+本次写入/库内总行数)`，给验收留可核对的计数。"""
    text = f"{row.get('dataset')}={row.get('status')}"
    rows = row.get("rows")
    total = row.get("total_rows")
    if isinstance(rows, int) and rows:
        suffix = f"+{rows}"
        if isinstance(total, int) and total:
            suffix += f"/{total}"
        text += f"({suffix})"
    return text


def _sync_close_summary(payload: Mapping[str, Any]) -> tuple[str, str]:
    """把 `run_sync` 的汇总折成 (状态, 明细)。

    状态跟着整轮同步的退出码走：全成 `ok`、部分失败 `degraded`、一个都没成 `failed`；
    这只描述本任务，不改变调度器「不崩溃」的契约。
    """
    summary = payload.get("summary") or {}
    results = [row for row in payload.get("results") or [] if isinstance(row, Mapping)]
    per = "、".join(_sync_close_row_text(row) for row in results) or "无数据集"
    days = sorted({day for row in results for day in (row.get("days") or [])})
    code = summary.get("exit_code")
    if code == EXIT_OK:
        status = "ok"
    elif code == EXIT_PARTIAL:
        status = "degraded"
    else:
        status = "failed"
    detail = f"同步 {per}，落库交易日 {len(days)} 个{f'（{days[-1]}）' if days else ''}"
    return status, detail


def _sync_close_review(day: date, root: Path | None) -> tuple[str, str]:
    """触发当日复盘生成；复盘自身的问题只降级，不把同步结果带崩。"""
    from ladderstock.review import pipeline

    try:
        _report, run = pipeline.generate(day.isoformat(), root=root)
    except Exception as exc:  # noqa: BLE001 - 复盘取数/AI 失败不拖垮同步任务
        return "failed", f"复盘异常：{_one_line(exc)}"
    if getattr(run, "failed", False):
        return "failed", "复盘存在失败小节"
    if getattr(run, "degraded", False):
        return "degraded", "复盘 AI 降级完成"
    return "ok", f"复盘已生成 {pipeline.review_path(day).name}"


def _run_sync_close(
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """15:35 收盘全量同步（QUOT-347）。

    顺序拉 `CLOSE_SYNC_DATASETS`（复用 `cli/sync.run_sync`，不新建采集链路，DEC-006），
    落 `archive/` 的 Parquet 日分区，再触发当日 `review` 生成。**仅交易日动作**：日历判定
    沿用 `core.intraday.local_moment` + 周末口径，非交易日整轮 `skipped`、不建空目录。
    幂等由 `duckdb_store.write_dataset` 兜底（replace 整日覆盖、append 按 `(code, trade_date)`
    upsert，分区按天原子重写），手动补跑 / wake 后 grace 窗口内重复触发都不产生重复分区。
    与 15:05 对账是两个独立触发点，本任务失败不会把对账那一轮带崩。
    """
    from ladderstock.core import intraday

    moment = intraday.local_moment(now, timezone)
    day = moment.date()
    base = {"date": day.isoformat()}
    if day.weekday() >= 5:
        return {**base, "status": "skipped", "detail": "非交易日（周末），不跑"}

    from ladderstock.cli import sync as sync_module
    from ladderstock.data import watermark
    from ladderstock.data.db_lock import writer_lock
    from ladderstock.data.provider_registry import ProviderRegistry

    registry = ProviderRegistry(root=root)
    store = watermark.WatermarkStore(root=registry.root)
    try:
        with writer_lock(root, holder="ldst schedule sync_close"):
            payload = sync_module.run_sync(registry, store, list(CLOSE_SYNC_DATASETS))
    except LadderStockError as exc:
        return {**base, "status": "failed", "detail": f"收盘同步中止：{exc.message}"}

    status, detail = _sync_close_summary(payload)
    review_status, review_detail = _sync_close_review(day, root)
    if status == "ok" and review_status != "ok":
        status = "degraded"
    return {**base, "status": status, "detail": f"{detail}；复盘：{review_detail}"}


def _wire_qfw_entry(wire_dir: Path) -> Path | None:
    """Wire venv 里的 qfw 入口（Windows `Scripts/qfw.exe` / POSIX `bin/qfw`）。"""
    for parts in ((".venv", "Scripts", "qfw.exe"), (".venv", "bin", "qfw")):
        candidate = wire_dir.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def _collect_wire_news() -> dict[str, Any]:
    """触发 Wire 自己的采集+提炼（`qfw run`），返回 {status, detail}。

    子进程 cwd 钉在 Wire 目录：QF_WIRE_API_KEY 由 Wire 从它自己的 .env/.env.local
    加载（真实环境 > .env.local > .env），LadderStock 的配置、argv、日志都不碰密钥。
    采集由 Wire peer 自己完成——LadderStock 不直连资讯站点（DEC-006）。
    退出码口径取 Wire 的 `core/errors.py`：0 全成、8 部分源失败、其余失败。
    """
    from ladderstock.supervisor.module_adapters import default_peer_dir

    wire_dir = default_peer_dir("wire")
    entry = _wire_qfw_entry(wire_dir)
    if entry is None:
        return {
            "status": "skipped",
            "detail": f"Wire 未安装（{wire_dir} 下没有 qfw 入口），跳过采集",
        }
    try:
        proc = subprocess.run(  # noqa: S603 — argv 来自本机 peer 目录解析，无 shell
            [str(entry), "--output", "json", "run"],
            cwd=str(wire_dir),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=WIRE_RUN_TIMEOUT_SEC,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "detail": f"qfw run 超时（>{WIRE_RUN_TIMEOUT_SEC}s）已终止",
        }
    except OSError as exc:
        return {"status": "failed", "detail": f"qfw run 启动失败：{_one_line(exc)}"}

    summary: dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        summary = parsed
    code = proc.returncode
    status = "ok" if code == 0 else ("degraded" if code == 8 else "failed")
    items, total = summary.get("items"), summary.get("sources_total")
    if isinstance(items, int) and isinstance(total, int):
        detail = (
            f"qfw run {summary.get('day') or '-'}：采集 {items} 条资讯，"
            f"信源 {summary.get('sources_ok')}/{total} 成功、"
            f"{summary.get('sources_unavailable')} 不可用"
        )
    else:
        detail = f"qfw run 退出码 {code}（无 JSON 摘要，详情见 Wire 日志）"
    if status == "degraded":
        detail += "；部分源不可用，如实记 degraded"
    return {"status": status, "detail": detail}


# ---------------------------------------------------------------- 档案定期刷新


#: 覆盖率告警阈值（QUOT-568）：已建档案的票数 / 关注票数（自选 ∪ 持仓）低于它就记
#: 一条问题项——告警只是"该看一眼"，不据此判定任务失败（失败看的是有没有票跑挂）。
DEFAULT_KNOWLEDGE_THRESHOLD = 0.8


def _held_codes(root: Path | None) -> list[str]:
    """账本重放后仍持有的代码（数量 > 0）；与时间轴 / 摘要同一口径。"""
    from ladderstock.core.portfolio import replay as replay_portfolio
    from ladderstock.journal.portfolio import list_trades

    result = replay_portfolio(list_trades(root=root), "average")
    return [
        str(holding.symbol) for holding in result.holdings if holding.shares > 0 and holding.symbol
    ]


def knowledge_universe(root: Path | None = None) -> list[dict[str, Any]]:
    """关注票集合：自选在前、持仓在后，同一代码只算一次。

    持仓单独列出来是因为它在自选之外——只刷自选会把正在拿的票漏掉；重复的票保留
    第一次出现的位置（自选顺序即用户加自选的顺序）。
    """
    from ladderstock.journal.watchlist import clean_code, list_watch

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list_watch(root=root):
        try:
            code = clean_code(str(row.get("code") or ""))
        except LadderStockError:
            continue
        if code in seen:
            continue
        seen.add(code)
        rows.append({"code": code, "origin": ["自选"]})
    for code in _held_codes(root):
        try:
            clean = clean_code(code)
        except LadderStockError:
            continue
        if clean in seen:
            for item in rows:
                if item["code"] == clean:
                    item["origin"].append("持有")
            continue
        seen.add(clean)
        rows.append({"code": clean, "origin": ["持有"]})
    return rows


def knowledge_coverage(
    codes: Sequence[str], *, data_root: Path | None = None, day: date | None = None
) -> dict[str, Any]:
    """档案覆盖率：`有档案票数 / 关注票数`。

    "有档案"= manifest 有条目，且当日取得到档案内容、状态（`as_of + valid_days`）不是
    stale。档案有两份来源：归档快照 `latest.md`（`archive_run` 写）与人工主档案
    `profile.md`（`load_profile` 写），召回读前者、调用方读后者——两边都要看，
    只查一边会把另一半的票误报成没档案。过期的档案仍在磁盘上，但召回时不该被当成
    有效结论；没档案和过期都进 `stale[]`（原因不同，处置相同：等下一轮刷新）。
    """
    from ladderstock.cli.paths import data_dir
    from ladderstock.knowledge import model, store
    from ladderstock.knowledge.recall import recall

    root = Path(data_root) if data_root is not None else data_dir()
    today = day or model.sh_today()
    manifest = store.load_manifest(root)
    covered: list[str] = []
    stale_codes: list[str] = []
    status: str | None
    for code in codes:
        market = _market_of(code)
        if store.manifest_entry(manifest, market, code) is None:
            stale_codes.append(code)
            continue
        dossier = store.load_profile(root, market, code)
        status = None
        if dossier is not None:
            status = dossier.computed_status(today)
        else:
            # refuted 是显式反证（不召回），与"没档案"同列：都不该算覆盖。
            hit = recall(root, market, code, today=today)
            status = hit.status if hit is not None else None
        if status != model.STATUS_FRESH:
            stale_codes.append(code)
            continue
        covered.append(code)
    total = len(codes)
    ratio = round(len(covered) / total, 4) if total else 1.0
    return {
        "total": total,
        "covered": covered,
        "stale": stale_codes,
        "ratio": ratio,
        "threshold": DEFAULT_KNOWLEDGE_THRESHOLD,
    }


def _market_of(code: str) -> str:
    from ladderstock.knowledge import model
    from ladderstock.web.symbols import market_of

    return model.normalize_market(market_of(code))


def _research_failure_reason(run: Any) -> str | None:
    """run 没产出可归档报告时，把原因折成一句话；能归档返回 None。"""
    from ladderstock.knowledge.store import should_archive

    # 未配置 AI 时流水线整段走占位叙述：结构上"成功"（各段 ok、有 report），
    # 但内容是假的。先于 should_archive 拦下，占位档案绝不落盘。
    if not bool(getattr(run, "configured", True)):
        return "AI 未配置：研究各段只产出占位叙述，未落档案"
    if should_archive(run):
        return None
    failed = list(getattr(run, "failed", None) or [])
    if failed:
        reasons: list[str] = []
        for item in getattr(run, "research_failures", None) or []:
            kind = item.get("kind") or "internal"
            reasons.append(f"{item.get('stage')}[{kind}]")
        detail = "、".join(reasons) or "、".join(failed)
        return f"研究段失败：{detail}"
    generated = "、".join(getattr(run, "generated", None) or []) or "无"
    return f"研究未产出报告段（generated={generated}）"


def _archive_view(run: Any) -> Any:
    """归档门槛认 `generated`，复用的段落在 `reused` 里——同日重跑时把两者并起来，
    否则一份完整（只是全从 checkpoint 复用）的 run 会被 `archive.skipped` 挡掉。"""
    reused = list(getattr(run, "reused", None) or [])
    if not reused:
        return run
    generated = list(getattr(run, "generated", None) or []) + [
        name for name in reused if name not in set(getattr(run, "generated", None) or [])
    ]
    return replace(run, generated=generated)


def _refresh_one(code: str, day: date, data_root: Path) -> dict[str, Any]:
    """单只票：研究 → 归档（既有出口，语义不变）。

    归档 frontmatter 的 `as_of` 就是 research run 的日期，也就是本次刷新的证据日，
    不需要二次改写（`archive_run` 不写 `profile.md`，改写 profile 只会制造两份档案）。
    """
    from ladderstock.knowledge import store
    from ladderstock.research.pipeline import ResearchPipeline

    pipeline = ResearchPipeline(code, root=data_root, config_root=config_dir())
    run = pipeline.run(day.isoformat())
    view = _archive_view(run)
    reason = _research_failure_reason(view)
    if reason is not None:
        return {"code": code, "status": "failed", "reason": reason}
    outcome = store.archive_run(
        data_root,
        _market_of(code),
        view,
        run_id=f"refresh-{day:%Y%m%d}",
        name=code,
    )
    if not outcome.archived:
        return {"code": code, "status": "failed", "reason": f"{outcome.event}：{outcome.detail}"}
    status = "degraded" if getattr(run, "degraded", False) else "ok"
    return {"code": code, "status": status, "reason": None, "archived": outcome.path}


def _alert_knowledge_coverage(
    job: Mapping[str, Any],
    *,
    root: Path | None,
    now: datetime | None,
    day: date,
    coverage: Mapping[str, Any],
    failed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """覆盖率过低 → 收件箱记一条问题项（能关） + 自愈同款桌面通知（不能关）。

    两条都走既有通道，不新造：收件箱是页面入口（复跑只更新同一条），通知用自愈的
    `ToastSink`（弹完即走、不驻留）。任一通道失败只让任务记 degraded，不改变刷新结果。
    """
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.supervisor.notify import ToastSink

    rows = sorted((dict(item) for item in failed), key=lambda item: str(item.get("code")))
    lines = [
        f"档案覆盖率 {len(coverage['covered'])}/{coverage['total']}"
        f"（{coverage['ratio']:.0%}，阈值 {coverage['threshold']:.0%}）",
    ]
    if coverage["stale"]:
        lines.append("无有效档案（未建或已过期）：" + "、".join(coverage["stale"]))
    if rows:
        reasons = "；".join(f"{r['code']} {r['reason'][:80]}" for r in rows[:10])
        lines.append("本轮失败原因：" + reasons)
    title = f"LadderStock 档案覆盖不足：{len(coverage['covered'])}/{coverage['total']}"
    item = inbox_store.add_item(
        str(job.get("id") or ""),
        str(job.get("name") or ""),
        title,
        lines,
        None,
        f"{job.get('id')}:{day.isoformat()}",
        "knowledge",
        root=root,
        now=now,
    )
    ToastSink(panel_url="http://127.0.0.1:5950/").failure(
        module="knowledge_refresh", ports=(), attempt=0, max_attempts=0, reason="；".join(lines)
    )
    return {"inbox_id": item["id"], "title": title}


def _run_knowledge_refresh(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """自选 + 持仓优先的档案定期刷新（QUOT-568）。

    每天一次（含周末：研究跑的是最近一个已收盘交易日的数据，周末补跑不产生新结论，
    真正的工作日数据到齐后由当天的定时轮自然覆盖）。逐只跑六段研究，产物只经既有
    `store.archive_run()` 出口落盘——"run 非 failed 才落盘"的语义不变，跑挂的票
    把原因写进结果的 `failed[]`，不伪造档案。

    `daily_limit` 是每轮最多刷几只（默认全量）：单轮时间预算不够时调小它，
    超出的票下一轮自然轮到，不静默跳过。
    """
    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday

    moment = intraday.local_moment(now, timezone)
    day = moment.date()
    base: dict[str, Any] = {"date": day.isoformat()}

    universe = knowledge_universe(root=root)
    if not universe:
        return {**base, "status": "skipped", "detail": "自选与持仓都为空，没有需要刷新的票"}

    limit = job.get("daily_limit")
    targets = universe if not isinstance(limit, int) or limit <= 0 else universe[:limit]
    data_root = Path(root) if root is not None else data_dir()

    refreshed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in targets:
        try:
            row = _refresh_one(item["code"], day, data_root)
        except Exception as exc:  # noqa: BLE001 - 单票异常不影响其余票
            row = {"code": item["code"], "status": "failed", "reason": f"异常：{_one_line(exc)}"}
        (refreshed if row["status"] != "failed" else failed).append(row)

    coverage = knowledge_coverage([item["code"] for item in universe], data_root=data_root, day=day)
    alert = None
    if coverage["ratio"] < coverage["threshold"]:
        try:
            alert = _alert_knowledge_coverage(
                job, root=root, now=now, day=day, coverage=coverage, failed=failed
            )
        except Exception as exc:  # noqa: BLE001 - 提醒失败不掩盖刷新结果
            alert = {"error": _one_line(exc)}

    if not refreshed:
        status = "failed"
    elif failed:
        status = "degraded"
    else:
        status = "ok"
    parts = [f"关注 {len(universe)} 只，本轮刷 {len(refreshed)} 只"]
    if failed:
        parts.append("失败 " + "；".join(f"{r['code']} {r['reason'][:60]}" for r in failed[:5]))
    parts.append(
        f"档案覆盖 {len(coverage['covered'])}/{coverage['total']}（{coverage['ratio']:.0%}）"
    )
    if alert and "title" in alert:
        parts.append("覆盖率低于阈值，已记问题项并弹通知")
    elif alert:
        parts.append(f"覆盖率告警投递失败：{alert['error']}")
    return {
        **base,
        "status": status,
        "detail": "；".join(parts),
        "refreshed": refreshed,
        "failed": failed,
        "coverage": coverage,
        "alert": alert,
    }


#: theme_news 同步行状态 → 任务状态。与 sync_batch 的差别：采集已经跑过，同步再
#: skipped（peer 不可达 / 赛道池为空）就是失败而不是安静跳过；empty（Wire 当日无
#: 内容）记 degraded——水位保持、不拿旧数据顶替。
_WIRE_SYNC_STATUS_MAP = {
    "ok": "ok",
    "degraded": "degraded",
    "empty": "degraded",
    "skipped": "failed",
    "failed": "failed",
}
_WIRE_STATUS_RANK = {"ok": 0, "degraded": 1, "failed": 2}


def _run_wire_news(
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """07:30 题材资讯每日采集（QUOT-554）：先 `qfw run`，再 theme_news 同步落库。

    **每天跑（含周末）**——资讯不收盘，与 sync_close 的仅交易日口径刻意不同。
    两段各自如实、整体取差者：采集失败不挡落库（Wire 手里有什么就同步什么，水位跟
    data_as_of 走，不拿旧数据顶替当日），但当日采集没达成整体就记 failed，明细保留
    同步结果；部分免费 RSS 源 403/不可用 → degraded，不补造。
    幂等由 replace 整日覆盖 + `qfw run` 同日重跑只覆盖当日兜底；手动补跑
    `ldst schedule run --once --job wire_news_daily`。
    """
    from ladderstock.core import intraday

    moment = intraday.local_moment(now, timezone)
    base = {"date": moment.date().isoformat(), "dataset": "theme_news"}

    collect = _collect_wire_news()
    if collect["status"] == "skipped":
        return {**base, "status": "skipped", "detail": collect["detail"]}

    from ladderstock.cli import sync as sync_module
    from ladderstock.data import watermark
    from ladderstock.data.db_lock import writer_lock
    from ladderstock.data.provider_registry import ProviderRegistry

    if registry is None:
        registry = ProviderRegistry(root=root)
    store = watermark.WatermarkStore(root=getattr(registry, "root", root))
    try:
        with writer_lock(root, holder="ldst schedule wire_news"):
            row = sync_module.sync_one(registry, store, "theme_news")
    except LadderStockError as exc:
        return {
            **base,
            "status": "failed",
            "detail": f"{collect['detail']}；同步中止：{exc.message}",
        }

    sync_status = _WIRE_SYNC_STATUS_MAP.get(str(row.get("status") or ""), "degraded")
    status = max((collect["status"], sync_status), key=lambda s: _WIRE_STATUS_RANK[s])
    return {
        **base,
        "status": status,
        "detail": f"{collect['detail']}；同步：{row.get('detail')}",
        "collect_status": collect["status"],
        "sync_status": str(row.get("status") or ""),
        "rows": row.get("rows"),
        "watermark_after": row.get("watermark_after"),
    }


def _run_sync_batch(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """盘中按间隔刷一条批量数据集（QUOT-352，目前只有 `fund_flow_realtime`）。

    取数、降级、水位全部复用 `cli/sync.sync_one` 与 S2 的退避链路（QUOT-349），
    这里只加交易时段门控：非交易时段一次上游都不打。
    """
    from ladderstock.core import intraday

    moment = intraday.local_moment(now, timezone)
    day = moment.date()
    dataset = str(job.get("dataset") or "")
    if not dataset:
        return {"status": "failed", "detail": "sync_batch 缺少 dataset"}
    if day.weekday() >= 5:
        return {"status": "skipped", "detail": "非交易日（周末），不跑", "quiet": True}
    if not intraday.in_trading_session(moment):
        return {
            "status": "skipped",
            "detail": "不在连续竞价时段（09:30–11:30 / 13:00–15:00），不跑",
            "quiet": True,
        }

    from ladderstock.cli import sync as sync_module
    from ladderstock.data import watermark
    from ladderstock.data.provider_registry import ProviderRegistry

    if registry is None:
        registry = ProviderRegistry(root=root)
    store = watermark.WatermarkStore(root=getattr(registry, "root", root))
    row = sync_module.sync_one(registry, store, dataset)
    mapped = {
        "ok": "ok",
        "degraded": "degraded",
        "empty": "degraded",
        "skipped": "skipped",
        "failed": "failed",
    }.get(str(row.get("status") or ""), "degraded")
    return {
        "status": mapped,
        "detail": str(row.get("detail") or ""),
        "dataset": dataset,
        "rows": row.get("rows"),
    }


def _run_digest(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """生成摘要 → 写 `inbox` → 投递本机通知（DEC-058）。

    顺序是刻意的：**先落库再投递**。桌面后端缺失 / 通知命令失败只把结果记成
    `degraded`，收件箱里那条仍然可读（页面是比通知更可靠的查看入口）。
    摘要本身 skipped（周末、过 11:30 的盘前补跑）时不写库——不给用户塞空提醒。
    """
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.watch import digest as digest_module

    summary = str(job.get("summary") or "")
    outcome = digest_module.build(
        str(job.get("id") or ""),
        summary,
        now=now,
        timezone=timezone,
        root=root,
        registry=registry,
    )
    base = {
        "date": outcome.trade_day,
        "summary": summary,
        "dedupe_key": outcome.dedupe_key,
    }
    if outcome.status == digest_module.STATUS_SKIPPED:
        return {**base, "status": "skipped", "detail": outcome.detail}
    if outcome.status != digest_module.STATUS_OK:
        return {**base, "status": "failed", "detail": outcome.detail}

    item = inbox_store.add_item(
        str(job.get("id") or ""),
        str(job.get("name") or ""),
        outcome.title,
        outcome.lines,
        outcome.link,
        outcome.dedupe_key,
        "digest",
        root=root,
        now=now,
    )
    delivery = notifier.send(
        job.get("channel"),
        outcome.title,
        "\n".join(outcome.lines),
    )
    status = "ok" if delivery.ok else "degraded"
    detail = (
        f"收件箱已写入 {item['id']}（{len(outcome.lines)} 行）；"
        f"通知[{delivery.channel}]：{delivery.detail}"
    )
    return {
        **base,
        "status": status,
        "detail": detail,
        "inbox_id": item["id"],
        "delivery": delivery.as_dict(),
    }


def run_job(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """执行一条任务；任何失败都折成结果字段，不向上抛（调度器要活着）。

    `now` / `timezone` 只给 snapshot 用：时点窗口按调度表的本地时间判定，
    测试可注入固定时刻（不靠系统时钟碰运气）。
    """
    started = datetime.now()
    result: dict[str, Any] = {
        "id": job.get("id"),
        "name": job.get("name"),
        "kind": job.get("kind"),
        "trigger": job.get("trigger"),
    }
    try:
        kind = str(job.get("kind") or "")
        if kind == "notify":
            delivery = notifier.send(
                job.get("channel"),
                str(job.get("name") or "LadderStock 提醒"),
                str(job.get("message") or ""),
            )
            result.update(
                status="ok" if delivery.ok else "degraded",
                detail=delivery.detail,
            )
        elif kind == "reconcile":
            result.update(_run_reconcile())
        elif kind == "snapshot":
            from ladderstock.core import intraday

            result.update(
                intraday.run_slot(
                    str(job.get("trigger") or ""),
                    now=now,
                    timezone=timezone,
                    root=root,
                    registry=registry,
                )
            )
        elif kind == "sync_close":
            result.update(_run_sync_close(now=now, timezone=timezone, root=root))
        elif kind == "wire_news":
            result.update(_run_wire_news(now=now, timezone=timezone, root=root, registry=registry))
        elif kind == "snapshot_fast":
            from ladderstock.core import intraday

            result.update(
                intraday.run_fast(now=now, timezone=timezone, root=root, registry=registry)
            )
        elif kind == "sync_batch":
            result.update(
                _run_sync_batch(job, now=now, timezone=timezone, root=root, registry=registry)
            )
        elif kind == "digest":
            result.update(
                _run_digest(job, now=now, timezone=timezone, root=root, registry=registry)
            )
        elif kind == "knowledge_refresh":
            result.update(_run_knowledge_refresh(job, now=now, timezone=timezone, root=root))
        else:
            result.update(status="failed", detail=f"未知任务类型 {kind!r}")
    except Exception as exc:  # noqa: BLE001 - 单条任务失败不拖垮整轮
        result.update(status="failed", detail=f"任务异常：{exc}")
    result["finished_at"] = _now_iso()
    result["elapsed_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    return result


def _run_job_logged(job: Mapping[str, Any], *, timezone: str | None = None) -> None:
    outcome = run_job(job, timezone=timezone)
    # 间隔任务在非交易时段每 30/60 秒返回一次 skipped；一天几千行会把真正的告警淹掉。
    # 只有「本来就不该跑」的轮次带 quiet 标记；时段内的 skipped（如代码池为空）照常记。
    if outcome.get("quiet"):
        return
    error(
        f"定时任务 {outcome.get('id')}（{outcome.get('trigger')}）"
        f"-> {outcome.get('status')}：{outcome.get('detail')}"
    )


def _trigger_time(trigger: str) -> tuple[int, int]:
    hour_text, minute_text = trigger.split(":", 1)
    return int(hour_text), int(minute_text)


def start_daemon(table: Mapping[str, Any], jobs: Sequence[Mapping[str, Any]]) -> None:
    """按任务表启动 APScheduler 长驻调度（阻塞到 Ctrl+C）。"""
    try:
        from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
            BlockingScheduler,
        )
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        _fail("定时调度需要 APScheduler：pip install apscheduler", EXIT_CONFIG)
        raise AssertionError from exc  # pragma: no cover - _fail 已退出

    scheduler_conf = table.get("scheduler") or {}
    timezone = str(scheduler_conf.get("timezone") or DEFAULT_TIMEZONE)
    grace = int(scheduler_conf.get("misfire_grace_seconds", DEFAULT_MISFIRE_GRACE_SECONDS))
    scheduler = BlockingScheduler(
        timezone=timezone,
        job_defaults={"misfire_grace_time": grace, "coalesce": True},
    )
    for job in jobs:
        interval = trigger_interval_seconds(job.get("trigger"))
        common: dict[str, Any] = {
            "id": str(job.get("id")),
            "name": str(job.get("name")),
            "args": [job],
            "kwargs": {"timezone": timezone},
            "replace_existing": True,
        }
        if interval is not None:
            # 高频任务：`max_instances=1` 防重叠（一轮 2.5–3.5s，远小于 30s），
            # grace 收到一个周期内，避免休眠唤醒后按错过的轮数连环补跑。
            scheduler.add_job(
                _run_job_logged,
                "interval",
                seconds=interval,
                max_instances=1,
                misfire_grace_time=min(grace, interval),
                coalesce=True,
                **common,
            )
            continue
        hour, minute = _trigger_time(str(job.get("trigger")))
        scheduler.add_job(
            _run_job_logged,
            "cron",
            hour=hour,
            minute=minute,
            **common,
        )
    error(
        f"定时任务已启动（{timezone}）："
        + "、".join(f"{job.get('trigger')} {job.get('name')}" for job in jobs)
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - 交互退出
        error("定时任务已停止")
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001 - 已经停了
            pass


# ---------------------------------------------------------------- 命令实现


def schedule_list(output: OutputOption = None) -> None:
    """列出调度表：时间、任务、类型、渠道、启用状态。"""
    try:
        table, warnings, source = load_schedule()
    except LadderStockError as exc:
        _fail(exc.message, exc.code)

    scheduler_conf = table["scheduler"]
    payload: dict[str, Any] = {
        "ok": True,
        "config": source,
        "timezone": scheduler_conf["timezone"],
        "misfire_grace_seconds": scheduler_conf["misfire_grace_seconds"],
        "job_count": len(table["jobs"]),
        "jobs": jobs_view(table),
    }
    if warnings:
        payload["warnings"] = warnings
        error(warnings[0])
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def schedule_run(
    once: Annotated[bool, typer.Option("--once", help="只跑一轮启用的任务后退出")] = False,
    job: Annotated[str | None, typer.Option("--job", help="只跑指定任务 id")] = None,
    output: OutputOption = None,
) -> None:
    """跑一轮启用的任务（`--once`），或按任务表长驻调度。"""
    try:
        table, warnings, source = load_schedule()
    except LadderStockError as exc:
        _fail(exc.message, exc.code)

    jobs: Sequence[Mapping[str, Any]] = [row for row in table["jobs"] if isinstance(row, Mapping)]
    if job is not None:
        matched = [row for row in jobs if row.get("id") == job]
        if not matched:
            known = "、".join(str(row.get("id")) for row in jobs) or "（空）"
            _fail(f"没有 id 为 {job!r} 的任务；已有：{known}", EXIT_USAGE)
        jobs = matched

    enabled = [row for row in jobs if row.get("enabled")]
    if not enabled:
        emit(
            {
                "ok": True,
                "config": source,
                "mode": "once" if once else "daemon",
                "detail": "没有启用的任务（检查 config/schedule.yaml 的 enabled）",
                "results": [],
            },
            output,
        )
        raise typer.Exit(EXIT_OK)

    if not once:
        start_daemon(table, enabled)
        return

    results = [run_job(row, timezone=table["scheduler"]["timezone"]) for row in enabled]
    payload: dict[str, Any] = {
        "ok": True,
        "config": source,
        "mode": "once",
        "ran_at": _now_iso(),
        "job_count": len(results),
        "results": results,
    }
    if warnings:
        payload["warnings"] = warnings
        error(warnings[0])
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def register(schedule_app: typer.Typer) -> None:
    """把 `schedule` 子命令挂到 main.py 已建好的 Typer 组上。"""
    schedule_app.command("list", help="查看调度表（时间 / 任务 / 类型 / 渠道 / 启用）")(
        schedule_list
    )
    schedule_app.command("run", help="跑一轮启用的任务（--once）或长驻调度")(schedule_run)


__all__ = [
    "CLOSE_SYNC_DATASETS",
    "DEFAULT_JOBS",
    "DEFAULT_MISFIRE_GRACE_SECONDS",
    "DEFAULT_TIMEZONE",
    "JOB_KINDS",
    "WIRE_RUN_TIMEOUT_SEC",
    "jobs_view",
    "load_schedule",
    "register",
    "run_job",
    "schedule_config_path",
    "schedule_list",
    "schedule_run",
    "start_daemon",
    "trigger_interval_seconds",
    "validate_table",
]
