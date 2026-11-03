"""`/__state/index`：浏览器分片索引的本机落盘通道（QUOT-288）。

进程边界（DEC-001）：Python **不读** IndexedDB。前端每次持久化后把「分片清单 +
版本 + 最后写入时间 + 体积 + 是否需重取」POST 上来，服务端落到
`<data_root>/state/frontend-index.json`，`ldst state inspect` 只读这个文件；
反向的 `ldst state reset` 只在同一个文件里登记 `pending_reset`，前端启动时
GET 读回、删掉对应分片再带 `ack_reset` 回执。

开发态这个端点由 Vite 插件实现（`frontend/vite.config.ts`），生产态 `frontend/dist`
是静态产物、不含中间件，于是 7 条路由的 console 各留一条 `405 POST /__state/index`
（`docs/UI-SPEC.md` §12.2 第 2 条登记至今）。本模块就是生产态的那一份实现，
**语义与 Vite 中间件逐条对齐**：同一套字段、同样的缺省值、同样的部分上报合并
（只带 `ack_reset` 的上报不清空概况）、同样的原子写入。

三条设计口径：

- **位置**：路径不是 `/api/*`，所以它必须先于 SPA 兜底注册；否则 GET 会被
  兜底当成前端路由回 `index.html`，POST 落不到任何路由回 405——正是本单要消除的现象。
- **闸门**：回环来源 + `Host` 白名单，与控制面同一套判据；**不看
  `LDST_CONTROL_ENABLED`**——那份文件里只有分片名与体积，没有交易数据，
  关掉写面的运维仍需要 `state inspect` 出得来东西。
- **写方法额外要正向来源证据**（DEC-033 §2.5 同一判据）：POST 会落盘，
  跨站页面用 `text/plain` 包一段 JSON 就能绕过预检，`Origin` / `X-LDST-Client`
  是挡住它的唯一证据。本机页面同源 `Origin` 天然满足，前端不需要改。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.web import control as control_plane

#: 路由前缀与完整路径（与 `frontend/persist/indexReporter.ts:INDEX_ENDPOINT` 一致）
STATE_INDEX_PREFIX = "/__state"
INDEX_ROUTE = "/__state/index"

#: 落盘位置：与 `cli/state.py:INDEX_RELATIVE`、`frontend/stores/manifest.json:indexFile` 同源
INDEX_RELATIVE = Path("state") / "frontend-index.json"

#: 单次上报的体积上限（同 Vite 的 `MAX_BODY_BYTES`）：超限 413，不写盘
MAX_INDEX_BODY_BYTES = 2 * 1024 * 1024

#: 响应里回报的相对路径：与开发态中间件逐字一致，前端与文档不必分两种口径看
INDEX_RESPONSE_PATH = "data/state/frontend-index.json"

BACKEND_FALLBACK = "indexeddb"
DENIED_READ = "状态索引只接受回环来源"
DENIED_WRITE = "状态索引写入缺少来源证据：需要本机页面 Origin，或显式头 X-LDST-Client: 1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# 载荷归一（与 `vite.config.ts` 的 `readDocument` / POST 合并同形）
# --------------------------------------------------------------------------- #

def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _reset_entry(value: Any) -> dict[str, Any] | None:
    """一条 `pending_reset`：`store` 缺席就整条丢掉（没有它前端无法定位分片）。"""
    if not isinstance(value, Mapping):
        return None
    store = _text_or_none(value.get("store"))
    if store is None:
        return None
    entry: dict[str, Any] = {
        "store": store,
        "requested_at": _text_or_none(value.get("requested_at")) or _now_iso(),
    }
    note = _text_or_none(value.get("note"))
    if note is not None:
        entry["note"] = note
    return entry


def _pending_reset(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        entry = _reset_entry(item)
        if entry is None or entry["store"] in seen:
            continue
        seen.add(str(entry["store"]))
        entries.append(entry)
    return entries


def empty_document() -> dict[str, Any]:
    """缺省形状与开发态一致：没有上报过时字段如实为 `null` / `{}`，不假装有数据。"""
    return {
        "backend": BACKEND_FALLBACK,
        "schema_version": None,
        "generated_at": None,
        "stores": {},
        "pending_reset": [],
    }


def normalise_document(raw: Any) -> dict[str, Any]:
    """把磁盘 / 请求里的字典投影成契约形状，顺带丢掉清单外的键。

    白名单之外不留：将来前端往上报里加字段，落盘文件与 `state inspect` 的读法都不用改。
    """
    source = raw if isinstance(raw, Mapping) else {}
    document = empty_document()
    document["backend"] = _text_or_none(source.get("backend")) or BACKEND_FALLBACK
    document["schema_version"] = _number_or_none(source.get("schema_version"))
    document["generated_at"] = _text_or_none(source.get("generated_at"))
    document["stores"] = _mapping_or_none(source.get("stores")) or {}
    document["pending_reset"] = _pending_reset(source.get("pending_reset"))
    return document


def merge_report(
    previous: dict[str, Any], body: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """把一次上报合进上一份，返回 (新文档, 本次回执清除的重置登记)。

    缺字段一律**保留上一份**而不是补空值：只带 `ack_reset` 的回执上报是常态，
    把 `stores` 清空会让 `state inspect` 以为分片都消失了。
    """
    ack_reset = body.get("ack_reset")
    acked: list[str] = []
    if isinstance(ack_reset, list):
        acked = [item for item in ack_reset if isinstance(item, str)]
    cleared = list(dict.fromkeys(acked))
    pending = [item for item in previous["pending_reset"] if item["store"] not in acked]

    document = dict(previous)
    report_backend = _text_or_none(body.get("backend"))
    if report_backend is not None:
        document["backend"] = report_backend
    report_version = _number_or_none(body.get("schema_version"))
    document["schema_version"] = (
        report_version if report_version is not None else previous["schema_version"]
    )
    report_generated = _text_or_none(body.get("generated_at"))
    document["generated_at"] = report_generated or previous["generated_at"]
    report_stores = _mapping_or_none(body.get("stores"))
    document["stores"] = report_stores if report_stores is not None else previous["stores"]
    document["pending_reset"] = pending
    return document, cleared


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #

def _read_document(path: Path) -> dict[str, Any]:
    """读索引文件；不存在 / 半截 / 形状不对都当空索引（同 Vite，不让巡检或上报卡死）。"""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_document()
    return normalise_document(parsed)


def _write_document(path: Path, document: dict[str, Any]) -> None:
    """原子写入：同目录临时文件 + `os.replace`，`ldst state inspect` 不会读到半截 JSON。

    临时文件名带随机后缀——CLI 侧 `ldst state reset` 用的是固定的 `<name>.tmp`，
    两边同名会在并发时互相覆盖出坏文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = ""
    try:
        prefix = f"{path.name}."
        handle_fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp, path)
    except OSError:
        _unlink_quietly(temp)
        raise


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 平面
# --------------------------------------------------------------------------- #

