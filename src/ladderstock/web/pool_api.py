"""代码池可配置：把"85 只写死在 sync.yaml 里"变成页面上可选的一件事。

为什么原来只有 85 只：`config/sync.yaml` 的 `codes: &base_pool` 是手写的一份观察池
（注释里写"约 76 只的大盘样本"，实测 85 只），后面十几个数据集用 YAML 锚点 `*base_pool`
共用这一份。它不是不能改，是没人给入口。

这一层做的事：
- **读**（`pool_view`）：当前池大小、来源文件、标的字典里有多少可选的票；
- **写**（`apply_pool`）：按用户选的规模重排 `codes: &base_pool` 的列表，保留锚点，
  其它数据集继续共享同一份；
- **不触发采集**。改配置 ≠ 开始采数：写完之后不调用任何 sync/job，页面上只是分母变了。
  真要跑数时用户自己点「跑一次」或等定时任务。

三条硬约束照实写进返回里，不粉饰：
- `MAX_POOL_CODES=100`：逐只数据集一只一次上游调用，超了同一轮会扇出 N 次（连续
  3 次失败即止损），所以 100 只以上要接受"一轮更慢、上游一抖就整轮断"；
- 批量数据集 150 只一次往返、单轮最多 450 只；
- 全市场 5,572 只（活跃）逐只不可行，只能走 TickHub 盘后包——那是另一条链路。

写入是**可回滚**的：先备份 `sync.yaml.bak-<时间戳>`，写临时文件再原子替换，
写完用 `yaml.safe_load` 校验「daily_k.codes == 预期」且「其它 `*base_pool` 数据集
仍然是同一份」，任一不成立就从备份还原并把错误原样返回。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException

from ladderstock.data import duckdb_store
from ladderstock.errors import LadderStockError

POOL_PREFIX = "/api/config"

ANCHOR_LINE = re.compile(r"^\s*codes:\s*&base_pool\s*$")

#: 页面上给的选择档位；size=None 表示"用标的字典里的全部活跃票"
POOL_CHOICES: tuple[dict[str, Any], ...] = (
    {"size": 85, "label": "85 只", "note": "现状：手写观察池，全部数据集共用"},
    {"size": 300, "label": "300 只", "note": "中盘规模：逐只数据集一轮扇出 300 次"},
    {"size": 800, "label": "800 只", "note": "宽基规模：一轮明显变慢，上游抖动会整轮断"},
    {"size": 1500, "label": "1,500 只", "note": "接近半个市场：建议改用批量数据集链路"},
    {"size": None, "label": "全部活跃", "note": "5,572 只：逐只链路不可行，只能走盘后包"},
)


def _project_root(root: Path | None) -> Path:
    from ladderstock.web.config_api import _project_root as impl

    return impl(root)


def _data_root(root: Path | None, project: Path) -> Path:
    from ladderstock.web.config_api import _data_root as impl

    return impl(root, project)


def _sync_path(project: Path) -> Path:
    return project / "config" / "sync.yaml"


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #


def _current_pool(project: Path) -> tuple[list[str], str | None]:
    """当前池：从 sync.yaml 解出来，并给文件最后修改时间。"""
    path = _sync_path(project)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return [], None
    datasets = (doc.get("sync") or {}).get("datasets") or {}
    codes: list[str] = []
    for spec in datasets.values():
        if isinstance(spec, dict) and isinstance(spec.get("codes"), list):
            candidate = [str(item) for item in spec["codes"]]
            if len(candidate) > len(codes):
                codes = candidate
    try:
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()
    except OSError:
        stamp = None
    return codes, stamp


def _anchor_users(project: Path) -> int:
    """有多少个数据集靠 `*base_pool` 复用这份池子（改一个，全跟着变）。"""
    path = _sync_path(project)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len(re.findall(r"codes:\s*\*base_pool", text))


def _universe(data_root: Path) -> dict[str, Any]:
    """标的字典里有多少可选的票；按成交额（价×量）降序取活跃票。"""
    db = data_root / "market.duckdb"
    fallback = {"symbols": None, "active": None, "as_of": None, "error": "没有历史库"}
    if not db.is_file():
        return fallback
    try:
        conn = duckdb_store.connect(db, read_only=True)
    except Exception as exc:  # noqa: BLE001 - 字典读不到时给出原因，不让页面 500
        return {**fallback, "error": " ".join(str(exc).split())[:160]}
    try:
        total = conn.execute("select count(*) from symbol_basic").fetchone()[0]
        active = conn.execute(
            "select count(*) from symbol_basic where json_extract_string(payload,'$.active') = 'true'"
        ).fetchone()[0]
        as_of = conn.execute("select max(trade_date) from symbol_basic").fetchone()[0]
        return {"symbols": int(total), "active": int(active), "as_of": str(as_of), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {**fallback, "error": " ".join(str(exc).split())[:160]}
    finally:
        duckdb_store.close(conn)


def _ranked_codes(data_root: Path, size: int | None) -> list[tuple[str, str]]:
    """标的字典里按成交额降序的 (code, name)；size=None 取全部活跃票。"""
    db = data_root / "market.duckdb"
    if not db.is_file():
        raise LadderStockError("没有历史库，无法按活跃度生成代码池")

    conn = duckdb_store.connect(db, read_only=True)
    try:
        sql = """
            select code,
                   coalesce(json_extract_string(payload, '$.name'), '') as name,
                   coalesce(cast(json_extract(payload, '$.price') as double), 0)
                     * coalesce(cast(json_extract(payload, '$.volume') as double), 0) as amt
            from symbol_basic
            where json_extract_string(payload, '$.active') = 'true'
            order by amt desc, code asc
        """
        rows = conn.execute(sql).fetchall()
    finally:
        duckdb_store.close(conn)
    codes = [(str(code), str(name or "")) for code, name, _amt in rows]
    return codes if size is None else codes[:size]


def pool_view(project: Path, data_root: Path) -> dict[str, Any]:
    codes, stamp = _current_pool(project)
    universe = _universe(data_root)
    size = len(codes)
    return {
        "ok": True,
        "size": size,
        "updated_at": stamp,
        "file": "config/sync.yaml",
        "anchor_users": _anchor_users(project),
        "codes_preview": codes[:12],
        "universe": universe,
        "choices": list(POOL_CHOICES),
        "limits": {
            "per_call": 100,
            "batch_per_call": 150,
            "max_batch": 450,
            "note": "逐只数据集一只一次上游调用；批量数据集 150 只一次往返、单轮最多 450 只",
        },
        "warning": _warning(size),
    }


def _warning(size: int) -> str | None:
    if size > 450:
        return (
            f"当前 {size} 只超过批量链路单轮上限 450 只：逐只数据集会在同一轮扇出 {size} 次调用，"
            "上游一抖连续 3 次失败就整轮止损。这个规模应该走 TickHub 盘后包，不是逐只链路。"
        )
    if size > 100:
        return (
            f"{size} 只超过逐只单批上限 100 只：同一轮会扇出 {size} 次调用（不自动分批），"
            "同步时长与失败面都随规模线性增长。"
        )
    return None


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #


def _render_block(codes: list[tuple[str, str]], item_indent: int) -> list[str]:
    pad = " " * item_indent
    out: list[str] = []
    for code, name in codes:
        comment = f"  # {name}" if name else ""
        out.append(f'{pad}- "{code}"{comment}')
    return out


def rewrite_pool_text(text: str, codes: list[tuple[str, str]]) -> str:
    """把 `codes: &base_pool` 下面那份列表整块换掉，锚点与缩进原样保留。

    找不到锚点行就抛 `LadderStockError`——宁可不写，也不要在错误的位置插一份池子。
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if ANCHOR_LINE.match(line):
            start = index
            break
    if start is None:
        raise LadderStockError("config/sync.yaml 里找不到 `codes: &base_pool`，拒绝改写")

    anchor_indent = len(lines[start]) - len(lines[start].lstrip())
    item_indent = anchor_indent + 2
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() == "":
            end += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= anchor_indent:
            break
        end += 1

    new_lines = lines[: start + 1] + _render_block(codes, item_indent) + lines[end:]
    return "\n".join(new_lines) + "\n"


