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
from dataclasses import dataclass, field, replace
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
# `cli` 是订阅通道（QUOT-401）：不读端点与密钥，凭据在本机 CLI 自己的登录态里，
# 所以两个环境变量名都是空串，`configured` 由「可执行文件在不在」判定。
BACKEND_KINDS: dict[str, tuple[str, str]] = {
    "openai": ("LDST_LLM_BASE_URL", "LDST_LLM_API_KEY"),
    "anthropic": ("LDST_LLM_ANTHROPIC_BASE_URL", "LDST_LLM_ANTHROPIC_API_KEY"),
    "cli": ("", ""),
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
    # 订阅通道（QUOT-401）：`LDST_LLM_CLI` 取 codex / claude / workbuddy；
    # 置空即回 HTTP 通道。`LDST_LLM_CLI_MODEL` 只在 CLI 需要显式模型名时填。
    "LDST_LLM_CLI",
    "LDST_LLM_CLI_MODEL",
)
#: 订阅通道选择键（写入 `ai.env` 后下次复盘直接生效，不需要重启）
CLI_ENV = "LDST_LLM_CLI"
#: 订阅通道的模型覆盖键（多数 CLI 会自己选模型，留空即可）
CLI_MODEL_ENV = "LDST_LLM_CLI_MODEL"
#: OpenAI 兼容后端的模型覆盖键：设置后取代该后端在 ai.yaml 里的默认模型
MODEL_OVERRIDE_ENV = "LDST_LLM_MODEL"

_ENV_KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# --------------------------------------------------------------------------- #
# 模块级 / 角色级配置（QUOT-498）
# --------------------------------------------------------------------------- #
#
# 解析顺序固定为 **角色 > 子模块 > 全局**，逐级回退、命中即止。空值或缺失一律
# 表示「继承上一级」，绝不解释成「关闭」——把一段 `model: ""` 读成「不用模型」
# 会让生产链路在用户毫无察觉的情况下降级。
#
# `modules` 段整个不存在时，下面所有查询都落到全局，行为与引入本机制之前一致。

#: 允许出现在 `modules:` 下的键（未知模块按键保留、不参与解析；PUT 接口拒收）
MODULE_KEYS: tuple[str, ...] = ("tickhub", "tickhub_us", "wire", "senate")
#: 只有 senate 有辩论角色（其余模块的 `roles:` 视为配置错误）
ROLE_MODULES: frozenset[str] = frozenset({"senate"})
#: senate 的合法角色键，与 StockSenate `ROLE_LLM_KEYS` 逐字一致（含别名归一后的键）
SENATE_ROLE_KEYS: tuple[str, ...] = (
    "market",
    "social",
    "news",
    "fundamentals",
    "policy",
    "hot_money",
    "lockup",
    "bull",
    "bear",
    "research_manager",
    "trader",
    "risk_aggressive",
    "risk_neutral",
    "risk_conservative",
    "quality_gate",
    "portfolio_manager",
)
#: 每个模块认识的角色键；没有角色概念的模块为空表
ROLE_KEYS: Mapping[str, tuple[str, ...]] = {"senate": SENATE_ROLE_KEYS}
#: `modules.<m>` 允许出现的字段
MODULE_FIELDS: frozenset[str] = frozenset({"model", "fallback_chain", "memory", "roles"})
#: `modules.<m>.roles.<r>` 允许出现的字段
ROLE_FIELDS: frozenset[str] = frozenset({"model"})
#: `modules.<m>.memory` 允许出现的字段
MEMORY_FIELDS: frozenset[str] = frozenset({"agent_id", "kb_scope"})
#: 键名里带这些片段的字段一律当明文密钥拒收（QUOT-498 禁止项）
SECRET_FIELD_MARKERS: tuple[str, ...] = ("key", "secret", "token", "password", "credential")
#: `memory.agent_id` 缺省值的前缀（缺省 = `ladderstock-<module>`）
AGENT_ID_PREFIX = "ladderstock-"
#: `memory.kb_scope` 缺省值
DEFAULT_KB_SCOPE = "all"
#: `memory` 段字段的固定顺序（配置文档与写回接口共用）
MEMORY_ORDER: tuple[str, ...] = ("agent_id", "kb_scope")
#: 生效来源的三档取值（对外接口同名词表）
SOURCES: tuple[str, ...] = ("role", "module", "global")

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_KB_SCOPE_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9._*-]{0,63}$")


