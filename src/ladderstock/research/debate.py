"""F7 多空对照表：StockSenate 桥 + 质量门控 + 可核对的对照产物（falsifier 复用 F5、出处复用 F4）。

为什么门控放在桥前面：本机 2026-11-17 的 7 份 600519 报告，**全部角色都是
「结构化输出校验失败」**——`debate.bull_bear.arguments[*].points` 是空数组、
`confidence=0`、`summary.partial` 与 `summary.low_quality` 都是 `true`、
`quality_score=60 < threshold=70`。桥接层如果原样搬进回答，用户看到的是
「多头：无 / 空头：无 / 裁决：持有」——这比不给辩论更伤信任。

所以本模块的产出只有两种，没有第三种：

- **达标**：`conclusion`（裁决 + `falsifier`）、`table`（多空对照）、
  `metric`（质量分 / 失败角色数）、`citation`（报告 run_id 与落盘时间）；
- **不达标**：`notice(degraded)` + `metric`（质量分、失败角色数），并说明
  「为什么不出表」——不拿两个空数组冒充辩论结果。

口径红线：
- 本模块**只读报告、不触发辩论**（`run_debate()` 是显式动作，由 CLI/端点调用）；
- 解析失败 / 报告缺失一律 `partial`，绝不补写论点；
- 所有路径与子进程可注入（`root` / `runner`），单测不碰真 CLI、不联网、不写用户数据。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

#: 默认质量阈值（与 StockSenate `config/budget.toml` 的 quality_threshold 同口径）
MIN_QUALITY = 70.0
#: 多空各至少要几条论点，否则"对照表"没有对照
MIN_POINTS = 2
#: 单次辩论默认超时（秒）。历史一轮实测 552s，留一倍余量。
DEFAULT_TIMEOUT = 900
#: 报告的落盘子目录（相对 senate home）
REPORT_SUBDIR = "reports"
#: 解析结果的落盘子目录（相对 data_dir）
DEBATE_SUBDIR = "debate"

ENV_HOME = "LDST_SENATE_HOME"

#: rating → 结论语气（只有这几种，别自创）
_TONE_BY_RATING: dict[str, str] = {
    "buy": "positive",
    "overweight": "positive",
    "hold": "neutral",
    "neutral": "neutral",
    "underweight": "negative",
    "sell": "negative",
}
_LABEL_BY_RATING: dict[str, str] = {
    "buy": "看多",
    "overweight": "偏多",
    "hold": "中性",
    "neutral": "中性",
    "underweight": "偏空",
    "sell": "看空",
}


# --------------------------------------------------------------------------- #
# 路径与可用性
# --------------------------------------------------------------------------- #


def senate_home(root: Path | None = None) -> Path:
    """StockSenate 部署目录（含 `config/`、`prompts/`、`reports/`）。

    优先 `LDST_SENATE_HOME`，否则工程根的 `file/modules/senate`。
    这只解决"东西在哪"，不解决"能不能跑"（见 `availability()`）。
    """
    env = os.environ.get(ENV_HOME)
    if root is not None:
        return Path(root)
    if env:
        return Path(env)
    from ladderstock.cli.paths import project_root

    return project_root() / "file" / "modules" / "senate"


def ssenate_cli(root: Path | None = None) -> Path | None:
    """`ssenate` 可执行文件；找不到返回 None（调用方据此降级，不猜命令名）。"""
    home = senate_home(root)
    for candidate in (
        home / "venv" / "Scripts" / "ssenate.exe",
        home / "venv" / "Scripts" / "ssenate",
        home / "venv" / "bin" / "ssenate",
        home / ".venv" / "Scripts" / "ssenate.exe",
        home / ".venv" / "bin" / "ssenate",
    ):
        if candidate.is_file():
            return candidate
    return None


def reports_dir(root: Path | None = None) -> Path:
    return senate_home(root) / REPORT_SUBDIR


def list_reports(root: Path | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    """已保存的报告（按落盘时间倒序）。只读文件名，不读 157KB 的 state.json。"""
    folder = reports_dir(root)
    if not folder.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in folder.iterdir():
        if not child.is_dir() or not (child / "summary.json").is_file():
            continue
        name = child.name
        # `<YYYY-MM-DD>-<code>-<hash>`
        parts = name.split("-")
        report_day = "-".join(parts[:3]) if len(parts) >= 3 else ""
        code = parts[3] if len(parts) >= 4 else ""
        try:
            mtime = (child / "summary.json").stat().st_mtime
        except OSError:
            mtime = 0.0
        items.append(
            {
                "run_id": name,
                "date": report_day,
                "code": code,
                "path": str(child),
                "mtime": mtime,
                "has_state": (child / "state.json").is_file(),
            }
        )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items[: max(1, limit)]


def latest_report(code: str, day: str | date | None = None, root: Path | None = None) -> Path | None:
    """某个标的某天的最近一份报告目录；`day=None` 时取该标的最新一份。"""
    want_day = day.isoformat() if isinstance(day, date) else (str(day) if day else "")
    best: tuple[float, Path] | None = None
    for item in list_reports(root, limit=500):
        if code and item["code"] != code:
            continue
        if want_day and item["date"] != want_day:
            continue
        path = Path(item["path"])
        if best is None or item["mtime"] > best[0]:
            best = (item["mtime"], path)
    return best[1] if best else None


def availability(root: Path | None = None) -> dict[str, Any]:
    """能不能跑、有没有报告——页面用这段解释"为什么这轮没有辩论"。"""
    home = senate_home(root)
    cli = ssenate_cli(root)
    reports = list_reports(root, limit=200)
    return {
        "home": str(home),
        "home_exists": home.is_dir(),
        "cli": str(cli) if cli else None,
        "cli_exists": cli is not None,
        "config_exists": (home / "config").is_dir(),
        "reports": len(reports),
        "latest_run_id": reports[0]["run_id"] if reports else None,
        "latest_mtime": reports[0]["mtime"] if reports else None,
    }


# --------------------------------------------------------------------------- #
# 跑一次辩论（显式动作，不自动触发）
# --------------------------------------------------------------------------- #


def _default_runner(cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - 参数由本模块拼装，非用户输入直传
            list(cmd),
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "elapsed": round(time.monotonic() - started, 1),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            "elapsed": round(time.monotonic() - started, 1),
            "error": f"超时（>{timeout}s）",
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "elapsed": round(time.monotonic() - started, 1),
            "error": f"启动失败：{exc}",
        }


def run_debate(
    code: str,
    day: str | date | None = None,
    *,
    root: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    runner: Callable[..., dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
    no_hitl: bool = True,
) -> dict[str, Any]:
    """显式跑一次多角色合议，返回 `{ok, run_id, report, elapsed, ...}`。

    超时/非零退出都**不抛异常**：调用方要能区分"没跑成"和"跑成了但质量差"，
    这两种降级话术完全不同。
    """
    cli = ssenate_cli(root)
    home = senate_home(root)
    report_day = day.isoformat() if isinstance(day, date) else (str(day) if day else date.today().isoformat())
    if cli is None:
        return {
            "ok": False,
            "reason": "找不到 ssenate CLI（先确认 StockSenate 装在 file/modules/senate）",
            "code": code,
            "date": report_day,
        }
    if not (home / "config").is_dir():
        return {
            "ok": False,
            "reason": f"Senate 目录缺 config/：{home}",
            "code": code,
            "date": report_day,
        }
    cmd = [str(cli), "analyze", str(code), "-d", report_day, "-o", "json", "-c", str(home)]
    if no_hitl:
        cmd.extend(["--no-hitl", "true"])
    env = {**os.environ, **dict(extra_env or {})}
    before = {item["run_id"] for item in list_reports(root, limit=200)}
    run = (runner or _default_runner)(cmd, cwd=home, env=env, timeout=int(timeout))
    fresh: Path | None = None
    for item in list_reports(root, limit=200):
        if item["run_id"] in before:
            continue
        fresh = Path(item["path"])
        break
    if fresh is None:
        # 报告可能落在同一天的最新目录里（同名 run_id 概率极低，但仍按 mtime 兜底）
        fresh = latest_report(code, report_day, root)
    result: dict[str, Any] = {
        "ok": bool(run.get("ok")) and fresh is not None,
        "code": code,
        "date": report_day,
        "elapsed": run.get("elapsed"),
        "returncode": run.get("returncode"),
        "report_dir": str(fresh) if fresh else None,
        "stdout_tail": str(run.get("stdout") or "")[-400:],
        "stderr_tail": str(run.get("stderr") or "")[-400:],
    }
    if run.get("error"):
        result["reason"] = run["error"]
    elif run.get("returncode") not in (0, None):
        result["reason"] = f"ssenate 退出码 {run.get('returncode')}（见 stderr_tail）"
    elif fresh is None:
        result["reason"] = "跑完了但没找到新报告（报告可能写到别的目录，检查 --config）"
    return result


# --------------------------------------------------------------------------- #
# 读报告 + 归一化
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def load_report(report_dir: Path | str) -> dict[str, Any]:
    """读一份报告：summary（必需）+ state（可选）+ markdown（可选）。"""
    folder = Path(report_dir)
    if not folder.is_dir():
        return {"ok": False, "reason": f"报告目录不存在：{folder}", "path": str(folder)}
    summary = _read_json(folder / "summary.json")
    if not summary:
        return {"ok": False, "reason": "summary.json 缺失或不是合法 JSON", "path": str(folder)}
    state = _read_json(folder / "state.json")
    markdown = ""
    md_path = folder / "complete_report.md"
    if md_path.is_file():
        try:
            markdown = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            markdown = ""
    return {
        "ok": True,
        "run_id": str(summary.get("run_id") or folder.name),
        "path": str(folder),
        "summary": summary,
        "state": state,
        "markdown": markdown,
    }


def _as_text(item: Any) -> str:
    """一把把角色输出里的各种形状压成一句可展示的文本；空形状返回空串。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        for key in (
            "point",
            "text",
            "statement",
            "rebuttal",
            "bull_point",
            "bear_point",
            "summary",
            "rationale",
        ):
            got = item.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
        for got in item.values():
            if isinstance(got, str) and got.strip():
                return got.strip()
    return ""


