"""模型 fallback 链与配置读取（M3 §6.3）。

规则（与 issue 验收一致）：

- 模型链：`ai.yaml: default_model` → `fallback_chain`（去重、保序）；
- **只有网络 / 额度错误才换模型**；格式错误先同模型重试一次（重试逻辑在编排层）；
- 截断 / 未归类错误不自动重试，也不换模型（换模型不会解决截断）；
- 密钥只从环境变量读取（`api_key_env` 指向的变量），配置与日志里都不出现明文。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ladderstock.ai.error_taxonomy import AiFailure
from ladderstock.core.boundary import BoundaryViolation

# 允许切换模型的失败类型（issue 要求：网络与额度才换）
SWITCH_KINDS: frozenset[str] = frozenset({"network", "quota"})
# 允许同模型重试的失败类型（格式错误重试一次）
RETRY_KINDS: frozenset[str] = frozenset({"format"})


@dataclass(frozen=True)
class SectionConfig:
    prompt_file: str
    output_schema: str


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

    @property
    def models(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for model in (self.default_model, *self.fallback_chain):
            if model and model not in ordered:
                ordered.append(model)
        return tuple(ordered)


def config_path(config_root: Any = None) -> Path:
    base = Path(config_root) if config_root is not None else _config_dir()
    return base / "ai.yaml"


def _config_dir() -> Path:
    from ladderstock.cli.paths import config_dir

    return config_dir()


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

    default_model = str(ai.get("default_model") or "").strip()
    if not default_model:
        raise BoundaryViolation("ai.yaml: default_model 不能为空")

    chain_raw = ai.get("fallback_chain") or []
    if not isinstance(chain_raw, list):
        raise BoundaryViolation("ai.yaml: fallback_chain 必须是数组")

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

    return AiConfig(
        default_model=default_model,
        fallback_chain=tuple(str(model) for model in chain_raw if str(model).strip()),
        base_url_env=str(ai.get("base_url_env") or "LDST_LLM_BASE_URL"),
        api_key_env=str(ai.get("api_key_env") or "LDST_LLM_API_KEY"),
        structured_output=str(ai.get("structured_output") or "prefer"),
        on_all_failed=str(ai.get("on_all_failed") or "degrade_to_text"),
        sections=sections,
        path=path,
    )


def model_chain(config: AiConfig) -> list[str]:
    return list(config.models)


def api_key(config: AiConfig) -> str:
    return os.environ.get(config.api_key_env, "").strip()


def base_url(config: AiConfig) -> str:
    return os.environ.get(config.base_url_env, "").strip()


def missing_env(config: AiConfig) -> list[str]:
    missing: list[str] = []
    if not base_url(config):
        missing.append(config.base_url_env)
    if not api_key(config):
        missing.append(config.api_key_env)
    return missing


def is_configured(config: AiConfig) -> bool:
    return not missing_env(config)


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
    return {
        "configured": not missing,
        "default_model": config.default_model,
        "fallback_chain": list(config.fallback_chain),
        "models": list(config.models),
        "base_url_env": config.base_url_env,
        "base_url": base_url(config) or None,
        "api_key_env": config.api_key_env,
        "api_key_set": bool(api_key(config)),
        "structured_output": config.structured_output,
        "on_all_failed": config.on_all_failed,
        "missing_env": missing,
        "config_path": str(config.path) if config.path else None,
        "sections": list(config.sections),
        "hint": (
            "未配置模型凭据：仅能走纯文本降级（不会崩溃，也不会发请求）"
            if missing
            else "已配置"
        ),
    }


__all__ = [
    "AiConfig",
    "SectionConfig",
    "SWITCH_KINDS",
    "api_key",
    "base_url",
    "config_path",
    "is_configured",
    "load_config",
    "missing_env",
    "model_chain",
    "section_config",
    "should_retry",
    "should_switch",
    "status_document",
]
