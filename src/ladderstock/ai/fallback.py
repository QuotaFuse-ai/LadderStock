"""模型 fallback 链与配置读取（M3 §6.3 / QUOT-236 多后端）。

规则（与 issue 验收一致）：

- 模型链：`ai.yaml: default_model` → `fallback_chain`（去重、保序）；
- **只有网络 / 额度错误才换模型**；格式错误先同模型重试一次（重试逻辑在编排层）；
- 截断 / 未归类错误不自动重试，也不换模型（换模型不会解决截断）；
- 密钥只从环境变量读取（`api_key_env` 指向的变量），配置与日志里都不出现明文。

多后端（QUOT-236）：

- `ai.yaml: backends[]` 声明各 provider（`id` / `type` / 两个环境变量名 / `models`）；
- `default_model` 与 `fallback_chain` 用 `backend:model` 链式引用；裸模型名按
  「第一个声明了该模型的后端」解析；
- 过渡期没有 `backends` 的旧配置继续可读：等价于一个隐式 `openai` 后端，
  标签就是裸模型名（不改变既有行为）。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ladderstock.ai.error_taxonomy import AiFailure
from ladderstock.core.boundary import BoundaryViolation

# 允许切换模型的失败类型（issue 要求：网络与额度才换）
SWITCH_KINDS: frozenset[str] = frozenset({"network", "quota"})
# 允许同模型重试的失败类型（格式错误重试一次）
RETRY_KINDS: frozenset[str] = frozenset({"format"})

# 协议类型 → (默认 base_url_env, 默认 api_key_env)
BACKEND_KINDS: dict[str, tuple[str, str]] = {
    "openai": ("LDST_LLM_BASE_URL", "LDST_LLM_API_KEY"),
    "anthropic": ("LDST_LLM_ANTHROPIC_BASE_URL", "LDST_LLM_ANTHROPIC_API_KEY"),
}
#: 旧配置没有 `backends` 时的隐式后端 id（标签不加前缀）
LEGACY_BACKEND_ID = "default"

#: 看板自己的 AI 凭据文件（与控制面写入的 peer env 分开）。
#: 进程环境变量优先；文件只补空缺，方便设置页保存后无需重启进程。
AI_ENV_FILENAME = "ai.env"
#: 设置页允许写入的键（白名单，不接受自由字符串）
AI_ENV_KEYS: tuple[str, ...] = (
    "LDST_LLM_BASE_URL",
    "LDST_LLM_MODEL",
    "LDST_LLM_API_KEY",
    "LDST_LLM_ANTHROPIC_BASE_URL",
    "LDST_LLM_ANTHROPIC_API_KEY",
)
#: OpenAI 兼容后端的模型覆盖键：设置后取代该后端在 ai.yaml 里的默认模型
MODEL_OVERRIDE_ENV = "LDST_LLM_MODEL"

_ENV_KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class SectionConfig:
    prompt_file: str
    output_schema: str


@dataclass(frozen=True)
class BackendConfig:
    """一个 provider 后端：协议类型 + 凭据来自哪两个环境变量 + 可用模型。"""

    id: str
    kind: str
    base_url_env: str
    api_key_env: str
    models: tuple[str, ...] = ()
    #: `ai.env` 里的值（进程环境变量优先，文件只补空缺）
    env_file: Mapping[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return _env_value(self.base_url_env, self.env_file)

    @property
    def api_key(self) -> str:
        return _env_value(self.api_key_env, self.env_file)

    @property
    def base_url_source(self) -> str | None:
        return _env_source(self.base_url_env, self.env_file)

    @property
    def api_key_source(self) -> str | None:
        return _env_source(self.api_key_env, self.env_file)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def missing_env(self) -> list[str]:
        return [
            name
            for name in (self.base_url_env, self.api_key_env)
            if not _env_value(name, self.env_file)
        ]


def _env_value(name: str, env_file: Mapping[str, str] | None = None) -> str:
    """进程环境变量优先，`ai.env` 只补空缺（显式 env 永远覆盖文件）。"""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if env_file:
        return str(env_file.get(name, "")).strip()
    return ""


def _env_source(name: str, env_file: Mapping[str, str] | None = None) -> str | None:
    if os.environ.get(name, "").strip():
        return "process"
    if env_file and str(env_file.get(name, "")).strip():
        return "file"
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """读 dotenv 子集（`KEY=VALUE`、`#` 注释、成对引号）；坏行忽略，不抛异常。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_KEY_LINE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