def _anchor_names(text: str) -> list[str]:
    """哪些数据集真的在共用 `*base_pool`。

    不能拿"所有带 codes 的数据集"来比长度：`index_daily` 有自己的 1 只（000300），
    它和基础池无关。PyYAML 对锚点复用会返回**同一个 list 对象**，按对象身份比就够。
    """
    try:
        datasets = (yaml.safe_load(text) or {}).get("sync", {}).get("datasets") or {}
    except yaml.YAMLError:
        return []
    base = datasets.get("daily_k", {}).get("codes")
    if not isinstance(base, list):
        return []
    return [
        name
        for name, spec in datasets.items()
        if isinstance(spec, dict) and isinstance(spec.get("codes"), list) and spec["codes"] is base
    ]


def _verify(text: str, expected: list[str], anchor_names: list[str]) -> None:
    doc = yaml.safe_load(text) or {}
    datasets = (doc.get("sync") or {}).get("datasets") or {}
    got = datasets.get("daily_k", {}).get("codes") or []
    if [str(code) for code in got] != expected:
        raise LadderStockError("写入后校验失败：daily_k.codes 与预期不一致")
    for name in anchor_names:
        spec = datasets.get(name) or {}
        codes = spec.get("codes") or []
        if [str(code) for code in codes] != expected:
            raise LadderStockError(f"写入后校验失败：{name} 没有跟着锚点一起变")


