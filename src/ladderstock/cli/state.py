"""`ldst state` —— 前端持久化分片的巡检与重置登记。

进程边界（DEC-001 / 任务要求 5）：Python **不读** IndexedDB，也不解析浏览器存储。
分片概况一律来自前端上报的 `data/state/frontend-index.json`：

- `state inspect` 只读该索引 + `config/state.yaml`，输出分片清单、版本、
  最后写入时间与体积，缺索引时如实说明「尚未上报」，不假装有数据；
- `state reset` 只在索引里登记 `pending_reset`，由前端下次启动时删除对应
  分片并回执（见 `frontend/persist/indexReporter.ts`）。命令本身绝不触碰
  IndexedDB，也不删除任何文件。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import config_dir, data_dir
from ladderstock.errors import EXIT_OK, EXIT_USAGE

INDEX_RELATIVE = Path("state") / "frontend-index.json"


# ---------------------------------------------------------------- 文件读写


def index_path() -> Path:
    """前端索引文件路径；与 `frontend/stores/manifest.json:indexFile` 一致。"""
    return data_dir() / INDEX_RELATIVE


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_config() -> tuple[dict[str, Any], list[str]]:
    """读 `config/state.yaml` 的 `state:` 段；缺失或损坏都不抛，交给调用方降级。"""
    path = config_dir() / "state.yaml"
    if not path.is_file():
        return {}, [f"配置缺失：{path}"]
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - 原样把原因交给用户
        return {}, [f"配置无法解析：{path}（{exc}）"]
    state = raw.get("state")
    if not isinstance(state, dict):
        return {}, [f"配置缺少 state 段：{path}"]
    return state, []


def _read_index(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (索引内容, 问题说明)。索引不存在/损坏都只是「没有可信概况」。"""
    if not path.is_file():
        return None, "索引文件不存在（前端还没上报过状态，先跑一次 npm run dev 打开看板）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"索引文件无法解析：{exc}"
    if not isinstance(data, dict):
        return None, "索引文件格式不符（顶层不是对象）"
    return data, None