def _texts(items: Any) -> list[str]:
    if not isinstance(items, (list, tuple)):
        return []
    out: list[str] = []
    for item in items:
        text = _as_text(item)
        if text:
            out.append(text)
    return out


def _side(state: Mapping[str, Any], side: str) -> dict[str, Any]:
    """从 `state.debate.bull_bear.arguments` 里取某一方；缺失即空。"""
    block = ((state.get("debate") or {}).get("bull_bear") or {}) if isinstance(state, Mapping) else {}
    args = block.get("arguments") if isinstance(block, Mapping) else None
    found: Mapping[str, Any] = {}
    if isinstance(args, (list, tuple)):
        for item in args:
            if not isinstance(item, Mapping):
                continue
            tag = str(item.get("side") or item.get("role") or "").strip().lower()
            if tag == side:
                found = item
    return {
        "side": side,
        "points": _texts(found.get("points") if isinstance(found, Mapping) else []),
        "rebuttals": _texts(found.get("rebuttals") if isinstance(found, Mapping) else []),
        "confidence": found.get("confidence") if isinstance(found, Mapping) else None,
        "partial": bool(found.get("partial")) if isinstance(found, Mapping) else True,
        "notes": str(found.get("notes") or "") if isinstance(found, Mapping) else "",
    }


