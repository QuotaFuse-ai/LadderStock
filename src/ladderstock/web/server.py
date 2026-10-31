"""本地工作台：只读 API + 静态看板（默认回环 5950，DEC-004）。

设计约束：

- **只读**：本模块只注册 GET 路由；没有任何写库/写文件接口。POST 等写方法由
  FastAPI 自动回 405。前端的状态持久化走浏览器 IndexedDB（DEC-001），不经这里。
  唯一的两个例外各自成模块、各自成前缀：控制面 `/api/control/*`（DEC-014）与
  研究面 `/api/research/*`（QUOT-275，只有一支续跑 POST）。
- **隐私**：`journal.sqlite` 的内容不进入任何响应；即使复盘 JSON 里混进
  `journal` / `trades` / `positions` / `watchlist` 字段，返回前也会被剥掉。
  唯一例外是 DEC-015 的 `GET /api/journal/stats`：**扁平 JSON、回环独占、只放聚合
  数字**（无 `note` / 逐笔明细 / 标的清单），不进 `LadderEnvelope`，因此天然进不了
  export / MCP / AI 三条出口。
- **空态**：review / sentiment / ladder 在没有数据时返回 200 + 明确
  `payload.data_source_status = "missing"`，而不是 404 或伪造数字。
- **静态**：优先托管 `frontend/dist`（SPA 回退到 `index.html`）；未构建时给出
  提示页，而不是空白 404。

日志统一走 stderr（uvicorn 的默认 handler 被替换，见 `serve()`）。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ladderstock import __version__
from ladderstock.cli.paths import CONFIG_FILES, config_dir, data_dir, project_root
from ladderstock.core.ladder import compute_ladder
from ladderstock.core.listener import LOOPBACK_HOSTS, TokenGuardMiddleware
from ladderstock.core.listener import require_binding_policy as _require_binding_policy
from ladderstock.core.models import LadderEnvelope

#: 隐私字段集合与剥离实现现在只有一份（`core/privacy.py`），web / MCP / export 共用；
#: 这里 re-export 保持既有导入路径不变（audit R-2 / F-2）。
from ladderstock.core.privacy import PRIVATE_KEYS, strip_private
from ladderstock.core.sentiment import compute_sentiment
from ladderstock.core.validation import load_validation, validation_path
from ladderstock.journal.stats import DEFAULT_DEVIATION_THRESHOLD, compute_stats
from ladderstock.watch import status as watch_status
from ladderstock.watch.signal import KIND_LABELS, KIND_THRESHOLD_DOWN, KIND_THRESHOLD_UP
from ladderstock.web.control import (
    ControlPlane,
    build_router,
    client_host,
    control_enabled,
    host_header_allowed,
    is_control_path,
    is_loopback_host,
)
from ladderstock.web.research import ResearchPlane
from ladderstock.web.research import build_router as build_research_router

REVIEW_SUBDIR = "review"
#: 周报产物目录（`ldst weekly` 落盘，QUOT-149）：`<data_dir>/weekly/<week>.json`
WEEKLY_SUBDIR = "weekly"
#: 周标签白名单：它直接拼进文件名，所以必须先过正则再拼路径（同 `research.normalize_code`）
_WEEK_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")
STATE_INDEX = Path("state") / "frontend-index.json"

WEB_PORT = 5950
MCP_PORT = 5951

#: 盯盘快照的可信窗口（`UI-SPEC.md §10` / `state.yaml:watch_snapshot.ttl_sec`）：
#: 超过这个秒数的状态文件一律视为过期，旧值不返回。
WATCH_SNAPSHOT_TTL_SEC = 300

#: 状态文件里带涨跌幅语义的信号类型（`change_pct` 只从它们取值）
_CHANGE_KINDS = frozenset({KIND_THRESHOLD_UP, KIND_THRESHOLD_DOWN})

#: `/api/journal/stats` 允许出站的顶层键（**白名单**，DEC-015 §4）。
#: `symbols`（标的清单）与逐笔偏离明细不在其中：标的价格组合足以还原持仓。
JOURNAL_STATS_FIELDS: tuple[str, ...] = (
    "local_only",
    "store",
    "data_source_status",
    "range",
    "total_trades",
    "buy_count",
    "sell_count",
    "closed_rounds",
    "wins",
    "losses",
    "flats",
    "win_rate",
    "avg_win",
    "avg_loss",
    "profit_loss_ratio",
    "realized_pnl",
    "unmatched_sell_qty",
    "deviation_review",
)

#: 投影后仍递归剔除的字段：自由文本（`list_trades()` 原生带 `note`）与逐笔明细。
#: **不依赖「我没把它拼进去」**——将来有人往投影层加字段，这里兜底（DEC-015 §3.4）。
JOURNAL_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "note",
    "reason",
    "remark",
    "memo",
    "comment",
    "symbols",
    "trades",
    "details",
    "entries",
)

_FORBIDDEN_FIELD_SET = frozenset(JOURNAL_FORBIDDEN_FIELDS)

#: `from` / `to` 只接受严格的 `YYYY-MM-DD`（`date.fromisoformat` 还认 `20261029`）
_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 默认只绑回环（DEC-004）；非回环必须显式声明 + 提供鉴权 Token
REMOTE_ALLOW_ENV = "LDST_WEB_ALLOW_REMOTE"
REMOTE_TOKEN_ENV = "LDST_WEB_TOKEN"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(UTC)


def _parse_day(value: str | None) -> date:
    if not value:
        raise HTTPException(status_code=400, detail="缺少 date（YYYY-MM-DD）")
    try:
        return date.fromisoformat(value)
    except ValueError:
        detail = f"非法日期：{value!r}（需要 YYYY-MM-DD）"
        raise HTTPException(status_code=400, detail=detail) from None


def _parse_day_param(value: str | None, name: str) -> date | None:
    """可选日期参数（`from` / `to`）：空 → `None`；非法 → 400（严格 `YYYY-MM-DD`）。"""
    text = (value or "").strip()
    if not text:
        return None
    detail = f"非法日期：{name}={value!r}（需要 YYYY-MM-DD）"
    if not _DAY_PATTERN.match(text):
        raise HTTPException(status_code=400, detail=detail)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=detail) from None


def _parse_week(value: str | None) -> str:
    """`?week=YYYY-Www`（大写 W、两位周序，小写归一）；非法一律 400。

    周标签要拼文件名，所以先过白名单再拼路径（防目录穿越，同
    `research/checkpoint.normalize_code()` 那道防线）。
    """
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="缺少 week（YYYY-Www）")
    matched = _WEEK_PATTERN.match(text.upper())
    detail = f"非法周标签：{value!r}（需要 YYYY-Www，例如 2026-W44）"
    if not matched:
        raise HTTPException(status_code=400, detail=detail)
    year, number = int(matched.group(1)), int(matched.group(2))
    if not 1 <= number <= 53:
        raise HTTPException(status_code=400, detail=detail)
    try:
        date.fromisocalendar(year, number, 1)
    except ValueError:
        raise HTTPException(status_code=400, detail=detail) from None
    return f"{year}-W{number:02d}"


def _parse_deviation(raw: str | None) -> float:
    """偏离阈值：缺省 `DEFAULT_DEVIATION_THRESHOLD`；非数字或越界一律 400（DEC-015 §6）。"""
    if raw is None or not raw.strip():
        return DEFAULT_DEVIATION_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="deviation 必须是 0~1 之间的小数") from None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise HTTPException(status_code=400, detail="deviation 必须在 0~1 之间（0.01 = 1%）")
    return value


def _journal_guard(request: Request) -> None:
    """`/api/journal/*` 的回环独占闸门（DEC-015 §3.1 / §3.2）。

    只复用控制面的「来源回环 + `Host` 白名单」（后者挡 DNS rebinding），**不挂**
    `control_enabled()`：`LDST_CONTROL_ENABLED=0` 关的是写面，不该连带关掉只读统计。
    即使开了 `LDST_WEB_ALLOW_REMOTE=1` + `LDST_WEB_TOKEN`，非回环来源一律 403——
    Token 管「谁连得进来」，不管「数据该不该出去」。
    """
    denied = HTTPException(status_code=403, detail="日志只接受回环来源")
    if not is_loopback_host(client_host(request.client)):
        raise denied
    if not host_header_allowed(request):
        raise denied


def _drop_forbidden_fields(value: Any) -> Any:
    """递归剔除自由文本 / 明细字段（`note`、`symbols`、`trades` …）。

    投影层已经只拼白名单键，这里再做一遍兜底：**显式丢弃，不靠「没拼进去」**
    （DEC-015 §3.4）。
    """
    if isinstance(value, Mapping):
        return {
            key: _drop_forbidden_fields(item)
            for key, item in value.items()
            if str(key).lower() not in _FORBIDDEN_FIELD_SET
        }
    if isinstance(value, list):
        return [_drop_forbidden_fields(item) for item in value]
    return value


def _count_or_zero(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _money_or_zero(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _sample_or_none(value: Any) -> float | None:
    """无样本的指标保持 `None`（不拿 0 冒充，DEC-015 §6「空 vs 满」）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _journal_stats_payload(
    stats: Mapping[str, Any], *, day_from: date | None, day_to: date | None
) -> dict[str, Any]:
    """把 `compute_stats()` 投影成 `/api/journal/stats` 的契约形状（DEC-015 §6）。

    只放 `JOURNAL_STATS_FIELDS` 白名单键：`symbols`（标的清单）与逐笔偏离明细永不出现。
    `compute_stats()` 返回的 `deviation_review.trades` 与 `PRIVATE_KEYS` 撞名，出站
    `strip_private()` 会把它**静默删掉**，所以这里只保留 `threshold_pct` 与 `count`，
    并保证投影结果过 `strip_private()` 恒等。
    """
    deviation = stats.get("deviation_review")
    deviation = deviation if isinstance(deviation, Mapping) else {}
    payload: dict[str, Any] = {
        "local_only": True,
        "store": "local_sqlite",
        "data_source_status": (
            "ok" if stats.get("data_source_status") == "ok" else "missing"
        ),
        "range": {
            "from": day_from.isoformat() if day_from else None,
            "to": day_to.isoformat() if day_to else None,
        },
        "total_trades": _count_or_zero(stats.get("total_trades")),
        "buy_count": _count_or_zero(stats.get("buy_count")),
        "sell_count": _count_or_zero(stats.get("sell_count")),
        "closed_rounds": _count_or_zero(stats.get("closed_rounds")),
        "wins": _count_or_zero(stats.get("wins")),
        "losses": _count_or_zero(stats.get("losses")),
        "flats": _count_or_zero(stats.get("flats")),
        "win_rate": _sample_or_none(stats.get("win_rate")),
        "avg_win": _sample_or_none(stats.get("avg_win")),
        "avg_loss": _sample_or_none(stats.get("avg_loss")),
        "profit_loss_ratio": _sample_or_none(stats.get("profit_loss_ratio")),
        "realized_pnl": _money_or_zero(stats.get("realized_pnl")),
        "unmatched_sell_qty": _money_or_zero(stats.get("unmatched_sell_qty")),
        "deviation_review": {
            "threshold_pct": _money_or_zero(deviation.get("threshold_pct")),
            "count": _count_or_zero(deviation.get("count")),
        },
    }
    # 白名单收口 + 禁用字段兜底，再交给调用方
    return _drop_forbidden_fields(
        {key: payload[key] for key in JOURNAL_STATS_FIELDS if key in payload}
    )


