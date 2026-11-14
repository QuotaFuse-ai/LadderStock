"""零配置 AI 自动发现链（QUOT-601，B 层）。

目标：普通人装完包就能用，全程不填任何 key。按优先级探测本机可复用的
AI 通道，命中即落地为 `config/ai.env` 的一个 openai 后端凭据：

- **P1** 本机 workbuddy 反代网关 `http://127.0.0.1:8317/v1`：探
  `GET /v1/models` + 一次最小补全，通过即写入（key 用占位 `workbuddy-local`）；
- **P2** 进程 / `ai.env` 里的 `OPENAI_*` / `ANTHROPIC_*` 环境变量：复用现有
  key，只读取、原样传递，不落盘、不写日志；
- **P3** 本机 Codex `~/.codex/auth.json` 与 Claude `~/.claude/settings.json`：
  **默认关闭**，需设置页显式开关（`LDST_REUSE_CLI_LOGINS=1`）+ 一次确认才读；
  关闭时完全不碰这两个文件。读到的 key 也只写内存探测结果，由用户「应用」
  才落 `ai.env`（这是复用，不是悄悄复制）；
- **P4** 兜底：设置页手填（现有能力保留，不在本模块）。

硬约束（issue 原文）：
- 密钥永不写日志、永不明文回显——对外载荷一律过 `mask_secrets` 或只给布尔
  `key_set`；探测结果的 `key` 字段只在「应用」动作里原样转发，绝不进日志。
- 全部失败时返回有序降级报告（每级 `ok` + 白话错误），由 UI 分色展示。

与 QUOT-401 的 `cli_provider` 的分工：那边是「把 prompt 交给本机 CLI 的
非交互入口」（订阅通道），这边是「从本机已知位置读 HTTP 凭据」——一个走
子进程，一个走 HTTP，互不替代。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ladderstock.ai import fallback

#: P1：workbuddy 反代网关（本机固定端口，无鉴权，key 需非空占位）
WORKBUDDY_GATEWAY_BASE = "http://127.0.0.1:8317/v1"
#: 网关不校验 key，但 providers.py 的 `_credentials` 要求非空，用固定占位
WORKBUDDY_PLACEHOLDER_KEY = "workbuddy-local"
#: P1 写入 ai.env 的键
WORKBUDDY_ENV_KEYS = ("LDST_LLM_BASE_URL", "LDST_LLM_API_KEY", "LDST_LLM_MODEL")

#: P3 开关（写进 ai.env；缺省=关，绝不碰 ~/.codex 与 ~/.claude）
REUSE_CLI_LOGINS_ENV = "LDST_REUSE_CLI_LOGINS"

#: P2/P3 候选环境变量名（进程 > ai.env 文件）
OPENAI_KEY_ENVS = ("OPENAI_API_KEY",)
OPENAI_BASE_ENVS = ("OPENAI_BASE_URL", "OPENAI_API_BASE")
ANTHROPIC_KEY_ENVS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
ANTHROPIC_BASE_ENVS = ("ANTHROPIC_BASE_URL",)

#: P3 已知登录态文件（相对用户 home）
CODEX_AUTH_JSON = ".codex/auth.json"
CLAUDE_SETTINGS_JSON = ".claude/settings.json"

#: 探测超时（秒）：本机网关 + 一次最小补全不该慢
PROBE_TIMEOUT = 6.0

#: 渠道来源标签（设置页「当前通道来源」的取值）
SOURCE_LABELS: dict[str, str] = {
    "workbuddy_gateway": "workbuddy网关",
    "env": "环境变量",
    "codex": "Codex",
    "claude": "Claude",
    "manual": "手填",
    "none": "未配置",
}


@dataclass(frozen=True)
class Candidate:
    """一条探测结果：来源、白话状态、落盘所需的凭据（key 只在此处原样存在）。"""

    priority: int
    source: str
    label: str
    ok: bool
    latency_ms: int | None
    base_url: str
    model: str
    #: 仅在内部流转；对外载荷一律 `key_set` 布尔
    key: str
    detail: str = ""


def _env_value(name: str, env_file: Any) -> str:
    if os.environ.get(name, "").strip():
        return os.environ[name].strip()
    if env_file:
        return str(env_file.get(name, "") or "").strip()
    return ""


def _codex_home() -> Path:
    home = os.environ.get("LDST_CLI_HOME", "").strip()
    if home:
        return Path(home)
    return Path.home()


def _pick_openai_model(models: Any) -> str:
    """从 /v1/models 的 id 列表里挑一个默认模型：优先 deepseek/glm flash 级。"""
    ids = [str(item) for item in models if isinstance(item, str)]
    for hint in ("deepseek", "glm", "kimi", "minimax"):
        for model_id in ids:
            if hint in model_id.lower() and "flash" in model_id.lower():
                return model_id
    for hint in ("deepseek", "glm", "kimi", "minimax"):
        for model_id in ids:
            if hint in model_id.lower():
                return model_id
    return ids[0] if ids else ""


def _probe_openai_chat(base_url: str, key: str, model: str) -> tuple[bool, str]:
    """一次最小补全；返回 `(ok, 白话错误)`。异常只翻译不透传。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "回答一个词：ok"}],
        "max_tokens": 8,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
            )
    except (httpx.HTTPError, OSError):
        return False, "本地 AI 服务未启动，点击修复"
    if response.status_code >= 400:
        return False, f"通道返回 HTTP {response.status_code}，请检查端点或密钥"
    try:
        data = response.json()
    except ValueError:
        return False, "通道响应不是合法 JSON"
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        return False, "通道响应缺少补全内容"
    return True, ""