def _write_index(path: Path, document: dict[str, Any]) -> None:
    """原子写入：先写临时文件再替换，避免前端读到半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _configured_stores(state: dict[str, Any]) -> list[dict[str, Any]]:
    stores = state.get("stores")
    if not isinstance(stores, list):
        return []
    return [item for item in stores if isinstance(item, dict) and item.get("name")]


def _index_stores(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not index:
        return {}
    stores = index.get("stores")
    if not isinstance(stores, dict):
        return {}
    return {str(key): value for key, value in stores.items() if isinstance(value, dict)}


def _pending_reset(index: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not index:
        return []
    pending = index.get("pending_reset")
    if not isinstance(pending, list):
        return []
    return [item for item in pending if isinstance(item, dict) and item.get("store")]


# ---------------------------------------------------------------- inspect


def build_report() -> dict[str, Any]:
    """拼出 `state inspect` 的结果（纯函数式，便于契约测试直接调用）。"""
    state, warnings = _load_config()
    path = index_path()
    index, problem = _read_index(path)
    if problem:
        warnings.append(problem)

    config_version = state.get("schema_version")
    index_version = index.get("schema_version") if index else None
    reported = _index_stores(index)

    rows: list[dict[str, Any]] = []
    for store in _configured_stores(state):
        name = str(store["name"])
        entry = reported.get(name)
        rows.append(
            {
                "name": name,
                "persist": bool(store.get("persist")),
                "ttl_sec": store.get("ttl_sec"),
                "source": "index" if entry else "config-only",
                "schema_version": entry.get("schema_version") if entry else config_version,
                "updated_at": entry.get("updated_at") if entry else None,
                "bytes": entry.get("bytes") if entry else None,
                "needs_refetch": entry.get("needs_refetch") if entry else None,
                "needs_refetch_reason": entry.get("needs_refetch_reason") if entry else None,
            }
        )
        if index is not None and entry is None:
            warnings.append(f"分片 {name} 在配置里有、前端尚未上报（可能还没打开过看板）")

    known = {row["name"] for row in rows}
    for name, entry in reported.items():
        if name in known:
            continue
        rows.append(
            {
                "name": name,
                "persist": True,
                "ttl_sec": entry.get("ttl_sec"),
                "source": "index-only",
                "schema_version": entry.get("schema_version"),
                "updated_at": entry.get("updated_at"),
                "bytes": entry.get("bytes"),
                "needs_refetch": entry.get("needs_refetch"),
                "needs_refetch_reason": entry.get("needs_refetch_reason"),
            }
        )
        warnings.append(f"分片 {name} 在索引里有、配置里没有（清单漂移，请核对 config/state.yaml）")

    if index_version is not None and config_version is not None and index_version != config_version:
        warnings.append(
            f"版本不一致：config/state.yaml 是 v{config_version}，前端上报的是 v{index_version}"
        )

    pending = _pending_reset(index)
    return {
        "config": str(config_dir() / "state.yaml"),
        "backend": state.get("backend"),
        "schema_version": config_version,
        "index_file": str(path),
        "index_present": index is not None,
        "index_generated_at": index.get("generated_at") if index else None,
        "index_schema_version": index_version,
        "stores": rows,
        "pending_reset": pending,
        "warnings": warnings,
    }


def state_inspect(output: OutputOption = None) -> None:
    """前端持久化分片摘要：只读，不修改任何数据。"""
    report = build_report()
    emit(report, output)
    raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------- reset


def state_reset(
    store: Annotated[
        str, typer.Option("--store", help="分片名（config/state.yaml 的 stores.name）")
    ],
    yes: Annotated[bool, typer.Option("--yes", help="非交互确认（脚本用）")] = False,
    output: OutputOption = None,
) -> None:
    """登记重置指定分片；真正删除由前端下次启动执行并回执。"""
    state, warnings = _load_config()
    names = [str(item["name"]) for item in _configured_stores(state)]
    if store not in names:
        error(f"未知分片 {store}；可选：{', '.join(names) if names else '（配置里没有分片）'}")
        raise typer.Exit(EXIT_USAGE)

    if not yes:
        if not sys.stdin.isatty():
            error(f"非交互环境需显式 --yes 才能重置分片 {store}")
            raise typer.Exit(EXIT_USAGE)
        if not typer.confirm(f"确认重置本机分片 {store}？该分片内容将不可恢复", default=False):
            error("已取消，未做任何改动")
            raise typer.Exit(EXIT_USAGE)

    path = index_path()
    index, problem = _read_index(path)
    if index is None:
        # 索引缺失不是错误：重置登记可以先行，前端启动时一并处理
        index = {
            "backend": state.get("backend") or "indexeddb",
            "schema_version": state.get("schema_version"),
            "generated_at": None,
            "stores": {},
        }
        if problem:
            warnings.append(problem)

    requested_at = _now_iso()
    pending = [item for item in _pending_reset(index) if item.get("store") != store]
    pending.append(
        {
            "store": store,
            "requested_at": requested_at,
            "note": "由 ldst state reset 登记，前端下次启动时清除",
        }
    )
    index["pending_reset"] = pending
    _write_index(path, index)

    emit(
        {
            "ok": True,
            "store": store,
            "requested_at": requested_at,
            "index_file": str(path),
            "applied": "前端下次启动时执行（重新打开看板即可）",
            "pending_reset": pending,
            "warnings": warnings,
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def register(state_app: typer.Typer) -> None:
    """把 `state` 子命令挂到 main.py 已建好的 Typer 组上（main.py 只加一行注册）。"""
    state_app.command("inspect", help="前端持久化分片摘要（只读，读前端上报的索引）")(state_inspect)
    state_app.command("reset", help="登记重置指定分片（前端下次启动执行，需确认）")(state_reset)