@dataclass(frozen=True)
class MemoryScope:
    """一个模块的记忆命名空间与知识库范围（QUOT-498 第三节）。

    `agent_id` 与 `kb_scope` 必须**同时**参与检索：只带其中一个等于跨模块召回。
    """

    agent_id: str
    kb_scope: str


@dataclass(frozen=True)
class RoleConfig:
    """`modules.senate.roles.<role>`：只允许 `model`，其余字段在读取时拒绝。"""

    model: str = ""


@dataclass(frozen=True)
class ModuleConfig:
    """一个子模块的覆盖项；空缺字段保持空串，表示继承。"""

    model: str = ""
    fallback_chain: tuple[str, ...] = ()
    roles: Mapping[str, RoleConfig] = field(default_factory=dict)
    memory: MemoryScope | None = None


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
    #: `kind == "cli"` 时用哪条订阅通道（codex / claude / workbuddy）
    cli: str = ""

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
        if self.kind == "cli":
            return self.cli_available
        return bool(self.base_url and self.api_key)

    @property
    def cli_available(self) -> bool:
        """订阅通道：可执行文件在本机 PATH 上就算「配好了」（凭据在 CLI 自己的登录态里）。"""
        if not self.cli:
            return False
        from ladderstock.ai.cli_provider import cli_spec, find_executable

        try:
            spec = cli_spec(self.cli)
        except KeyError:
            return False
        return find_executable(spec) is not None

    def missing_env(self) -> list[str]:
        if self.kind == "cli":
            # 没有环境变量可缺：缺的是可执行文件或登录态，由 `cli_provider.probe` 说明
            return [] if self.cli_available else [f"(CLI:{self.cli or '未指定'} 未安装)"]
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
    #: `modules:` 段（QUOT-498）；为空表示未启用分层配置，全部落到全局
    modules: Mapping[str, ModuleConfig] = field(default_factory=dict)

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

    # ---------------------------------------------------------------- 三层解析
    #
    # 角色 > 子模块 > 全局。每个查询都返回 `(生效值, 来源)`，`source_for` 就是这个
    # 二元组的第二项——接口与页面徽标都用它说明「这条生效值来自哪一级」。

    def module_config(self, module: str) -> ModuleConfig | None:
        return self.modules.get(module)

    def role_config(self, module: str, role: str | None) -> RoleConfig | None:
        if not role:
            return None
        found = self.modules.get(module)
        if found is None:
            return None
        return found.roles.get(role)

    def _model_levels(self, module: str, role: str | None) -> list[tuple[str, str]]:
        """候选值按优先级排列：`(source, ref)`，空值已在调用方过滤。"""
        levels: list[tuple[str, str]] = []
        role_cfg = self.role_config(module, role)
        if role_cfg is not None and role_cfg.model:
            levels.append(("role", role_cfg.model))
        found = self.modules.get(module)
        if found is not None and found.model:
            levels.append(("module", found.model))
        if self.default_model:
            levels.append(("global", self.default_model))
        return levels

    def resolve_for(self, module: str, role: str | None = None) -> ModelTarget:
        """按角色 > 子模块 > 全局解析一次调用的生效模型。"""
        check_module_role(module, role)
        levels = self._model_levels(module, role)
        if not levels:
            raise BoundaryViolation(
                "ai.yaml: 没有任何可用的 default_model，无法解析模型；"
                "请在 `default_model` 里声明一个 `backend:model`"
            )
        return self.resolve(levels[0][1])

    def source_for(self, module: str, role: str | None = None) -> str:
        """生效值来自哪一级：`role` / `module` / `global`。"""
        check_module_role(module, role)
        levels = self._model_levels(module, role)
        if not levels:
            raise BoundaryViolation("ai.yaml: 没有任何可用的 default_model，无法说明生效来源")
        return levels[0][0]

    def chain_for(self, module: str, role: str | None = None) -> tuple[ModelTarget, ...]:
        """生效回调链：生效模型在前，其后按「角色链 → 子模块链 → 全局链」逐级补齐。

        每一级的 `fallback_chain` **整体替换**上一级的链（父单约定的「声明即替换」），
        不是合并；去重保留首次出现。
        """
        check_module_role(module, role)
        primary = self.resolve_for(module, role)
        refs: list[str] = [primary.label]
        found = self.modules.get(module)
        # 角色级没有自己的链，所以链从「子模块」开始；模型本身已按三级解析。
        for chain in (
            found.fallback_chain if found is not None else (),
            self.fallback_chain,
        ):
            if chain:
                refs.extend(chain)
                break
        ordered: list[ModelTarget] = []
        seen: set[tuple[str, str]] = set()
        for ref in refs:
            target = self.resolve(ref)
            key = (target.backend.id, target.model)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(target)
        return tuple(ordered)

    def memory_scope(self, module: str) -> MemoryScope:
        """模块的记忆命名空间；未配置时 `ladderstock-<module>` + `all`。"""
        found = self.modules.get(module)
        if found is not None and found.memory is not None:
            return found.memory
        return MemoryScope(agent_id=f"{AGENT_ID_PREFIX}{module}", kb_scope=DEFAULT_KB_SCOPE)

    def module_document(self) -> dict[str, Any]:
        """`GET /control/ai/modules` 的载荷：每个模块 / 角色的三件套。

        - `value`：当前生效的 `backend:model`；
        - `source`：`role` / `module` / `global`；
        - `inherited_value`：把本级覆盖拿掉之后会生效的值（全局档时为 None）。
        """
        models = list(self.backend_models())
        modules: dict[str, Any] = {}
        known = list(MODULE_KEYS) + sorted(set(self.modules) - set(MODULE_KEYS))
        for name in known:
            found = self.modules.get(name) or ModuleConfig()
            value, source = self._describe_model(name, None, found.model)
            scope = self.memory_scope(name)
            entry: dict[str, Any] = {
                "value": value,
                "source": source,
                "configured": bool(found.model),
                "inherited_value": self._inherited_value(name, None, found.model),
                "fallback_chain": list(found.fallback_chain),
                "agent_id": scope.agent_id,
                "kb_scope": scope.kb_scope,
                "roles": {},
            }
            # 角色全集来自 ROLE_KEYS：没覆盖过的角色也要带出来（source=module/global）
            for role in ROLE_KEYS.get(name, ()):
                role_model = found.roles[role].model if role in found.roles else ""
                role_value, role_source = self._describe_model(name, role, role_model)
                entry["roles"][role] = {
                    "value": role_value,
                    "source": role_source,
                    "configured": bool(role_model),
                    "inherited_value": self._inherited_value(name, role, role_model),
                }
            modules[name] = entry
        return {"modules": modules, "models": models}

    def backend_models(self) -> tuple[str, ...]:
        """所有已声明后端的 `id:model` 全集（下拉选项的唯一来源）。"""
        return tuple(
            f"{backend.id}:{model}"
            for backend in self.declared_backends
            for model in backend.models
        )

    def _describe_model(self, module: str, role: str | None, override: str) -> tuple[str, str]:
        if override:
            return self._label(override), "role" if role else "module"
        if role and self.modules.get(module) is not None and self.modules[module].model:
            return self._label(self.modules[module].model), "module"
        if not self.default_model:
            return "", "global"
        return self._label(self.default_model), "global"

    def _inherited_value(self, module: str, role: str | None, override: str) -> str | None:
        """拿掉本级覆盖后的值：模块档看全局；角色档看子模块（未配置则看全局）。"""
        if not override:
            return None
        if role:
            found = self.modules.get(module)
            if found is not None and found.model:
                return self._label(found.model)
        return self._label(self.default_model) if self.default_model else None

    def _label(self, ref: str) -> str:
        return self.resolve(ref).label


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

    # 订阅通道（QUOT-401）：`ai.env` 里选了 CLI 就把它排到链首并接管默认模型，
    # HTTP 后端原样留在链尾——用户想切回去只是把 LDST_LLM_CLI 清空。
    cli_backend = _cli_backend(env_file)
    if cli_backend is not None:
        backends = (cli_backend, *backends)
        default_model = f"{cli_backend.id}:{cli_backend.models[0]}"

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

    modules = _parse_modules(ai.get("modules"))
    config = replace(config, modules=modules)
    _validate_refs(config)
    return config