def _probe_models(base_url: str) -> tuple[bool, str, list[str]]:
    """`GET {base}/models`；返回 `(ok, 白话错误, model ids)`。"""
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT) as client:
            response = client.get(f"{base_url.rstrip('/')}/models")
    except (httpx.HTTPError, OSError):
        return False, "本地 AI 服务未启动，点击修复", []
    if response.status_code >= 400:
        return False, f"模型列表返回 HTTP {response.status_code}", []
    try:
        data = response.json()
    except ValueError:
        return False, "模型列表响应不是合法 JSON", []
    raw = data.get("data") if isinstance(data, dict) else None
    ids = []
    if isinstance(raw, list):
        ids = [
            str(item.get("id"))
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]
    return True, "", ids


def candidate_workbuddy_gateway() -> Candidate:
    """P1：探 8317 网关 —— models + 一次最小补全。"""
    started = time.monotonic()
    base = WORKBUDDY_GATEWAY_BASE
    ok, error, ids = _probe_models(base)
    if not ok:
        return Candidate(
            priority=1,
            source="workbuddy_gateway",
            label=SOURCE_LABELS["workbuddy_gateway"],
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            base_url=base,
            model="",
            key=WORKBUDDY_PLACEHOLDER_KEY,
            detail=error,
        )
    model = _pick_openai_model(ids)
    chat_ok, chat_error = _probe_openai_chat(base, WORKBUDDY_PLACEHOLDER_KEY, model)
    return Candidate(
        priority=1,
        source="workbuddy_gateway",
        label=SOURCE_LABELS["workbuddy_gateway"],
        ok=chat_ok,
        latency_ms=int((time.monotonic() - started) * 1000),
        base_url=base,
        model=model,
        key=WORKBUDDY_PLACEHOLDER_KEY,
        detail=chat_error,
    )


