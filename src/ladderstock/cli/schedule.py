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
import os
import subprocess
import sys
import time
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
    EXIT_DB_BUSY,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    LadderStockError,
)
from ladderstock.watch import notifier

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MISFIRE_GRACE_SECONDS = 300
#: 收盘同步是当天唯一的关键落盘动作，失败就是全天缺数——所以肯等：
#: 页面打开时触发的补数（ensure-on-open）一轮能占住写锁几分钟，30s 的默认等待
#: 必然超时（2026-11-17 就是这样丢了一整轮收盘同步）。
CLOSE_SYNC_LOCK_WAIT_SEC = 900.0
CLOSE_SYNC_RETRY_SEC = 90.0


def _log_schedule(message: str) -> None:
    """调度器自己的进度行：走 stderr，与 `ldst sync` 的进度行同一条流。"""
    sys.stderr.write(f"ldst: [schedule] {message}\n")
    sys.stderr.flush()


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
    # 第④步：定时研究（一轮交给 agent）与盯盘提醒（本机报价快照过阈值）
    "agent_research",
    "watch_alert",
    # 盘中 30 秒级实时盯盘（现取 spot_quote，只在交易时段跑）
    "watch_live",
    # 盘中实时情绪快照（market_sentiment，每分钟一轮，只在交易时段跑）
    "sentiment_live",
    # 跨轮任务规划：把用户「盯它一周」这类长期目标按频率推进（ai/plans.py）
    "plan_tick",
    # 收盘后晚间补取：龙虎榜按交易所规则收盘后才公布（通常 18:00 之后），
    # 15:35 的收盘同步拿不到，只能晚一点再来一趟。
    "sync_evening",
    # 资讯雷达批量采集（公告 / 研报 / 快讯）：公告类栏目上游要求 code 必填，
    # 单跑 `ldst news` 不带 `--code` 每轮都是 skipped，见 `_run_news_collect`。
    "news_collect",
    # F9 到期观点回访：扫账本、拉日线判对错（`ai/recall.py` 的 run_recall）。
    "recall",
    # DEC-087 提醒引擎：用户自配规则（价格/均线/放量/浮盈亏）一轮求值 + 投递，
    # 执行体 `alert/run.py:run_once()`（CLI / Web / 调度同一个入口）。
    "alert_check",
)

#: 单次 `qfw run`（全赛道采集 + LLM 提炼）的时长上限：正常 1–3 分钟，超上限即终止
#: 记 failed，再按 Wire 实际已落库的数据同步。cron 任务 max_instances=1，不会重叠。
WIRE_RUN_TIMEOUT_SEC = 900

