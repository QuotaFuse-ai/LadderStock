"""旧版配置与数据目录迁移（`ldst config migrate` 的实现落脚点）。

设计口径（DEC-001 本地优先 / 源文档 §6.5）：

- **旧文件只读**：迁移不改写、不删除旧目录里的任何文件，只把内容复制进新目录；
- **先备份后改动**：目标文件（`config/state.yaml`、`data/journal.sqlite`）在改动前
  先落一份带时间戳的副本，任一步失败就按备份回滚（审计 R-6b）；
- **幂等**：`schema_version` 已达当前版本时不再产生任何写入，重复执行返回
  `up_to_date`；因此把本命令放进 CI 矩阵是安全的；
- **不猜未来**：`schema_version` 高于当前程序支持值时直接拒绝（退出码 3），
  绝不「猜」下一版结构，也绝不降级改写用户数据。

分工：本模块负责计划、备份、执行与回滚；`cli/main.py` 只做薄封装（解析参数、
输出结构化结果）。SQLite 结构迁移仍由 `ladderstock.data.sqlite_store` 负责，
这里只保证「迁移前已备份、失败能回滚」。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ladderstock.cli.paths import config_dir, data_dir
from ladderstock.errors import EXIT_CONFIG, LadderStockError

#: 当前程序支持的前端分片 schema 版本（与 `config/state.yaml`、
#: `frontend/persist/migrate.ts` 的 `SCHEMA_VERSION` 同口径）
SUPPORTED_SCHEMA_VERSION = 2

STATE_FILENAME = "state.yaml"
BACKUP_DIRNAME = "backups"
MARKER_RELATIVE = Path("state") / "migration.json"
WATCH_TTL_SEC = 300

#: v2 的四个分片：顺序固定，迁移后缺哪个补哪个
CANONICAL_STORES: tuple[str, ...] = (
    "watch_snapshot",
    "review_draft",
    "validation_draft",
    "journal_draft",
)

CONFIG_STEP_ID = "config:state.yaml"
SQLITE_STEP_ID = "sqlite:journal.sqlite"
LEGACY_DATA_STEP_ID = "data:legacy"

#: 旧版配置/数据位置的显式覆盖（过渡期调试用）
LEGACY_CONFIG_ENV = "LDST_LEGACY_CONFIG_DIR"
LEGACY_DATA_ENV = "LDST_LEGACY_DATA_DIR"
#: `ldst run` 首启动自动迁移的开关（`1` 可关掉，便于排障）
AUTO_MIGRATE_ENV = "LDST_SKIP_AUTO_MIGRATE"

LEGACY_CONFIG_MARKERS: tuple[str, ...] = ("vibe-research.config.json",)
LEGACY_DATA_MARKERS: tuple[str, ...] = ("data.js", "review")
LEGACY_DATA_HOMES: tuple[str, ...] = ("~/.duanxian-agents", ".local")

STATE_HEADER = """\
# 前端持久化分片（文档 §6.4）
# schema_version 2：分片结构在 v1 基础上做了拆分（复盘单块正文 → 五段式、
# 验证条件补 id/confirmed、盯盘快照补 fetchedAt），迁移实现见
# frontend/persist/migrate.ts（v1→v2 逐级迁移，失败丢弃该分片）。
# 本文件由 `ldst config migrate` 迁移生成；迁移前的旧文件已带时间戳备份到 data/backups/。
"""


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Step:
    """一条迁移动作；`id` 稳定，供测试与日志引用。"""

    id: str
    action: str
    detail: str
    files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "detail": self.detail,
            "files": list(self.files),
        }


@dataclass
class MigrationPlan:
    """一次迁移的完整计划：只描述，不改任何文件。"""

    config_root: Path
    data_root: Path
    from_version: int
    to_version: int
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    legacy_configs: list[Path] = field(default_factory=list)
    legacy_data: Path | None = None

    @property
    def marker_path(self) -> Path:
        return self.data_root / MARKER_RELATIVE


def build_plan(
    config_root: Path | None = None,
    data_root: Path | None = None,
) -> MigrationPlan:
    """只读地算出迁移计划；`schema_version` 过高直接报配置错误。"""
    base = Path(config_root) if config_root is not None else config_dir()
    target = Path(data_root) if data_root is not None else data_dir()
    payload = _load_state(base / STATE_FILENAME)
    plan = MigrationPlan(
        config_root=base,
        data_root=target,
        from_version=_state_version(payload),
        to_version=SUPPORTED_SCHEMA_VERSION,
    )

    if payload is None:
        plan.warnings.append(
            f"未找到 {base / STATE_FILENAME}：没有可迁移的配置，跳过（不新建文件）"
        )
    elif plan.from_version > SUPPORTED_SCHEMA_VERSION:
        raise LadderStockError(
            f"config/state.yaml 的 schema_version={plan.from_version} 高于当前程序支持的 "
            f"{SUPPORTED_SCHEMA_VERSION}；请升级 LadderStock（不会降级改写你的配置）",
            EXIT_CONFIG,
        )
    elif plan.from_version < SUPPORTED_SCHEMA_VERSION:
        plan.steps.append(
            Step(
                CONFIG_STEP_ID,
                "rewrite",
                f"分片结构 v{plan.from_version} → v{plan.to_version}："
                "补齐 v2 四类分片、校验 watch_snapshot.ttl_sec",
                (str(base / STATE_FILENAME),),
            )
        )

    _add_sqlite_step(plan)
    _add_legacy_steps(plan)
    return plan


def _state_version(payload: dict[str, Any] | None) -> int:
    if payload is None:
        return SUPPORTED_SCHEMA_VERSION
    state = payload.get("state")
    if not isinstance(state, dict):
        return 1
    raw = state.get("schema_version", 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _add_sqlite_step(plan: MigrationPlan) -> None:
    from ladderstock.data import sqlite_store

    db = plan.data_root / sqlite_store.DB_FILENAME
    if not db.is_file():
        return
    version = _file_user_version(db)
    if version > sqlite_store.SCHEMA_VERSION:
        raise LadderStockError(
            f"本机隐私库版本 {version} 高于当前程序支持的 {sqlite_store.SCHEMA_VERSION}；"
            "请升级 LadderStock（不会自动降级或改写数据）",
            EXIT_CONFIG,
        )
    if version < sqlite_store.SCHEMA_VERSION:
        plan.steps.append(
            Step(
                SQLITE_STEP_ID,
                "upgrade",
                f"隐私库 schema v{version} → v{sqlite_store.SCHEMA_VERSION}（迁移前备份）",
                (str(db),),
            )
        )


def _add_legacy_steps(plan: MigrationPlan) -> None:
    """旧目录只在「显式指定」或「确实存在旧产物」时才纳入计划。"""
    plan.legacy_configs = _find_legacy_configs()
    for path in plan.legacy_configs:
        plan.warnings.append(
            f"发现旧版配置 {path}：模型与 provider 需要人工确认后写入 config/ai.yaml"
            "（不猜测、不复制凭据）；旧文件保持只读"
        )

    legacy_data = _find_legacy_data_dir()
    if legacy_data is None:
        return
    plan.legacy_data = legacy_data
    sources = [
        str(legacy_data / name)
        for name in LEGACY_DATA_MARKERS
        if (legacy_data / name).exists()
    ]
    plan.steps.append(
        Step(
            LEGACY_DATA_STEP_ID,
            "copy",
            "把旧数据产物复制到 data/legacy/（旧目录保持只读，不移动、不删除）",
            tuple(sources),
        )
    )


def _find_legacy_configs() -> list[Path]:
    found: list[Path] = []
    explicit = os.environ.get(LEGACY_CONFIG_ENV)
    if explicit:
        found.append(Path(explicit).expanduser())
    for base in (Path.home(), Path.cwd()):
        for name in LEGACY_CONFIG_MARKERS:
            candidate = base / name
            if candidate.is_file():
                found.append(candidate)
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def _find_legacy_data_dir() -> Path | None:
    explicit = os.environ.get(LEGACY_DATA_ENV)
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_dir() else None
    for raw in LEGACY_DATA_HOMES:
        candidate = Path(raw).expanduser()
        if not candidate.is_dir():
            continue
        if any((candidate / name).exists() for name in LEGACY_DATA_MARKERS):
            return candidate
    return None


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #

def run_migration(
    *,
    dry_run: bool = False,
    config_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """迁移入口：返回可直接 `emit()` 的结构化结果。"""
    plan = build_plan(config_root=config_root, data_root=data_root)
    if dry_run:
        # 无事可做时不要谎报“有计划”：幂等的仓库/已迁移机器应看到 up_to_date
        status = "planned" if plan.steps else "up_to_date"
        return _report(plan, dry_run=True, status=status, backups=[])
    if not plan.steps:
        return _report(plan, dry_run=False, status="up_to_date", backups=[])
    backups = apply_plan(plan)
    return _report(plan, dry_run=False, status="applied", backups=backups)


def apply_plan(plan: MigrationPlan) -> list[str]:
    """按计划执行；任一步失败按备份回滚后抛 `LadderStockError`（退出码 3）。"""
    backups: list[str] = []
    rollback = _Rollback()
    try:
        for step in plan.steps:
            if step.id == CONFIG_STEP_ID:
                _migrate_state_file(plan, rollback, backups)
            elif step.id == SQLITE_STEP_ID:
                _upgrade_sqlite(plan, rollback, backups)
            elif step.id == LEGACY_DATA_STEP_ID:
                _import_legacy_data(plan, rollback, backups)
        _write_marker(plan, backups)
    except LadderStockError:
        rollback.run()
        raise
    except Exception as exc:  # noqa: BLE001 - 迁移失败必须转成配置错误并回滚
        rollback.run()
        raise LadderStockError(f"迁移失败，已按备份回滚：{exc}", EXIT_CONFIG) from exc
    return backups


def _migrate_state_file(
    plan: MigrationPlan, rollback: _Rollback, backups: list[str]
) -> None:
    path = plan.config_root / STATE_FILENAME
    payload = _load_state(path)
    if payload is None:  # 计划阶段已告警；执行阶段再确认一次，不凭空造文件
        return
    backup = backup_config_file(path, plan.data_root)
    backups.append(str(backup))
    rollback.restores.append((backup, path))
    _atomic_write(path, render_state_yaml(upgrade_state_payload(payload)))


def _upgrade_sqlite(
    plan: MigrationPlan, rollback: _Rollback, backups: list[str]
) -> None:
    from ladderstock.data import sqlite_store

    db = plan.data_root / sqlite_store.DB_FILENAME
    conn = sqlite3.connect(str(db), timeout=5.0, isolation_level=None)
    try:
        version = _user_version(conn)
        if version > 0 or sqlite_store.table_names(conn):
            backup = backup_sqlite_file(conn, db, version=version)
            backups.append(str(backup))
            rollback.restores.append((backup, db))
        sqlite_store.migrate(conn, backup=False)
    finally:
        conn.close()


def _import_legacy_data(
    plan: MigrationPlan, rollback: _Rollback, backups: list[str]
) -> None:
    if plan.legacy_data is None:
        return
    source = plan.legacy_data
    dest = plan.data_root / "legacy" / f"import-{_stamp()}"
    rollback.created.append(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_DATA_MARKERS:
        origin = source / name
        if origin.is_dir():
            shutil.copytree(origin, dest / name, dirs_exist_ok=True)
        elif origin.is_file():
            shutil.copy2(origin, dest / name)


def _write_marker(plan: MigrationPlan, backups: list[str]) -> None:
    marker = plan.marker_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "migrated_at": _now_iso(),
        "steps": [step.id for step in plan.steps],
        "backups": backups,
        "legacy_data": str(plan.legacy_data) if plan.legacy_data else None,
    }
    marker.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _report(
    plan: MigrationPlan, *, dry_run: bool, status: str, backups: list[str]
) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "status": status,
        "schema": {
            "from": plan.from_version,
            "to": plan.to_version,
            "supported": SUPPORTED_SCHEMA_VERSION,
        },
        "config_dir": str(plan.config_root),
        "data_dir": str(plan.data_root),
        "steps": [step.to_dict() for step in plan.steps],
        "backups": list(backups),
        "warnings": list(plan.warnings),
        "legacy": {
            "config": [str(path) for path in plan.legacy_configs],
            "data": str(plan.legacy_data) if plan.legacy_data else None,
        },
        "marker": str(plan.marker_path) if status == "applied" else None,
    }


# --------------------------------------------------------------------------- #
# 首启动钩子（源文档 §6.5）
# --------------------------------------------------------------------------- #

def ensure_migrated(
    *, config_root: Path | None = None, data_root: Path | None = None
) -> dict[str, Any] | None:
    """`ldst run` 启动前的自动迁移：能迁就迁，迁不了只告警并继续用旧路径。

    返回值：实际执行的迁移报告；无事可做或失败时返回 `None`。
    失败**不阻塞启动**——这正是 §6.5 要求的「迁移失败回退到旧启动路径并提示」。
    """
    if os.environ.get(AUTO_MIGRATE_ENV):
        return None
    try:
        report = run_migration(dry_run=False, config_root=config_root, data_root=data_root)
    except LadderStockError as exc:
        _notify(f"旧配置/旧数据迁移未完成，继续使用现有目录（{exc.message}）")
        return None
    if report.get("status") == "applied":
        steps = "、".join(step["id"] for step in report["steps"])
        backups = "、".join(report["backups"])
        _notify(f"已自动迁移 {steps}" + (f"；备份：{backups}" if backups else ""))
        return report
    return None


def _notify(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 备份与回滚
# --------------------------------------------------------------------------- #

@dataclass
class _Rollback:
    """失败时按备份复原的目标文件，以及需要清理的新建路径。"""

    restores: list[tuple[Path, Path]] = field(default_factory=list)
    created: list[Path] = field(default_factory=list)

    def run(self) -> None:
        for path in reversed(self.created):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 回滚尽力而为
                pass
        for backup, original in reversed(self.restores):
            try:
                for suffix in ("-wal", "-shm"):
                    Path(f"{original}{suffix}").unlink(missing_ok=True)
                shutil.copy2(backup, original)
            except OSError:  # pragma: no cover - 回滚尽力而为
                pass


def backup_config_file(path: Path, data_root: Path) -> Path:
    """把配置文件复制到 `<data_root>/backups/<时间戳>/`，返回备份路径。"""
    target_dir = Path(data_root) / BACKUP_DIRNAME / _stamp()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(path).name
    shutil.copy2(path, target)
    return target


def backup_sqlite_file(
    conn: sqlite3.Connection,
    path: Path | str,
    *,
    version: int,
    backup_root: Path | None = None,
) -> Path:
    """迁移前备份 SQLite：优先 `VACUUM INTO`（含 WAL 中的已提交数据），失败退回文件复制。

    文件名带版本与时间戳（`journal.sqlite.bak-v1-20261028T101530123456`），
    与原始库同目录，便于用户自己找回。
    """
    source = Path(path)
    name = f"{source.name}.bak-v{version}-{_stamp()}"
    target = Path(backup_root) / name if backup_root is not None else source.with_name(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error:
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)
    return target


# --------------------------------------------------------------------------- #
# state.yaml 结构升级
# --------------------------------------------------------------------------- #

def upgrade_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把 v1 分片结构升级成 v2：四类分片齐全、`watch_snapshot` 有合法 TTL。"""
    state = payload.get("state")
    if not isinstance(state, dict):
        raise LadderStockError(
            "config/state.yaml 缺少 state 段，无法迁移（请先跑 ldst config validate）",
            EXIT_CONFIG,
        )
    upgraded = dict(state)
    upgraded["schema_version"] = SUPPORTED_SCHEMA_VERSION
    upgraded.setdefault("backend", "indexeddb")
    upgraded["stores"] = _normalise_stores(state.get("stores"))
    result = dict(payload)
    result["state"] = upgraded
    return result


