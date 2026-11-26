"""F11 always-on 简报：晨报 / 收盘复盘 / 持仓异动。

与 `watch/digest.py` 的分工：

- `digest` 是**单类摘要**（盘前价格 / 收盘盈亏 / 自选变化），一份 job 一份正文，
  `dedupe_key = job:trade_day`；
- 这里是**组合简报**：一份简报 = ≤5 个条目，每个条目自带来源与指纹，
  同一天重复生成不会重复推送（跨 job 也去重——晨报和收盘复盘共用的那段行情
  只推一次）。条目落 `journal/inbox`（提醒中心）并按开关走 `watch/notifier`。

三条硬口径：

1. **只写有来源的事实**：每条都注明取自哪个域 / 哪个文件；取不到就写「未取到（原因）」，
   不补 0、不猜；
2. **每日 ≤5 条**：超出就截断并在 detail 里写明「另有 N 条未推送」——宁少勿滥；
3. **零重复推送**：指纹 = sha1(标题 + 正文行)，与 job 名无关；同一交易日内命中即跳过。

入口：
- `build_morning` / `build_close_review` / `build_position_alerts` → `Brief`
- `publish` → 去重 + 写提醒 + 投递，返回本次真实推送了什么
- CLI `ldst brief morning|close|alerts|list`，API `GET /api/brief/today`
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.core import quotes
from ladderstock.core.intraday import local_moment
from ladderstock.data import archive
from ladderstock.journal import inbox as inbox_store
from ladderstock.journal import position as position_store
from ladderstock.journal import watchlist as watchlist_store
from ladderstock.watch import digest as digest_module
from ladderstock.watch import notifier

BRIEF_SUBDIR = "brief"

BRIEF_MORNING = "morning"
BRIEF_CLOSE = "close_review"
BRIEF_ALERTS = "position_alerts"

BRIEFS: tuple[str, ...] = (BRIEF_MORNING, BRIEF_CLOSE, BRIEF_ALERTS)

BRIEF_LABELS: dict[str, str] = {
    BRIEF_MORNING: "晨报",
    BRIEF_CLOSE: "收盘复盘",
    BRIEF_ALERTS: "持仓异动",
}

#: 每份简报最多推几条（文档口径：每日 ≤5 条）
MAX_ITEMS = 5
#: 持仓异动的默认阈值（与公告前置筛同一口径 ±5%）
ALERT_THRESHOLD_PCT = 5.0
#: 异动条目的上限（避免一份简报被 30 只票刷满）
ALERT_ROWS_CAP = 12

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

MISSING = "—"

#: 日程里值得在简报里逐条列出的任务类型（会产出提醒或研究产物）；
#: 采集 / 快照类只报数量，避免简报被 20 个 job 刷满
HIGHLIGHT_KINDS: tuple[str, ...] = ("digest", "notify", "watch_alert", "agent_research", "plan_tick")

LINK_OF: dict[str, str] = {
    BRIEF_MORNING: "/my-stocks",
    BRIEF_CLOSE: "/positions",
    BRIEF_ALERTS: "/positions",
}


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class Item:
    """一条简报：`lines` 是给人看的正文，`source` 说明取数出处。"""

    kind: str
    title: str
    lines: list[str]
    source: str
    link: str | None = None

    @property
    def fingerprint(self) -> str:
        """指纹只看**内容**：同一天同一段正文，不管挂在哪份简报下都只推一次。"""
        body = "\n".join([self.title, *self.lines]).strip()
        return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "lines": list(self.lines),
            "source": self.source,
            "link": self.link,
            "fingerprint": self.fingerprint,
        }


@dataclass
class Brief:
    job: str
    kind: str
    trade_day: str
    status: str
    detail: str
    items: list[Item] = field(default_factory=list)
    generated_at: str = ""

    @property
    def title(self) -> str:
        return f"{BRIEF_LABELS.get(self.kind, self.kind)} {self.trade_day}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "kind": self.kind,
            "label": BRIEF_LABELS.get(self.kind, self.kind),
            "trade_day": self.trade_day,
            "status": self.status,
            "detail": self.detail,
            "generated_at": self.generated_at,
            "title": self.title,
            "items": [item.as_dict() for item in self.items],
        }


# --------------------------------------------------------------------------- #
# 取数（缺失一律写原因）
# --------------------------------------------------------------------------- #


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


def _config_dir() -> Path:
    from ladderstock.cli.paths import config_dir

    return config_dir()


def _today(moment: datetime) -> str:
    return moment.date().isoformat()


def _pct(value: float | None) -> str:
    return MISSING if value is None else f"{value:+.2f}%"


def _price(value: float | None) -> str:
    return MISSING if value is None else f"{value:.2f} 元"


def _pool_codes(root: Path | None) -> list[str]:
    """代码池：口径与定时采集完全一致（`sync.yaml` 的 `daily_k.codes`），不另建一份。"""
    path = _config_dir() / "sync.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    # 根键是 `sync.datasets.daily_k.codes`；兼容顶层写法（有人手工改过配置）
    block: Any = None
    if isinstance(raw, dict):
        sync_block = raw.get("sync")
        if isinstance(sync_block, dict):
            block = sync_block.get("daily_k")
            if block is None:
                datasets = sync_block.get("datasets")
                if isinstance(datasets, dict):
                    block = datasets.get("daily_k")
        if block is None:
            block = raw.get("daily_k")
    codes = block.get("codes") if isinstance(block, dict) else None
    if not isinstance(codes, list):
        return []
    out: list[str] = []
    for code in codes:
        text = str(code).strip().upper()
        if text and text not in out:
            out.append(text)
    return out


def _held_and_watched(root: Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        holdings = list(position_store.list_positions(root=root))
    except Exception:  # noqa: BLE001 - 账本读不动不该让简报整份失败
        holdings = []
    try:
        watch = watchlist_store.list_watch(root=root)
    except Exception:  # noqa: BLE001
        watch = []
    codes: list[str] = []
    for row in holdings:
        code = str(row.get("symbol") or row.get("code") or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    for row in watch:
        code = str(row.get("code") or row.get("symbol") or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    return holdings, codes


def _price_moves(
    codes: list[str], day: str, root: Path | None, threshold: float
) -> tuple[list[dict[str, Any]], str]:
    """当日涨跌（`daily_k` 口径），只回超过阈值的条目；返回值带取数原因。"""
    if not codes:
        return [], "本机没有自选或持仓"
    try:
        daily, status = quotes.daily_change_map(codes, day, root=root)
    except Exception as exc:  # noqa: BLE001
        return [], f"行情读取异常：{type(exc).__name__}"
    rows: list[dict[str, Any]] = []
    for code in codes:
        info = daily.get(code) or {}
        if info.get("status") != quotes.STATUS_OK:
            continue
        close = info.get("close")
        prev = info.get("prev_close")
        try:
            close_value = float(close) if close is not None else None
            prev_value = float(prev) if prev is not None else None
        except (TypeError, ValueError):
            continue
        if close_value is None or prev_value is None or prev_value <= 0:
            continue
        pct = (close_value - prev_value) / prev_value * 100.0
        if abs(pct) >= threshold:
            rows.append({"code": code, "close": close_value, "prev_close": prev_value, "pct": pct})
    rows.sort(key=lambda row: (-abs(row["pct"]), row["code"]))
    reason = "" if rows or not codes else str(status.get("reason") or "")
    return rows, reason


def _movers(root: Path | None, day: str, threshold: float) -> tuple[list[str], str]:
    """池 ∪ 池外异动（复用 `news/candidates` 的口径），返回人读行与来源说明。"""
    codes = _pool_codes(root)
    if not codes:
        return ["代码池为空（`sync.yaml` 的 `daily_k.codes` 没配对）"], "代码池"
    try:
        from ladderstock.news import candidates as candidates_module

        # data_root 与代码池都显式传入：简报不依赖进程默认目录
        result = candidates_module.announcement_candidates(
            codes,
            data_root=root if root is not None else _data_dir(),
            threshold=threshold,
            today=date.fromisoformat(day),
        )
    except Exception as exc:  # noqa: BLE001
        return [f"异动快照读不出来（{type(exc).__name__}），本轮不判断异动"], "news/candidates"
    if result.get("fallback"):
        reason = str(result.get("fallback_reason") or result.get("note") or "原因未给")
        return [f"异动快照不可用（{reason}），本轮按全池处理"], "news/candidates"
    movers = list(result.get("movers") or [])
    lines = [
        f"分母 = 池 {result.get('pool_size')} ∪ 池外异动 {len(result.get('extra') or [])}"
        f" = {result.get('union_size') or len(result.get('union') or [])}；"
        f"阈值 ±{threshold:g}%；行情日 {result.get('snapshot_day') or MISSING}"
    ]
    for row in movers[:ALERT_ROWS_CAP]:
        flag = "（池外）" if not row.get("in_pool") else ""
        lines.append(f"{row.get('code')} {row.get('name') or ''} {_pct(row.get('pct'))}{flag}")
    if not movers:
        lines.append("当日没有异动票（池内外都没有超过阈值）")
    elif len(movers) > ALERT_ROWS_CAP:
        lines.append(f"另有 {len(movers) - ALERT_ROWS_CAP} 只异动未列出")
    return lines, f"news/candidates · {result.get('snapshot_day') or MISSING}"


def _announcement_status(root: Path | None, day: str) -> tuple[list[str], str]:
    """公告/研报采集状态：读当天 sidecar（前置筛的落盘），没跑过就照实说。"""
    base = root if root is not None else _data_dir()
    try:
        from ladderstock.news import candidates as candidates_module

        data = candidates_module.read_candidates(base, day)
    except Exception as exc:  # noqa: BLE001
        return [f"候选侧车读不出来（{type(exc).__name__}）"], "news/candidates"
    if not data:
        return (
            [f"今天还没跑过公告前置筛采集（news_candidates/{day}.json 不存在），公告覆盖数未知"],
            f"news_candidates/{day}.json（缺）",
        )
    requested = data.get("requested_codes") or []
    collected = data.get("collected") or {}
    movers = data.get("movers") or []
    denominator = len(data.get("union") or []) or data.get("union_size") or 0
    lines = [
        f"分母 {denominator} 只 · 请求 {len(requested)} 只 · 已采 {len(collected)} 只"
        f" · 当天异动 {len(movers)} 只"
    ]
    datasets = data.get("ann_datasets") or []
    if datasets:
        lines.append("公告数据集：" + "、".join(str(item) for item in datasets))
    if collected:
        head = list(collected.items())[:6]
        lines.append("落盘：" + "、".join(f"{name}={value}" for name, value in head))
    return lines, f"news_candidates/{day}.json"


def _schedule_lines(day: str) -> tuple[list[str], str]:
    """今日日程：只列 `config/schedule.yaml` 里启用的任务与时间，不预测执行结果。"""
    path = _config_dir() / "schedule.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ["日程配置读不出来（config/schedule.yaml）"], "schedule.yaml"
    jobs = raw.get("jobs") if isinstance(raw, dict) else None
    if not isinstance(jobs, list):
        return ["日程配置里没有 jobs 列表"], "schedule.yaml"
    rows: list[str] = []
    others: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("enabled") is False:
            continue
        name = str(job.get("name") or job.get("id") or "").strip()
        trigger = str(job.get("trigger") or "").strip()
        kind = str(job.get("kind") or "").strip()
        if not name:
            continue
        # name 一般已带时间前缀（"07:30 题材资讯采集"），别把时间写两遍
        head = name if trigger and name.startswith(trigger) else f"{trigger or MISSING} {name}"
        line = head + (f"（{kind}）" if kind else "")
        # 简报只列「会产出提醒/研究」的任务；采集/快照类只给数量，不然一份简报被
        # 20 个 job 刷满，真正需要人看的那些反而被挤掉
        (rows if kind in HIGHLIGHT_KINDS else others).append(line)
    lines = [f"{day} 共 {len(rows) + len(others)} 个启用任务"]
    lines += rows[:8]
    if len(rows) > 8:
        lines.append(f"另有 {len(rows) - 8} 个提醒/研究类任务未列出")
    if others:
        preview = "、".join(item.split(" ")[-1] or item for item in others[:4])
        lines.append(f"其余 {len(others)} 个是采集/快照类（{preview}…）")
    return lines, "config/schedule.yaml"


def _research_lines(root: Path | None, day: str) -> tuple[list[str], str]:
    """今天/最近有什么研究产物可读（六段档案 + 合议报告），只报已有产物。"""
    lines: list[str] = []
    source_parts: list[str] = []
    try:
        from ladderstock.research import archive as research_archive

        items = research_archive.scan(root=root)
        if not items:
            lines.append("研究档案为空（还没跑过六段研究）")
        else:
            newest = items[0]
            lines.append(
                f"最近研究 {newest['code']} · {newest['date']} "
                f"（{newest['stage_count']}/{newest['total_stages']} 段）"
            )
            today_items = [item for item in items if item["date"] == day]
            lines.append(f"{day} 新增 {len(today_items)} 份" if today_items else f"{day} 没有新的研究档案")
        source_parts.append("research_archive")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"研究档案读不出来（{type(exc).__name__}）")
    try:
        from ladderstock.research import debate as debate_bridge

        reports = debate_bridge.list_reports(root=None, limit=3)
        if reports:
            lines.append(f"合议报告 {len(reports)} 份可读，最近 {reports[0]['run_id']}")
        else:
            lines.append("还没有合议报告")
        source_parts.append("senate/reports")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"合议报告清单读不出来（{type(exc).__name__}）")
    return lines, " · ".join(source_parts) or "研究产物"


# --------------------------------------------------------------------------- #
# 三类简报
# --------------------------------------------------------------------------- #


def build_morning(
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
    threshold: float = ALERT_THRESHOLD_PCT,
    limit: int = MAX_ITEMS,
) -> Brief:
    """晨报 = 盘前行情 + 池内外异动 + 公告采集状态 + 今日日程 + 研究就绪。"""
    moment = local_moment(now, timezone)
    day = _today(moment)
    items: list[Item] = []

    summary = digest_module.build(
        "brief:morning", digest_module.SUMMARY_PREMARKET, now=moment, root=root, registry=registry
    )
    items.append(
        Item(
            kind="quotes",
            title="盘前行情" if summary.status == STATUS_OK else "盘前行情（未生成）",
            lines=[summary.detail, *summary.lines][:12],
            source=f"watch/digest · premarket · {summary.status}",
            link=LINK_OF[BRIEF_MORNING],
        )
    )

    lines, source = _movers(root, day, threshold)
    items.append(Item(kind="movers", title="池内外异动", lines=lines, source=source, link="/config"))

    lines, source = _announcement_status(root, day)
    items.append(Item(kind="announcements", title="公告/研报采集", lines=lines, source=source, link="/config"))

    lines, source = _schedule_lines(day)
    items.append(Item(kind="schedule", title="今日日程", lines=lines, source=source, link="/config"))

    lines, source = _research_lines(root, day)
    items.append(Item(kind="research", title="研究产物", lines=lines, source=source, link="/research"))

    return _finalize("brief:morning", BRIEF_MORNING, day, items, limit=limit, moment=moment)


def build_close_review(
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,  # 收盘链路不取数，占位为了与 build_morning 同形
    threshold: float = ALERT_THRESHOLD_PCT,
    limit: int = MAX_ITEMS,
) -> Brief:
    """收盘复盘 = 收盘摘要 + 持仓异动 + 研究产物 + 明日日程。"""
    moment = local_moment(now, timezone)
    day = _today(moment)
    items: list[Item] = []

    summary = digest_module.build("brief:close", digest_module.SUMMARY_CLOSE, now=moment, root=root)
    items.append(
        Item(
            kind="pnl",
            title="收盘盈亏" if summary.status == STATUS_OK else "收盘盈亏（未生成）",
            lines=[summary.detail, *summary.lines][:14],
            source=f"watch/digest · close · {summary.status}",
            link=LINK_OF[BRIEF_CLOSE],
        )
    )

    holdings, codes = _held_and_watched(root)
    rows, reason = _price_moves(codes, digest_module._latest_day(root).isoformat(), root, threshold)
    if rows:
        lines = [f"{len(rows)} 只超过 ±{threshold:g}%（持仓 {len(holdings)} 只）"]
        lines += [f"{row['code']} {_pct(row['pct'])}（收盘 {_price(row['close'])}）" for row in rows]
    else:
        lines = [f"没有超过 ±{threshold:g}% 的标的" + (f"（{reason}）" if reason else "")]
    items.append(
        Item(kind="moves", title="持仓/自选异动", lines=lines, source="daily_k · quotes.daily_change_map", link=LINK_OF[BRIEF_CLOSE])
    )

    lines, source = _research_lines(root, day)
    items.append(Item(kind="research", title="今日研究产物", lines=lines, source=source, link="/research"))

    lines, source = _schedule_lines(day)
    items.append(Item(kind="schedule", title="今日已排任务", lines=lines, source=source, link="/config"))

    return _finalize("brief:close", BRIEF_CLOSE, day, items, limit=limit, moment=moment)


def build_position_alerts(
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,  # 同上：纯本地日线口径
    threshold: float = ALERT_THRESHOLD_PCT,
    limit: int = MAX_ITEMS,
) -> Brief:
    """持仓异动：只出超过阈值的持仓/自选；没有就明确写「无异动」，不发空简报。"""
    moment = local_moment(now, timezone)
    day = _today(moment)
    holdings, codes = _held_and_watched(root)
    if not codes:
        return Brief(
            "brief:alerts",
            BRIEF_ALERTS,
            day,
            STATUS_SKIPPED,
            "本机没有持仓或自选，异动检查无对象",
            [],
            generated_at=_iso(moment),
        )
    rows, reason = _price_moves(codes, digest_module._latest_day(root).isoformat(), root, threshold)
    if not rows:
        return Brief(
            "brief:alerts",
            BRIEF_ALERTS,
            day,
            STATUS_SKIPPED,
            f"没有超过 ±{threshold:g}% 的持仓/自选" + (f"（{reason}）" if reason else ""),
            [],
            generated_at=_iso(moment),
        )
    lines = [f"阈值 ±{threshold:g}% · 命中 {len(rows)} 只"]
    lines += [
        f"{row['code']} {_pct(row['pct'])}（{_price(row['prev_close'])} → {_price(row['close'])}）"
        for row in rows[:ALERT_ROWS_CAP]
    ]
    items = [
        Item(
            kind="alert",
            title="持仓/自选异动",
            lines=lines,
            source="daily_k · quotes.daily_change_map",
            link=LINK_OF[BRIEF_ALERTS],
        )
    ]
    return _finalize("brief:alerts", BRIEF_ALERTS, day, items, limit=limit, moment=moment)


def _finalize(
    job: str,
    kind: str,
    day: str,
    items: list[Item],
    *,
    limit: int,
    moment: datetime,
) -> Brief:
    """按条目数上限截断，并把截断事实写进 detail（不偷偷丢）。"""
    cap = max(1, int(limit))
    kept = items[:cap]
    dropped = len(items) - len(kept)
    detail = f"{BRIEF_LABELS.get(kind, kind)} 共 {len(items)} 条，推送 {len(kept)} 条"
    if dropped > 0:
        detail += f"；另有 {dropped} 条未推送（每日上限 {cap}）"
    return Brief(job, kind, day, STATUS_OK, detail, kept, generated_at=_iso(moment))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 去重状态
# --------------------------------------------------------------------------- #


def _state_path(root: Path | None, day: str) -> Path:
    base = root if root is not None else _data_dir()
    return base / BRIEF_SUBDIR / f"{day}.json"


def _read_state(root: Path | None, day: str) -> dict[str, Any]:
    path = _state_path(root, day)
    if not path.is_file():
        return {"day": day, "published": {}, "briefs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"day": day, "published": {}, "briefs": []}
    data.setdefault("published", {})
    data.setdefault("briefs", [])
    return data


def _write_state(root: Path | None, day: str, state: dict[str, Any]) -> Path:
    path = _state_path(root, day)
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=1) + "\n")
    return path


# --------------------------------------------------------------------------- #
# 发布
# --------------------------------------------------------------------------- #


def publish(
    brief: Brief,
    *,
    root: Path | None = None,
    notify: bool = False,
    channel: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """去重 → 写提醒中心 →（可选）投递。返回**本次真实推送了什么**。

    已推送过的条目不会再进 inbox，也不会再触发通知；`skipped` 如实列出。
    """
    state = _read_state(root, brief.trade_day)
    published: dict[str, Any] = dict(state.get("published") or {})

    pushed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in brief.items:
        fingerprint = item.fingerprint
        if fingerprint in published:
            skipped.append({"title": item.title, "fingerprint": fingerprint, "reason": "同日已推送"})
            continue
        inbox_store.add_item(
            brief.job,
            BRIEF_LABELS.get(brief.kind, brief.kind),
            item.title,
            item.lines,
            link=item.link,
            dedupe_key=f"brief:{brief.trade_day}:{fingerprint}",
            kind="brief",
            root=root,
            now=now,
        )
        published[fingerprint] = {
            "title": item.title,
            "kind": item.kind,
            "job": brief.job,
            "at": _iso(datetime.now().astimezone() if now is None else now),
        }
        pushed.append({"title": item.title, "fingerprint": fingerprint})

    state["published"] = published
    entries = [entry for entry in state.get("briefs") or [] if entry.get("kind") != brief.kind]
    entries.append(
        {
            "kind": brief.kind,
            "label": BRIEF_LABELS.get(brief.kind, brief.kind),
            "status": brief.status,
            "detail": brief.detail,
            "items": len(brief.items),
            "pushed": len(pushed),
            "generated_at": brief.generated_at,
        }
    )
    state["briefs"] = entries
    path = _write_state(root, brief.trade_day, state)

    delivered: list[dict[str, Any]] = []
    if notify and pushed:
        body = "\n".join(f"{index + 1}. {row['title']}" for index, row in enumerate(pushed))
        for result in notifier.send_many([channel], brief.title, body):
            delivered.append({"channel": result.channel, "ok": result.ok, "detail": result.detail})

    return {
        "ok": brief.status == STATUS_OK,
        "kind": brief.kind,
        "trade_day": brief.trade_day,
        "title": brief.title,
        "detail": brief.detail,
        "pushed": pushed,
        "skipped": skipped,
        "delivered": delivered,
        "state_path": str(path),
    }


def history(root: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """最近若干天的简报状态（按日期倒序）。"""
    base = (root if root is not None else _data_dir()) / BRIEF_SUBDIR
    if not base.is_dir():
        return []
    files = sorted(base.glob("*.json"), reverse=True)[: max(1, int(limit))]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "trade_day": data.get("day") or path.stem,
                "briefs": data.get("briefs") or [],
                "published": len(data.get("published") or {}),
                "path": str(path),
            }
        )
    return out


BUILDERS: dict[str, Any] = {
    BRIEF_MORNING: build_morning,
    BRIEF_CLOSE: build_close_review,
    BRIEF_ALERTS: build_position_alerts,
}


# --------------------------------------------------------------------------- #
# 开关（config/brief.yaml）
# --------------------------------------------------------------------------- #

SETTINGS_NAME = "brief.yaml"

DEFAULT_SETTINGS: dict[str, Any] = {
    # 默认不弹窗：先只落收件箱，用户显式打开才走 console/desktop
    "notify": False,
    "channel": notifier.DEFAULT_CHANNEL,
    "threshold": ALERT_THRESHOLD_PCT,
}


def settings_path(config_root: Path | None = None) -> Path:
    return (config_root if config_root is not None else _config_dir()) / SETTINGS_NAME


def load_settings(config_root: Path | None = None) -> dict[str, Any]:
    """读开关；文件缺失或坏了回默认值（不能因为配置文件坏了就让简报整个停下）。"""
    path = settings_path(config_root)
    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return settings
    if not isinstance(raw, dict):
        return settings
    brief_block = raw.get("brief")
    block = brief_block if isinstance(brief_block, dict) else raw
    if isinstance(block.get("notify"), bool):
        settings["notify"] = block["notify"]
    try:
        settings["channel"] = notifier.normalize_channel(str(block.get("channel") or ""))
    except notifier.UnknownNotifierChannel:
        # 配置里写错了渠道不影响读：保留默认 console，写回时会按当前值覆盖
        pass
    try:
        threshold = float(block.get("threshold"))
    except (TypeError, ValueError):
        threshold = settings["threshold"]
    else:
        # 阈值必须为正，否则"异动"等于全部标的
        settings["threshold"] = threshold if threshold > 0 else DEFAULT_SETTINGS["threshold"]
    return settings


def save_settings(config_root: Path | None = None, **changes: Any) -> dict[str, Any]:
    """合并写回开关文件（原子写）；非法渠道名直接报错，不静默改成默认值。"""
    settings = load_settings(config_root)
    if "notify" in changes and changes["notify"] is not None:
        value = changes["notify"]
        if not isinstance(value, bool):
            raise ValueError("notify 必须是 true / false")
        settings["notify"] = value
    if "channel" in changes and changes["channel"] is not None:
        try:
            settings["channel"] = notifier.normalize_channel(str(changes["channel"]))
        except notifier.UnknownNotifierChannel as exc:
            raise ValueError(str(exc)) from exc
    if "threshold" in changes and changes["threshold"] is not None:
        try:
            threshold = float(changes["threshold"])
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold 必须是数字") from exc
        if threshold <= 0:
            raise ValueError("threshold 必须大于 0")
        settings["threshold"] = threshold
    path = settings_path(config_root)
    body = yaml.safe_dump({"brief": settings}, allow_unicode=True, sort_keys=False)
    atomic_write(path, body)
    return {**settings, "path": str(path)}


def build(
    kind: str,
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
    threshold: float = ALERT_THRESHOLD_PCT,
) -> Brief:
    """按类型分派；不认识的类型折成失败结果，不抛异常（调度器不该被拖垮）。"""
    name = str(kind or "").strip()
    builder = BUILDERS.get(name)
    if builder is None:
        moment = local_moment(now, timezone)
        return Brief(
            f"brief:{name or 'unknown'}",
            name or "unknown",
            _today(moment),
            STATUS_FAILED,
            f"不认识的简报类型 {name!r}（只支持 {' / '.join(BRIEFS)}）",
            [],
            generated_at=_iso(moment),
        )
    try:
        return builder(now=now, timezone=timezone, root=root, registry=registry, threshold=threshold)
    except Exception as exc:  # noqa: BLE001 - 简报失败折算结果
        moment = local_moment(now, timezone)
        reason = " ".join(str(exc).split()) or exc.__class__.__name__
        return Brief(f"brief:{name}", name, _today(moment), STATUS_FAILED, f"简报生成异常：{reason}", [], generated_at=_iso(moment))


__all__ = [
    "ALERT_ROWS_CAP",
    "ALERT_THRESHOLD_PCT",
    "BRIEFS",
    "BRIEF_ALERTS",
    "BRIEF_CLOSE",
    "BRIEF_LABELS",
    "BRIEF_MORNING",
    "BRIEF_SUBDIR",
    "BUILDERS",
    "Brief",
    "DEFAULT_SETTINGS",
    "Item",
    "MAX_ITEMS",
    "SETTINGS_NAME",
    "build",
    "build_close_review",
    "build_morning",
    "build_position_alerts",
    "history",
    "load_settings",
    "publish",
    "save_settings",
    "settings_path",
]