def _envelope(
    domain: str,
    topic: str,
    day: date | None,
    payload: dict[str, Any],
    *,
    available: bool,
    source_detail: str = "",
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """套 `LadderEnvelope`；`available=False` 时 `data_as_of=None`（METRICS.md §4.3：
    无数据不拿请求日期顶替数据时点）。`fetched_at` 可注入真实取数时刻，
    默认取当前时间。
    """
    envelope = LadderEnvelope(
        domain=domain,  # type: ignore[arg-type]
        topic=topic,
        data_as_of=day if available else None,
        # 兜底：即使上游 payload 混进隐私字段，也在出站前剥掉
        payload={
            **strip_private(payload),
            "data_source_status": "ok" if available else "missing",
        },
        source="ladderstock",
        source_detail=source_detail,
        fetched_at=fetched_at if fetched_at is not None else _now(),
        freshness="cached",
        confidence="medium" if available else "low",
        source_status="ok" if available else "partial",
    )
    return envelope.model_dump(mode="json")


def _resolve_data_root(data_root: Path | None) -> Path:
    return Path(data_root) if data_root is not None else data_dir()


def _parse_status_timestamp(value: Any) -> datetime | None:
    """解析状态文件里的时间戳（`cli/watch.py` 写的是**本地 naive** ISO）；失败返回 None。

    读不出来就不声称知道快照有多新——调用方据此判 `available=false`。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        return None
    if parsed.tzinfo is not None:  # 外部手工写的文件可能带时区：换成本地 naive 再比
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _watch_age(document: Mapping[str, Any]) -> tuple[datetime | None, float]:
    """状态文件的取数时刻与其年龄（秒）；没有可解析时间戳时返回 `(None, 0.0)`。"""
    stamp = _parse_status_timestamp(document.get("last_run_at")) or _parse_status_timestamp(
        document.get("started_at")
    )
    if stamp is None:
        return None, 0.0
    return stamp, (datetime.now() - stamp).total_seconds()


def _format_age(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, rest = divmod(total, 60)
    return f"{minutes} 分 {rest} 秒" if minutes else f"{rest} 秒"


def _status_trade_date(document: Mapping[str, Any]) -> date | None:
    raw = document.get("date")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _watch_rows(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 `recent_events` 归并成「每股一行」的快照行。

    状态文件里只有 `ts / code / kind / value / source_detail`：`name` /
    `limit_up_count` 没有来源，一律给 `null`，**不借别的数据源补造**；
    `change_pct` 只从越阈信号（`threshold_up` / `threshold_down`）的 `value` 取。
    """
    events = document.get("recent_events")
    if not isinstance(events, list):
        return []

    latest: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        code = event.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        # 事件按时间升序落盘：同一 code 后出现的覆盖先出现的
        latest[code.strip()] = event

    rows: list[dict[str, Any]] = []
    for code, event in latest.items():
        kind = event.get("kind")
        kind_text = kind.strip() if isinstance(kind, str) and kind.strip() else None
        value = event.get("value")
        change_pct = (
            float(value)
            if kind_text in _CHANGE_KINDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            else None
        )
        name = event.get("name")
        count = event.get("limit_up_count")
        rows.append(
            {
                "code": code,
                "name": name.strip() if isinstance(name, str) and name.strip() else None,
                "limit_up_count": (
                    count if isinstance(count, int) and not isinstance(count, bool) else None
                ),
                "change_pct": change_pct,
                "status": KIND_LABELS.get(kind_text, kind_text) if kind_text else None,
            }
        )
    return rows


def _validation_conditions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """验证条件映射到 `ValidationPayload.conditions`（`{code,trigger,note}`）。

    源文件的条件只有 `id / text / metric / operator / value`，没有标的代码，
    因此 `code` 给 `null`；已对账的条件把结论写进 `note`。
    """
    verdicts: dict[str, Any] = {}
    reconcile = document.get("reconcile")
    if isinstance(reconcile, dict):
        for row in reconcile.get("results") or []:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                verdicts[row["id"]] = row.get("passed")

    conditions = document.get("conditions")
    if not isinstance(conditions, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in conditions:
        if not isinstance(item, dict):
            continue
        note: str | None = None
        cid = item.get("id")
        if isinstance(cid, str) and cid in verdicts:
            passed = verdicts[cid]
            if passed is True:
                note = "对账：通过"
            elif passed is False:
                note = "对账：未通过"
            else:
                note = "对账：无法判定"
        code = item.get("code")
        text = item.get("text")
        rows.append(
            {
                "code": code.strip() if isinstance(code, str) and code.strip() else None,
                "trigger": text.strip() if isinstance(text, str) and text.strip() else None,
                "note": note,
            }
        )
    return rows


def _watch_unavailable_detail(read: watch_status.StatusRead) -> str:
    """状态文件不可用时的 `source_detail`：说清是哪一种不可用，不给旧值。"""
    path = read.path
    if read.state == watch_status.STATE_MISSING:
        return f"盯盘状态文件不存在：{path}（先运行 ldst watch start）"
    if read.state == watch_status.STATE_CORRUPT:
        return f"盯盘状态文件不可读：{path}（{read.error}）"
    return f"盯盘状态文件结构不合法：{path}（{read.error}）"


def default_dist_dir() -> Path:
    """前端构建产物目录：`frontend/dist`（Vite 默认 outDir）。"""
    return project_root() / "frontend" / "dist"


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #

def create_app(
    *,
    data_root: Path | None = None,
    dist_dir: Path | None = None,
    control_plane: Any | None = None,
    research_plane: Any | None = None,
) -> FastAPI:
    """构建 FastAPI 应用；`data_root` / `dist_dir` / `control_plane` / `research_plane`
    可注入，便于测试。

    控制面（`/api/control/*`，DEC-014）与数据面严格分层：控制面单独成前缀，
    路由注册在 SPA 兜底之前；`control_plane` 不传时按 `data_root` 自建。
    """
    root = _resolve_data_root(data_root)
    dist = Path(dist_dir) if dist_dir is not None else default_dist_dir()

    app = FastAPI(
        title="LadderStock 本地工作台",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def control_switch(request: Request, call_next: Any) -> Any:
        """控制面总开关的**方法无关**短路（QUOT-123 P1-1 / DEC-014 §1）。

        `LDST_CONTROL_ENABLED=0` 时 `/api/control` 前缀下所有方法（含 HEAD /
        OPTIONS / 裸路径 / 未知子路径）一律回与 SPA 兜底同形的 404；不能交给路由层，
        否则 HEAD/OPTIONS 会先被打成 405 并带出 `Allow` 头，等于告诉外面「这里有
        控制面」。数据面 `/api/*` 不受影响。
        """
        if not control_enabled() and is_control_path(request.url.path):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    # -------------------------------------------------------------- 只读 API

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "name": "ladderstock",
            "version": __version__,
            "time": _now().isoformat(timespec="seconds"),
            "data_dir": str(root),
            "read_only": True,
        }

    @app.get("/api/review")
    def review(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        target = _parse_day(date_str)
        path = root / REVIEW_SUBDIR / f"{target.isoformat()}.json"
        if not path.is_file():
            return _envelope(
                "review",
                "review",
                target,
                {"date": target.isoformat(), "review": None},
                available=False,
                source_detail=f"local review cache: {path.name} 不存在",
            )
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"复盘文件不可读：{exc}") from None
        clean = strip_private(parsed)
        if isinstance(clean, dict) and "domain" in clean and "payload" in clean:
            clean.setdefault("source", "ladderstock")
            clean.setdefault("source_detail", f"local review cache: {path.name}")
            clean.setdefault("fetched_at", _now().isoformat(timespec="seconds"))
            return clean
        return _envelope(
            "review",
            "review",
            target,
            {"date": target.isoformat(), "review": clean},
            available=True,
            source_detail=f"local review cache: {path.name}",
        )

    @app.get("/api/weekly")
    def weekly(
        week: Annotated[str | None, Query(alias="week", description="YYYY-Www")] = None,
    ) -> dict[str, Any]:
        """周报只读视图（R11 / QUOT-149）：读 `weekly/<week>.json`，不重算任何东西。

        与 `/api/review` 同一条空态口径：没有产物就 200 + `data_source_status=missing`，
        不 404、不伪造数字；`domain` 复用 `review`（`LadderEnvelope` 的域名枚举里没有 weekly）。
        """
        tag = _parse_week(week)
        path = root / WEEKLY_SUBDIR / f"{tag}.json"
        if not path.is_file():
            return _envelope(
                "review",
                "weekly_report",
                None,
                {"week": tag, "weekly": None},
                available=False,
                source_detail=f"local weekly cache: {path.name} 不存在",
            )
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"周报文件不可读：{exc}") from None
        clean = strip_private(parsed)
        if not isinstance(clean, dict):
            raise HTTPException(status_code=500, detail="周报结构不合法：顶层不是对象")
        session_days = [
            str(item)
            for item in ((clean.get("facts") or {}).get("session_days") or [])
            if isinstance(item, str)
        ]
        as_of: date | None = None
        if session_days:
            try:
                as_of = date.fromisoformat(session_days[-1][:10])
            except ValueError:
                as_of = None
        available = bool(clean.get("available")) and as_of is not None
        return _envelope(
            "review",
            "weekly_report",
            as_of,
            {"week": tag, "weekly": clean},
            available=available,
            source_detail=str((clean.get("facts") or {}).get("source_detail") or path.name),
        )

    @app.get("/api/sentiment")
    def sentiment(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        target = _parse_day(date_str)
        result = compute_sentiment(target, root=root)
        available = bool(result.get("available"))
        return _envelope(
            "sentiment",
            "shortline_sentiment",
            target,
            {"sentiment": result},
            available=available,
            source_detail=str(result.get("source_detail") or ""),
        )

    @app.get("/api/ladder")
    def ladder(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        target = _parse_day(date_str)
        result = compute_ladder(target, root=root)
        available = bool(result.get("available"))
        return _envelope(
            "sentiment",
            "limit_ladder",
            target,
            {"ladder": result},
            available=available,
            source_detail=str(result.get("source_detail") or ""),
        )

    @app.get("/api/watch/snapshot")
    def watch_snapshot() -> dict[str, Any]:
        """盯盘快照（`watch_snapshot` 分片，TTL 300s）：过期即不返回旧值。

        数据源只有一份：`watch/status.py` 的状态文件（QUOT-109 定下的唯一事实来源）。
        文件缺失 / 结构不合法 / 超过 TTL / 时间戳不可解析时一律 `available=false`
        + `rows=[]`，**不拿旧快照充数**。
        """
        path = watch_status.status_path(root)
        read = watch_status.read_status_document(path)
        if not read.ok:
            return _envelope(
                "sentiment",
                "watch_snapshot",
                None,
                {"trade_date": None, "rows": []},
                available=False,
                source_detail=_watch_unavailable_detail(read),
            )

        document = read.document or {}
        fetched_at, age_seconds = _watch_age(document)
        if fetched_at is None:
            return _envelope(
                "sentiment",
                "watch_snapshot",
                None,
                {"trade_date": None, "rows": []},
                available=False,
                source_detail=(
                    f"盯盘状态文件缺少可解析的时间戳（last_run_at / started_at）：{path}"
                    "；无法确认快照时效，请重新执行 ldst watch start"
                ),
            )
        if age_seconds > WATCH_SNAPSHOT_TTL_SEC:
            return _envelope(
                "sentiment",
                "watch_snapshot",
                None,
                {"trade_date": None, "rows": []},
                available=False,
                source_detail=(
                    f"快照已超过 TTL（{_format_age(age_seconds)} > {WATCH_SNAPSHOT_TTL_SEC} 秒），"
                    "旧值已隐藏，请重新取数"
                ),
                fetched_at=fetched_at,
            )

        trade_date = _status_trade_date(document)
        payload: dict[str, Any] = {
            "trade_date": trade_date.isoformat() if trade_date is not None else None,
            "rows": _watch_rows(document),
        }
        return _envelope(
            "sentiment",
            "watch_snapshot",
            trade_date,
            payload,
            available=True,
            source_detail=watch_status.status_detail(document),
            fetched_at=fetched_at,
        )

    @app.get("/api/validation")
    def validation(
        date_str: Annotated[str | None, Query(alias="date", description="YYYY-MM-DD")] = None,
    ) -> dict[str, Any]:
        """明日验证条件（`validation_draft` 分片）：只读，不现场触发 `reconcile()`。

        没有落盘文件就如实 `available=false`（先跑 `ldst review --date`），
        不做重计算、不产生副作用。
        """
        target = _parse_day(date_str)
        path = validation_path(target, root)
        document = load_validation(target, root=root)
        if document is None:
            return _envelope(
                "validation",
                "validation",
                None,
                {"trade_date": target.isoformat(), "conditions": [], "reconcile": None},
                available=False,
                source_detail=(
                    f"local validation cache 不存在：{path.name}"
                    f"（先运行 ldst review --date {target}）"
                ),
            )

        reconcile = document.get("reconcile")
        detail = f"local validation cache: {path.name}"
        if isinstance(reconcile, dict):
            detail += f"；对账 {reconcile.get('status') or '未知'}"
            if reconcile.get("next_session"):
                detail += f"（下一交易日 {reconcile['next_session']}）"
        else:
            detail += "；尚未对账（ldst validate reconcile）"
        return _envelope(
            "validation",
            "validation",
            target,
            {
                "trade_date": target.isoformat(),
                "conditions": _validation_conditions(document),
                "reconcile": reconcile if isinstance(reconcile, dict) else None,
            },
            available=True,
            source_detail=detail,
        )

    @app.get("/api/journal/stats")
    def journal_stats(
        request: Request,
        day_from: Annotated[
            str | None, Query(alias="from", description="起始日 YYYY-MM-DD（含）")
        ] = None,
        day_to: Annotated[
            str | None, Query(alias="to", description="结束日 YYYY-MM-DD（含）")
        ] = None,
        deviation: Annotated[
            str | None, Query(description="偏离计划阈值，0 ≤ x ≤ 1，默认 0.01")
        ] = None,
    ) -> JSONResponse:
        """交易日志聚合统计（DEC-015 Tier 1）：**本机只读、扁平 JSON、非 `LadderEnvelope`**。

        五条硬约束：回环独占 / 只 GET / 不出自由文本 / 键名不撞 `PRIVATE_KEYS` / 空态 200。
        只回聚合数字：`symbols`（标的清单）、逐笔偏离明细、`note` 一律不出。
        """
        _journal_guard(request)
        start = _parse_day_param(day_from, "from")
        end = _parse_day_param(day_to, "to")
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=400, detail="from 不能晚于 to")
        threshold = _parse_deviation(deviation)

        stats = compute_stats(
            day_from=start,
            day_to=end,
            deviation_threshold=threshold,
            root=root,
        )
        payload = _journal_stats_payload(stats, day_from=start, day_to=end)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/api/state/config")
    def state_config() -> dict[str, Any]:
        """配置与数据目录摘要——只报存在性与版本，不回显任何配置内容/凭据。"""
        base = config_dir()
        files = [
            {"file": name, "present": (base / name).is_file()} for name in CONFIG_FILES
        ]
        files.append(
            {
                "file": "schema/",
                "present": (base / "schema").is_dir(),
            }
        )
        index = root / STATE_INDEX
        return {
            "ok": True,
            "read_only": True,
            "config_dir": str(base),
            "data_dir": str(root),
            "files": files,
            "state_index_present": index.is_file(),
            "state_schema_version": _state_schema_version(base / "state.yaml"),
            "ports": {"web": WEB_PORT, "mcp": MCP_PORT},
        }

    # ---------------------------------------------------- 控制面（DEC-014）
    #
    # 读写面单独成前缀 `/api/control/*`：`LDST_CONTROL_ENABLED=0` 时整族 404，
    # 数据面 7 个只读接口不受影响；非回环来源一律 403（见 `control._guard`）。
    #
    # 关掉控制面时**不构造** ControlPlane：它的启动校验（peer 目录不得在共享盘上，
    # QUOT-194 P1-3）会对整机路径做判定，关掉控制面的人只想要数据面，不该被它拦下。
    plane = control_plane
    if plane is None and control_enabled():
        plane = ControlPlane(data_root=root)
    if plane is not None:
        app.include_router(build_router(plane))

    # ------------------------------------------------ 研究面（QUOT-275 / SPEC-RESEARCH-PAGES §2）
    #
    # `/api/research/*` 三接口：六段只读视图、单段续跑、任务进度。字段照 SPEC 定稿，
    # 前端不改字段即可从演示态切真实通道。闸门同控制面（回环 + Host + 写方法来源证据），
    # 但**不受 `LDST_CONTROL_ENABLED` 影响**——读取属于数据面。
    research = research_plane if research_plane is not None else ResearchPlane(data_root=root)
    app.include_router(build_research_router(research))

    # ------------------------------------------------------------ 静态看板

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        page = dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse(_hint_page(), status_code=200)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Any:
        if full_path == "api" or full_path.startswith(("api/", "openapi", "docs", "redoc")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if dist.is_dir():
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not Found") from None
            if candidate.is_file():
                return FileResponse(candidate)
            index_page = dist / "index.html"
            if index_page.is_file():
                return FileResponse(index_page)  # SPA 路由回退
        return HTMLResponse(_hint_page(), status_code=200)

    return app


def _state_schema_version(path: Path) -> int | None:
    """只读 `state.yaml` 的 schema_version；失败时返回 None（不抛给前端）。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("schema_version")
    return value if isinstance(value, int) else None


def _hint_page() -> str:
    page = _static_dir() / "index.html"
    try:
        return page.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - 静态文件缺失时的兜底
        return (
            "<!doctype html><meta charset='utf-8'><title>LadderStock</title>"
            "<h1>LadderStock 本地看板</h1>"
            "<p>前端产物尚未构建：请在 <code>frontend/</code> 下执行 "
            "<code>npm install &amp;&amp; npm run build</code>，然后重启 "
            "<code>ldst run</code>。</p>"
        )


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def log_config() -> dict[str, Any]:
    """uvicorn 日志配置：全部写 stderr（docs/CLI.md §F）。"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": "ldst: %(levelname)s %(message)s"}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": "WARNING", "propagate": False},
        },
    }