def render_state_yaml(payload: dict[str, Any]) -> str:
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100)
    return STATE_HEADER + body


def _normalise_stores(raw: Any) -> list[dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []
    for entry in _store_entries(raw):
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in CANONICAL_STORES:
            known[name] = entry
        else:
            extras.append(entry)

    stores: list[dict[str, Any]] = []
    for name in CANONICAL_STORES:
        entry = dict(known.get(name) or {"name": name})
        entry["name"] = name
        entry.setdefault("persist", True)
        if name == "watch_snapshot":
            ttl = entry.get("ttl_sec")
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
                entry["ttl_sec"] = WATCH_TTL_SEC
        stores.append(entry)
    stores.extend(extras)
    return stores


def _store_entries(raw: Any) -> list[dict[str, Any]]:
    """v1 可能把分片写成 `name -> 配置` 的映射，v2 统一成列表。"""
    if isinstance(raw, dict):
        entries: list[dict[str, Any]] = []
        for name, value in raw.items():
            entry = dict(value) if isinstance(value, dict) else {}
            entry["name"] = name
            entries.append(entry)
        return entries
    if isinstance(raw, list):
        entries = []
        for value in raw:
            if isinstance(value, str):
                entries.append({"name": value})
            elif isinstance(value, dict):
                entries.append(dict(value))
        return entries
    return []


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LadderStockError(f"{path} 无法解析：{exc}", EXIT_CONFIG) from exc
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + `os.replace`：写一半崩掉也不会留下半个 YAML。"""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _file_user_version(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        return _user_version(conn)
    finally:
        conn.close()


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row and row[0] else 0


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S%f")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "CANONICAL_STORES",
    "SUPPORTED_SCHEMA_VERSION",
    "MigrationPlan",
    "Step",
    "apply_plan",
    "backup_config_file",
    "backup_sqlite_file",
    "build_plan",
    "ensure_migrated",
    "render_state_yaml",
    "run_migration",
    "upgrade_state_payload",
]
