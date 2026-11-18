"""跨轮任务规划：把「帮我盯它一周」变成一个会自己推进的任务，而不是一句话。

它解决的问题：之前每一轮对话都是孤立的——用户说完「盯住浦发，有变化告诉我」，
下一轮就忘了。这里把这种**持续性目标**变成一条有状态的任务记录：

- 用户一句话建任务（标的 / 期限 / 频率都是确定性解析，不调模型）；
- 任务状态落盘，跨进程、跨重启都在；
- 每轮对话把它注入 prompt，模型知道「我现在手上有哪些长期目标」；
- 由调度器按频率推进（`plan_tick` 任务），每次推进跑一轮 agent 并把结论落卡；
- 到期自动关闭，用户可以暂停或提前结束。

和 `preferences` 的分工：偏好是「用户是谁」（不变），计划是「用户让我做什么」
（有始有终）。和 `reflection` 的分工：反思修一轮内的证据缺口，计划管跨轮的连续推进。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

#: 任务文件（与 preferences 同级，用户能直接看到）
FILENAME = "research_plans.json"

#: 同时进行中的任务上限：再多就不是「盯几只」，是没边界了
MAX_ACTIVE = 5

#: 历史只留最近这么多轮，避免文件无限长
MAX_HISTORY = 6

#: 连续失败到这个次数就自动暂停（熔断）：再推下去只是拿同样的错刷同样的日志，
#: 用户又看不到「它一直在失败」。暂停后状态在页面上显式可见，用户点「恢复」才继续。
MAX_FAILURES = 3

#: 默认期限（天）与默认频率
DEFAULT_DAYS = 7
FREQUENCY_DAILY = "daily"
FREQUENCY_INTRADAY = "intraday"
FREQUENCY_ON_MOVE = "on_move"

FREQUENCY_LABELS = {
    FREQUENCY_DAILY: "每天盘后",
    FREQUENCY_INTRADAY: "盘中每半小时",
    FREQUENCY_ON_MOVE: "有异动才推",
}

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"

SECTION_HEADER = "【进行中的研究任务】（用户此前交待过的长期目标，按它的节奏推进）"

#: 「盯 X」的动词表：没有这些词就不当任务，避免把普通提问变成长任务。
#: 长词在前（`盯住` 必须先于 `盯`），否则「帮我盯住浦发银行」会被切成「住浦发银行」——
#: 这个坑实测把一条正常任务解析成了 None。
_TRIGGER_RE = re.compile(
    r"(?:帮我|请|麻烦你?)?"
    r"(盯着|盯住|盯一下|盯|跟踪一下|跟踪|持续关注|持续跟踪|每天看|留意一下|留意|关注下)"
)

#: 标的：6 位代码优先
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

#: 期限：3天 / 一周 / 两周 / 一个月 / 到下月 3 号
_DAYS_RE = re.compile(r"(?<![\d.])(\d{1,2}|一|两|二|三|四|五|六|七|十)\s*(天|日)")
_WEEKS_RE = re.compile(r"(?<![\d.])(\d{1,2}|一|两|二|三|四|五|六|七|十|半个)\s*(周|星期|礼拜)")
_MONTHS_RE = re.compile(r"(?<![\d.])(\d{1,2}|一|两|二|三|四|五|六|七|十)\s*(个月|月)")

_CN_NUM = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
    "八": 8, "九": 9, "十": 10, "半个": 0.5,
}

#: 频率判定词（先匹配者优先）
_FREQ_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(有异动|异动才|只在异动|异常才)"), FREQUENCY_ON_MOVE),
    (re.compile(r"(盘中|每半小时|每小时|实时)"), FREQUENCY_INTRADAY),
    (re.compile(r"(每天|每日|盘后|收盘后|每天晚上)"), FREQUENCY_DAILY),
)

#: 任务固定的三步：取数 → 解释 → 只讲变化。写进任务，推进时按它提问。
STEPS = (
    "取数：行情、资金流、板块位置（只用工具取到的数据）",
    "解释：发生了什么、可能的原因是什么",
    "只讲变化：和上次比有变化才说，没变化就一句「无变化」",
)

#: 没有标的时，主题必须是可研究的板块/行业/概念/指数——否则不建任务
_TOPIC_RE = re.compile(r"(板块|行业|概念|指数|大盘|市场|赛道)")


@dataclass
class Plan:
    """一条持续性研究任务。`runs`/`last_summary` 让用户回看「它到底干了什么」。"""

    id: str
    goal: str
    symbols: list[str]
    frequency: str
    created_at: str
    expires_at: str
    status: str = STATUS_ACTIVE
    updated_at: str = ""
    runs: int = 0
    last_run_at: str = ""
    last_summary: str = ""
    steps: list[str] = field(default_factory=lambda: list(STEPS))
    history: list[dict[str, Any]] = field(default_factory=list)
    #: 连续失败次数：成功就清零，到 `MAX_FAILURES` 自动暂停（熔断）
    failures: int = 0
    #: 最后一次失败的原因（写给用户看，不是堆栈）
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def symbol_label(self) -> str:
        return "、".join(self.symbols) if self.symbols else "整体市场"

    def due(self, now: datetime, *, min_gap_min: float | None = None) -> bool:
        """是否该推进：到期/暂停的不跑；daily 一天一次；intraday 有间隔。"""
        if self.status != STATUS_ACTIVE:
            return False
        moment = _naive(now)
        if _parse(self.expires_at) <= moment:
            return False
        if not self.last_run_at:
            return True
        last = _parse(self.last_run_at)
        if self.frequency == FREQUENCY_INTRADAY:
            return (moment - last) >= timedelta(minutes=float(min_gap_min or 30))
        # daily / on_move：同一天只推一次（on_move 的即时性由盯盘那条链路负责）
        return last.date() != moment.date()


def path_for(data_root: Path | str | None = None) -> Path:
    root = Path(data_root) if data_root is not None else _data_dir()
    return root / FILENAME


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #


def load(data_root: Path | str | None = None) -> list[Plan]:
    path = path_for(data_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("plans_unreadable path=%s detail=%s", path, exc)
        return []
    items = raw.get("plans") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: list[Plan] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                Plan(
                    id=str(item["id"]),
                    goal=str(item.get("goal") or ""),
                    symbols=[str(code) for code in (item.get("symbols") or [])],
                    frequency=str(item.get("frequency") or FREQUENCY_DAILY),
                    created_at=str(item.get("created_at") or ""),
                    expires_at=str(item.get("expires_at") or ""),
                    status=str(item.get("status") or STATUS_ACTIVE),
                    updated_at=str(item.get("updated_at") or ""),
                    runs=int(item.get("runs") or 0),
                    last_run_at=str(item.get("last_run_at") or ""),
                    last_summary=str(item.get("last_summary") or ""),
                    steps=[str(step) for step in (item.get("steps") or list(STEPS))],
                    history=[dict(row) for row in (item.get("history") or [])][:MAX_HISTORY],
                    failures=int(item.get("failures") or 0),
                    last_error=str(item.get("last_error") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("plans_skip_bad_row detail=%s", exc)
    return out


def save(plans: Iterable[Plan], data_root: Path | str | None = None) -> Path:
    path = path_for(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": _now(),
        "plans": [plan.to_dict() for plan in plans],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def active(data_root: Path | str | None = None) -> list[Plan]:
    return [plan for plan in load(data_root) if plan.status == STATUS_ACTIVE]


# --------------------------------------------------------------------------- #
# 解析：一句话 → 一条任务
# --------------------------------------------------------------------------- #


def parse_period_days(text: str) -> int | None:
    """抽期限（天）。没写返回 None，由调用方决定默认值。"""
    for pattern, unit in ((_DAYS_RE, 1), (_WEEKS_RE, 7), (_MONTHS_RE, 30)):
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        value = _CN_NUM.get(raw)
        if value is None:
            try:
                value = float(raw)
            except ValueError:
                continue
        # 「半个周」= 3.5 天 → 取整到天
        return max(1, int(round(value * unit)))
    return None


def parse_frequency(text: str) -> str | None:
    for pattern, kind in _FREQ_RULES:
        if pattern.search(text):
            return kind
    return None


def parse_symbols(
    text: str, resolver: Callable[[str], str | None] | None = None
) -> list[str]:
    """抽标的：先 6 位代码，再交给 `resolver` 把中文名换成代码（调用方注入，便于测试）。"""
    codes = _CODE_RE.findall(text or "")
    if codes:
        return sorted({code for code in codes})
    if resolver is None:
        return []
    # 中文名：取触发词后面那一小段做候选（「盯住浦发银行」→「浦发银行」）
    match = _TRIGGER_RE.search(text or "")
    if not match:
        return []
    tail = text[match.end() :]
    candidate = re.split(r"[，,。；;\s]|每天|盘后|一周|两周|一个月|有异动|盘中", tail)[0]
    candidate = candidate.strip(" 　到至止于")
    if len(candidate) < 2 or len(candidate) > 12:
        return []
    code = resolver(candidate)
    return [code] if code else []


def build_plan(
    question: str,
    *,
    resolver: Callable[[str], str | None] | None = None,
    now: datetime | None = None,
) -> Plan | None:
    """从一句话里造一条任务；不是任务句式就返回 None（绝不把普通提问变成长任务）。"""
    text = (question or "").strip()
    if not text or not _TRIGGER_RE.search(text):
        return None
    symbols = parse_symbols(text, resolver=resolver)
    if not symbols:
        # 没有可解析的标的：「帮我盯着半导体板块」这种也行，按整体主题推进；
        # 但主题必须是**板块/行业/概念/指数**这类可研究的对象——「帮我盯着这家公司」
        # 解析不出标的就不该建任务，否则会建出一条永远答不出东西的任务。
        topic = _topic_of(text)
        if not topic or not _TOPIC_RE.search(topic):
            return None
    else:
        topic = ""

    moment = now or datetime.now()
    days = parse_period_days(text) or DEFAULT_DAYS
    frequency = parse_frequency(text) or FREQUENCY_DAILY
    goals = text if len(text) <= 80 else text[:80]
    return Plan(
        # 同一秒内可能建多条（「盯 A」紧跟「盯 B」）：id 必须唯一，否则按 id 改状态会改错任务
        id=f"plan-{moment.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
        goal=goals,
        symbols=symbols,
        frequency=frequency,
        created_at=moment.isoformat(timespec="seconds"),
        expires_at=(moment + timedelta(days=days)).isoformat(timespec="seconds"),
        updated_at=moment.isoformat(timespec="seconds"),
        steps=list(STEPS) + ([f"主题：{topic}"] if topic else []),
    )


def _topic_of(text: str) -> str:
    match = _TRIGGER_RE.search(text)
    if not match:
        return ""
    tail = text[match.end() :]
    topic = re.split(r"[，,。；;！!？?]|每天|盘后|盘中|一周|两周|一个月|有异动", tail)[0].strip(" 　")
    return topic[:20]


# --------------------------------------------------------------------------- #
# 建 / 停 / 结束
# --------------------------------------------------------------------------- #


def _same_goal(plan: Plan, goal: str, symbols: Sequence[str]) -> bool:
    return plan.goal == goal and list(plan.symbols) == list(symbols)


def maybe_create(
    question: str,
    *,
    data_root: Path | str | None = None,
    resolver: Callable[[str], str | None] | None = None,
    now: datetime | None = None,
) -> Plan | None:
    """回答成立后尝试建任务；重复说不重复建；超过上限不再建（返回 None）。"""
    plan = build_plan(question, resolver=resolver, now=now)
    if plan is None:
        return None
    plans = load(data_root)
    for existing in plans:
        if existing.status == STATUS_ACTIVE and _same_goal(existing, plan.goal, plan.symbols):
            return None
    if len([item for item in plans if item.status == STATUS_ACTIVE]) >= MAX_ACTIVE:
        logger.info("plans_limit_reached active=%d", MAX_ACTIVE)
        return None
    plans.append(plan)
    save(plans, data_root)
    logger.info("plan_created id=%s symbols=%s frequency=%s", plan.id, plan.symbols, plan.frequency)
    return plan


def set_status(
    plan_id: str,
    status: str,
    *,
    data_root: Path | str | None = None,
    now: datetime | None = None,
) -> Plan | None:
    if status not in (STATUS_ACTIVE, STATUS_PAUSED, STATUS_DONE):
        raise ValueError(f"未知任务状态：{status!r}")
    plans = load(data_root)
    moment = (now or datetime.now()).isoformat(timespec="seconds")
    for plan in plans:
        if plan.id == plan_id:
            plan.status = status
            plan.updated_at = moment
            # 用户手动恢复 = 重新给它机会：失败计数归零，否则恢复后第一轮就又被熔断。
            if status == STATUS_ACTIVE:
                plan.failures = 0
                plan.last_error = ""
            save(plans, data_root)
            return plan
    return None


# --------------------------------------------------------------------------- #
# 推进
# --------------------------------------------------------------------------- #


def question_for(plan: Plan) -> str:
    """推进时的提问：把三步写死进 prompt，避免每轮跑成「泛泛聊两句」。"""
    target = plan.symbol_label()
        # 有标的就逐只问；没标的按整体主题问
    return (
        f"持续跟踪：{target}。\n"
        "1) 先用工具取数：行情、资金流、板块位置；\n"
        "2) 再解释：这段时间发生了什么、可能的原因是什么；\n"
        "3) 只讲变化：和上一次相比有变化才说；没有变化就明确写「无变化」。\n"
        "取不到的就直说没取到，不要用记忆补数字。"
    )


def record_run(
    plan: Plan,
    *,
    status: str,
    summary: str,
    path: str = "",
    now: datetime | None = None,
    data_root: Path | str | None = None,
) -> Plan:
    """把一次推进结果写回任务（它同时是「这个任务真的跑过」的唯一凭证）。"""
    moment = now or datetime.now()
    stamp = moment.isoformat(timespec="seconds")
    plan.runs += 1
    plan.last_run_at = stamp
    plan.last_summary = (summary or "")[:400]
    plan.updated_at = stamp
    plan.history.append({"at": stamp, "status": status, "summary": plan.last_summary, "path": path})
    plan.history = plan.history[-MAX_HISTORY:]
    # 失败也记一次推进（否则「跑过但挂了」看起来像没跑过），但只有成功才把
    # last_run_at 当成「今天已经交付过」的凭证——所以失败时回滚它，让当天还能重试。
    if status != "ok":
        plan.last_run_at = ""
        plan.failures += 1
        plan.last_error = (summary or "推进失败，没有留下原因")[:200]
        # 熔断：连续失败到阈值就自己停下，而不是每天静默重试同一件事。
        # 用户能在面板上看到「已暂停 · 连续 N 次失败」以及最后一次的失败原因。
        if plan.failures >= MAX_FAILURES and plan.status == STATUS_ACTIVE:
            plan.status = STATUS_PAUSED
            plan.last_summary = (
                f"连续 {plan.failures} 次推进失败，已自动暂停。最后一次：{plan.last_error}"
            )[:400]
            logger.warning(
                "plan_breaker_tripped id=%s failures=%d detail=%s",
                plan.id,
                plan.failures,
                plan.last_error,
            )
    else:
        plan.failures = 0
        plan.last_error = ""
    if _parse(plan.expires_at) <= _naive(moment) and plan.status != STATUS_PAUSED:
        plan.status = STATUS_DONE
    plans = load(data_root)
    for index, existing in enumerate(plans):
        if existing.id == plan.id:
            plans[index] = plan
            break
    else:
        plans.append(plan)
    save(plans, data_root)
    return plan


def tick(
    *,
    data_root: Path | str | None = None,
    now: datetime | None = None,
    runner: Callable[[Plan], dict[str, Any]] | None = None,
    min_gap_min: float = 30,
) -> list[dict[str, Any]]:
    """推进所有到期的任务。`runner` 由调用方注入（默认走 agent），测试传假的。"""
    moment = now or datetime.now()
    results: list[dict[str, Any]] = []
    for plan in load(data_root):
        if not plan.due(moment, min_gap_min=min_gap_min):
            continue
        try:
            outcome = runner(plan) if runner is not None else {}
        except Exception as exc:  # noqa: BLE001 - 单条任务失败不该拖垮其余
            record_run(
                plan,
                status="failed",
                summary=f"{type(exc).__name__}: {exc}",
                now=moment,
                data_root=data_root,
            )
            results.append({"id": plan.id, "status": "failed", "detail": str(exc)[:200]})
            continue
        record_run(
            plan,
            status=str(outcome.get("status") or "ok"),
            summary=str(outcome.get("summary") or ""),
            path=str(outcome.get("path") or ""),
            now=moment,
            data_root=data_root,
        )
        results.append({"id": plan.id, "status": outcome.get("status") or "ok", "path": outcome.get("path")})
    return results


# --------------------------------------------------------------------------- #
# 注入
# --------------------------------------------------------------------------- #


def render(plans: Iterable[Plan] | None = None, *, limit: int = MAX_ACTIVE) -> str:
    """注入段：让模型知道手上还有哪些长期目标。空表返回空串。"""
    listed = [plan for plan in (plans if plans is not None else active()) if plan.status == STATUS_ACTIVE]
    if not listed:
        return ""
    lines = [SECTION_HEADER]
    for plan in listed[:limit]:
        due_day = plan.expires_at[:10]
        line = (
            f"- {plan.symbol_label()}（{FREQUENCY_LABELS.get(plan.frequency, plan.frequency)}，"
            f"至 {due_day}）已推进 {plan.runs} 次"
        )
        if plan.last_summary:
            line += f"；上次：{plan.last_summary[:60]}"
        lines.append(line)
    return "\n".join(lines)


def as_dict(plans: Iterable[Plan] | None = None) -> dict[str, Any]:
    listed = list(plans) if plans is not None else load()
    return {
        "count": len(listed),
        "active": len([plan for plan in listed if plan.status == STATUS_ACTIVE]),
        "items": [plan.to_dict() for plan in sorted(listed, key=lambda p: p.created_at, reverse=True)],
    }


def review(plan: Plan, *, limit: int = MAX_HISTORY) -> list[dict[str, Any]]:
    """把 `history` 变成能读的复盘：每一轮什么时候、成没成、说了什么。

    用户问「这一个礼拜它到底干什么了」时，答案不该是「已推进 N 次」这种计数，
    而是逐轮的结论——这也是判断「它还值不值得继续跟」的唯一依据。
    """
    rows: list[dict[str, Any]] = []
    for item in list(plan.history)[-limit:]:
        at = str(item.get("at") or "")
        status = str(item.get("status") or "")
        rows.append(
            {
                "at": at,
                "date": at[:10],
                "status": status,
                "ok": status == "ok",
                "summary": str(item.get("summary") or "")[:200],
            }
        )
    return rows


def _parse(stamp: str) -> datetime:
    """解析时间戳并**去掉时区**：任务是本地概念，调度器给的时刻可能带 tz。

    两边混着比会直接 `TypeError`（naive vs aware），而这类错误一旦在盘后任务里出现，
    整条任务链就静默不跑了。
    """
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        # 时间戳坏了就当成「很久以前」：宁可多跑一次，也不要让任务永久卡死
        return datetime.now() - timedelta(days=365)
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