def check_module_role(module: str, role: str | None = None) -> None:
    """模块 / 角色名必须是已声明的那一组，否则 `BoundaryViolation`。"""
    if module not in MODULE_KEYS:
        known = "、".join(MODULE_KEYS)
        raise BoundaryViolation(f"ai.yaml: 未知子模块 {module!r}（已声明：{known}）")
    if role and module not in ROLE_MODULES:
        raise BoundaryViolation(f"ai.yaml: 子模块 {module!r} 不支持角色级覆盖")
    if role:
        allowed = ROLE_KEYS.get(module, ())
        if role not in allowed:
            raise BoundaryViolation(
                f"ai.yaml: 子模块 {module!r} 没有角色 {role!r}（合法角色：{'、'.join(allowed)}）"
            )


def _parse_modules(raw: Any) -> dict[str, ModuleConfig]:
    """读 `ai.yaml: modules`；整段缺失返回空表（行为与引入前 100% 一致）。

    严格到这里：未知模块 / 未知字段 / 看起来像密钥的字段名 / `roles` 挂错模块
    一律 `BoundaryViolation`——配置拼错了应该报错，而不是静默忽略。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BoundaryViolation("ai.yaml: modules 必须是对象")
    parsed: dict[str, ModuleConfig] = {}
    for name, item in raw.items():
        module = str(name)
        check_module_role(module)
        if item is None:
            item = {}
        if not isinstance(item, dict):
            raise BoundaryViolation(f"ai.yaml: modules.{module} 必须是对象")
        unknown = sorted(set(item) - MODULE_FIELDS)
        secret = sorted(
            field
            for field in unknown
            if any(marker in field.lower() for marker in SECRET_FIELD_MARKERS)
        )
        if secret:
            raise BoundaryViolation(
                f"ai.yaml: modules.{module}.{secret[0]} 看起来是密钥字段；"
                "密钥只允许放在 ai.env / 环境变量里"
            )
        if unknown:
            raise BoundaryViolation(
                f"ai.yaml: modules.{module} 含未知字段 {unknown[0]!r}"
                f"（允许：{'、'.join(sorted(MODULE_FIELDS))}）"
            )
        model = _module_scalar(module, "model", item.get("model"))
        chain_raw = item.get("fallback_chain")
        fallback_chain: tuple[str, ...] = ()
        if chain_raw is not None:
            if not isinstance(chain_raw, list):
                raise BoundaryViolation(f"ai.yaml: modules.{module}.fallback_chain 必须是数组")
            fallback_chain = tuple(
                ref
                for ref in (
                    _module_scalar(module, "fallback_chain", item_ref) for item_ref in chain_raw
                )
                if ref
            )

        roles_raw = item.get("roles")
        roles: dict[str, RoleConfig] = {}
        if roles_raw is not None:
            if module not in ROLE_MODULES:
                raise BoundaryViolation(
                    f"ai.yaml: modules.{module}.roles 只在 {'、'.join(sorted(ROLE_MODULES))} 下可用"
                )
            if not isinstance(roles_raw, dict):
                raise BoundaryViolation(f"ai.yaml: modules.{module}.roles 必须是对象")
            for role_name, role_item in roles_raw.items():
                role = str(role_name)
                if role_item is None:
                    role_item = {}
                if not isinstance(role_item, dict):
                    raise BoundaryViolation(f"ai.yaml: modules.{module}.roles.{role} 必须是对象")
                check_module_role(module, role)
                unknown_role = sorted(set(role_item) - ROLE_FIELDS)
                if unknown_role:
                    raise BoundaryViolation(
                        f"ai.yaml: modules.{module}.roles.{role} 含未知字段 "
                        f"{unknown_role[0]!r}（允许：{'、'.join(sorted(ROLE_FIELDS))}）"
                    )
                roles[role] = RoleConfig(
                    model=_module_scalar(module, f"roles.{role}.model", role_item.get("model"))
                )

        memory = _parse_memory(module, item.get("memory"))
        parsed[module] = ModuleConfig(
            model=model,
            fallback_chain=fallback_chain,
            roles=roles,
            memory=memory,
        )
    return parsed


def _module_scalar(module: str, field: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise BoundaryViolation(f"ai.yaml: modules.{module}.{field} 必须是字符串")
    return str(value).strip()


def _parse_memory(module: str, raw: Any) -> MemoryScope | None:
    """`modules.<m>.memory`：`agent_id` / `kb_scope` 成对出现，缺省走默认值。

    任一字段为空串视为「没填」——记忆命名空间必须两维齐全才能检索，
    `agent_id: ''` 若被当成显式值就会落成跨模块召回。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BoundaryViolation(f"ai.yaml: modules.{module}.memory 必须是对象")
    unknown = sorted(set(raw) - MEMORY_FIELDS)
    if unknown:
        raise BoundaryViolation(
            f"ai.yaml: modules.{module}.memory 含未知字段 {unknown[0]!r}"
            f"（允许：{'、'.join(sorted(MEMORY_FIELDS))}）"
        )
    agent_id = _module_scalar(module, "memory.agent_id", raw.get("agent_id")) or (
        f"{AGENT_ID_PREFIX}{module}"
    )
    kb_scope = _module_scalar(module, "memory.kb_scope", raw.get("kb_scope")) or DEFAULT_KB_SCOPE
    if not _AGENT_ID_RE.match(agent_id):
        raise BoundaryViolation(
            f"ai.yaml: modules.{module}.memory.agent_id 非法（{agent_id!r}）："
            "只允许字母数字与 . _ -，且不超过 80 字符"
        )
    if not _KB_SCOPE_RE.match(kb_scope):
        raise BoundaryViolation(
            f"ai.yaml: modules.{module}.memory.kb_scope 非法（{kb_scope!r}）："
            "只允许字母数字与 . _ - *，且不超过 64 字符"
        )
    return MemoryScope(agent_id=agent_id, kb_scope=kb_scope)


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
        cli_id = str(item.get("cli") or "").strip().lower()
        if kind == "cli" and not cli_id:
            raise BoundaryViolation(
                f"ai.yaml: backends[{index}].cli 不能为空（可选：codex / claude / workbuddy）"
            )
        models_raw = item.get("models") or []
        if not isinstance(models_raw, list) or not [
            model for model in models_raw if str(model).strip()
        ]:
            # 订阅通道多数不需要显式模型名，允许省略；HTTP 通道仍然必须给
            if kind != "cli":
                raise BoundaryViolation(f"ai.yaml: backends[{index}].models 必须是非空数组")
            models_raw = ["default"]
        parsed.append(
            BackendConfig(
                id=backend_id,
                kind=kind,
                base_url_env=str(item.get("base_url_env") or default_url),
                api_key_env=str(item.get("api_key_env") or default_key),
                models=tuple(str(model).strip() for model in models_raw if str(model).strip()),
                env_file=env_file or {},
                cli=cli_id,
            )
        )
    return tuple(parsed)


