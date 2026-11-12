"""supervisor 面（QUOT-491 / 482-A / 482-C / 523 / 556）。

三类路由：

- `GET /api/supervisor/state`：每模块 `health: ok|restarting|needs_human` 与
  `restarts_5min`（任务书对外契约）；另附 `self_heal: {attempts, max, status}`、
  `state`（484 前端面板契约）与 482-A 归属视角（`ownership` / `evidence` /
  `listening`）。QUOT-556 起每模块带 `adopted_peer`，接事件总线时顶层带
  `events_latest`（最新事件 seq，供既有轮询发现新事件）。
- `GET /api/supervisor/events?since=<seq>`（QUOT-556）：peer 级健康事件通道
  （crash / offline / port_conflict / needs_human / recovered），前端弹窗
  定时轮询订阅；只读，回环 + Host 闸门。QUOT-562 起同一通道还承载采集→
  入库七阶段 `stage` 事件（`/state` 带整链路 `stages` 快照）；QUOT-563 起
  再纳入辩论三阶段（module=debate，`core/stage_events.py` 同一注册表）。
- `POST /api/supervisor/state/{module}/reset`：人工解除 `needs_human`（辅助
  清窗口；前端「一键重启」走真 `/restart`，不是 reset）。
- `POST /api/supervisor/start|restart|stop`（QUOT-523 单入口）：按注册表顺序
  全起 / 单模块重起 / 反向全停；**只动账本能证明是我们拉起的进程**（归属判定
  见 `SupervisorEngine`，外部进程一律不碰）。QUOT-556 起认领态 peer 的
  `/restart` 必须 `confirm: true`（用户确认），确认只授权拉起替代实例，
  绝不授权终止外部进程。写面闸门**复用雷达面同一份实现**
  （`privacy_write_guard`：回环 + Host 白名单 + 写方法来源证据），不新增鉴权。

引擎组装（`build_supervisor_engines`）：一张账本同时供自愈巡检（482-C）与
顺序启停（482-A）使用——两边看到的是同一份 pid / state，不会各记各的。
`own_processes()`（`LDST_SUPERVISOR_OWN_PROCESSES=1`）决定看板启动 / 退出时
是否连子进程一起起停；默认关闭（附加模式）：看板只是观测 + 按需操作，
绝不因为开关看板而碰生产上的 5950/8000/9100/9000 进程。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from ladderstock.core.boundary import DATASET_CATALOG
from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_WARN,
    STAGE_COLLECT_PROCESS,
    STAGE_COLLECT_SCRIPT,
    STAGE_COLLECT_STORE,
    STAGE_COLLECT_SUCCESS,
    STAGE_INGEST_IDEMPOTENT,
    STAGE_INGEST_PERSIST,
    STAGE_INGEST_WATERMARK,
    StageSample,
)
from ladderstock.data import duckdb_store
from ladderstock.data.watermark import WatermarkError, WatermarkStore
from ladderstock.errors import EXIT_DB_BUSY, LadderStockError
from ladderstock.supervisor.adapters import MODULE_ORDER
from ladderstock.supervisor.debate_probes import build_debate_probes
from ladderstock.supervisor.engine import SupervisorEngine, SupervisorError
from ladderstock.supervisor.health_events import (
    OWNER_FREE,
    HealthEventBus,
    PortInspector,
    PortObservation,
)
from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import build_default_adapters
from ladderstock.supervisor.notify import build_default_notifier
from ladderstock.supervisor.page_boot import PageBoot
from ladderstock.supervisor.proctree import cmdline_hash, default_process_ops
from ladderstock.supervisor.restart import AdapterRestarter, pid_alive
from ladderstock.supervisor.selfheal import (
    DEFAULT_LOG_MAX_LINES,
    WINDOW_SECONDS,
    RestartOutcome,
    SelfHealEngine,
)
from ladderstock.supervisor.stage_monitor import StageHealthMonitor
from ladderstock.web.port_diagnostics import classify_port_owner
from ladderstock.web.radar import privacy_write_guard

try:
    import psutil
except ImportError:  # pragma: no cover - 依赖探测（与 module_adapters 同一形态）
    psutil = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ladderstock.supervisor.ledger import LedgerEntry

SUPERVISOR_PREFIX = "/api/supervisor"

SUPERVISOR_DENIED = "自愈状态面只对本机回环开放（127.0.0.1 / localhost / ::1）"
SUPERVISOR_WRITE_DENIED = "自愈写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

#: 看板启动 / 退出时是否连子进程一起起停（QUOT-523 单入口）。默认 0 = 附加模式：
#: 只观测、只响应用户显式操作，看板开关本身不碰任何进程。
OWN_PROCESSES_ENV = "LDST_SUPERVISOR_OWN_PROCESSES"

#: 显式确认字段：全停是不可逆批量动作、外部 peer 重启碰 adopt-only 边界，
#: 两者都要求请求体带 `confirm: true`（全停也可用 modules 只停单个来免确认）。
CONFIRMATION_FIELD = "confirm"


def own_processes(env: Mapping[str, str] | None = None) -> bool:
    """单入口模式开关：`LDST_SUPERVISOR_OWN_PROCESSES` 非空且不为 0。"""
    values = os.environ if env is None else env
    return (values.get(OWN_PROCESSES_ENV) or "").strip() not in ("", "0")


async def _supervisor_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=SUPERVISOR_DENIED,
        subject="自愈状态面",
        write_denied=SUPERVISOR_WRITE_DENIED,
    )


@dataclass
class SupervisorEngines:
    """看板侧的 supervisor 装配：自愈巡检 + 顺序启停共用一张账本。"""

    heal: SelfHealEngine
    processes: SupervisorEngine
    ledger: Ledger


def build_port_inspector(
    spawned_pids: Callable[[], Mapping[int, str]] | None = None,
) -> PortInspector:
    """生产端口观测（QUOT-556 / QUOT-557）：一次连接表扫描看全部托管模块的端口归属。

    分类口径与 QUOT-547 诊断同一份实现（`classify_port_owner`，失败关闭）。
    QUOT-557（S4）：
    - 观测结果带占用者 `pid`，供冲突事件与「自有 vs 外来」精确区分；
    - `spawned_pids` 给出本任 supervisor spawn 过的 pid→指纹时，把**此刻仍存活且
      指纹相符**的那些 pid 作为 `mine_pids`——残留自有进程占口也能认成 own_child
      （可安全重启）。死 pid / 指纹不符（PID 复用）绝不进 mine_pids，防把外来进程
      误认成自有。未提供时退化为 QUOT-556 口径（mine_pids 为空，只按该模块账本判）。
    psutil 不可用 / 扫描失败返回空映射：监控本轮只看存活类事件，不误报端口类。
    只读，绝不终止任何进程。
    """

    def inspect(entries: Sequence[LedgerEntry]) -> dict[str, tuple[PortObservation, ...]]:
        if psutil is None:  # pragma: no cover - 依赖探测
            return {}
        try:
            listeners: dict[int, int | None] = {}
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                    continue
                listeners.setdefault(int(conn.laddr.port), int(conn.pid) if conn.pid else None)
        except (psutil.Error, OSError):  # pragma: no cover - 宿主查询失败
            return {}
        cmdline_cache: dict[int, tuple[str, ...] | None] = {}

        def _cmdline(pid: int) -> tuple[str, ...] | None:
            if pid not in cmdline_cache:
                try:
                    cmdline_cache[pid] = tuple(psutil.Process(pid).cmdline())
                except (psutil.Error, OSError):
                    cmdline_cache[pid] = None
            return cmdline_cache[pid]

        # S4：可证明自有的 pid 才进 mine_pids（存活 + 指纹相符，防 PID 复用误认）。
        mine: set[int] = set()
        if spawned_pids is not None:
            try:
                candidates = spawned_pids()
            except Exception:  # noqa: BLE001 - 归属证据查询失败按「无自有证据」降级
                candidates = {}
            for owned_pid, expected in candidates.items():
                live = _cmdline(int(owned_pid))
                if live is not None and cmdline_hash(live) == expected:
                    mine.add(int(owned_pid))

        report: dict[str, tuple[PortObservation, ...]] = {}
        for entry in entries:
            observations: list[PortObservation] = []
            for port in entry.ports:
                if port not in listeners:
                    observations.append(
                        PortObservation(port=port, listening=False, owner=OWNER_FREE)
                    )
                    continue
                pid = listeners[port]
                owner, _reason = classify_port_owner(
                    pid=pid,
                    mine_pids=mine,
                    record=entry,
                    live_cmdline=_cmdline(pid) if pid is not None else None,
                )
                observations.append(
                    PortObservation(port=port, listening=True, owner=owner, pid=pid)
                )
            report[entry.module] = tuple(observations)
        return report

    return inspect


#: 阶段探针的锚点数据集：行情主链路（`daily_k`），采集与入库都以它为准。
#: 不做全数据集轮询——七阶段是管线级状态，主链路断了一切都断；主链路在而
#: 某个次要数据集挂了，由 `ldst sync` 自己的 exit code / runs 记录表达。
STAGE_PROBE_DATASET = "daily_k"

#: 「陈旧」阈值：水位 / 最新记录落后今天超过该天数即如实报 warn / down。
STAGE_STALE_DAYS = 7

#: 最近一次同步成功但距今超过该时长 → warn（盘后同步的日常节奏，超过一天没跑就是陈旧）
STAGE_RUN_STALE = timedelta(hours=24)


def _stage_sample(stage: str, health: str, reason: str) -> StageSample:
    """探针结论 → schema 样本（注册表口径补齐 module / module_class / phase）。"""
    return StageSample.of(stage, health=health, reason=reason)


def build_stage_probes(
    root: str | Path,
    *,
    ledger: Ledger,
    is_pid_alive: Callable[[int], bool] | None = None,
    sync_yaml: Path | None = None,
) -> dict[str, Callable[[], StageSample]]:
    """生产阶段探针（QUOT-562）：只读观测采集 → 入库七阶段，绝不触发采集。

    口径（全部「如实标注」，不编数）：

    - `collect_script`：sync 配置存在、可解析且声明了数据集（链路就绪的最小证据）；
    - `collect_process`：DATASET_CATALOG 涉及的采集 peer 按账本 pid 判活——与 S3
      同一份口径（`pid_alive`），面板未登记 / 已退出都如实计入不在线；
    - `collect_store`：本机历史库（DuckDB）有没有**今天**的行情记录；没有但有
      近 7 天记录 → warn，更旧 / 空库 → down；
    - `collect_success`：水位 runs 里最近一次同步的 status（ok 超 24h 也算陈旧）；
    - `ingest_watermark`：主数据集水位推进到今天 → ok，落后 → warn / down；
    - `ingest_persist`：历史库可读且有行（issue 字面写 SQLite，实际管线按
      DEC-007 落 DuckDB + Parquet，以真实架构为准）；
    - `ingest_idempotent`：当天 (code, trade_date) 无重复行。

    DuckDB 被写入进程占用（`EXIT_DB_BUSY`）是**瞬态争用**不是故障，报 warn
    并说明稍后自动恢复；其余异常失败关闭报 down。探针只读，绝不写库。
    """
    data_root = Path(root)
    config_path = Path(sync_yaml) if sync_yaml is not None else None

    def _today() -> date:
        return datetime.now().astimezone().date()

    def _store() -> WatermarkStore:
        # 每次新建（sync 是另一个进程在写盘，复用实例会读到陈旧缓存）
        return WatermarkStore(path=data_root / "state" / "watermark.json")

    def _connect():
        return duckdb_store.connect(duckdb_store.db_path(data_root), read_only=True)

    def _probe_sync_yaml() -> StageSample:
        path = config_path if config_path is not None else None
        if path is None:
            from ladderstock.cli.paths import config_dir

            path = config_dir() / "sync.yaml"
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return _stage_sample(
                STAGE_COLLECT_SCRIPT, HEALTH_DOWN, "同步配置缺失，采集链路未就绪"
            )
        except (OSError, yaml.YAMLError):
            return _stage_sample(
                STAGE_COLLECT_SCRIPT, HEALTH_DOWN, "同步配置读不出来（文件损坏或不可读）"
            )
        if (doc.get("sync") or {}).get("datasets"):
            return _stage_sample(STAGE_COLLECT_SCRIPT, HEALTH_OK, "采集链路就绪（同步配置齐备）")
        return _stage_sample(
            STAGE_COLLECT_SCRIPT, HEALTH_DOWN, "同步配置里没有声明任何数据集，采集链路未就绪"
        )

    def _probe_collect_process() -> StageSample:
        peers = sorted({binding.peer for binding in DATASET_CATALOG.values()})
        online: list[str] = []
        offline: list[str] = []
        for peer in peers:
            entry = ledger.get(peer)
            pid = entry.pid if entry is not None else None
            if pid is not None and (is_pid_alive is None or is_pid_alive(pid)):
                online.append(peer)
            else:
                offline.append(peer)
        if offline and online:
            return _stage_sample(
                STAGE_COLLECT_PROCESS,
                HEALTH_WARN,
                f"部分采集 peer 不在线：{'、'.join(offline)}（其余正常）",
            )
        if online:
            return _stage_sample(
                STAGE_COLLECT_PROCESS,
                HEALTH_OK,
                f"采集 peer 全部在线：{'、'.join(online)}",
            )
        return _stage_sample(
            STAGE_COLLECT_PROCESS,
            HEALTH_DOWN,
            "采集 peer 全部不在线（或未在面板登记），行情取不到数",
        )

    def _probe_collect_store() -> StageSample:
        try:
            if duckdb_store.query_dataset(
                STAGE_PROBE_DATASET, day=_today(), limit=1, root=data_root
            ):
                return _stage_sample(
                    STAGE_COLLECT_STORE, HEALTH_OK, "今天的行情数据已经落到本机库"
                )
            latest = _latest_trade_date()
        except LadderStockError as exc:
            return _db_error_sample(STAGE_COLLECT_STORE, exc)
        if latest is None:
            return _stage_sample(
                STAGE_COLLECT_STORE, HEALTH_DOWN, "本机行情库还是空的，还没有任何采集记录"
            )
        if latest >= _today() - timedelta(days=STAGE_STALE_DAYS):
            return _stage_sample(
                STAGE_COLLECT_STORE,
                HEALTH_WARN,
                f"本机行情库还没有今天的记录（最新到 {latest.isoformat()}）",
            )
        return _stage_sample(
            STAGE_COLLECT_STORE,
            HEALTH_DOWN,
            f"本机行情库停留在 {latest.isoformat()}，超过一周没有新数据",
        )

    def _latest_trade_date() -> date | None:
        path = duckdb_store.db_path(data_root)
        if not path.is_file():
            return None
        conn = _connect()
        try:
            if not duckdb_store.table_exists(conn, STAGE_PROBE_DATASET):
                return None
            # 表名与 duckdb_store._table 同规则；数据集名是本模块硬编码字面量，
            # 已被 DATASET_CATALOG 校验域覆盖，无注入面。
            row = conn.execute(
                f'SELECT MAX(trade_date) FROM "{STAGE_PROBE_DATASET}"'
            ).fetchone()
        finally:
            duckdb_store.close(conn)
        return row[0] if row and row[0] else None

    def _db_error_sample(stage: str, exc: LadderStockError) -> StageSample:
        if exc.code == EXIT_DB_BUSY:
            return _stage_sample(
                stage, HEALTH_WARN, "本机数据库正被同步进程占用，稍后会自动恢复可读"
            )
        return _stage_sample(stage, HEALTH_DOWN, "本机数据库打不开，采集结果读不到")

    def _probe_collect_success() -> StageSample:
        try:
            runs = _store().runs(limit=1)
        except WatermarkError:
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_DOWN, "同步运行记录损坏，无法判断最近一次采集"
            )
        if not runs:
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_DOWN, "还没有运行过任何同步"
            )
        run = runs[0]
        status = str(run.get("status") or "")
        detail = str(run.get("detail") or "").strip()[:80]
        suffix = f"：{detail}" if detail else ""
        if status == "ok":
            stale_after = _parse_iso(run.get("finished_at")) + STAGE_RUN_STALE
            if datetime.now().astimezone() > stale_after:
                return _stage_sample(
                    STAGE_COLLECT_SUCCESS,
                    HEALTH_WARN,
                    f"最近一次同步成功是在 {str(run.get('finished_at'))[:16]}，之后一直没有再跑",
                )
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_OK, "最近一次同步成功完成"
            )
        if status == "degraded":
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_WARN, f"最近一次同步降级完成{suffix}"
            )
        if status == "empty":
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_WARN, "最近一次同步没有取到新数据"
            )
        if status == "skipped":
            return _stage_sample(
                STAGE_COLLECT_SUCCESS, HEALTH_WARN, "最近一次同步被跳过（上游不可用）"
            )
        return _stage_sample(
            STAGE_COLLECT_SUCCESS, HEALTH_DOWN, f"最近一次同步失败{suffix}"
        )

    def _parse_iso(value: Any) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.fromtimestamp(0).astimezone()
        return parsed if parsed.tzinfo else parsed.astimezone()

    def _probe_ingest_watermark() -> StageSample:
        try:
            value = _store().value(STAGE_PROBE_DATASET)
        except WatermarkError:
            return _stage_sample(
                STAGE_INGEST_WATERMARK, HEALTH_DOWN, "入库水位文件损坏，无法判断同步进度"
            )
        if not value:
            return _stage_sample(
                STAGE_INGEST_WATERMARK, HEALTH_DOWN, "还没有入库水位（同步从未完成过）"
            )
        today = _today().isoformat()
        stale_line = (_today() - timedelta(days=STAGE_STALE_DAYS)).isoformat()
        if value >= today:
            return _stage_sample(
                STAGE_INGEST_WATERMARK, HEALTH_OK, "入库水位已推进到最新"
            )
        if value >= stale_line:
            return _stage_sample(
                STAGE_INGEST_WATERMARK,
                HEALTH_WARN,
                f"入库水位停留在 {value}，今天还没有新数据落库",
            )
        return _stage_sample(
            STAGE_INGEST_WATERMARK,
            HEALTH_DOWN,
            f"入库水位停留在 {value}，超过一周没有推进",
        )

    def _probe_ingest_persist() -> StageSample:
        try:
            rows = duckdb_store.dataset_rows(STAGE_PROBE_DATASET, root=data_root)
        except LadderStockError as exc:
            return _db_error_sample(STAGE_INGEST_PERSIST, exc)
        if rows > 0:
            return _stage_sample(
                STAGE_INGEST_PERSIST, HEALTH_OK, f"本机历史库已有 {rows} 条行情记录，可正常读写"
            )
        return _stage_sample(
            STAGE_INGEST_PERSIST, HEALTH_DOWN, "本机历史库还没有数据，入库从未成功"
        )

    def _probe_ingest_idempotent() -> StageSample:
        try:
            path = duckdb_store.db_path(data_root)
            if not path.is_file():
                return _stage_sample(
                    STAGE_INGEST_IDEMPOTENT, HEALTH_DOWN, "本机历史库还没有建立，没有可校验的数据"
                )
            conn = _connect()
            try:
                if not duckdb_store.table_exists(conn, STAGE_PROBE_DATASET):
                    return _stage_sample(
                        STAGE_INGEST_IDEMPOTENT,
                        HEALTH_DOWN,
                        "本机历史库还没有行情表，没有可校验的数据",
                    )
                row = conn.execute(
                    f'SELECT COUNT(*) AS total, '
                    f'(SELECT COUNT(*) FROM (SELECT code FROM "{STAGE_PROBE_DATASET}" '
                    f"WHERE trade_date = ? GROUP BY code HAVING COUNT(*) > 1 LIMIT 1)) AS dupes "
                    f'FROM "{STAGE_PROBE_DATASET}" WHERE trade_date = ?',
                    [_today().isoformat(), _today().isoformat()],
                ).fetchone()
            finally:
                duckdb_store.close(conn)
        except LadderStockError as exc:
            return _db_error_sample(STAGE_INGEST_IDEMPOTENT, exc)
        total = int(row[0]) if row else 0
        dupes = int(row[1]) if row else 0
        if dupes:
            return _stage_sample(
                STAGE_INGEST_IDEMPOTENT, HEALTH_WARN, "今天的行情数据存在重复行，重跑没有正确去重"
            )
        if not total:
            return _stage_sample(
                STAGE_INGEST_IDEMPOTENT, HEALTH_WARN, "今天还没有入库数据，幂等校验暂无可查对象"
            )
        return _stage_sample(
            STAGE_INGEST_IDEMPOTENT, HEALTH_OK, "今天的行情数据没有重复行，幂等校验通过"
        )

    return {
        STAGE_COLLECT_SCRIPT: _probe_sync_yaml,
        STAGE_COLLECT_PROCESS: _probe_collect_process,
        STAGE_COLLECT_STORE: _probe_collect_store,
        STAGE_COLLECT_SUCCESS: _probe_collect_success,
        STAGE_INGEST_WATERMARK: _probe_ingest_watermark,
        STAGE_INGEST_PERSIST: _probe_ingest_persist,
        STAGE_INGEST_IDEMPOTENT: _probe_ingest_idempotent,
    }


def build_supervisor_engines(
    root: str | Path,
    *,
    panel_url: str,
    env: Mapping[str, str] | None = None,
) -> SupervisorEngines:
    """按 `<data_root>/supervisor/` 组装生产引擎：一张账本 + 自愈 + 顺序启停。

    重拉通道 = `AdapterRestarter`（bundle 自带命令表，QUOT-526），不再读
    `restart-commands.json`。单入口模式（`LDST_SUPERVISOR_OWN_PROCESSES=1`）下
    自愈还负责**冷启动**：托管 `MODULE_ORDER` 全表，账本无 pid 的模块按命令表
    立刻拉起。默认附加模式只认领既有实例、绝不自动拉起（peer adopt-only）。
    QUOT-556：自愈巡检同时驱动健康事件观测（`HealthEventBus` + 端口归属
    探针），事件经 `GET /api/supervisor/events` 供前端轮询。
    QUOT-562：同一条巡检还驱动采集→入库七阶段观测（`StageHealthMonitor` +
    `build_stage_probes` 只读探针），`/state` 带 `stages` 快照、`/events`
    承载 `stage` 事件，schema 单一来源见 `core/stage_events`。
    QUOT-563：同一 monitor 再挂辩论三阶段探针（`build_debate_probes`，只读
    StockSenate 报告），十阶段共用一份注册表与事件通道。
    """
    supervisor_dir = Path(root) / "supervisor"
    ledger = Ledger(supervisor_dir / "ledger.json", window_seconds=WINDOW_SECONDS)
    notifier = build_default_notifier(supervisor_dir / "notifications.log", panel_url=panel_url)
    processes = SupervisorEngine(
        adapters=build_default_adapters(env=env, log_dir=supervisor_dir / "logs"),
        ledger=ledger,
        procs=default_process_ops(),
    )
    cold_start = own_processes(env)
    bus = HealthEventBus()
    heal = SelfHealEngine(
        ledger,
        AdapterRestarter(processes, spawn=cold_start),
        notifier,
        log_dir=supervisor_dir / "logs",
        log_max_lines=DEFAULT_LOG_MAX_LINES,
        modules=MODULE_ORDER if cold_start else None,
        cold_start=cold_start,
        event_bus=bus,
        port_inspector=build_port_inspector(spawned_pids=lambda: processes.spawned_pids),
        port_conflict_restarter=_own_conflict_restarter(processes),
        is_pid_alive=pid_alive,
        stage_monitor=StageHealthMonitor(
            bus,
            probes={
                **build_stage_probes(root, ledger=ledger, is_pid_alive=pid_alive),
                # QUOT-563：辩论三阶段（module=debate, cog）走同一 monitor、同一 schema。
                **build_debate_probes(env=env),
            },
        ),
    )
    return SupervisorEngines(heal=heal, processes=processes, ledger=ledger)


def _own_conflict_restarter(processes: SupervisorEngine) -> Callable[[str, int], RestartOutcome]:
    """S4 端口冲突安全处置通道（QUOT-557）：只对可证明自有的占用者重绑 + 重启。

    包装 `engine.restart_own_occupant`（引擎内再验「本任 spawn + 存活 + 指纹相符」，
    证明不了自有绝不终止）。成功回新 pid；失败 / 拒绝回 `ok=False`，自愈照常记窗口。
    外来 / adopted 占用者根本不会走到这里（观测侧已按分类只上报弹窗）。
    """

    def restart(module: str, occupant_pid: int) -> RestartOutcome:
        try:
            view = processes.restart_own_occupant(module, occupant_pid)
        except SupervisorError as exc:
            return RestartOutcome(ok=False, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - 处置异常统一转成失败结论
            return RestartOutcome(ok=False, detail=f"端口冲突处置异常：{exc}")
        record = view.get("record") or {}
        return RestartOutcome(ok=True, pid=record.get("pid"), detail="自有进程安全重启解冲突")

    return restart


def build_page_boot(root: str | Path, *, ledger: Ledger | None = None) -> PageBoot:
    """按 `<data_root>` 组装页面自启（QUOT-522）：同一份账本 + 真适配器识别 peer。

    适配器构造不碰宿主（只在 ensure()/identify() 时才查询），Windows 之外的开发机
    也能安全构建；任何一路失败都在 `ensure()` 内部折叠进报告。
    """
    from ladderstock.cli.paths import project_root
    from ladderstock.supervisor.module_adapters import build_default_adapters

    data_root = Path(root)
    supervisor_dir = data_root / "supervisor"
    resolved = ledger or Ledger(supervisor_dir / "ledger.json", window_seconds=WINDOW_SECONDS)
    try:
        adapters: dict[str, Any] = dict(build_default_adapters(log_dir=supervisor_dir / "logs"))
    except Exception:  # noqa: BLE001 - 识别器组装失败时 peer 一律记 not_listening
        adapters = {}
    return PageBoot(
        data_root=data_root,
        ledger=resolved,
        adapters=adapters,
        run_cwd=project_root(),
        log_dir=supervisor_dir / "logs",
    )


def build_process_engine(
    root: str | Path,
    *,
    heal: SelfHealEngine,
    env: Mapping[str, str] | None = None,
) -> SupervisorEngine:
    """只组装顺序启停引擎，复用既有自愈账本（`supervisor_engine=` 注入时用）。"""
    supervisor_dir = Path(root) / "supervisor"
    return SupervisorEngine(
        adapters=build_default_adapters(env=env, log_dir=supervisor_dir / "logs"),
        ledger=heal.ledger,
        procs=default_process_ops(),
    )


def build_selfheal_engine(root: str | Path, *, panel_url: str) -> SelfHealEngine:
    """只组装自愈引擎（482-C 旧入口；测试与只读观测仍可用）。"""
    return build_supervisor_engines(root, panel_url=panel_url).heal


def _stop_result(processes: Any, modules: list[str]) -> dict[str, Any]:
    """按前端契约 `{confirm: true, modules: [...]}` 停模块；空列表 = 反向全停。

    外部 peer 认领（`adopted_peer`，QUOT-545）引擎会跳过：如实进 `skipped`
    （没停，不是失败），`ok` 不受影响。
    """
    stopped: list[str] = []
    skipped: list[str] = []
    if not modules:
        outcome = processes.stop_all()
        for name, item in (outcome.get("modules") or {}).items():
            if item.get("ok") and not item.get("skipped"):
                stopped.append(name)
            else:
                skipped.append(name)
        return {"ok": bool(outcome.get("ok")), "stopped": stopped, "skipped": skipped}
    failed = False
    for name in modules:
        try:
            view = processes.stop_module(name)
        except SupervisorError:
            skipped.append(name)
            failed = True
        else:
            record = view.get("record") or {}
            if record.get("adopted_peer"):
                skipped.append(name)
            else:
                stopped.append(name)
    return {"ok": not failed, "stopped": stopped, "skipped": skipped}


def _adopted_restart_gate(processes: Any, modules: list[str]) -> list[str]:
    """重启前需要用户显式确认的模块（QUOT-556）：认领态一律要 confirm。

    口径与 DEC-079 一致（失败关闭）：`adopted_peer=True` 或 `origin="adopted"`
    （含旧账本滞后标记）都按外部 peer 对待。读不到账本（假件 / 未接线）时不
    加闸——引擎层的停止边界（DEC-078）仍然兜底，外部进程绝不会被终止。
    """
    ledger = getattr(processes, "ledger", None)
    record_of = getattr(ledger, "record", None)
    if not callable(record_of):
        return []
    gated: list[str] = []
    for name in modules:
        record = record_of(name)
        if record is not None and (
            getattr(record, "adopted_peer", False) or getattr(record, "origin", None) == "adopted"
        ):
            gated.append(name)
    return gated


def build_stop_router(processes: Any) -> APIRouter:
    """`/api/supervisor/{start,restart,stop}`（QUOT-523）：全部走写面闸门。"""
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.post("/start", dependencies=[Depends(_supervisor_write_guard)])
    def start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        modules = [str(name) for name in (payload or {}).get("modules") or []]
        if not modules:
            outcome = processes.start_all()
            return {"ok": bool(outcome.get("ok")), "modules": outcome.get("modules") or {}}
        results: dict[str, Any] = {}
        for name in modules:
            try:
                processes.start_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
        return {"ok": all(item["ok"] for item in results.values()), "modules": results}

    @router.post("/restart", dependencies=[Depends(_supervisor_write_guard)])
    def restart(payload: dict[str, Any]) -> dict[str, Any]:
        modules = [str(name) for name in payload.get("modules") or []]
        if not modules:
            raise HTTPException(status_code=422, detail="restart 需要 modules: [模块名]")
        # QUOT-556：外部认领的 peer 一键重启必须用户显式确认（前端弹窗主操作）。
        # 确认只授权「拉起替代实例」，绝不授权终止外部进程（引擎闸门兜底）。
        gated = _adopted_restart_gate(processes, modules)
        if gated and payload.get(CONFIRMATION_FIELD) is not True:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"模块 {', '.join(gated)} 是外部认领的 peer（adopt-only）："
                    f"一键重启需要用户确认 {CONFIRMATION_FIELD}: true"
                    "（不会终止任何外来进程）"
                ),
            )
        results: dict[str, Any] = {}
        for name in modules:
            try:
                processes.restart_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
                if name in gated:
                    # 确认重启成功 = 归属已回本控制面（引擎 spawn 清标记）；
                    # 清掉自愈失败窗口，让 526 自愈重新有完整预算看护新进程。
                    reset_heal = getattr(getattr(processes, "ledger", None), "reset_heal", None)
                    if callable(reset_heal):
                        reset_heal(name)
        return {"ok": all(item["ok"] for item in results.values()), "modules": results}

    @router.post("/stop", dependencies=[Depends(_supervisor_write_guard)])
    def stop(payload: dict[str, Any]) -> dict[str, Any]:
        # 全停是不可逆动作（七个模块一起下），必须显式确认；单模块停不要求
        modules = [str(name) for name in payload.get("modules") or []]
        if not modules and payload.get(CONFIRMATION_FIELD) is not True:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"全停需要 {CONFIRMATION_FIELD}: true"
                    "（或给出 modules: [模块名] 只停指定模块）"
                ),
            )
        return _stop_result(processes, modules)

    return router


def build_supervisor_router(heal: Any) -> APIRouter:
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.get("/state")
    def state() -> dict[str, Any]:
        return heal.snapshot()

    @router.get("/events", dependencies=[Depends(_supervisor_write_guard)])
    def events(
        since: int = 0, module: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """peer 级健康事件轮询通道（QUOT-556，前端 S5 弹窗订阅源）。

        `since` = 上次见过的最大 seq；返回其后事件（升序）与当前 `latest`。
        事件含模块 / 状态 / 白话原因；seq 出现空洞 = 客户端落后被环形缓冲挤掉，
        应重新对齐（重拉 /state 全量）。未接事件总线时 configured=False 降级。
        """
        bus = getattr(heal, "event_bus", None)
        if bus is None:
            return {"ok": True, "configured": False, "latest": 0, "events": []}
        items = bus.since(max(0, since), module=module, limit=limit)
        return {
            "ok": True,
            "configured": True,
            "latest": bus.latest_seq,
            "events": [item.to_dict() for item in items],
        }

    @router.post("/state/{module}/reset", dependencies=[Depends(_supervisor_write_guard)])
    def reset(module: str) -> dict[str, Any]:
        result = heal.reset(module)
        if result is None:
            raise HTTPException(status_code=404, detail=f"账本中无模块：{module}")
        return {"ok": True, **result}

    return router


def build_page_boot_router(boot: PageBoot | None) -> APIRouter:
    """页面自启面（QUOT-522）：只读报告 + 回环写方法幂等再跑一次。"""
    router = APIRouter(prefix=SUPERVISOR_PREFIX, tags=["supervisor"])

    @router.get("/page-boot")
    def page_boot_state() -> dict[str, Any]:
        last = boot.last_report() if boot is not None else None
        return {
            "ok": True,
            "configured": boot is not None,
            "enabled": bool(boot.enabled) if boot is not None else False,
            "last_report": last,
        }

    @router.post("/page-boot", dependencies=[Depends(_supervisor_write_guard)])
    def page_boot_ensure() -> dict[str, Any]:
        if boot is None:
            raise HTTPException(status_code=503, detail="页面自启未接线")
        return boot.ensure(trigger="api-request")

    return router


def mount_supervisor(
    app: FastAPI,
    heal: Any,
    *,
    processes: Any | None = None,
    own_processes_on_lifecycle: bool = False,
) -> None:
    """挂路由 + 引擎随应用启停。

    `processes` 给出时注册启停路由；`own_processes_on_lifecycle` 为真时启动
    全起、退出只停自有子进程（单入口模式，QUOT-527：adopted peer 保留）；
    否则退出只停自愈巡检线程，不碰任何子进程（附加模式——绝不因为关看板而
    停生产进程）。
    """
    app.include_router(build_supervisor_router(heal))
    if processes is not None:
        app.include_router(build_stop_router(processes))

    async def startup() -> None:
        if processes is not None and own_processes_on_lifecycle:
            await _in_thread(processes.start_all)
        await _in_thread(heal.start)

    async def shutdown() -> None:
        if processes is not None and own_processes_on_lifecycle:
            # QUOT-527：应用退出 = 只停本控制面 spawn 的子进程；adopted peer 保留
            await _in_thread(processes.stop_owned)
        await _in_thread(lambda: heal.stop(timeout=2.0))

    app.router.on_startup.append(startup)
    app.router.on_shutdown.append(shutdown)


async def _in_thread(fn: Any) -> None:
    """起停是阻塞调用（等端口 / 等进程），挪出事件循环，别卡住启动/关闭握手。"""
    await asyncio.to_thread(fn)


def mount_page_boot(app: FastAPI, boot: PageBoot) -> None:
    """QUOT-522：路由 + startup 钩子（打开网页 = 拉起自有子进程，peer 只认领）。

    引擎自身的启停另有 `mount_supervisor`；这里只管页面自启与 own children 的停。
    """
    app.include_router(build_page_boot_router(boot))
    app.router.on_startup.append(boot.ensure)
    app.router.on_shutdown.append(boot.stop_children)


__all__ = [
    "CONFIRMATION_FIELD",
    "OWN_PROCESSES_ENV",
    "SUPERVISOR_DENIED",
    "SUPERVISOR_PREFIX",
    "SUPERVISOR_WRITE_DENIED",
    "SupervisorEngines",
    "build_page_boot",
    "build_page_boot_router",
    "build_port_inspector",
    "build_selfheal_engine",
    "build_process_engine",
    "build_stop_router",
    "build_supervisor_engines",
    "build_supervisor_router",
    "mount_page_boot",
    "mount_supervisor",
    "own_processes",
]