@dataclass(frozen=True)
class ModelTarget:
    """一次模型调用的落点：哪个后端、哪个模型、对外展示的标签。"""

    backend: BackendConfig
    model: str
    label: str


@dataclass(frozen=True)
class AiConfig:
    default_model: str
    fallback_chain: tuple[str, ...]
    base_url_env: str
    api_key_env: str
    structured_output: str
    on_all_failed: str
    sections: dict[str, SectionConfig]
    path: Path | None = None
    backends: tuple[BackendConfig, ...] = ()
    #: `ai.env` 里的值（用于旧配置回退时也能读到文件凭据）
    env_file: Mapping[str, str] = field(default_factory=dict)

    @property
    def declared_backends(self) -> tuple[BackendConfig, ...]:
        """显式 `backends[]`；旧配置回退成一个隐式 openai 后端。"""
        if self.backends:
            return self.backends
        return (
            BackendConfig(
                id=LEGACY_BACKEND_ID,
                kind="openai",
                base_url_env=self.base_url_env,
                api_key_env=self.api_key_env,
                models=tuple(self._chain_refs),
                env_file=self.env_file,
            ),
        )

    @property
    def _chain_refs(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for ref in (self.default_model, *self.fallback_chain):
            if ref and ref not in ordered:
                ordered.append(ref)
        return tuple(ordered)

    def resolve(self, ref: str) -> ModelTarget:
        """把 `backend:model`（或裸模型名）解析成调用落点。

        - 带前缀：后端 id 必须已声明，否则 `BoundaryViolation`；
        - 裸模型名：第一个声明了该模型的后端；都没有时落到首个后端（兼容
          `ai chain test --model` 传任意模型名）。
        """
        name = (ref or "").strip()
        backends = self.declared_backends
        if ":" in name:
            prefix, _, model = name.partition(":")
            for backend in backends:
                if backend.id == prefix:
                    return self._target(backend, model.strip())
            known = "、".join(backend.id for backend in backends)
            raise BoundaryViolation(f"ai.yaml: 未知后端 {prefix!r}（已声明：{known}）")
        for backend in backends:
            if name in backend.models:
                return self._target(backend, name)
        return self._target(backends[0], name)

    def _target(self, backend: BackendConfig, model: str) -> ModelTarget:
        label = f"{backend.id}:{model}" if self.backends else model
        return ModelTarget(backend=backend, model=model, label=label)

    @property
    def targets(self) -> tuple[ModelTarget, ...]:
        ordered: list[ModelTarget] = []
        seen: set[tuple[str, str]] = set()
        for ref in self._chain_refs:
            target = self.resolve(ref)
            key = (target.backend.id, target.model)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(target)
        return tuple(ordered)

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(target.label for target in self.targets)


def config_path(config_root: Any = None) -> Path:
    base = Path(config_root) if config_root is not None else _config_dir()
    return base / "ai.yaml"


def env_file_path(config_root: Any = None) -> Path:
    """看板自己的 AI 凭据文件（与 `ai.yaml` 同目录）。"""
    return config_path(config_root).with_name(AI_ENV_FILENAME)


def _config_dir() -> Path:
    from ladderstock.cli.paths import config_dir

    return config_dir()


# --------------------------------------------------------------------------- #
# 读取与校验
# --------------------------------------------------------------------------- #

def load_config(config_root: Any = None) -> AiConfig:
    """读 `config/ai.yaml`；缺失或字段非法时抛 `BoundaryViolation`（配置错误）。"""
    path = config_path(config_root)
    if not path.is_file():
        raise BoundaryViolation(f"缺少 AI 配置：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BoundaryViolation(f"ai.yaml 解析失败：{exc}") from None
    ai = raw.get("ai") if isinstance(raw, dict) else None
    if not isinstance(ai, dict):
        raise BoundaryViolation("ai.yaml 缺少顶层 ai 配置")

    env_file = read_env_file(env_file_path(config_root))
    backends = _parse_backends(ai.get("backends"), env_file)

    default_model = str(ai.get("default_model") or "").strip()
    if not default_model:
        if not backends:
            raise BoundaryViolation("ai.yaml: default_model 不能为空")
        default_model = f"{backends[0].id}:{backends[0].models[0]}" if backends[0].models else ""

    chain_raw = ai.get("fallback_chain") or []
    if not isinstance(chain_raw, list):
        raise BoundaryViolation("ai.yaml: fallback_chain 必须是数组")
    fallback_chain = tuple(str(model).strip() for model in chain_raw if str(model).strip())

    default_model, fallback_chain = _apply_model_override(
        default_model, fallback_chain, backends, env_file
    )

    sections_raw = ai.get("sections") or {}
    sections: dict[str, SectionConfig] = {}
    if isinstance(sections_raw, dict):
        for name, item in sections_raw.items():
            if not isinstance(item, dict):
                continue
            sections[str(name)] = SectionConfig(
                prompt_file=str(item.get("prompt_file") or ""),
                output_schema=str(item.get("output_schema") or ""),
            )

    config = AiConfig(
        default_model=default_model,
        fallback_chain=fallback_chain,
        base_url_env=str(ai.get("base_url_env") or BACKEND_KINDS["openai"][0]),
        api_key_env=str(ai.get("api_key_env") or BACKEND_KINDS["openai"][1]),
        structured_output=str(ai.get("structured_output") or "prefer"),
        on_all_failed=str(ai.get("on_all_failed") or "degrade_to_text"),
        sections=sections,
        path=path,
        backends=backends,
        env_file=env_file,
    )
    _validate_refs(config)
    return config


def _apply_model_override(
    default_model: str,
    fallback_chain: tuple[str, ...],
    backends: tuple[BackendConfig, ...],
    env_file: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]:
    """`LDST_LLM_MODEL` 覆盖首个后端的模型，并把链里指向它的档位收敛到同一个模型。

    设置页只让用户填一个模型名；不这样做的话，`ai.yaml` 里留的
    `model-a` / `model-b` 占位会变成「换模型后必然失败」的假回退。
    """
    override = _env_value(MODEL_OVERRIDE_ENV, env_file)
    if not override:
        return default_model, fallback_chain

    if not backends:
        # 旧配置（隐式 openai 后端）：模型就是覆盖值，不再保留占位链
        return override, ()

    first_id = backends[0].id
    rewritten: list[str] = []
    for ref in (default_model, *fallback_chain):
        if ":" in ref:
            prefix, _, _name = ref.partition(":")
            rewritten.append(f"{first_id}:{override}" if prefix == first_id else ref)
        else:
            rewritten.append(f"{first_id}:{override}")
    ordered = tuple(dict.fromkeys(rewritten))
    return ordered[0], ordered[1:]


def _parse_backends(
    raw: Any, env_file: Mapping[str, str] | None = None
) -> tuple[BackendConfig, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BoundaryViolation("ai.yaml: backends 必须是数组")
    parsed: list[BackendConfig] = []
    ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BoundaryViolation(f"ai.yaml: backends[{index}] 必须是对象")
        backend_id = str(item.get("id") or "").strip()
        if not backend_id:
            raise BoundaryViolation(f"ai.yaml: backends[{index}].id 不能为空")
        if backend_id in ids:
            raise BoundaryViolation(f"ai.yaml: backends[].id 重复：{backend_id}")
        ids.add(backend_id)

        kind = str(item.get("type") or "").strip().lower()
        if kind not in BACKEND_KINDS:
            raise BoundaryViolation(
                f"ai.yaml: backends[{index}].type 必须是 {'/'.join(BACKEND_KINDS)}，收到 {kind!r}"
            )
        default_url, default_key = BACKEND_KINDS[kind]
        models_raw = item.get("models") or []
        if not isinstance(models_raw, list) or not [
            model for model in models_raw if str(model).strip()
        ]:
            raise BoundaryViolation(f"ai.yaml: backends[{index}].models 必须是非空数组")
        parsed.append(
            BackendConfig(
                id=backend_id,
                kind=kind,
                base_url_env=str(item.get("base_url_env") or default_url),
                api_key_env=str(item.get("api_key_env") or default_key),
                models=tuple(str(model).strip() for model in models_raw if str(model).strip()),
                env_file=env_file or {},
            )
        )
    return tuple(parsed)


def _validate_refs(config: AiConfig) -> None:
    """`default_model` / `fallback_chain` 里的后端前缀必须已声明。"""
    if not config.backends:
        return
    known = {backend.id for backend in config.backends}
    for ref in config._chain_refs:
        if ":" not in ref:
            continue
        prefix = ref.partition(":")[0]
        if prefix not in known:
            raise BoundaryViolation(
                f"ai.yaml: {ref!r} 引用了未声明的后端 {prefix!r}"
                f"（已声明：{'、'.join(sorted(known))}）"
            )


# --------------------------------------------------------------------------- #
# 对外查询
# --------------------------------------------------------------------------- #

def model_chain(config: AiConfig) -> list[str]:
    """可用的模型链标签：只保留凭据齐全的后端（未配置的后端静默跳过）。"""
    return [target.label for target in config.targets if target.backend.configured]


def api_key(config: AiConfig) -> str:
    backend = config.declared_backends[0]
    return backend.api_key


def base_url(config: AiConfig) -> str:
    backend = config.declared_backends[0]
    return backend.base_url


def missing_env(config: AiConfig) -> list[str]:
    missing: list[str] = []
    for backend in config.declared_backends:
        for name in backend.missing_env():
            if name not in missing:
                missing.append(name)
    return missing


def is_configured(config: AiConfig) -> bool:
    """至少有一个后端凭据齐全，才算「能发请求」。"""
    return bool(model_chain(config))


def should_switch(failure: AiFailure) -> bool:
    """网络 / 额度 → 换模型。"""
    return failure.kind in SWITCH_KINDS


def should_retry(failure: AiFailure) -> bool:
    """格式错误 → 同模型重试一次。"""
    return failure.kind in RETRY_KINDS


def section_config(config: AiConfig, section: str) -> SectionConfig:
    found = config.sections.get(section)
    if found is None:
        return SectionConfig(prompt_file=f"prompts/{section}.md", output_schema="")
    return found


def status_document(config: AiConfig) -> dict[str, Any]:
    """`ldst ai status` 的输出：只报环境变量名与是否配置，绝不回显密钥。"""
    missing = missing_env(config)
    backends = [
        {
            "id": backend.id,
            "type": backend.kind,
            "base_url_env": backend.base_url_env,
            "base_url": backend.base_url or None,
            "base_url_source": backend.base_url_source,
            "api_key_env": backend.api_key_env,
            "api_key_set": bool(backend.api_key),
            "api_key_source": backend.api_key_source,
            "models": list(backend.models),
            "configured": backend.configured,
        }
        for backend in config.declared_backends
    ]
    return {
        "configured": is_configured(config),
        "default_model": config.default_model,
        "fallback_chain": list(config.fallback_chain),
        "models": list(config.models),
        "backends": backends,
        # 兼容旧键：取首个后端的地址 / 变量名（多后端以 backends[] 为准）
        "base_url_env": config.declared_backends[0].base_url_env,
        "base_url": config.declared_backends[0].base_url or None,
        "api_key_env": config.declared_backends[0].api_key_env,
        "api_key_set": bool(config.declared_backends[0].api_key),
        "structured_output": config.structured_output,
        "on_all_failed": config.on_all_failed,
        "missing_env": missing,
        "config_path": str(config.path) if config.path else None,
        "env_file": str(env_file_path(config.path.parent)) if config.path else None,
        "env_file_exists": bool(config.path and env_file_path(config.path.parent).is_file()),
        "model_override": _env_value(MODEL_OVERRIDE_ENV, config.env_file) or None,
        "sections": list(config.sections),
        "hint": (
            "未配置模型凭据：仅能走纯文本降级（不会崩溃，也不会发请求）"
            if not is_configured(config)
            else "已配置"
        ),
    }


__all__ = [
    "AI_ENV_KEYS",
    "AI_ENV_FILENAME",
    "AiConfig",
    "BACKEND_KINDS",
    "BackendConfig",
    "LEGACY_BACKEND_ID",
    "MODEL_OVERRIDE_ENV",
    "ModelTarget",
    "SectionConfig",
    "SWITCH_KINDS",
    "api_key",
    "base_url",
    "config_path",
    "env_file_path",
    "is_configured",
    "load_config",
    "missing_env",
    "model_chain",
    "read_env_file",
    "section_config",
    "should_retry",
    "should_switch",
    "status_document",
]