def _cli_backend(env_file: Mapping[str, str] | None = None) -> BackendConfig | None:
    """`ai.env` 选了订阅通道时，构造一个 CLI 后端（不存在就不注入）。"""
    cli_id = _env_value(CLI_ENV, env_file).lower()
    if not cli_id:
        return None
    model = _env_value(CLI_MODEL_ENV, env_file) or "default"
    return BackendConfig(
        id=f"cli_{cli_id}",
        kind="cli",
        base_url_env="",
        api_key_env="",
        models=(model,),
        env_file=env_file or {},
        cli=cli_id,
    )


def _validate_refs(config: AiConfig) -> None:
    """`default_model` / `fallback_chain` / `modules.*` 里的后端前缀必须已声明。"""
    if not config.backends:
        return
    known = {backend.id for backend in config.backends}
    refs: list[tuple[str, str]] = [("default_model", ref) for ref in config._chain_refs]
    for module, found in config.modules.items():
        refs.append((f"modules.{module}.model", found.model))
        refs.extend((f"modules.{module}.fallback_chain", ref) for ref in found.fallback_chain)
        for role, role_cfg in found.roles.items():
            refs.append((f"modules.{module}.roles.{role}.model", role_cfg.model))
    for where, ref in refs:
        if ":" not in ref:
            continue
        prefix = ref.partition(":")[0]
        if prefix not in known:
            raise BoundaryViolation(
                f"ai.yaml: {where} = {ref!r} 引用了未声明的后端 {prefix!r}"
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
            "cli": backend.cli or None,
            "cli_available": backend.cli_available if backend.kind == "cli" else None,
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
        "modules": list(config.modules),
        "hint": (
            "未配置模型凭据：仅能走纯文本降级（不会崩溃，也不会发请求）"
            if not is_configured(config)
            else "已配置"
        ),
    }


__all__ = [
    "AGENT_ID_PREFIX",
    "AI_ENV_KEYS",
    "AI_ENV_FILENAME",
    "AiConfig",
    "BACKEND_KINDS",
    "BackendConfig",
    "DEFAULT_KB_SCOPE",
    "LEGACY_BACKEND_ID",
    "MODEL_OVERRIDE_ENV",
    "MODULE_FIELDS",
    "MODULE_KEYS",
    "MEMORY_FIELDS",
    "MEMORY_ORDER",
    "MemoryScope",
    "ModelTarget",
    "ModuleConfig",
    "ROLE_FIELDS",
    "ROLE_KEYS",
    "ROLE_MODULES",
    "RoleConfig",
    "SENATE_ROLE_KEYS",
    "SOURCES",
    "SectionConfig",
    "SWITCH_KINDS",
    "check_module_role",
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