def _failed_roles(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = summary.get("skipped")
    out: list[dict[str, str]] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                role = str(item.get("role") or "").strip()
                reason = str(item.get("reason") or "").strip()
            else:
                role, reason = str(item).strip(), ""
            if role:
                out.append({"role": role, "reason": reason})
    return out


def normalize(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把 `{summary, state}` 归一化成对照表 + 裁决 + 质量块（不含门控结论）。"""
    summary: Mapping[str, Any] = payload.get("summary") or {}
    state: Mapping[str, Any] = payload.get("state") or {}
    decision = state.get("decision") if isinstance(state.get("decision"), Mapping) else {}
    quality_block = state.get("quality") if isinstance(state.get("quality"), Mapping) else {}
    trade = state.get("trade") if isinstance(state.get("trade"), Mapping) else {}
    research = state.get("research") if isinstance(state.get("research"), Mapping) else {}

    bull = _side(state, "bull")
    bear = _side(state, "bear")
    failed = _failed_roles(summary)
    participants = state.get("analysts") if isinstance(state.get("analysts"), list) else []

    rating = str(summary.get("rating") or decision.get("rating") or "").strip().lower()
    action = str(summary.get("action") or decision.get("action") or "").strip()
    horizon = str(summary.get("horizon") or decision.get("horizon") or "").strip()
    confidence = summary.get("confidence")
    if confidence is None:
        confidence = decision.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None

    # 翻转条件：先取决策给出的风险，再取交易段的，最后取"未解决项"——
    # 全是报告自己写的，不生成、不推断。
    flip: list[str] = []
    for source in (decision.get("risks"), trade.get("risks"), research.get("unresolved")):
        for text in _texts(source):
            if text and text not in flip:
                flip.append(text)

    score = summary.get("quality_score")
    if score is None:
        score = quality_block.get("score")
    threshold = summary.get("quality_threshold")
    if threshold is None:
        threshold = quality_block.get("threshold")

    return {
        "run_id": str(summary.get("run_id") or ""),
        "code": str(summary.get("code") or ""),
        "date": str(summary.get("date") or ""),
        "generated_at": str(summary.get("generated_at") or ""),
        "rating": rating,
        "rating_label": _LABEL_BY_RATING.get(rating, rating or "未知"),
        "action": action,
        "horizon": horizon,
        "confidence": confidence_value,
        "bull": bull,
        "bear": bear,
        "clash": {
            # 多头对空头的反驳 / 空头对多头的反驳（别写反：写反了页面会把
            # 「多方的反驳」标成「空方反驳」，读者会误判谁在进攻）
            "bull_rebuttals": bull["rebuttals"],
            "bear_rebuttals": bear["rebuttals"],
        },
        "flip_conditions": flip,
        "failed_roles": failed,
        "participants": len(participants),
        "quality": {
            "score": score,
            "threshold": threshold,
            "low_quality": bool(summary.get("low_quality") or quality_block.get("low_quality")),
            "partial": bool(summary.get("partial") or payload.get("partial") or quality_block.get("partial")),
            "defects": list(quality_block.get("defects") or [])[:10],
        },
        "data_status": summary.get("data") or {},
    }


def assess(
    normalized: Mapping[str, Any],
    *,
    min_quality: float = MIN_QUALITY,
    min_points: int = MIN_POINTS,
) -> dict[str, Any]:
    """质量门控：够不够格出「多空对照表」。不够就逐条说清为什么。"""
    reasons: list[str] = []
    quality = normalized.get("quality") or {}
    bull = normalized.get("bull") or {}
    bear = normalized.get("bear") or {}
    score = quality.get("score")
    try:
        score_value = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_value = None
    threshold = float(min_quality)

    if quality.get("partial"):
        reasons.append("报告标记 partial（有角色没跑完或输出不可用）")
    if quality.get("low_quality"):
        reasons.append("报告标记 low_quality")
    if score_value is None:
        reasons.append("报告没有质量分（quality_score 缺失）")
    elif score_value < threshold:
        reasons.append(f"质量分低于阈值（{score_value:g} < {threshold:g}）")
    if len(bull.get("points") or []) < min_points:
        reasons.append(f"多头有效论点不足（{len(bull.get('points') or [])} < {min_points}）")
    if len(bear.get("points") or []) < min_points:
        reasons.append(f"空头有效论点不足（{len(bear.get('points') or [])} < {min_points}）")
    if not any(normalized.get("clash", {}).get(key) for key in ("bear_rebuttals", "bull_rebuttals")):
        reasons.append("没有交锋记录（双方都没给出反驳）")
    if not normalized.get("rating"):
        reasons.append("没有裁决 rating")
    if not normalized.get("flip_conditions"):
        reasons.append("没有翻转条件（报告里 risks / unresolved 都为空）")

    return {
        "passed": not reasons,
        "score": score_value,
        "threshold": threshold,
        "reasons": reasons,
        "min_points": min_points,
        "bull_points": len(bull.get("points") or []),
        "bear_points": len(bear.get("points") or []),
        "failed_roles": list(normalized.get("failed_roles") or []),
    }


def latest_view(code: str, day: str | date | None = None, root: Path | None = None) -> dict[str, Any] | None:
    """最近一份该标的的报告 → 归一化 + 质量门；没有可用报告返回 None。

    `root` 是 StockSenate 部署目录（不是 LadderStock 的 data 根），缺省按
    `senate_home()` 解析。读失败一律当"没有报告"——主问答不该被一份坏报告拖垮。
    """
    folder = latest_report(code, day, root=root)
    if folder is None:
        return None
    payload = load_report(folder)
    if not payload.get("ok"):
        return None
    normalized = normalize(payload)
    return {"path": str(folder), "normalized": normalized, "gate": assess(normalized)}


def summary_block(
    view: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """主问答里的一句话版本（只出一块，不挤正文的 6 块预算）。

    达标：`conclusion`（裁决 + 第一翻转条件）——完整对照表仍在 `/review` 页；
    不达标：`notice(degraded)` 说清为什么没引用它的结论（最多两条原因），绝不搬空表。

    红线 4：`conclusion.text` / `notice.message` 都不许带数字，所以这里不写日期与分值，
    只写定性结论；分数与失败角色留在 `to_blocks()` 的 metric 里。
    """
    from ladderstock.ai import answer_blocks as ab

    normalized = view.get("normalized") or {}
    gate = view.get("gate") or {}
    label = str(normalized.get("rating_label") or normalized.get("rating") or "未知")

    if gate.get("passed"):
        flip = (normalized.get("flip_conditions") or [None])[0]
        return ab.build_block(
            ab.BLOCK_CONCLUSION,
            {
                "text": (
                    f"多空合议的裁决是「{label}」，动作「{normalized.get('action') or '无动作'}」"
                    "（最近一份合议报告）"
                ),
                "tone": _TONE_BY_RATING.get(str(normalized.get("rating") or ""), "neutral"),
                "confidence": normalized.get("confidence")
                if normalized.get("confidence") is not None
                else 0.0,
                **({"falsifier": flip} if flip else {}),
            },
        )

    reasons = [_plain_reason(item) for item in (gate.get("reasons") or [])][:2]
    tail = f"（{'；'.join(reasons)}）" if reasons else ""
    return ab.build_block(
        ab.BLOCK_NOTICE,
        {
            "kind": "degraded",
            "message": (
                "已有一份多空合议报告，但质量门没过，本次没有引用它的结论"
                f"{tail}。完整数字与失败角色在 `/review` 页。"
            ),
        },
    )


_DIGITS = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _plain_reason(reason: Any) -> str:
    """门控原因 → 无数字的定性短句（notice.message 不许带数字）。"""
    text = _DIGITS.sub("", str(reason or ""))
    text = text.replace("（ ", "（").replace("（）", "").replace("()", "")
    text = text.replace(" < ", " 低于 ").replace("<", "低于")
    for filler in ("  ", " ，", "。", "、", "：", ":"):
        text = text.replace(filler, filler.strip() or "")
    text = text.strip(" ，。、；;:")
    return text or "质量门未过"


# --------------------------------------------------------------------------- #
# 块装配（复用既有 8 种块，不新增类型，不动 MAX_BLOCKS）
# --------------------------------------------------------------------------- #


def _rows(normalized: Mapping[str, Any]) -> list[list[Any]]:
    bull = normalized.get("bull") or {}
    bear = normalized.get("bear") or {}
    clash = normalized.get("clash") or {}
    rows: list[list[Any]] = []
    for point in bull.get("points") or []:
        rows.append(["多头", point, "研究员·多头"])
    for point in bear.get("points") or []:
        rows.append(["空头", point, "研究员·空头"])
    for text in clash.get("bear_rebuttals") or []:
        rows.append(["空方反驳", text, "空头 → 多头"])
    for text in clash.get("bull_rebuttals") or []:
        rows.append(["多方反驳", text, "多头 → 空头"])
    return rows


def to_blocks(
    normalized: Mapping[str, Any],
    gate: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
) -> list[dict[str, Any]]:
    """把辩论结果装成回答块（≤4 块）。全部经 `answer_blocks.build_block` 校验。

    达标：conclusion（裁决 + falsifier）→ table（对照）→ metric（质量）→ citation；
    不达标：notice(degraded) → metric（质量 + 失败角色）——**绝不出空表**。
    """
    from ladderstock.ai import answer_blocks as ab

    now = fetched_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    blocks: list[dict[str, Any]] = []
    rating = str(normalized.get("rating") or "")
    label = str(normalized.get("rating_label") or rating or "未知")
    tone = _TONE_BY_RATING.get(rating, "neutral")
    flip = (normalized.get("flip_conditions") or [None])[0]
    score = gate.get("score")
    threshold = gate.get("threshold")
    failed = list(gate.get("failed_roles") or [])

    if gate.get("passed"):
        conclusion = ab.build_block(
            ab.BLOCK_CONCLUSION,
            {
                "text": f"多空对照的裁决是「{label}」（{normalized.get('action') or '无动作'}）",
                "tone": tone,
                "confidence": normalized.get("confidence") if normalized.get("confidence") is not None else 0.0,
                **({"falsifier": flip} if flip else {}),
            },
        )
        blocks.append(conclusion)
        rows = _rows(normalized)
        blocks.append(
            ab.build_block(
                ab.BLOCK_TABLE,
                {
                    "columns": ["立场", "要点", "来源角色"],
                    "rows": rows,
                    "rows_total": len(rows),
                    "caption": f"StockSenate 合议（{normalized.get('run_id') or '未知 run'}）",
                },
            )
        )
        items: list[dict[str, Any]] = [
            {"label": "辩论质量分", "value": float(score) if score is not None else 0.0, "unit": f"/{threshold:g}" if threshold else ""},
            {"label": "有效论点", "value": float(len(rows))},
        ]
        if failed:
            items.append({"label": "失败角色", "value": float(len(failed))})
        items.append({"label": "参与角色", "value": float(normalized.get("participants") or 0)})
        blocks.append(ab.build_block(ab.BLOCK_METRIC, {"items": items[: ab.MAX_METRIC_ITEMS]}))
        blocks.append(
            ab.build_block(
                ab.BLOCK_CITATION,
                {
                    "items": [
                        {
                            "name": f"StockSenate 合议报告 {normalized.get('run_id') or ''}".strip(),
                            "fetched_at": now,
                            "symbol": str(normalized.get("code") or ""),
                            "as_of": str(normalized.get("date") or "") or None,
                        }
                    ]
                },
            )
        )
        return blocks

    message = (
        "本轮没有出多空对照表：合议报告的质量没到阈值。"
        "原因见下面的数字与失败角色清单，不是「双方都没话说」。"
    )
    blocks.append(ab.build_block(ab.BLOCK_NOTICE, {"kind": "degraded", "message": message}))
    items = [
        {"label": "辩论质量分", "value": float(score) if score is not None else 0.0, "unit": f"/{threshold:g}" if threshold else ""},
        {"label": "多头论点", "value": float(gate.get("bull_points") or 0)},
        {"label": "空头论点", "value": float(gate.get("bear_points") or 0)},
        {"label": "失败角色", "value": float(len(failed))},
    ]
    blocks.append(ab.build_block(ab.BLOCK_METRIC, {"items": items[: ab.MAX_METRIC_ITEMS]}))
    return blocks


# --------------------------------------------------------------------------- #
# 落盘（可追溯：一份辩论一份快照，页面与档案都从这里读）
# --------------------------------------------------------------------------- #


def save_result(data_root: Path, payload: Mapping[str, Any]) -> Path | None:
    """`data/debate/<YYYY-MM-DD>/<run_id>.json`：解析 + 门控 + 就地的报告路径。"""
    run_id = str(payload.get("run_id") or "").strip()
    day = str(payload.get("date") or date.today().isoformat())[:10]
    if not run_id:
        return None
    folder = Path(data_root) / DEBATE_SUBDIR / day
    path = folder / f"{run_id}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