class StateIndexPlane:
    """`<data_root>/state/frontend-index.json` 的唯一读写入口。

    读—改—写在进程内串行（`_lock`）：同一次上报里 `pending_reset` 要先读出来
    再按回执剔除，两条并发上报交叉就会把对方刚登记的条目吃掉。跨进程的
    `ldst state reset` 与这里仍有可能交错，那是「谁后写谁赢」的窗口，
    最坏结果是一条重置登记需要前端下次启动再处理一次——不丢数据。
    """

    def __init__(self, data_root: Path) -> None:
        self._path = Path(data_root) / INDEX_RELATIVE
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def document(self) -> dict[str, Any]:
        with self._lock:
            return _read_document(self._path)

    def apply_report(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """合并一次上报并落盘；写不动就 500，**不冒充 200**。"""
        try:
            with self._lock:
                document, cleared = merge_report(_read_document(self._path), body)
                _write_document(self._path, document)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"状态索引无法写入：{exc}") from None
        return {"ok": True, "path": INDEX_RESPONSE_PATH, "cleared_reset": cleared}


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #

async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单；不受 `LDST_CONTROL_ENABLED` 影响。"""
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail=DENIED_READ)
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（状态索引只接受 127.0.0.1 / localhost / ::1）"
            ),
        )


async def _write_guard(request: Request) -> None:
    """写面闸门 = 读面闸门 + 正向来源证据（同 DEC-033 §2.5）。"""
    await _guard(request)
    if not control_plane.write_source_allowed(request):
        raise HTTPException(status_code=403, detail=DENIED_WRITE)
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.strip().lower() not in control_plane.SAME_ORIGIN_SITES:
        raise HTTPException(
            status_code=403,
            detail=f"跨站来源被拒绝：Sec-Fetch-Site={site}（只接受 same-origin / none）",
        )


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

async def _read_body(request: Request) -> dict[str, Any]:
    """收 body 并解析成对象：超限 413（先认 `Content-Length`，再逐块累加），坏 JSON 400。"""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared.strip())
        except ValueError:
            size = -1
        if size > MAX_INDEX_BODY_BYTES:
            raise _too_large()

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_INDEX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="状态索引上报需要一个 JSON 对象")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"上报内容不是合法 JSON：{exc}") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="上报的顶层必须是 JSON 对象")
    return parsed


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"状态索引上报超过 {MAX_INDEX_BODY_BYTES} 字节上限",
    )


def build_router(plane: StateIndexPlane) -> APIRouter:
    """`GET /__state/index` 读回 + `POST /__state/index` 上报（注册在 SPA 兜底之前）。"""
    router = APIRouter(prefix=STATE_INDEX_PREFIX)

    @router.get("/index", dependencies=[Depends(_guard)])
    async def read_index() -> JSONResponse:
        document = await anyio.to_thread.run_sync(plane.document)
        return JSONResponse(document, headers={"Cache-Control": "no-store"})

    @router.post("/index", dependencies=[Depends(_write_guard)])
    async def write_index(request: Request) -> JSONResponse:
        body = await _read_body(request)
        result = await anyio.to_thread.run_sync(plane.apply_report, body)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    # 其余方法显式回 405 + `Allow: GET, POST`。不交给路由层的自动 405：那条只认第一条
    # 部分匹配（GET），会把 POST 藏在外面（开发态中间件给的就是完整两条，口径要一致）。
    @router.api_route(
        "/index",
        methods=["PUT", "PATCH", "DELETE", "OPTIONS"],
        dependencies=[Depends(_guard)],
    )
    async def unsupported() -> JSONResponse:
        return JSONResponse(
            {"ok": False, "error": "只支持 GET / POST"},
            status_code=405,
            headers={"Allow": "GET, POST", "Cache-Control": "no-store"},
        )

    return router


__all__ = [
    "BACKEND_FALLBACK",
    "DENIED_READ",
    "DENIED_WRITE",
    "INDEX_RELATIVE",
    "INDEX_RESPONSE_PATH",
    "INDEX_ROUTE",
    "MAX_INDEX_BODY_BYTES",
    "STATE_INDEX_PREFIX",
    "StateIndexPlane",
    "build_router",
    "empty_document",
    "merge_report",
    "normalise_document",
]