#: 资讯批量采集的默认批大小：公告类工具一次只认一个 code，逐票调用；
#: 一批 100 只是 `sync.yaml` 里逐只数据集的同一口径（单批上限），不是上游限制。
NEWS_COLLECT_BATCH = 100


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
    # 复盘情绪段直接要这两个：没有它们，炸板家数与昨日涨停会读成 0，
    # 封板率 100%、温度虚高——「五段中 1 段缺数」就是这么来的。收盘后都能取，
    # 所以跟着 15:35 一起落，别留给用户手动补。
    "broken_board",
    "yesterday_limit_up",
    "fund_flow",
    "concept_board",
    # 行业维度：题材段有 industry_board 就用它，没有就退到行业板块资金流。
    "industry_fund_flow",
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
        # 公告 / 研报 / 快讯批量采集：公告类栏目上游要求 code 必填，必须按代码池
        # 分批传（`_run_news_collect`），裸调 `ldst news` 不带 `--code` 每轮都是 skipped。
        # 默认 **关闭**：用户要求先只接代码、不真采（IP / 上游额度没落地）。
        # 打开方式：把 `enabled` 改成 true，或 `ldst schedule run --job news_radar`。
        "id": "news_radar",
        "name": "07:45 公告/研报/快讯批量采集",
        "kind": "news_collect",
        "trigger": "07:45",
        "enabled": False,
        "datasets": (
            "announcement_cninfo,announcement_szse,research_report,"
            "flash_cls,flash_wallstreetcn,flash_eastmoney"
        ),
        "batch_size": NEWS_COLLECT_BATCH,
        "limit": 100,
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
    {
        # F9：到期观点自动回访——收盘后扫一遍账本，拉日线判对错。
        # 缺行情的记录留 open 等下一轮，不按「没数据」判错（见 ai/recall.py）。
        "id": "recall_due",
        "name": "16:10 观点回访判定",
        "kind": "recall",
        "trigger": "16:10",
        "enabled": True,
    },
    {
        # DEC-087：提醒引擎终检——用户自配规则按收盘价再判一遍，命中即收件箱 +
        # 出站渠道。默认关：先让用户用 `ldst alert run` / `/api/alert/run` 手动跑顺
        # 再开定时；盘中巡检把 trigger 改成间隔形态（如 "300s"）。
        "id": "alert_close",
        "name": "15:05 收盘提醒终检",
        "kind": "alert_check",
        "trigger": "15:05",
        "enabled": False,
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
        if kind == "agent_research" and not str(job.get("question") or "").strip():
            problems.append(f"{where}/question: agent_research 必须声明研究问题")
        alert_pct = job.get("alert_pct")
        if kind == "watch_alert" and alert_pct is not None:
            if not isinstance(alert_pct, (int, float)) or float(alert_pct) <= 0:
                problems.append(f"{where}/alert_pct: 需要正数（百分比）")
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
    # 拿不到锁不是「数据没了」，是「另一轮同步正占着」：等得久一点，撞满一轮
    # 补数就再等一次。只有真的等不到才落 failed，并说清是谁占着。
    payload: dict[str, Any] | None = None
    last_error: LadderStockError | None = None
    for attempt in range(2):
        try:
            with writer_lock(
                root, holder="ldst schedule sync_close", timeout=CLOSE_SYNC_LOCK_WAIT_SEC
            ):
                payload = sync_module.run_sync(registry, store, list(CLOSE_SYNC_DATASETS))
            last_error = None
            break
        except LadderStockError as exc:
            last_error = exc
            if exc.code != EXIT_DB_BUSY or attempt == 1:
                break
            _log_schedule(f"收盘同步等待写锁超时，{CLOSE_SYNC_RETRY_SEC:.0f}s 后重试一次")
            time.sleep(CLOSE_SYNC_RETRY_SEC)
    if payload is None:
        detail = f"收盘同步中止：{last_error.message if last_error else '未知错误'}"
        if last_error is not None and last_error.code == EXIT_DB_BUSY:
            detail += "（锁被另一轮同步占着，等满两轮仍未拿到；下一轮调度会再跑）"
        return {**base, "status": "failed", "detail": detail}

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


def _wire_unavailable_sources(wire_dir: Path, day: Any, *, limit: int = 60) -> list[dict[str, Any]]:
    """从 Wire 当天的归档里读出「哪几个源不可用」，而不是只给一个数量。

    `qfw run --output json` 的摘要只有 `sources_unavailable` 计数；逐源状态写在
    `data/archive/<day>/sources.json`（Wire 自己落的盘，含 source_id / status /
    error）。读它不需要改 Wire，也不打任何网络请求。

    只读、失败即返回 []：这是增强信息，读不到不能拖垮采集任务本身。
    """
    if not day:
        return []
    path = Path(wire_dir) / "data" / "archive" / str(day) / "sources.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status == "ok":
            continue
        rows.append(
            {
                "id": str(item.get("source_id") or ""),
                "name": str(item.get("source_name") or ""),
                "status": status,
                "error": _one_line(str(item.get("error") or ""))[:80],
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _collect_wire_news() -> dict[str, Any]:
    """触发 Wire 自己的采集+提炼（`qfw run`），返回 {status, detail, sources_unavailable}。

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
    unavailable = _wire_unavailable_sources(wire_dir, summary.get("day"))
    if unavailable:
        names = [str(item.get("id") or "?") for item in unavailable]
        shown = "、".join(names[:3])
        detail += f"；不可用源 {len(unavailable)} 个：{shown}"
        if len(names) > 3:
            detail += f" 等（详见记录里的 sources_unavailable）"
    if status == "degraded":
        detail += "；部分源不可用，如实记 degraded"
    return {"status": status, "detail": detail, "sources_unavailable": unavailable}


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
        "sources_unavailable": collect.get("sources_unavailable") or [],
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


def _run_sync_evening(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """收盘后晚间补取（`sync_evening`）：把当天晚些才公布的数据集拉回来。

    目前是龙虎榜：交易所按规则**收盘后才公布**（通常 18:00 之后），15:35 的收盘
    同步必然拿不到——所以复盘页那一段在傍晚之前为空是正常时序，不是本机缺数。
    这条任务在 18:30 再来一趟，取到就落当日分区，取不到就记 `degraded` 并说清原因
    （不补造、不拿上一天顶替）。

    `job.datasets` 逗号分隔；缺省 `dragon_tiger,dragon_tiger_seats`。
    逐条走 `sync.sync_one`，一条失败不影响其余条；只有全都失败才整条判 `failed`。
    """
    from ladderstock.core import intraday

    moment = intraday.local_moment(now, timezone)
    day = moment.date()
    base = {"date": day.isoformat()}
    if day.weekday() >= 5:
        return {**base, "status": "skipped", "detail": "非交易日（周末），不跑", "quiet": True}

    raw = str(job.get("datasets") or "dragon_tiger,dragon_tiger_seats")
    datasets = [item.strip() for item in raw.split(",") if item.strip()]
    if not datasets:
        return {**base, "status": "failed", "detail": "sync_evening 缺少 datasets"}

    from ladderstock.cli import sync as sync_module
    from ladderstock.data import watermark
    from ladderstock.data.db_lock import writer_lock
    from ladderstock.data.provider_registry import ProviderRegistry

    if registry is None:
        registry = ProviderRegistry(root=root)
    store = watermark.WatermarkStore(root=getattr(registry, "root", root))

    parts: list[str] = []
    statuses: list[str] = []
    try:
        with writer_lock(
            root, holder="ldst schedule sync_evening", timeout=CLOSE_SYNC_LOCK_WAIT_SEC
        ):
            for dataset in datasets:
                row = sync_module.sync_one(registry, store, dataset)
                mapped = {
                    "ok": "ok",
                    "degraded": "degraded",
                    "empty": "degraded",
                    "skipped": "skipped",
                    "failed": "failed",
                }.get(str(row.get("status") or ""), "degraded")
                statuses.append(mapped)
                detail = str(row.get("detail") or row.get("source") or "").strip()
                parts.append(f"{dataset}={mapped}{f'（{detail}）' if detail else ''}")
    except LadderStockError as exc:
        return {**base, "status": "failed", "detail": f"晚间补取中止：{exc.message}"}

    if all(item == "ok" for item in statuses):
        status = "ok"
    elif any(item == "ok" for item in statuses):
        status = "degraded"
    else:
        status = "failed"
    return {**base, "status": status, "detail": "；".join(parts), "datasets": datasets}


def _dataset_list(value: Any) -> list[str]:
    """`datasets` 两种写法都收：逗号分隔字符串（与 `sync_evening` 同构）或数组。"""
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _news_pool(registry: Any) -> list[str]:
    """公告/研报用的代码池：`sync.yaml` 的 `*base_pool` 锚点（与逐只数据集共用一份）。

    不读 `journal.sqlite` 的自选：资讯采集是产品级动作，不该随某个账户的自选表变。
    """
    declared = ((getattr(registry, "datasets", None) or {}).get("daily_k") or {})
    return [str(code).strip() for code in declared.get("codes") or [] if str(code).strip()]


def _run_news_collect(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """资讯雷达批量采集：公告 / 研报按代码池分批，快讯按栏目整批。

    这条任务的由来：公告栏目（`announcement_cninfo` / `announcement_szse`）上游要求
    `code` 必填，而排班里的批量入口从来没传 code，于是每轮 `skipped`、`news_items`
    里一条公告都没有（`news_runs.detail` 原话：`需要指定 code（--code <code>[,<code>]）`）。
    研报同理：采到了 100 条/轮，却只进 `news_items`、不进 `data/archive` 与历史库，
    查询层拿不到。所以这里做两件事、都不新开采集通道（仍走 TickHub peer 缓存）：

    1. **分批传 code**：代码池按 `batch_size`（默认 100）切片，一批一次 `collect_stream`；
       不带 code 的栏目（快讯 / 新闻联播）只调一次，不跟批次重复打 peer。
    2. **落归档**：`collect_stream(persist=True)` 现在会同时写 `news.sqlite` 与
       `data/archive` + `market.duckdb`（见 `news/stream.py:_archive_items`）。

    `enabled: false` 时调度器不会跑到它；本函数本身不发任何网络请求之外的副作用，
    也不改 `sync.yaml` 的代码池。
    """
    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday
    from ladderstock.core.boundary import NEWS_STREAM_DATASETS
    from ladderstock.news import candidates as news_candidates
    from ladderstock.news import stream as news_stream

    moment = intraday.local_moment(now, timezone)
    day = moment.date()

    datasets = _dataset_list(job.get("datasets"))
    if not datasets:
        return {
            "status": "skipped",
            "date": day.isoformat(),
            "detail": "news_collect 没有配置 datasets（公告/研报/快讯栏目名），未发起调用",
        }

    unknown = [d for d in datasets if d not in NEWS_STREAM_DATASETS]
    if unknown:
        return {
            "status": "failed",
            "date": day.isoformat(),
            "detail": (
                f"未登记的资讯栏目：{', '.join(unknown)}；"
                f"可选值见 core/boundary.py 的 NEWS_STREAM_DATASETS"
            ),
        }

    batch_size = max(1, int(job.get("batch_size") or NEWS_COLLECT_BATCH))
    limit = max(1, int(job.get("limit") or 100))

    code_datasets = [d for d in datasets if d in news_stream.CODE_SCOPED_DATASETS]
    plain_datasets = [d for d in datasets if d not in news_stream.CODE_SCOPED_DATASETS]
    # 公告逐票源吃前置筛（池 ∪ 当日异动，见 news/candidates.py）；研报/个股新闻仍按全池。
    ann_datasets = [d for d in code_datasets if d in news_candidates.ANNOUNCEMENT_DATASETS]
    other_code_datasets = [d for d in code_datasets if d not in news_candidates.ANNOUNCEMENT_DATASETS]

    batches: list[tuple[list[str], list[str]]] = []
    pool: list[str] = []
    cand: dict[str, Any] | None = None
    if code_datasets:
        pool = _news_pool(registry) if registry is not None else []
        if not pool:
            return {
                "status": "skipped",
                "date": day.isoformat(),
                "detail": (
                    f"代码池为空（config/sync.yaml 的 base_pool 未配置），"
                    f"{', '.join(code_datasets)} 需要 code 才能取，未发起调用"
                ),
            }
    ann_codes: list[str] = []
    use_filter = True
    data_root = Path(root) if root is not None else data_dir()
    if ann_datasets:
        use_filter = bool(job.get("candidate_filter", True))
        cand = news_candidates.announcement_candidates(pool, data_root=data_root)
        # 只请求异动票：池内异动 + 池外异动。这才是前置筛省请求的地方——
        # 池内当天没异动的票按策略不查，但仍留在分母里（分母 = 池 ∪ 池外异动），
        # 覆盖率不会被 100% 掩盖成「都查过了」。
        # 护栏：没有本机行情快照（或读失败/过期）时 candidates 已标 fallback，
        # 前置筛不能把采集变成一只都不采——回退全池。
        if use_filter and not cand["fallback"]:
            ann_codes = [str(item["code"]) for item in cand["movers"]]
        else:
            ann_codes = pool
        for start in range(0, len(ann_codes), batch_size):
            batches.append((ann_datasets, ann_codes[start : start + batch_size]))
    if other_code_datasets:
        for start in range(0, len(pool), batch_size):
            batches.append((other_code_datasets, pool[start : start + batch_size]))
    if plain_datasets:
        batches.append((plain_datasets, []))

    total_items = 0
    total_stored = 0
    total_archived = 0
    ok_datasets: list[str] = []
    problems: list[str] = []
    # 逐票结果只对公告有意义：`{code: 本轮取回条数}`。0 条也是"采过"，
    # 所以不拿它当覆盖率的分子，而是拿"哪些 code 出现在这里"当已采。
    collected: dict[str, int] = {}

    for group, codes in batches:
        try:
            result = news_stream.collect_stream(
                start=day,
                end=day,
                datasets=group,
                codes=codes,
                registry=registry,
                root=root,
                persist=True,
                limit=limit,
            )
        except LadderStockError as exc:
            problems.append(f"{'/'.join(group)}（{len(codes)} 只）：{' '.join(str(exc).split())[:160]}")
            continue

        total_items += len(result.items)
        total_stored += result.stored
        archive = result.archive or {}
        total_archived += int(archive.get("written") or 0)
        for status in result.statuses:
            if status.dataset in news_candidates.ANNOUNCEMENT_DATASETS:
                for code, count in (status.per_code or {}).items():
                    collected[code] = max(collected.get(code, 0), int(count))
            if status.status == "ok":
                ok_datasets.append(status.dataset)
            else:
                problems.append(
                    f"{status.dataset}：{status.status}"
                    + (f"（{status.detail}）" if status.detail else "")
                )
        for err in archive.get("errors") or []:
            problems.append(f"归档失败 {err}")

    scope = (
        f"{len(code_datasets)} 个代码栏目（代码池 {len(pool)} 只 / 每批 {batch_size} 只）"
        if code_datasets
        else ""
    )
    if plain_datasets:
        scope += ("，" if scope else "") + f"{len(plain_datasets)} 个整批栏目"
    detail = (
        f"{scope}，{len(batches)} 批：采到 {total_items} 条，落库 {total_stored} 条，"
        f"归档 {total_archived} 行"
    )
    ann_info: dict[str, Any] | None = None
    if cand is not None:
        denominator = len(cand.get("union") or pool)
        extra = list(cand.get("extra") or [])
        skipped = max(0, denominator - len(collected))
        ann_info = {
            "note": str(cand.get("note") or ""),
            "pool_size": int(cand.get("pool_size") or 0),
            "union_size": denominator,
            "union": list(cand.get("union") or pool),
            "requested": len(ann_codes),
            "extra": extra,
            "extra_total": len(extra),
            "snapshot_day": cand.get("snapshot_day"),
            "threshold": cand.get("threshold"),
            "filtered": bool(use_filter and not cand.get("fallback")),
            "fallback_reason": str(cand.get("fallback_reason") or ""),
            "covered": len(collected),
            "skipped": skipped,
        }
        if not use_filter:
            detail += "；公告前置筛已关闭（candidate_filter: false），按全池采"
        elif cand.get("fallback"):
            detail += (
                f"；公告前置筛未生效（{cand.get('fallback_reason') or '无当日异动'}），"
                f"回退全池 {len(pool)} 只"
            )
        elif not ann_codes:
            detail += (
                f"；公告候选：{cand.get('note')}，当天没有异动票，"
                f"两个公告源按策略跳过（0 请求），分母仍是 {denominator} 只"
            )
        else:
            detail += (
                f"；公告候选：{cand.get('note')}，本轮只请求异动票 {len(ann_codes)} 只，"
                f"已采 {len(collected)} 只（分母 {denominator} 只，其余按策略未采）"
            )
        news_candidates.write_candidates(
            data_root,
            {
                "day": day.isoformat(),
                **ann_info,
                "ann_datasets": ann_datasets,
                "collected": collected,
                "movers": list(cand.get("movers") or [])[:60],
                "requested_codes": ann_codes,
            },
        )
    if problems:
        detail += f"；{len(problems)} 处异常：{'；'.join(problems)[:400]}"

    if not batches:
        status = "skipped"
    elif problems and not total_stored:
        status = "failed"
    elif problems:
        status = "degraded"
    elif not total_items:
        status = "empty"
    else:
        status = "ok"

    return {
        "status": status,
        "date": day.isoformat(),
        "detail": detail,
        "items": total_items,
        "stored": total_stored,
        "archived": total_archived,
        "batches": len(batches),
        "datasets_ok": sorted(set(ok_datasets)),
        "problems": problems[:20],
        "ann_candidates": ann_info,
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


def _run_brief(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """F11 简报（晨报 / 收盘复盘 / 持仓异动）：生成 → 指纹去重 → 收件箱 →（可选）投递。

    与 `_run_digest` 的三处差别：
    1. 一份简报 = 多条条目，每条按**内容指纹**去重（晨报和收盘复盘共用的那段行情
       只推一次），不是 `job:day`；
    2. `notify` 优先看 job 字段，再看 `config/brief.yaml` 的开关——默认只落收件箱；
    3. 全部条目都在同日推过时 `status=skipped`，detail 写明「已推过 N 条」，不重发。
    """
    from ladderstock.watch import brief as brief_module

    kind = str(job.get("summary") or job.get("brief") or "").strip()
    threshold = job.get("threshold")
    settings = brief_module.load_settings()
    try:
        value = float(threshold) if threshold is not None else settings["threshold"]
    except (TypeError, ValueError):
        value = settings["threshold"]
    outcome = brief_module.build(
        kind,
        now=now,
        timezone=timezone,
        root=root,
        registry=registry,
        threshold=value,
    )
    base = {"date": outcome.trade_day, "summary": kind, "brief": kind}
    if outcome.status != brief_module.STATUS_OK:
        # skipped（周末无持仓等）不写库：不给用户塞一条空简报
        status = "skipped" if outcome.status == brief_module.STATUS_SKIPPED else "failed"
        return {**base, "status": status, "detail": outcome.detail}

    notify = job.get("notify")
    if notify is None:
        notify = settings["notify"]
    channel = job.get("channel") or settings["channel"]
    published = brief_module.publish(
        outcome,
        root=root,
        notify=bool(notify),
        channel=channel if notify else None,
        now=now,
    )
    if not published["pushed"]:
        return {
            **base,
            "status": "skipped",
            "detail": f"{outcome.title} 的 {len(published['skipped'])} 条今日已推送过，本轮不重发",
            "skipped": len(published["skipped"]),
        }
    delivered = published["delivered"]
    failed = [row for row in delivered if not row.get("ok")]
    status = "degraded" if failed else "ok"
    detail = (
        f"{outcome.title}：推送 {len(published['pushed'])} 条"
        f" · 跳过（同日已推）{len(published['skipped'])} 条"
    )
    if delivered:
        detail += "；通知 " + "、".join(
            f"{row['channel']}={'成功' if row['ok'] else row['detail']}" for row in delivered
        )
    return {
        **base,
        "status": status,
        "detail": detail,
        "pushed": len(published["pushed"]),
        "skipped": len(published["skipped"]),
        "delivery": delivered,
        "state_path": published["state_path"],
    }


def _run_agent_research(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """定时研究（第④步）：把这一轮研究交给 agent，落研究卡 + 收件箱，再投递通知。

    与 `digest` 的区别在于**谁来回答**：digest 是代码拼好的固定摘要，这里是模型自己
    选工具、自己定轮次。取数只走 agent 工具（读本机 DuckDB），所以本任务不需要
    registry，也不出网取行情。

    - `symbols` 留空 = 一轮市场视角的研究；给了标的就逐只问一次；
    - 产物先落 `data/research_digest/<date>/<job>.json`（同日重跑覆盖，幂等），
      再写收件箱、投通知——页面永远比通知可靠；
    - 单标的失败记进 `failed[]`，全失败才折成 `failed`，不让一条任务拖垮调度器。
    """
    import asyncio
    import json as json_module

    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.watch import notifier
    from ladderstock.web import assistant as assistant_module

    moment = intraday.local_moment(now, timezone)
    day = moment.date().isoformat()
    job_id = str(job.get("id") or "")
    question = str(job.get("question") or "").strip()
    base: dict[str, Any] = {"date": day, "question": question}
    if not question:
        return {
            **base,
            "status": "failed",
            "detail": "缺少 question：定时研究必须写明这一轮要研究什么",
        }

    data_root = Path(root) if root is not None else data_dir()
    symbols = [str(item).strip() for item in (job.get("symbols") or []) if str(item).strip()]
    targets: list[str | None] = list(symbols) or [None]
    context = (
        "本机缓存的最新交易日数据。只回答工具能取到的部分；取不到就说没取到，不要用记忆补数字。"
    )

    answers: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for symbol in targets:
        try:
            payload = asyncio.run(
                assistant_module.ask_agent(
                    question=question,
                    context=context,
                    symbol=symbol,
                    page="research",
                    page_label="定时研究",
                    data_root=data_root,
                    sync_memory=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单标的失败不影响其余
            failed.append({"symbol": symbol, "reason": _one_line(exc)})
            continue
        answers.append(
            {
                "symbol": symbol,
                "reply": str(payload.get("reply") or ""),
                "model": payload.get("model"),
                "as_of_sources": payload.get("sources") or [],
                "used_tools": bool(payload.get("used_tools")),
                "fetched_at": payload.get("fetched_at"),
            }
        )

    out_path = data_root / "research_digest" / day / f"{job_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json_module.dumps(
            {"id": job_id, "date": day, "question": question, "answers": answers, "failed": failed},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not answers:
        return {
            **base,
            "status": "failed",
            "detail": f"全部标的都没跑出结果：{failed[:3]}",
            "failed": failed,
            "path": str(out_path),
        }

    lines: list[str] = []
    for item in answers:
        head = item["symbol"] or "整体"
        body = _one_line(item["reply"]) if item["reply"] else "（模型没有给出正文）"
        lines.append(f"- {head}：{body[:400]}")

    title = f"定时研究：{job.get('name') or job_id}（{day}）"
    dedupe = f"agent_research:{job_id}:{day}"
    item_id = inbox_store.add_item(
        job_id,
        str(job.get("name") or ""),
        title,
        lines,
        "/research",
        dedupe,
        "agent_research",
        root=data_root,
        now=now,
    )
    delivery = notifier.send(job.get("channel"), title, "\n".join(lines))
    status = "ok" if delivery.ok and not failed else "degraded"
    detail = (
        f"研究卡已落 {out_path}（{len(answers)} 份回答）；收件箱 {item_id['id']}；"
        f"通知[{delivery.channel}]：{delivery.detail}"
    )
    if failed:
        detail += f"；失败 {len(failed)} 个标的"
    return {
        **base,
        "status": status,
        "detail": detail,
        "inbox_id": item_id["id"],
        "path": str(out_path),
        "answered": len(answers),
        "failed": failed,
        "delivery": delivery.as_dict(),
    }


def _run_watch_alert(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """盯盘提醒（第④步）：自选在本机报价快照里越过阈值时写一条收件箱提醒。

    数据只读 `symbol_basic`（全市场字典，带 price / prev_close），不联网、不直打上游。
    快照日期不是今天时，提醒正文里**如实标出数据日期**——盘中提醒宁可写「11-16 收盘」，
    也不冒充实时价。阈值取任务上的 `alert_pct`（默认 5），涨跌都算。
    """
    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday
    from ladderstock.data import duckdb_store, symbol_universe
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.journal import watchlist as watchlist_store
    from ladderstock.watch import notifier

    moment = intraday.local_moment(now, timezone)
    day = moment.date().isoformat()
    job_id = str(job.get("id") or "")
    data_root = Path(root) if root is not None else data_dir()
    threshold = abs(float(job.get("alert_pct") or 5.0))
    base: dict[str, Any] = {"date": day, "alert_pct": threshold}

    codes = sorted(
        {str(row.get("code") or "").strip() for row in watchlist_store.list_watch(root=root)} - {""}
    )
    if not codes:
        return {**base, "status": "skipped", "detail": "自选为空，没有需要盯的票"}

    snapshot_day = symbol_universe.latest_trade_date(root=data_root)
    if snapshot_day is None:
        return {
            **base,
            "status": "skipped",
            "detail": "本机没有报价快照（symbol_basic 为空），不编价格",
        }
    rows = duckdb_store.query_dataset("symbol_basic", snapshot_day, root=data_root)
    by_code = {str(row.get("code") or ""): row for row in rows}

    hits: list[str] = []
    missing: list[str] = []
    for code in codes:
        row = by_code.get(code)
        if row is None:
            missing.append(code)
            continue
        price = row.get("price")
        prev = row.get("prev_close")
        if price is None or prev is None:
            missing.append(code)
            continue
        try:
            price_f = float(price)
            prev_f = float(prev)
        except (TypeError, ValueError):
            missing.append(code)
            continue
        if prev_f <= 0:
            missing.append(code)
            continue
        change = (price_f - prev_f) / prev_f * 100
        if abs(change) >= threshold:
            arrow = "↑" if change > 0 else "↓"
            hits.append(
                f"- {row.get('name') or code}（{code}）{price_f:.2f} 元，"
                f"{arrow} {change:+.2f}%（阈值 {threshold:.0f}%；"
                f"数据日期 {snapshot_day.isoformat()}）"
            )

    if not hits:
        detail = f"{len(codes)} 只自选里没有越过 {threshold:.0f}% 的票"
        if missing:
            detail += f"；{len(missing)} 只本机无报价快照（{','.join(missing[:5])}）"
        return {**base, "status": "ok", "detail": detail, "alerted": 0, "missing": missing}

    title = f"盯盘提醒：{job.get('name') or job_id}（{snapshot_day.isoformat()}）"
    lines = [
        f"本机报价快照日期 {snapshot_day.isoformat()}（不是分时实时价）：",
        *hits,
    ]
    dedupe = f"watch_alert:{job_id}:{day}"
    item_id = inbox_store.add_item(
        job_id,
        str(job.get("name") or ""),
        title,
        lines,
        "/watchlist",
        dedupe,
        "watch_alert",
        root=data_root,
        now=now,
    )
    delivery = notifier.send(job.get("channel"), title, "\n".join(lines))
    status = "ok" if delivery.ok else "degraded"
    return {
        **base,
        "status": status,
        "detail": (
            f"{len(hits)} 只越过阈值；收件箱 {item_id['id']}；"
            f"通知[{delivery.channel}]：{delivery.detail}"
        ),
        "alerted": len(hits),
        "missing": missing,
        "inbox_id": item_id["id"],
        "delivery": delivery.as_dict(),
    }


def _in_trading_window(moment: Any) -> bool:
    """A 股连续竞价时段：09:30–11:30 / 13:00–15:00，周末不开盘。

    30 秒级任务只在盘内真取数，盘外直接 skip——否则一天要白跑上千次上游调用，
    既烧额度又让"提醒"在下班后响起来。
    """
    if getattr(moment, "weekday", lambda: 0)() >= 5:
        return False
    minutes = moment.hour * 60 + moment.minute
    return (9 * 60 + 30) <= minutes <= (11 * 60 + 30) or (13 * 60) <= minutes <= (15 * 60)


def _board_limit_pct(code: str, name: str | None) -> float:
    """按板块推涨停幅度：ST 5%、创业板/科创 20%、北交所 30%、其余主板 10%。"""
    upper = (name or "").upper()
    if "ST" in upper:
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4", "92")):
        return 30.0
    return 10.0


def _limit_price_from_prev(prev_close: float | None, code: str, name: str | None) -> float | None:
    """用昨收推当日涨停价：`昨收 × (1 + 幅度)`，四舍五入到分。

    当日涨停池还没同步（盘中就是这种情况）时，这是**唯一**能给出封板判定基准的办法；
    取不到昨收就返回 None，宁可判不了封板，也不拿一个编出来的价当基准。
    """
    if prev_close is None or prev_close <= 0:
        return None
    pct = _board_limit_pct(code, name)
    return round(prev_close * (1 + pct / 100.0), 2)


def _sample_pool_codes(
    data_root: Path, day: str, *, limit: int = 40
) -> tuple[str, list[str], dict[str, str]]:
    """自选 / 持仓为空时的兜底样本源：**最近一个交易日**的涨停池。

    新用户一只自选都没加时，盘中采集不该整条 skipped——那样 /watch 永远等不到样本。
    取最近的涨停池（而不是当日）是因为当日涨停池要 15:35 才同步；这些票正是今天
    最可能继续封板/炸板的一批，用来喂样本最合适。

    返回 `(实际用的日期, codes, {code: name})`；找不到就返回 `("", [], {})`。
    """
    from datetime import date as date_cls
    from datetime import timedelta

    from ladderstock.data import archive

    anchor = date_cls.fromisoformat(day)
    for back in range(1, 8):  # 最多回溯 7 天，覆盖周末与假期
        probe = (anchor - timedelta(days=back)).isoformat()
        try:
            snap = archive.snapshot("limit_pool", probe, root=data_root)
        except Exception:  # noqa: BLE001 - 该日没有缓存就继续往前找
            continue
        records = getattr(snap, "records", None) or []
        codes: list[str] = []
        names: dict[str, str] = {}
        for row in records:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip().partition(".")[0][-6:]
            if not code or not code.isdigit():
                continue
            codes.append(code)
            name = str(row.get("name") or "").strip()
            if name:
                names[code] = name
        if codes:
            return probe, codes[:limit], names
    return "", [], {}


def _run_watch_live(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """盘中实时盯盘（30 秒级）：自选的**实时报价**越过阈值就提醒。

    与 `watch_alert`（每日快照）的区别是数据口径：这里每次现取 `spot_quote`，
    拿的是此刻的价，不是缓存里那一天的收盘。三条提醒规则，都写进正文：

    1. 相对昨收的涨跌幅越过 `alert_pct`（默认 3%）；
    2. 相对上一次快照的变动越过 `move_pct`（默认 1%，抓盘中急拉急杀）；
    3. 同一只票 `min_gap_min` 分钟内只响一次（默认 30），避免 30 秒刷屏。

    上一次快照落 `data/watch_live_state.json`，同一天内沿用；跨日自动重置。
    """
    import json as json_module

    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday
    from ladderstock.data.provider_registry import ProviderRegistry
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.journal import position as position_store
    from ladderstock.journal import watchlist as watchlist_store
    from ladderstock.watch import bars as bars_store
    from ladderstock.watch import notifier

    moment = intraday.local_moment(now, timezone)
    day = moment.date().isoformat()
    job_id = str(job.get("id") or "")
    data_root = Path(root) if root is not None else data_dir()
    alert_pct = abs(float(job.get("alert_pct") or 3.0))
    move_pct = abs(float(job.get("move_pct") or 1.0))
    min_gap_min = abs(float(job.get("min_gap_min") or 30.0))
    base: dict[str, Any] = {"date": day, "alert_pct": alert_pct, "move_pct": move_pct}

    if not _in_trading_window(moment):
        return {
            **base,
            "status": "skipped",
            "detail": "非交易时段（09:30–11:30 / 13:00–15:00 才盯盘）",
        }

    # 盯盘面 = 自选 ∪ 持仓：持仓票比自选更需要盯异动，样本也要留给扫描器。
    # 自选与持仓都为空时**回退到最近交易日的涨停池**：否则新用户盘中一条样本都没有，
    # /watch 永远「等取数器落盘」。这类兜底票只落样本、不逐票发提醒——几百只票每轮
    # 都推 inbox 会把信号流淹没（用户要的是「涨停池只喂封板判定、不逐票产信号」）。
    codes = sorted(
        (
            {str(row.get("code") or "").strip() for row in watchlist_store.list_watch(root=root)}
            | {
                str(row.get("code") or "").strip()
                for row in position_store.list_positions(root=data_root)
            }
        )
        - {""}
    )
    pool_day = ""
    pool_names: dict[str, str] = {}
    sample_only: set[str] = set()
    if not codes:
        pool_day, pool_codes, pool_names = _sample_pool_codes(data_root, day)
        sample_only = set(pool_codes)
        codes = pool_codes
    if not codes:
        return {
            **base,
            "status": "skipped",
            "detail": "自选与持仓都为空，最近的涨停池也读不到，没有可采的样本",
        }

    prefix_of = {"6": "sh", "0": "sz", "3": "sz", "8": "bj", "4": "bj", "9": "bj"}
    param_codes = ",".join(prefix_of.get(code[0], "sh") + code for code in codes)
    try:
        outcome = ProviderRegistry(root=data_root).fetch_live(
            "spot_quote",
            params={"codes": param_codes, "max_codes": len(codes)},
            allow_cache=False,
            timeout=8.0,
        )
    except Exception as exc:  # noqa: BLE001 - 取不到就不能编价格
        detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
        return {**base, "status": "failed", "detail": f"实时报价未取到（{detail}）"}
    payload = getattr(getattr(outcome, "envelope", None), "payload", {}) or {}
    rows = [
        row
        for row in (payload.get("records") or payload.get("rows") or [])
        if isinstance(row, dict)
    ]
    if not rows:
        return {**base, "status": "failed", "detail": "实时报价上游返回 0 行"}

    state_path = data_root / "watch_live_state.json"
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            loaded = json_module.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("date") == day:
                state = loaded.get("items") or {}
        except (OSError, ValueError):
            state = {}

    now_ts = moment.timestamp()
    hits: list[str] = []
    hit_codes: list[str] = []
    bars_rows: list[dict[str, Any]] = []

    def _limit_price_map() -> dict[str, float]:
        """当日涨停池里的涨停价，**只喂样本做封板/开板判定**，不额外产信号。

        只认当日：涨停价是当日概念，拿昨天的价判今天的封板就是给错基准。
        涨跌幅再做一次兜底校验（ST 5% / 主板 10% / 双创 20% / 北交所 30%），
        取最宽的下限 4.5%，防上游给错行。
        """
        from ladderstock.data import archive

        try:
            snap = archive.snapshot("limit_pool", day, root=data_root)
        except Exception:  # noqa: BLE001 - 涨停池读不到就不带涨停价，不阻断盯盘
            return {}
        prices: dict[str, float] = {}
        for row in getattr(snap, "records", None) or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip()
            raw_price = row.get("price")
            raw_change = row.get("change_pct")
            if raw_price is None or raw_change is None:
                continue
            try:
                price = float(raw_price)
                change = float(raw_change)
            except (TypeError, ValueError):
                continue
            if code and price > 0 and change >= 4.5:
                prices[code] = price
        return prices

    limit_prices = _limit_price_map()

    def _bare(code: str) -> str:
        return code.partition(".")[0].lstrip("shSHzsbj")[-6:]  # noqa: B005

    for row in rows:
        code = _bare(str(row.get("code") or ""))
        if not code:
            continue
        raw_price = row.get("price")
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
            raw_prev = row.get("prev_close")
            prev = float(raw_prev) if raw_prev is not None else None
        except (TypeError, ValueError):
            continue
        prev_snap = state.get(code) or {}
        last_price = prev_snap.get("price")
        last_alert_ts = float(prev_snap.get("alert_ts") or 0)
        reasons: list[str] = []
        # 兜底样本（涨停池）只采数据、不产信号：提醒只对自选 / 持仓生效
        if code not in sample_only:
            change_prev = ((price - prev) / prev * 100) if prev and prev > 0 else None
            if change_prev is not None and abs(change_prev) >= alert_pct:
                reasons.append(f"较昨收 {change_prev:+.2f}%（阈值 {alert_pct:.0f}%）")
            if last_price and last_price > 0:
                move = (price - float(last_price)) / float(last_price) * 100
                if abs(move) >= move_pct:
                    reasons.append(f"较上次快照 {move:+.2f}%（阈值 {move_pct:.0f}%）")
        throttled = (now_ts - last_alert_ts) < min_gap_min * 60
        if reasons and not throttled:
            hits.append(
                f"- {row.get('name') or code}（{code}）{price:.2f} 元，"
                + "；".join(reasons)
                + f"（{moment.strftime('%H:%M:%S')} 现取实时报价）"
            )
            hit_codes.append(code)
            prev_snap["alert_ts"] = now_ts
        prev_snap["price"] = price
        prev_snap["at"] = moment.isoformat(timespec="seconds")
        state[code] = prev_snap
        # 顺手记成分钟样本：盯盘扫描器的唯一输入就在这里产生（`watch/bars.py`）。
        # 这一份是**此刻现取的价**，落盘时还会按「必须是当日」再校验一次。
        bar: dict[str, Any] = {"ts": prev_snap["at"], "code": code, "price": price}
        # 名称只为 `/watch` 显示：实时报价里有就用它，兜底样本用涨停池里的名字。
        # 两者都没有就不写这个键（页面显示 `—`，不拿代码顶名称）。
        bar_name = str(row.get("name") or pool_names.get(code) or "").strip()
        if bar_name:
            bar["name"] = bar_name
        limit_price = row.get("limit_price")
        if limit_price is None:
            limit_price = row.get("limit_up_price")
        if limit_price is None:
            # 涨停池只提供「涨停价」，不提供样本：票本身仍按自选/持仓采，样本条数不受影响
            limit_price = limit_prices.get(code)
        if limit_price is None and prev is not None and code in sample_only:
            # 兜底样本来自涨停池：当日池还没同步（盘中常态），用昨收推涨停价，
            # 封板/开板判定才有基准。自选票不推算——不替普通票编一个涨停价。
            limit_price = _limit_price_from_prev(
                prev, code, pool_names.get(code) or str(row.get("name") or "")
            )
        if limit_price is not None:
            bar["limit_price"] = limit_price
        bars_rows.append(bar)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json_module.dumps({"date": day, "items": state}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 分钟样本：写入 `data/minute_bars/<day>.jsonl`，盯盘扫描器据此出信号。
    # 落盘失败不能让「已经比对完的提醒」丢掉，异常在这里就地收口。
    try:
        bars_result: dict[str, Any] = bars_store.append_minute_bars(day, bars_rows, root=data_root)
    except Exception as exc:  # noqa: BLE001 - 落盘问题单独报，不影响提醒
        bars_result = {"written": 0, "detail": f"分钟样本落盘失败：{exc}"}
    bars_note = f"；分钟样本 {bars_result.get('written', 0)} 条"
    pool_note = (
        f"（自选与持仓为空，用 {pool_day} 的涨停池 {len(sample_only)} 只兜底采集，"
        "只落样本、不逐票提醒）"
        if sample_only
        else ""
    )

    if not hits:
        detail = f"{len(rows)} 只票实时价比对完毕，没有触发提醒{pool_note}{bars_note}"
        return {
            **base,
            "status": "ok",
            "detail": detail,
            "alerted": 0,
            "sample_pool_day": pool_day,
            "sample_only": len(sample_only),
            "minute_bars": bars_result,
        }

    bucket = int(now_ts // (min_gap_min * 60))
    title = f"盘中盯盘：{job.get('name') or job_id}（{moment.strftime('%H:%M')}）"
    lines = [f"{moment.strftime('%H:%M:%S')} 现取实时报价（非缓存快照）：", *hits]
    item_id = inbox_store.add_item(
        job_id,
        str(job.get("name") or ""),
        title,
        lines,
        "/watchlist",
        f"watch_live:{job_id}:{day}:{bucket}",
        "watch_live",
        root=data_root,
        now=now,
    )
    delivery = notifier.send(job.get("channel"), title, "\n".join(lines))
    status = "ok" if delivery.ok else "degraded"
    # 提醒先落地，再谈自动研究：通知失败/研究失败都不能让「已经发现异动」这件事丢掉。
    research: dict[str, Any] | None = None
    if job.get("auto_research") and hit_codes:
        research = _auto_research_on_hit(
            job,
            codes=hit_codes,
            now=now,
            timezone=timezone,
            root=root,
            moment=moment,
        )
    detail = (
        f"{len(hits)} 只触发；收件箱 {item_id['id']}；通知[{delivery.channel}]："
        f"{delivery.detail}{bars_note}"
    )
    if research is not None:
        detail += f"；异动研究[{research.get('status')}]：{str(research.get('detail') or '')[:120]}"
    result_payload: dict[str, Any] = {
        **base,
        "status": status,
        "detail": detail,
        "alerted": len(hits),
        "inbox_id": item_id["id"],
        "delivery": delivery.as_dict(),
        "sample_pool_day": pool_day,
        "sample_only": len(sample_only),
        "minute_bars": bars_result,
    }
    if research is not None:
        result_payload["auto_research"] = research
    return result_payload


#: 一次异动最多自动研究几只：30 秒级任务里跑 agent 是重动作，宁可少跑几只
AUTO_RESEARCH_MAX_SYMBOLS = 2


def _run_sentiment_live(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """盘中实时情绪快照（每分钟一轮）：把「此刻的涨停 / 炸板 / 封板率 / 最高板」落盘。

    为什么单独一条任务：`limit_up_pool` 只给最近一个**已收盘**交易日，15:35 之前
    当天根本没有涨停池归档，情绪温度页只能靠实时读数。`market_sentiment` 是当日实时
    的，因此由这条任务把它钉到 `data/live/sentiment/<date>.json`，页面读得到、
    时间戳可查。落盘只写 live 目录，不碰 archive、不推水位——收盘口径仍由 15:35
    的 `sync_close` 负责，两者不会互相覆盖。
    """
    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday, live_limit_pool, live_sentiment
    from ladderstock.data.provider_registry import ProviderRegistry

    moment = intraday.local_moment(now, timezone)
    day = moment.date()
    data_root = Path(root) if root is not None else data_dir()
    base: dict[str, Any] = {"date": day.isoformat()}

    if not _in_trading_window(moment):
        return {
            **base,
            "status": "skipped",
            "detail": "非交易时段（09:30–11:30 / 13:00–15:00 才取实时情绪）",
            "quiet": True,
        }

    try:
        outcome = ProviderRegistry(root=data_root).fetch_live("sentiment", allow_cache=False)
        records = dict(outcome.envelope.payload.get("records") or {})
    except Exception as exc:  # noqa: BLE001 - 取数失败只记 degraded，守护进程要活着
        return {**base, "status": "degraded", "detail": f"实时情绪取数失败：{exc}"}

    # 守卫：接口给的必须是**今天**的读数，否则宁可这条不落（拿昨天的数当今天就是造假）。
    stamp = str(records.get("date") or "")
    if stamp and stamp != day.strftime("%Y%m%d"):
        return {
            **base,
            "status": "degraded",
            "detail": f"实时情绪返回的是 {stamp} 的读数，不是今天 {day:%Y%m%d}，不落盘",
        }
    if not isinstance(records.get("limit_up"), int):
        return {**base, "status": "degraded", "detail": "实时情绪没有涨停家数，不落盘"}

    path = live_sentiment.write_snapshot(
        day,
        records,
        fetched_at=moment,
        source=str(outcome.envelope.source or ""),
        root=data_root,
    )

    # 顺带落当日涨停池：盘中的 `limit_up_pool` 有时能给当日全集（含 streak），
    # 有了它晋级率与每一档家数就能真算，而不是只列龙头前 10 只。给不到当日就
    # 不落（守卫与情绪快照同一条：不接受别天的池冒充今天）。
    pool_note = ""
    pool_path: Path | None = None
    try:
        pool = ProviderRegistry(root=data_root).fetch(
            "limit_pool", params={"date": day.isoformat()}
        )
        pool_rows = [
            row for row in (pool.envelope.payload.get("records") or []) if isinstance(row, dict)
        ]
        if pool.envelope.data_as_of == day and pool_rows:
            pool_path = live_limit_pool.write_snapshot(
                day,
                pool_rows,
                fetched_at=moment,
                source=str(pool.envelope.source or ""),
                root=data_root,
            )
            pool_note = f"；当日涨停池 {len(pool_rows)} 只已落盘"
        elif pool_rows:
            pool_note = f"；涨停池给的是 {pool.envelope.data_as_of} 的，不是今天，未落盘"
    except Exception as exc:  # noqa: BLE001 - 涨停池落不下不影响情绪快照
        pool_note = f"；当日涨停池未落盘（{type(exc).__name__}）"

    return {
        **base,
        "status": "ok",
        "detail": (
            f"涨停 {records.get('limit_up')} / 炸板 {records.get('broken')} / 最高 "
            f"{records.get('max_streak')} 板；写入 {path.name}{pool_note}"
        ),
        "records": {
            "limit_up": records.get("limit_up"),
            "broken": records.get("broken"),
            "max_streak": records.get("max_streak"),
            "seal_rate": records.get("seal_rate"),
        },
        "limit_pool_path": str(pool_path) if pool_path is not None else None,
    }


def _auto_research_on_hit(
    job: Mapping[str, Any],
    *,
    codes: Sequence[str],
    now: datetime | None,
    timezone: str | None,
    root: Path | None,
    moment: datetime,
) -> dict[str, Any]:
    """命中即研究：把盘中异动的票交给 agent 跑一轮，结论另存一份。

    这是「条件触发」那条腿：此前盯盘只会响铃（写收件箱 + 投通知），发现异常之后
    还得人等开盘后自己去问。现在命中就把这几只票交给同一套 agent 工具链，让它用
    真实数据说明「发生了什么 / 可能的原因 / 接下来盯什么」，结论落到研究卡与收件箱。

    三条自我约束（重动作不能无节制）：

    1. **数量**：默认最多 `AUTO_RESEARCH_MAX_SYMBOLS` 只，可由
       `auto_research_max` 覆盖（1–5）。
    2. **冷却**：同一只票 `auto_research_cooldown_min`（默认 60）分钟内只研究一次，
       状态落 `data/auto_research_state.json`；与提醒的 `min_gap_min` 是两套口径——
       提醒可以 30 分钟响一次，研究不必跟着响一次。
    3. **不拖累提醒**：任何异常都折成 `status=failed` 返回，且冷却不推进，下一轮可以重试。
    """
    import json as json_module

    from ladderstock.cli.paths import data_dir

    data_root = Path(root) if root is not None else data_dir()
    cooldown_min = abs(float(job.get("auto_research_cooldown_min") or 60.0))
    limit = int(job.get("auto_research_max") or AUTO_RESEARCH_MAX_SYMBOLS)
    limit = max(1, min(5, limit))

    state_path = data_root / "auto_research_state.json"
    state: dict[str, float] = {}
    if state_path.is_file():
        try:
            loaded = json_module.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = {
                    str(key): float(value)
                    for key, value in loaded.items()
                    if isinstance(value, (int, float))
                }
        except (OSError, ValueError):
            state = {}

    now_ts = moment.timestamp()
    ready = [code for code in codes if now_ts - float(state.get(code) or 0.0) >= cooldown_min * 60]
    if not ready:
        return {
            "status": "skipped",
            "detail": f"命中票都在 {cooldown_min:.0f} 分钟冷却内，不重复研究",
        }
    ready = ready[:limit]

    question = str(job.get("auto_research_question") or "").strip() or (
        "这只票在盘中出现了异动。只用工具能取到的数据分析三件事："
        "一、现在发生了什么（价格、成交量、资金流）；二、可能的原因（公告、快讯、板块联动）；"
        "三、接下来该盯哪些指标。取不到的就直说没取到，不要用记忆补数字。"
    )
    temp_job: dict[str, Any] = {
        "id": f"{job.get('id') or 'watch_live'}:auto:{moment.strftime('%H%M')}",
        "name": f"异动研究·{moment.strftime('%H:%M')}",
        "question": question,
        "symbols": ready,
        "channel": job.get("channel"),
        "trigger": "auto",
    }
    try:
        result = _run_agent_research(temp_job, now=now, timezone=timezone, root=root)
    except Exception as exc:  # noqa: BLE001 - 研究失败不该影响已发出的提醒
        return {"status": "failed", "detail": _one_line(exc), "symbols": ready}

    for code in ready:
        state[code] = now_ts
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json_module.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": str(result.get("status") or "ok"),
        "detail": str(result.get("detail") or ""),
        "symbols": ready,
        "path": result.get("path"),
    }


def _run_plan_tick(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """跨轮任务推进：把「盯它一周」这类长期目标按频率各跑一轮。

    与 `agent_research` 的分工：那条问的是**表里写死的探索问题**（今天整体怎么样），
    这条问的是**用户上一轮亲口交待的目标**（盯住这只，变化了告诉我），并且把上次的
    结论带进判断——「和上次比有变化才说」。两条都调同一个 agent，只是问题来源不同。

    没有到期的任务时按 `skipped + quiet` 返回：间隔任务一天跑几十次，不该每次都写
    一条告警日志（会淹掉真正的异动）。
    """
    import asyncio
    import json as json_module

    from ladderstock.ai import plans as plans_module
    from ladderstock.cli.paths import data_dir
    from ladderstock.core import intraday
    from ladderstock.journal import inbox as inbox_store
    from ladderstock.watch import notifier
    from ladderstock.web import assistant as assistant_module

    moment = intraday.local_moment(now, timezone)
    day = moment.date().isoformat()
    job_id = str(job.get("id") or "plan_tick")
    data_root = Path(root) if root is not None else data_dir()
    min_gap_min = abs(float(job.get("min_gap_min") or 30.0))
    # 「推进时能不能新建任务」是显式开关，默认关：
    # 推进提问本身带「持续跟踪 600000…」，若允许建任务，每推进一步就繁殖一条同标任务，
    # 五天就把 MAX_ACTIVE 打满。只有明确需要在推进里续建任务时才打开（`allow_plan_create: true`）。
    allow_plan_create = bool(job.get("allow_plan_create") or False)
    base: dict[str, Any] = {"date": day, "plan_count": len(plans_module.active(data_root))}

    due = [
        plan for plan in plans_module.active(data_root) if plan.due(moment, min_gap_min=min_gap_min)
    ]
    if not due:
        return {**base, "status": "skipped", "detail": "没有到期的研究任务", "quiet": True}

    digest_dir = data_root / "research_digest" / day
    digest_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def runner(plan: Any) -> dict[str, Any]:
        payload = asyncio.run(
            assistant_module.ask_agent(
                question=plans_module.question_for(plan),
                context="跨轮任务。只用工具取到的数据；和上次比只讲变化，没变化就说「无变化」。",
                symbol=plan.symbols[0] if len(plan.symbols) == 1 else None,
                page="research",
                page_label="跨轮任务",
                data_root=data_root,
                sync_memory=False,
                # 见上方 `allow_plan_create`：默认关，避免推进自我繁殖任务。
                remember_plan=allow_plan_create,
            )
        )
        reply = str(payload.get("reply") or "").strip()
        out_path = digest_dir / f"plan-{plan.id}.json"
        out_path.write_text(
            json_module.dumps(
                {
                    "plan_id": plan.id,
                    "goal": plan.goal,
                    "symbols": plan.symbols,
                    "at": moment.isoformat(timespec="seconds"),
                    "reply": reply,
                    "sources": payload.get("sources") or [],
                    "used_tools": bool(payload.get("used_tools")),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        summary = " ".join(reply.split())
        lines.append(f"- {plan.symbol_label()}：{summary[:300]}")
        return {"status": "ok", "summary": summary, "path": str(out_path)}

    results = plans_module.tick(
        data_root=data_root, now=moment, runner=runner, min_gap_min=min_gap_min
    )
    failed = [item for item in results if item.get("status") == "failed"]
    if not lines:
        detail = f"到期的 {len(due)} 条任务都没跑出结果"
        return {**base, "status": "failed", "detail": detail, "results": results, "failed": failed}

    title = f"跨轮任务：{len(lines)} 条推进（{moment.strftime('%H:%M')}）"
    item_id = inbox_store.add_item(
        job_id,
        str(job.get("name") or ""),
        title,
        lines,
        "/research",
        f"plan_tick:{job_id}:{day}:{moment.strftime('%H%M')}",
        "plan_tick",
        root=data_root,
        now=now,
    )
    delivery = notifier.send(job.get("channel"), title, "\n".join(lines))
    status = "ok" if delivery.ok and not failed else "degraded"
    return {
        **base,
        "status": status,
        "detail": (
            f"{len(lines)} 条任务推进；收件箱 {item_id['id']}；"
            f"通知[{delivery.channel}]：{delivery.detail}"
        ),
        "results": results,
        "inbox_id": item_id["id"],
        "delivery": delivery.as_dict(),
    }


def _run_recall(*, root: Path | None = None) -> dict[str, Any]:
    """F9：到期观点扫一遍，拉日线判对错；缺行情留 open 等下一轮。

    与 `/api/assistant/recall` 走同一个 `recall.run_recall`，不另写一套判定，
    否则手动跑和定时跑会得出两种结论。
    """
    from ladderstock.ai import recall
    from ladderstock.data import chat_store

    conn = chat_store.connect(root)
    if conn is None:
        return {"status": "failed", "detail": "chat_store 不可用，本轮无法回访"}
    try:
        stats = recall.run_recall(conn, data_root=root)
    finally:
        chat_store.close(conn)
    if not stats["due"]:
        return {"status": "ok", "detail": "没有到期记录，本轮无需回访", "stats": stats}
    detail = (
        f"到期 {stats['due']} 条：命中 {stats['hit']} / 未中 {stats['miss']} / "
        f"翻转 {stats['flipped']} / 判不了 {stats['undecided']} / "
        f"等行情或未到期 {stats['waiting'] + stats['skipped']}"
    )
    return {"status": "ok", "detail": detail, "stats": stats}


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
        elif kind == "sync_evening":
            result.update(
                _run_sync_evening(job, now=now, timezone=timezone, root=root, registry=registry)
            )
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
        elif kind == "brief":
            result.update(
                _run_brief(job, now=now, timezone=timezone, root=root, registry=registry)
            )
        elif kind == "news_collect":
            result.update(
                _run_news_collect(job, now=now, timezone=timezone, root=root, registry=registry)
            )
        elif kind == "knowledge_refresh":
            result.update(_run_knowledge_refresh(job, now=now, timezone=timezone, root=root))
        elif kind == "agent_research":
            result.update(_run_agent_research(job, now=now, timezone=timezone, root=root))
        elif kind == "watch_alert":
            result.update(_run_watch_alert(job, now=now, timezone=timezone, root=root))
        elif kind == "watch_live":
            result.update(_run_watch_live(job, now=now, timezone=timezone, root=root))
        elif kind == "sentiment_live":
            result.update(_run_sentiment_live(job, now=now, timezone=timezone, root=root))
        elif kind == "plan_tick":
            result.update(_run_plan_tick(job, now=now, timezone=timezone, root=root))
        elif kind == "recall":
            result.update(_run_recall(root=root))
        elif kind == "alert_check":
            # DEC-087 提醒引擎：与 `ldst alert run` / `POST /api/alert/run` 同一个执行体。
            # 取数失败的规则按 no_data 记入 results，不拖垮整轮（执行体内部兜）。
            from ladderstock.alert import run as alert_run

            result.update(
                alert_run.run_once(
                    root=root,
                    now=now,
                    registry=registry,
                )
            )
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

    from ladderstock.cli.paths import data_dir
    from ladderstock.data import scheduler_lock
    from ladderstock.supervisor.page_boot import (
        CHILD_STOP_ARG_ENV,
        OWNER_PID_ENV,
        owner_gone,
    )

    stop_raw = os.environ.get(CHILD_STOP_ARG_ENV)
    stop_marker = Path(stop_raw) if stop_raw else None
    owner_pid = os.environ.get(OWNER_PID_ENV)

    def should_exit() -> bool:
        if stop_marker is not None and stop_marker.exists():
            return True
        return owner_gone(owner_pid)

    lease = scheduler_lock.wait_acquire(
        data_dir(),
        holder="ldst schedule run",
        should_stop=should_exit,
        on_standby=lambda holder: error(f"已有调度器在执行任务表（{holder}），本进程待命"),
    )
    if lease is None:
        error("定时任务未启动：收到退出信号")
        return

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

    # 看板写了退出标记、或拉起本进程的看板已经不在（被杀 / 崩溃）：自行收尾。
    # Windows 上子进程不会随父进程退出，不自查就会留成孤儿，下次启动再叠一个调度器。
    def exit_if_orphaned() -> None:
        if should_exit():
            scheduler.shutdown(wait=False)

    scheduler.add_job(
        exit_if_orphaned,
        "interval",
        seconds=2,
        id="ldst-exit-watch",
        replace_existing=True,
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
    finally:
        lease.release()


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
