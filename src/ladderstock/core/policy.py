"""工具调用的权限闸门（F5）。

解决的问题：Agent 自主补数时会写库、打上游、耗时间，这些动作不该和"读一个行情"
享受同样的自由度。这里把动作分成三级，再用用户选定的档位决定要不要先问。

三档语义（与前端输入框下拉一一对应）：

* ``always_ask``：连只读查询都要问一遍。对应"访问互联网始终询问"的口径，
  适合初次使用或排查阶段。
* ``ask_on_risk``（默认）：只拦有副作用的动作（采集、写库、建表），只读放行。
* ``allow_all``：不打断。但启停服务、改配置这类会改变系统状态的动作仍然要问——
  放行的是"取数"，不是"改环境"。

为什么要单独一层而不是复用命令行的"按需确认"：命令行模式管的是**本机进程执行**
（能不能跑 shell、改文件），这里管的是**Agent 工具调用**（会不会写业务库）。
两者边界不同，混在一起会出现"命令行放行了、工具把库写脏了"的缺口。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POLICY_ALWAYS_ASK = "always_ask"
POLICY_ASK_ON_RISK = "ask_on_risk"
POLICY_ALLOW_ALL = "allow_all"

POLICIES: tuple[str, ...] = (POLICY_ALWAYS_ASK, POLICY_ASK_ON_RISK, POLICY_ALLOW_ALL)
DEFAULT_POLICY = POLICY_ASK_ON_RISK

#: 动作级别。
TIER_READ = "read"
TIER_COLLECT = "collect"
TIER_ADMIN = "admin"

TIERS: tuple[str, ...] = (TIER_READ, TIER_COLLECT, TIER_ADMIN)

SCOPE_SESSION = "session"
SCOPE_GLOBAL = "global"

POLICY_FILENAME = "policy.json"


class PolicyError(ValueError):
    """档位或动作级别不认识。"""


def normalize_policy(value: str | None) -> str:
    """把外部传入的档位归一；空值或非法值回落默认档。"""

    text = str(value or "").strip().lower()
    if text in POLICIES:
        return text
    return DEFAULT_POLICY


def normalize_tier(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in TIERS:
        return text
    raise PolicyError(f"未知动作级别：{value!r}（可选 {', '.join(TIERS)}）")


def _root_dir(root: Any | None) -> Path:
    if root is None:
        from ladderstock.cli.paths import data_dir

        return Path(data_dir())
    return Path(root)


def policy_path(root: Any | None = None) -> Path:
    return _root_dir(root) / "supervisor" / POLICY_FILENAME


def load_policy(root: Any | None = None) -> dict[str, Any]:
    """读当前策略；文件不存在或损坏时返回默认档，不抛异常。

    这里刻意不抛：权限读失败不该让整轮对话挂掉，回落默认档（``ask_on_risk``）
    是安全的一侧——宁可多问一句，不可默默放行。
    """

    path = policy_path(root)
    payload: dict[str, Any] = {
        "confirm_policy": DEFAULT_POLICY,
        "scope": SCOPE_SESSION,
        "updated_at": None,
    }
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return payload
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("policy_read_failed path=%s detail=%s", path, exc)
        return payload

    if isinstance(data, dict):
        payload["confirm_policy"] = normalize_policy(data.get("confirm_policy"))
        scope = str(data.get("scope") or SCOPE_SESSION).strip().lower()
        payload["scope"] = scope if scope in (SCOPE_SESSION, SCOPE_GLOBAL) else SCOPE_SESSION
        payload["updated_at"] = data.get("updated_at")
    return payload


def save_policy(
    confirm_policy: str,
    *,
    scope: str = SCOPE_SESSION,
    root: Any | None = None,
) -> dict[str, Any]:
    """写入策略。非法档位直接拒绝，避免把拼错的字符串写进配置文件。"""

    policy = str(confirm_policy or "").strip().lower()
    if policy not in POLICIES:
        raise PolicyError(f"未知权限档位：{confirm_policy!r}（可选 {', '.join(POLICIES)}）")
    scope_value = str(scope or SCOPE_SESSION).strip().lower()
    if scope_value not in (SCOPE_SESSION, SCOPE_GLOBAL):
        raise PolicyError(f"未知作用域：{scope!r}（可选 {SCOPE_SESSION} / {SCOPE_GLOBAL}）")

    path = policy_path(root)
    payload = {
        "confirm_policy": policy,
        "scope": scope_value,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return payload


def needs_confirm(action_tier: str, *, policy: str | None = None, root: Any | None = None) -> bool:
    """给定动作级别，返回"要不要先弹窗"。

    * ``always_ask``：一律要问（含只读）
    * ``ask_on_risk``：采集与管理类要问，只读不问
    * ``allow_all``：只有管理类要问
    """

    tier = normalize_tier(action_tier)
    policy_value = normalize_policy(policy) if policy else load_policy(root)["confirm_policy"]

    if policy_value == POLICY_ALLOW_ALL:
        return tier == TIER_ADMIN
    if policy_value == POLICY_ALWAYS_ASK:
        return True
    # ask_on_risk
    return tier in (TIER_COLLECT, TIER_ADMIN)


def describe(policy: str | None = None) -> dict[str, Any]:
    """给前端下拉用的说明：档位 + 每个级别是否放行。"""

    policy_value = normalize_policy(policy)
    return {
        "confirm_policy": policy_value,
        "tiers": {
            tier: not needs_confirm(tier, policy=policy_value) for tier in TIERS
        },
        "labels": {
            POLICY_ALWAYS_ASK: "始终询问",
            POLICY_ASK_ON_RISK: "按需确认",
            POLICY_ALLOW_ALL: "全部允许",
        },
        "descriptions": {
            POLICY_ALWAYS_ASK: "每个操作都先问你，含只读查询",
            POLICY_ASK_ON_RISK: "只对采集、写库、改配置确认（推荐）",
            POLICY_ALLOW_ALL: "不打断；启停服务仍会询问",
        },
    }