def require_binding_policy(host: str) -> str | None:
    """决定监听地址能否放行；返回需要强制的 Token（回环不需要，返回 None）。

    判据本体已抽到 `core/listener.py`（与 `ldst mcp http` 共用同一份，DRY）：
    回环放行；非回环必须 `LDST_WEB_ALLOW_REMOTE=1` + `LDST_WEB_TOKEN`（≥16 字符），
    否则抛 `OSError`，`cli/run.py` 按配置错误退出（码 3）。

    非回环时所有 `/api/*` 请求都要求 `Authorization: Bearer <token>` 或
    `X-Web-Token: <token>`（见 `_install_token_guard`）。
    """
    return _require_binding_policy(
        host,
        allow_env=REMOTE_ALLOW_ENV,
        token_env=REMOTE_TOKEN_ENV,
        surface="本地看板",
    )


def _install_token_guard(app: FastAPI, token: str) -> None:
    """给 `/api/*` 挂 Bearer Token 校验；静态页面不含隐私数据，保持可访问。"""
    app.add_middleware(
        TokenGuardMiddleware,
        token=token,
        path_prefix="/api/",
        extra_header="X-Web-Token",
    )


def serve(app: FastAPI, *, host: str, port: int) -> None:
    """阻塞运行 uvicorn；Ctrl+C 由调用方捕获（uvicorn 自己会先优雅关闭）。

    启动前先过绑定策略：默认只允许回环；非回环必须显式开关 + Token，
    并据此给应用挂上鉴权中间件。
    """
    import uvicorn

    token = require_binding_policy(host)
    if token:
        _install_token_guard(app, token)
    uvicorn.run(app, host=host, port=port, log_config=log_config(), access_log=False)


__all__ = [
    "LOOPBACK_HOSTS",
    "PRIVATE_KEYS",
    "REMOTE_ALLOW_ENV",
    "REMOTE_TOKEN_ENV",
    "create_app",
    "default_dist_dir",
    "log_config",
    "require_binding_policy",
    "serve",
]