def candidate_env(env_file: Any = None) -> Candidate | None:
    """P2：读进程 / ai.env 里的 OPENAI_*（Anthropic 协议本链不做自动落地）。

    只读取不落盘：key 原样留在内存里，由「应用」动作写入 ai.env（本来就要
    写的那个文件，不属于扩散）。base_url 缺省视为未配置。
    """
    key = next(
        (_env_value(name, env_file) for name in OPENAI_KEY_ENVS if _env_value(name, env_file)),
        "",
    )
    base = next(
        (_env_value(name, env_file) for name in OPENAI_BASE_ENVS if _env_value(name, env_file)),
        "",
    )
    model = _env_value("OPENAI_MODEL", env_file) or _env_value("LDST_LLM_MODEL", env_file)
    if not key or not base:
        return None
    from_process = _env_value(OPENAI_KEY_ENVS[0], env_file) == key
    return Candidate(
        priority=2,
        source="env",
        label=SOURCE_LABELS["env"],
        ok=True,
        latency_ms=None,
        base_url=base,
        model=model,
        key=key,
        detail="复用现有环境变量，只读取不落盘" if from_process else "",
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def candidate_codex(env_file: Any = None) -> Candidate | None:
    """P3a：读 `~/.codex/auth.json` 的 OPENAI_API_KEY（API key 型登录态）。"""
    data = _read_json(_codex_home() / CODEX_AUTH_JSON)
    if not data:
        return None
    key = str(data.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    return Candidate(
        priority=3,
        source="codex",
        label=SOURCE_LABELS["codex"],
        ok=True,
        latency_ms=None,
        base_url=_env_value("OPENAI_BASE_URL", env_file) or "https://api.openai.com/v1",
        model=_env_value("OPENAI_MODEL", env_file) or "",
        key=key,
        detail="复用本机 Codex 登录态，只读不复制落库",
    )


def candidate_claude(env_file: Any = None) -> Candidate | None:
    """P3b：读 `~/.claude/settings.json` env 段的 ANTHROPIC_*（key 型登录态）。

    Claude 的 base_url 往往指向第三方反代（`/v1` 结尾），协议是 Anthropic
    的 `/v1/messages`；本链自动落地只做 OpenAI 兼容通道，因此 P3b 只探测
    并报告，不参与 P1/P2 的落地链，由设置页作为可选来源展示。
    """
    data = _read_json(_codex_home() / CLAUDE_SETTINGS_JSON)
    env = data.get("env") if data and isinstance(data.get("env"), dict) else None
    if not env:
        return None
    key = str(env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None
    base = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
    return Candidate(
        priority=3,
        source="claude",
        label=SOURCE_LABELS["claude"],
        ok=True,
        latency_ms=None,
        base_url=base or "https://api.anthropic.com",
        model=str(env.get("ANTHROPIC_MODEL") or "").strip(),
        key=key,
        detail="复用本机 Claude Code 登录态（Anthropic 协议），只读不复制落库",
    )


def reuse_cli_logins_enabled(env_file: Any = None) -> bool:
    """P3 开关：进程环境变量优先，`ai.env` 只补空缺；缺省关。"""
    return _env_value(REUSE_CLI_LOGINS_ENV, env_file) == "1"


def discover(env_file: Any = None, *, include_cli_logins: bool | None = None) -> dict[str, Any]:
    """跑完整条发现链，返回有序候选 + 当前生效来源 + P3 开关状态。

    返回值里**没有**任何明文 key（每个候选只有 `key_set` 布尔）——这是
    `mask_secrets` 之外的另一层保险：结构上就不给前端明文。
    """
    if include_cli_logins is None:
        include_cli_logins = reuse_cli_logins_enabled(env_file)
    candidates: list[Candidate] = []
    first = candidate_workbuddy_gateway()
    candidates.append(first)
    if not first.ok:
        env = candidate_env(env_file)
        if env is not None:
            candidates.append(env)
        if include_cli_logins:
            for build in (candidate_codex, candidate_claude):
                found = build(env_file)
                if found is not None:
                    candidates.append(found)
    return _document(candidates, include_cli_logins=include_cli_logins)


def _document(candidates: list[Candidate], *, include_cli_logins: bool) -> dict[str, Any]:
    public = [
        {
            "priority": item.priority,
            "source": item.source,
            "label": item.label,
            "ok": item.ok,
            "latency_ms": item.latency_ms,
            "base_url": item.base_url,
            "model": item.model,
            "key_set": bool(item.key),
            "detail": item.detail,
        }
        for item in candidates
    ]
    hit = next((item for item in candidates if item.ok), None)
    return {
        "ok": hit is not None,
        "include_cli_logins": include_cli_logins,
        "reuse_cli_logins_env": REUSE_CLI_LOGINS_ENV,
        "current_source": hit.source if hit else ("manual" if _manual_channel() else "none"),
        "source_labels": dict(SOURCE_LABELS),
        "candidates": public,
        "detail": hit.detail if hit else "未发现可用通道；请在设置页手填端点与密钥",
    }


def _manual_channel() -> bool:
    """现有配置是否已是手填通道（ai.yaml 后端有 base_url + key 即算）。"""
    try:
        config = fallback.load_config()
    except Exception:
        return False
    return any(
        backend.kind != "cli" and backend.base_url and backend.api_key
        for backend in config.declared_backends
    )


def apply_candidate(candidate: Candidate, config_root: Any = None) -> dict[str, Any]:
    """把命中的候选落地为 `config/ai.env`（openai 兼容通道）。

    Anthropic 协议候选（claude）不落地，返回说明；其余写
    `LDST_LLM_BASE_URL` / `LDST_LLM_API_KEY` / `LDST_LLM_MODEL`。
    """
    if candidate.source == "claude":
        return {
            "applied": False,
            "reason": "Claude 登录态是 Anthropic 协议，请在设置页手填后使用",
        }
    path = fallback.env_file_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: dict[str, str] = {
        "LDST_LLM_BASE_URL": candidate.base_url,
        "LDST_LLM_API_KEY": candidate.key,
    }
    if candidate.model:
        lines["LDST_LLM_MODEL"] = candidate.model
    existing = fallback.read_env_file(path)
    existing.update(lines)
    ordered = [name for name in fallback.AI_ENV_KEYS if name in existing]
    text = "".join(f"{name}={existing[name]}\n" for name in ordered)
    text += "".join(
        f"{name}={value}\n" for name, value in existing.items() if name not in fallback.AI_ENV_KEYS
    )
    path.write_text(text, encoding="utf-8")
    return {"applied": True, "env_file": str(path), "source": candidate.source}