#: 进程内串行：两个请求同时改写同一个文件会互相覆盖
_WRITE_LOCK = __import__("threading").Lock()


def apply_pool(project: Path, data_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """按选择的规模重写代码池。**只改配置，不触发采集**。"""
    mode = str(payload.get("mode") or "size")
    if mode == "codes":
        raw = payload.get("codes") or []
        if not isinstance(raw, list) or not raw:
            raise LadderStockError("codes 必须是非空列表")
        codes = [(str(item).strip(), "") for item in raw if str(item).strip()]
        if not codes:
            raise LadderStockError("codes 里没有有效代码")
    elif mode == "size":
        size_raw = payload.get("size")
        if size_raw is None:
            codes = _ranked_codes(data_root, None)
        else:
            try:
                size = int(size_raw)
            except (TypeError, ValueError):
                raise LadderStockError("size 必须是整数或 null")
            if size < 1:
                raise LadderStockError("size 必须大于 0")
            codes = _ranked_codes(data_root, size)
    else:
        raise LadderStockError(f"不认识的 mode：{mode}")

    path = _sync_path(project)
    with _WRITE_LOCK:
        original = path.read_text(encoding="utf-8")
        anchor_names = _anchor_names(original)
        new_text = rewrite_pool_text(original, codes)
        expected = [code for code, _name in codes]
        try:
            _verify(new_text, expected, anchor_names)
        except LadderStockError:
            raise
        except yaml.YAMLError as exc:
            raise LadderStockError(f"写入后 YAML 解析失败：{exc}") from exc

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"sync.yaml.bak-{stamp}")
        shutil.copy2(path, backup)
        tmp = path.with_name("sync.yaml.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)

        try:
            _verify(path.read_text(encoding="utf-8"), expected, anchor_names)
        except Exception as exc:  # noqa: BLE001 - 落盘后再校验，失败就回滚
            shutil.copy2(backup, path)
            raise LadderStockError(f"落盘后校验失败，已从备份还原：{exc}") from exc

    view = pool_view(project, data_root)
    return {
        **view,
        "applied": True,
        "backup": backup.name,
        "codes_written": len(expected),
        "note": "只改了 config/sync.yaml 的代码池，没有触发任何采集；要跑数请点定时任务的「跑一次」。",
    }


def build_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=POOL_PREFIX)
    project = _project_root(root)
    data_root = _data_root(root, project)

    @router.get("/pool")
    def get_pool() -> dict[str, Any]:
        return pool_view(project, data_root)

    @router.post("/pool")
    def post_pool(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        try:
            return apply_pool(project, data_root, payload)
        except LadderStockError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = ["POOL_PREFIX", "POOL_CHOICES", "apply_pool", "build_router", "pool_view", "rewrite_pool_text"]
