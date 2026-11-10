"""StockSenate 辩论角色多模型桥接（`/api/control/peers/senate/role-models`）。

LadderStock 只写 StockSenate 自己加载的那份 `config/llm_config.toml`：规范化后的
`[llm.role_llms]`、写入 `[llm.llm_provider]` 全局档、以及 `.env` 里 `MINIMAX_API_KEY`
一类的**变量名**（见 `PeerControl.secret_keys`）。密钥明文永不进 TOML、永不进
`ai.yaml`、永不回给页面；连通性探测只从进程环境变量 / peer 的 dotenv 取 key。

契约真源是 StockSenate 包本身（`schema.ROLE_LLM_KEYS` / `schema.ROLE_ALIASES` /
`roles/llm.py` 的 provider 表）；能 import 就现读，拿不到才退回本模块内联的表。
内联表只是镜像，不是新的事实来源：`tests/test_senate.py` 的真仓用例会在
`STOCKSENATE_ROOT` 可用时逐项比对。

写盘沿用 `ai_yaml_edit.write_ai_yaml` 的 fail-closed 姿态：候选文本先落临时目录、
用同一解析器完整校验（角色键 / 别名冲突 / provider 与 model / 未知键），通过后
才「备份 + 临时文件 + os.replace」替换正式文件；任何一步不过，文件一个字节不动。
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import shutil
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ladderstock.ai.ai_yaml_edit import write_ai_yaml
from ladderstock.core.boundary import BoundaryViolation, PeerControl

LLM_CONFIG_FILE = "llm_config.toml"
ROLE_MODELS_SECTION = "llm.role_llms"

#: 探测超时：minimal chat 一次往返，慢于这个就当网络不可达
DEFAULT_PROBE_TIMEOUT_SEC = 30.0
PROBE_MAX_TOKENS = 8

#: 内联镜像（StockSenate `config/schema.py::ROLE_LLM_KEYS` 顺序即展示顺序）
ROLE_LLM_KEYS: tuple[str, ...] = (
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

ROLE_LABELS: dict[str, str] = {
    "market": "技术面",
    "social": "情绪面",
    "news": "新闻",
    "fundamentals": "基本面",
    "policy": "政策",
    "hot_money": "游资",
    "lockup": "解禁",
    "bull": "多头研究员",
    "bear": "空头研究员",
    "research_manager": "研究经理",
    "trader": "交易员",
    "risk_aggressive": "激进风控",
    "risk_neutral": "中性风控",
    "risk_conservative": "保守风控",
    "quality_gate": "质检",
    "portfolio_manager": "组合经理",
}

#: 逐字镜像 StockSenate `config/schema.py::ROLE_ALIASES`（导入失败时兜底）。
#: `technical` 是别名、`market` 是它指向的 canonical 角色，两者都在 16 键里。
ROLE_ALIASES: dict[str, str] = {
    "technical": "market",
    "basic": "fundamentals",
    "sentiment": "social",
    "unlock": "lockup",
}

#: 真实 7 个 provider（`roles/llm.py` 起）；`offline` 另有单独条目（不发网络）
PROVIDERS: tuple[str, ...] = (
    "minimax",
    "deepseek",
    "openai",
    "dashscope",
    "qwen",
    "moonshot",
    "zhipu",
)

LOCAL_PROVIDER = "offline"

#: 与 `config/loader.py::_apply_env` 一致：`STOCKSENATE_LLM_PROVIDER` 覆盖 TOML 的
#: `[llm].llm_provider`，且 `offline` 会把所有角色一并压成本地引擎
ENV_PROVIDER = "STOCKSENATE_LLM_PROVIDER"

PROVIDER_KEY_ENV: dict[str, str] = {
    "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "minimax": "https://api.minimax.chat/v1",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
}

MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "minimax": ["MiniMax-M2"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "openai": ["gpt-4.1-mini", "gpt-4.1"],
    "dashscope": ["qwen-plus", "qwen-max"],
    "qwen": ["qwen-plus", "qwen-max"],
    "moonshot": ["moonshot-v1-8k", "kimi-k2-0905-preview"],
    "zhipu": ["glm-4.6", "glm-4.5-air"],
}

#: `[llm]` 下允许被本接口改写的全局键；其余全局键只读、原样保留
GLOBAL_WRITABLE_KEYS: tuple[str, ...] = ("llm_provider", "deep_think_llm", "quick_think_llm")

#: `[llm.role_llms.<role>]` 的合法字段（StockSenate `schema._LLM_ROLE_ENTRY` 只认
#: provider / model；`role_llms` 层没有 `api_key_env`，密钥变量名是 `[llm]` 全局键）
ROLE_ENTRY_FIELDS = frozenset({"provider", "model"})


# --------------------------------------------------------------------------- #
# 错误码
# --------------------------------------------------------------------------- #


class SenateError(Exception):
    """带稳定 `code` 的桥接错误；路由层转成 `{code, detail}` 响应体。"""

    STATUS: dict[str, int] = {
        "SENATE_UNAVAILABLE": 409,
        "SENATE_INVALID_VALUE": 400,
        "SENATE_SECRET_REJECTED": 400,
        "SENATE_STALE": 409,
        "SENATE_WRITE_FAILED": 500,
        "SENATE_NO_API_KEY": 400,
        "SENATE_NETWORK_DENIED": 403,
        "SENATE_UNREACHABLE": 502,
    }

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return self.STATUS.get(self.code, 400)


# --------------------------------------------------------------------------- #
# 契约解析（真实包优先，内联镜像兜底）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Contract:
    roles: tuple[str, ...]
    aliases: dict[str, str]
    providers: tuple[str, ...]
    provider_key_env: dict[str, str]
    default_base_urls: dict[str, str]
    role_labels: dict[str, str]
    model_suggestions: dict[str, list[str]]

    def canonical(self, role: str) -> str:
        key = str(role or "").strip()
        return self.aliases.get(key, key)

    def canonical_keys(self) -> set[str]:
        return {self.canonical(role) for role in self.roles}


_CONTRACT_CACHE: dict[str, Contract] = {}


def _import_stock_senate(root: Path | None) -> tuple[Any | None, Any | None]:
    """在 StockSenate 检出目录里 import 真实包；失败返回 (None, None)。

    检出目录里可能是 `<root>/src/stock_senate/`（当前布局），也可能是
    `<root>/configured/custom/` 这类自定义布局，两种都试。`sys.path` 只在导入
    时临时借用，成功后从 `sys.modules` 取模块，不常驻污染后续 import。
    """
    if root is None:
        return None, None
    import sys

    candidates = [root / "src", root / "configured" / "custom"]
    added: list[str] = []
    try:
        for candidate in candidates:
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
                added.append(str(candidate))
        try:
            schema = importlib.import_module("stock_senate.config.schema")
            llm = importlib.import_module("stock_senate.roles.llm")
        except Exception:  # noqa: BLE001 - 任何导入失败都退回内联表
            return None, None
        return schema, llm
    finally:
        for entry in added:
            try:
                sys.path.remove(entry)
            except ValueError:  # pragma: no cover - 并发下已被别的线程移除
                pass


def load_contract(root: Path | None) -> tuple[Contract, bool]:
    """返回 (契约, 是否来自真实包)。同一 root 的结果按包文件 mtime 缓存。"""
    cache_key = str(root) if root is not None else ""
    schema, llm = _import_stock_senate(root)
    if schema is None or llm is None:
        mirrored = Contract(
            roles=ROLE_LLM_KEYS,
            aliases=dict(ROLE_ALIASES),
            providers=PROVIDERS,
            provider_key_env=dict(PROVIDER_KEY_ENV),
            default_base_urls=dict(DEFAULT_BASE_URLS),
            role_labels=dict(ROLE_LABELS),
            model_suggestions={k: list(v) for k, v in MODEL_SUGGESTIONS.items()},
        )
        return mirrored, False

    real_roles = tuple(schema.ROLE_LLM_KEYS)
    # technical 不是别名，而是 StockSenate 对「技术面」的既有叫法（历史键），
    # 若真实包把它放进 ROLE_ALIASES 则如实照抄——绝不在这里自创映射。
    aliases = {str(k): str(v) for k, v in dict(schema.ROLE_ALIASES).items()}
    providers = tuple(llm._DEFAULT_BASE_URLS)  # noqa: SLF001 - 契约即包内表
    provider_key_env = {str(k): str(v) for k, v in dict(llm._PROVIDER_KEY_ENV).items()}
    base_urls = {str(k): str(v) for k, v in dict(llm._DEFAULT_BASE_URLS).items()}
    contract = Contract(
        roles=real_roles,
        aliases=aliases,
        providers=providers,
        provider_key_env=provider_key_env,
        default_base_urls=base_urls,
        role_labels={role: ROLE_LABELS.get(role, role) for role in real_roles},
        model_suggestions={
            provider: [
                model for model in MODEL_SUGGESTIONS.get(provider, []) if provider in providers
            ]
            for provider in providers
        },
    )
    _CONTRACT_CACHE[cache_key] = contract
    return contract, True


# --------------------------------------------------------------------------- #
# TOML 读写
# --------------------------------------------------------------------------- #


def read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return _parse_toml(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SenateError(
            "SENATE_UNAVAILABLE", f"{path.name} 读不出来：{exc}；请先修复该文件"
        ) from None


def _llm_section(data: dict[str, Any]) -> dict[str, Any]:
    llm = data.get("llm")
    return llm if isinstance(llm, dict) else {}


def _role_llms_of(llm: dict[str, Any]) -> dict[str, Any]:
    table = llm.get("role_llms")
    return table if isinstance(table, dict) else {}


def revision_of(path: Path) -> str:
    """内容指纹：读-改-写之间文件被动过就判 `SENATE_STALE`。"""
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise SenateError("SENATE_UNAVAILABLE", f"{path.name} 读不出来：{exc}") from None
    return hashlib.sha256(blob).hexdigest()[:16]


def _write_config(path: Path, text: str, validate: Callable[[Path], Any]) -> Path | None:
    """同 `write_ai_yaml` 的 fail-closed 姿态，但允许目标文件尚不存在（首次生成）。"""
    if path.is_file():
        return write_ai_yaml(path, text, validate).backup
    tmp_dir = path.with_name(f".{path.name}.tmp")
    temp = path.with_name(f".{path.name}.replace")
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        candidate = tmp_dir / path.name
        candidate.write_text(text, encoding="utf-8", newline="")
        validate(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(text, encoding="utf-8", newline="")
        os.replace(temp, path)
    except BoundaryViolation:
        raise
    except OSError as exc:
        raise BoundaryViolation(f"写入 {path.name} 失败：{exc}") from None
    finally:
        temp.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return None


def _toml_string(value: str) -> str:
    out = ['"']
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _render_entry(entry: dict[str, str]) -> str:
    parts = [
        f"provider = {_toml_string(entry['provider'])}",
        f"model = {_toml_string(entry['model'])}",
    ]
    return "{ " + ", ".join(parts) + " }"


def _replace_role_llms_table(text: str, entries: dict[str, dict[str, str]]) -> str:
    """只重写 `[llm.role_llms]` 这一段；其余行（含注释）原样保留。"""
    lines = text.splitlines()
    header = "[llm.role_llms]"
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    rendered = [f"{role} = {_render_entry(entry)}" for role, entry in entries.items()]
    if start is None:
        # 没有这一段：追加到文件末尾（`[llm.role_llms]` 必须是新的顶层表头）
        while lines and not lines[-1].strip():
            lines.pop()
        block = ["", header] + rendered
        return "\n".join(lines + block) + "\n"

    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    # 新表独占一段；段内原注释全部随旧条目一起被替换（这些注释只解释旧值）
    new_block = [header] + rendered
    tail = lines[end:]
    if tail and tail[0].strip():
        new_block.append("")
    return "\n".join(lines[:start] + new_block + tail) + "\n"


def _replace_global_key_env(text: str, key_env: str, existing: Any) -> tuple[str, bool]:
    """把变量名写进 `[llm]` 的 `api_key_env`；已有同值则不动。返回 (新文本, 是否改动)。"""
    if isinstance(existing, str) and existing.strip() == key_env:
        return text, False
    lines = text.splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == "[llm]":
            start = index
            continue
        if start is not None and line.strip().startswith("["):
            end = index
            break
    rendered = f"api_key_env = {_toml_string(key_env)}"
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join([*lines, "", "[llm]", rendered]) + "\n", True
    for index in range(start + 1, end):
        if "=" in lines[index] and lines[index].split("=", 1)[0].strip() == "api_key_env":
            lines[index] = rendered
            return "\n".join(lines) + "\n", True
    lines.insert(start + 1, rendered)
    return "\n".join(lines) + "\n", True


def _render_new_config(entries: dict[str, dict[str, str]], key_env: str | None = None) -> str:
    """首次写入（Senate 的 config/ 还没有 llm_config.toml）时生成最小合法文件。"""
    lines = [
        "# StockSenate 模型配置（由 LadderStock 控制面首次生成）",
        "",
        "[llm]",
        f"llm_provider = {_toml_string('minimax')}",
        f"deep_think_llm = {_toml_string('MiniMax-M2')}",
        f"quick_think_llm = {_toml_string('MiniMax-M2')}",
    ]
    if key_env:
        lines.append(f"api_key_env = {_toml_string(key_env)}")
    lines.extend(["", "[llm.role_llms]"])
    lines.extend(f"{role} = {_render_entry(entry)}" for role, entry in entries.items())
    return "\n".join(lines) + "\n"


def _validate_candidate(path: Path, contract: Contract) -> dict[str, Any]:
    """完整重解析：结构 → 角色键 → 别名冲突 → provider/model 形态 → 密钥变量名。"""
    try:
        data = _parse_toml(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BoundaryViolation(f"候选 TOML 解析失败：{exc}") from None
    return _validate_data(data, contract)


def _parse_toml(text: str) -> dict[str, Any]:
    from tomllib import loads as toml_loads

    data = toml_loads(text)
    return data if isinstance(data, dict) else {}


def _validate_data(data: dict[str, Any], contract: Contract) -> dict[str, Any]:
    llm = _llm_section(data)
    provider = llm.get("llm_provider")
    if not isinstance(provider, str) or not provider:
        raise BoundaryViolation("候选 llm_provider 必须是非空字符串")
    seen: dict[str, str] = {}
    role_llms = _role_llms_of(llm)
    if not role_llms:
        raise BoundaryViolation("候选 role_llms 必须是表")
    for key, entry in role_llms.items():
        canonical = contract.canonical(str(key))
        if canonical not in contract.roles:
            raise BoundaryViolation(f"非法角色键 {key}；合法键为 {', '.join(contract.roles)}")
        if canonical in seen:
            raise BoundaryViolation(f"role_llms.{key} 与 {seen[canonical]} 指向同一角色")
        seen[canonical] = str(key)
        if not isinstance(entry, dict):
            raise BoundaryViolation(f"role_llms.{key} 必须是表")
        if set(entry) - ROLE_ENTRY_FIELDS:
            raise BoundaryViolation(
                f"role_llms.{key} 只接受 provider / model / api_key_env"
                f"（多出：{', '.join(sorted(set(entry) - ROLE_ENTRY_FIELDS))}）"
            )
        for field_name in ("provider", "model"):
            value = entry.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise BoundaryViolation(f"role_llms.{key}.{field_name} 必须是非空字符串")
    key_env = llm.get("api_key_env")
    if key_env is not None and (not isinstance(key_env, str) or not _is_variable_name(key_env)):
        raise BoundaryViolation("llm.api_key_env 只能是环境变量名")
    return data


def _is_variable_name(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char == "_" for char in value)


# --------------------------------------------------------------------------- #
# 面向控制面的实现
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SenateRoleModels:
    """`/peers/senate/role-models` 的三条实现（GET / PUT / POST test）。"""

    ctl: PeerControl
    env_reader: Any  # (ctl) -> Mapping[str,str]；读 peer 的 dotenv
    http_post: Any  # (url, payload, headers, timeout) -> (status, body)
    environ: Any  # os.environ 同形
    resolve: Any  # host -> list[str]
    is_forbidden: Any  # ip -> bool
    clock: Any  # monotonic

    # ------------------------------------------------------------- 路径 / 契约

    @property
    def config_dir(self) -> Path:
        return Path(self.ctl.directory) / "config"

    @property
    def config_path(self) -> Path:
        return self.config_dir / LLM_CONFIG_FILE

    @property
    def env_path(self) -> Path:
        return Path(self.ctl.directory) / self.ctl.env_filename

    def _contract(self) -> tuple[Contract, bool]:
        return load_contract(Path(self.ctl.directory))

    def _effective_provider(self, file_provider: str) -> str:
        """StockSenate 进程里真正生效的 provider：TOML 起步，环境变量覆盖（仅启动时抓一次）。"""
        override = str(self.environ.get(ENV_PROVIDER) or "").strip()
        return override or str(file_provider or "").strip()

    # ------------------------------------------------------------------ GET

    def status(self) -> dict[str, Any]:
        contract, authoritative = self._contract()
        path = self.config_path
        directory_ok = Path(self.ctl.directory).is_dir()
        data = read_toml(path)
        llm = _llm_section(data)
        file_provider = str(llm.get("llm_provider") or "")
        provider = self._effective_provider(file_provider)
        role_llms = _role_llms_of(llm)
        # `role_llms` 层没有 api_key_env：StockSenate 按角色取 key 时只认全局
        # `[llm].api_key_env` 与 provider 默认变量，这里如实回显该全局变量名。
        global_key_env = str(llm.get("api_key_env") or "").strip() or None
        env_override = provider != file_provider

        roles: list[dict[str, Any]] = []
        unconfigured: list[str] = []
        for role in contract.roles:
            entry = role_llms.get(role)
            if not (isinstance(entry, dict) and entry.get("provider") and entry.get("model")):
                unconfigured.append(role)
                roles.append(
                    {
                        "role": role,
                        "label": contract.role_labels.get(role, role),
                        "configured": False,
                        "provider": None,
                        "model": None,
                        "api_key_env": None,
                    }
                )
                continue
            roles.append(
                {
                    "role": role,
                    "label": contract.role_labels.get(role, role),
                    "configured": True,
                    "provider": str(entry["provider"]),
                    "model": str(entry["model"]),
                    "api_key_env": global_key_env,
                }
            )

        global_block = {
            "provider": provider or None,
            "deep_think_llm": llm.get("deep_think_llm"),
            "quick_think_llm": llm.get("quick_think_llm"),
            "backend_url_set": bool(str(llm.get("backend_url") or "").strip()),
            "api_key_env": llm.get("api_key_env"),
        }

        providers = [
            {
                "name": name,
                "label": name,
                "api_key_env": contract.provider_key_env.get(name),
                "base_url": contract.default_base_urls.get(name),
                "models": list(contract.model_suggestions.get(name, [])),
            }
            for name in contract.providers
        ]
        providers.append(
            {
                "name": LOCAL_PROVIDER,
                "label": "offline（本地确定性引擎，不发网络）",
                "api_key_env": None,
                "base_url": None,
                "models": [LOCAL_PROVIDER],
            }
        )

        if provider.lower() == LOCAL_PROVIDER:
            source = (
                f"环境变量 STOCKSENATE_LLM_PROVIDER={provider}（优先于 TOML）"
                if env_override
                else f"[llm].llm_provider = {provider}"
            )
            hint = (
                f"{source}：StockSenate 走本地确定性引擎、不访问任何模型服务；"
                "改回某个远程 provider 后才会真正发请求。"
            )
            reload_hint = "写入 llm_config.toml 在下次进程启动时生效；运行中的 Senate 需重启。"
        else:
            hint = (
                "StockSenate 的 llm_provider 只从 config/llm_config.toml 读，环境变量的 "
                "LLM_PROVIDER 覆盖只在进程启动时抓一次；本页写的是 TOML。"
            )
            reload_hint = (
                "写入 llm_config.toml 是权威来源（优先于 WebUI 里的临时设置）；"
                "运行中的 Senate 在下次进程启动时加载，需重启才生效。"
            )

        return {
            "engine": {
                "authority": "env",
                "reload": "webui_per_job",
                "provider": provider or None,
                "file_provider": file_provider or None,
                "env_override": env_override,
                "config_file": str(path),
                "contract_source": "package" if authoritative else "mirror",
                "roles_authoritative": authoritative,
                "hint": hint,
                "reload_hint": reload_hint,
            },
            "roles": roles,
            "unconfigured_roles": unconfigured,
            "role_count": len(roles),
            "global": global_block,
            "providers": providers,
            "aliases": dict(contract.aliases),
            "directory_exists": directory_ok,
            "config_exists": path.is_file(),
            "env_file": str(self.env_path),
            "env_keys": list(self.ctl.secret_keys),
            "revision": revision_of(path),
        }

    # ------------------------------------------------------------------ PUT

    def update(self, roles: Any, expected_revision: str | None = None) -> dict[str, Any]:
        if not Path(self.ctl.directory).is_dir():
            raise SenateError(
                "SENATE_UNAVAILABLE",
                f"StockSenate 目录不存在：{self.ctl.directory}",
            )
        if not isinstance(roles, dict) or not roles:
            raise SenateError("SENATE_INVALID_VALUE", "roles 必须是非空对象")
        contract, _ = self._contract()
        canonical_keys = contract.canonical_keys()

        clean: dict[str, dict[str, str]] = {}
        clean_key_env: str | None = None
        for raw_role, instruction in roles.items():
            role = str(raw_role).strip()
            if role not in canonical_keys:
                raise SenateError(
                    "SENATE_INVALID_VALUE",
                    f"非法角色键 {role}（合法键：{', '.join(contract.roles)}；"
                    f"别名：{', '.join(sorted(contract.aliases)) or '无'}）",
                )
            if not isinstance(instruction, dict):
                raise SenateError("SENATE_INVALID_VALUE", f"角色 {role} 的值必须是对象")
            for field_name, value in instruction.items():
                canonical_field = str(field_name)
                if canonical_field not in {"provider", "model", "api_key_env"}:
                    raise SenateError(
                        "SENATE_SECRET_REJECTED"
                        if _looks_like_secret(canonical_field)
                        else "SENATE_INVALID_VALUE",
                        f"角色 {role} 不接受字段 {canonical_field}"
                        "（只允许 provider / model / api_key_env）",
                    )
                if isinstance(value, (dict, list, tuple)) or value is None:
                    raise SenateError(
                        "SENATE_INVALID_VALUE",
                        f"角色 {role}.{canonical_field} 必须是字符串",
                    )
            for required in ("provider", "model"):
                value = instruction.get(required)
                if not isinstance(value, str) or not value.strip():
                    raise SenateError("SENATE_INVALID_VALUE", f"角色 {role}.{required} 必填")
            entry = {
                "provider": str(instruction["provider"]).strip().lower(),
                "model": str(instruction["model"]).strip(),
            }
            # StockSenate 的 `role_llms` 条目只认 provider / model；`api_key_env` 是
            # `[llm]` 全局键。这里只收变量名并把它写进 `[llm].api_key_env`，
            # 不塞进角色条目（否则 schema.validate_dir 会判非法键）。
            key_env = instruction.get("api_key_env")
            if key_env is not None and str(key_env).strip():
                env_name = str(key_env).strip()
                if not _is_variable_name(env_name):
                    raise SenateError(
                        "SENATE_SECRET_REJECTED",
                        "api_key_env 只接受环境变量名（例如 MINIMAX_API_KEY），不接受密钥内容",
                    )
                clean_key_env = env_name
            clean[role] = entry

        path = self.config_path
        current = revision_of(path)
        if expected_revision and expected_revision != current:
            raise SenateError(
                "SENATE_STALE",
                f"llm_config.toml 已被外部修改"
                f"（期望 revision {expected_revision}，实际 {current}），请刷新后重试",
            )

        before_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        data = read_toml(path)
        llm = _llm_section(data)
        merged: dict[str, dict[str, str]] = {}
        existing = _role_llms_of(llm)
        aliased: dict[str, str] = {}
        for key, entry in existing.items():
            canonical = contract.canonical(str(key))
            if canonical not in canonical_keys or not isinstance(entry, dict):
                continue
            normalised = {
                name: str(entry[name])
                for name in ("provider", "model")
                if isinstance(entry.get(name), str)
            }
            if set(entry) - ROLE_ENTRY_FIELDS:  # 存量里的非法字段不跟着写回去
                continue
            if canonical != str(key):
                aliased[canonical] = str(key)
            merged[canonical] = normalised
        merged.update(clean)

        ordered: dict[str, dict[str, str]] = {
            role: merged[role] for role in contract.roles if role in merged
        }
        for role, entry in merged.items():  # 未知键早已被过滤；保留保序兜底
            ordered.setdefault(role, entry)

        wrote_key_env = False
        if before_text:
            candidate_text = _replace_role_llms_table(before_text, ordered)
            if clean_key_env:
                candidate_text, wrote_key_env = _replace_global_key_env(
                    candidate_text, clean_key_env, llm.get("api_key_env")
                )
        else:
            candidate_text = _render_new_config(ordered, clean_key_env)
            wrote_key_env = bool(clean_key_env)

        try:
            _write_config(
                path,
                candidate_text,
                lambda candidate: _validate_candidate(candidate, contract),
            )
        except BoundaryViolation as exc:
            raise SenateError("SENATE_WRITE_FAILED", str(exc)) from None

        payload = self.status()
        payload["updated"] = sorted(clean)
        payload["aliases_rewritten"] = {role: aliased[role] for role in clean if role in aliased}
        payload["revision"] = revision_of(path)
        payload["api_key_env_written"] = wrote_key_env
        return payload

    # ------------------------------------------------------------- POST test

    def test(
        self,
        *,
        role: str,
        provider: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
    ) -> dict[str, Any]:
        contract, _ = self._contract()
        canonical = contract.canonical(role)
        if canonical not in contract.canonical_keys():
            raise SenateError(
                "SENATE_INVALID_VALUE",
                f"非法角色键 {role}（合法键：{', '.join(contract.roles)}）",
            )
        data = read_toml(self.config_path)
        llm = _llm_section(data)
        role_llms = _role_llms_of(llm)
        raw_saved = role_llms.get(canonical)
        saved: dict[str, Any] = raw_saved if isinstance(raw_saved, dict) else {}
        # 优先反映 StockSenate 进程真正生效的 provider：离线覆盖与全局离线
        # 都会让所有角色走本地引擎，此时探测必须短路，不能拿角色里的存量去发请求。
        global_provider = self._effective_provider(str(llm.get("llm_provider") or ""))
        env_offline = global_provider.strip().lower() == LOCAL_PROVIDER
        provider_source = "override"
        if provider and str(provider).strip():
            effective_provider = str(provider).strip().lower()
        elif env_offline:
            effective_provider, provider_source = LOCAL_PROVIDER, "env_override"
        elif saved.get("provider"):
            effective_provider, provider_source = str(saved["provider"]).strip().lower(), "role"
        else:
            effective_provider, provider_source = (
                str(global_provider or LOCAL_PROVIDER).strip().lower(),
                "config",
            )
        effective_model = str(
            model
            or saved.get("model")
            or llm.get("deep_think_llm")
            or llm.get("quick_think_llm")
            or ""
        ).strip()
        # `role_llms` 条目里没有 api_key_env（StockSenate schema 只认 provider/model），
        # key 变量名只来自请求或全局 `[llm].api_key_env`。
        env_name = (
            str(api_key_env).strip() if api_key_env and str(api_key_env).strip() else ""
        ) or str(llm.get("api_key_env") or "").strip()

        base = {
            "role": canonical,
            "provider": effective_provider,
            "provider_source": provider_source,
            "model": effective_model,
        }

        if effective_provider == LOCAL_PROVIDER:
            return {
                **base,
                "ok": True,
                "skipped": True,
                "latency_ms": 0,
                "error_code": None,
                "message": (
                    "llm_provider = offline：StockSenate 走本地引擎、不发网络请求，已跳过连通性探测"
                ),
            }
        if not effective_model:
            return {
                **base,
                "ok": False,
                "skipped": False,
                "latency_ms": 0,
                "error_code": "SENATE_INVALID_VALUE",
                "message": "没有可用模型：请在请求里给 model，或先在 llm_config.toml 配好该角色",
            }

        key, key_source = self._api_key(effective_provider, env_name)
        if not key:
            names = [
                name
                for name in (
                    env_name,
                    contract.provider_key_env.get(effective_provider),
                )
                if name
            ]
            return {
                **base,
                "ok": False,
                "skipped": False,
                "latency_ms": 0,
                "error_code": "SENATE_NO_API_KEY",
                "message": (
                    f"未在进程环境变量或 {self.env_path.name} 中找到 "
                    f"{' / '.join(dict.fromkeys(names)) or 'API key'}；"
                    "桥接只读环境变量，不读本机 AI 设置"
                ),
            }

        # `backend_url` 是模型服务地址（可能是内网/本机中转），探测要如实沿用它，
        # 否则"能不能连通当前模型"就答不准；覆盖项走 provider 自己的默认端点。
        base_url = (
            self._base_url(contract, llm, effective_provider)
            if provider_source in {"role", "config", "env_override"}
            else (
                contract.default_base_urls.get(effective_provider)
                or DEFAULT_BASE_URLS.get(effective_provider)
                or ""
            )
        )
        started = self.clock()
        try:
            self._guard_url(base_url, carry_secret=bool(key))
        except SenateError as exc:
            return {
                **base,
                "ok": False,
                "skipped": False,
                "latency_ms": int((self.clock() - started) * 1000),
                "error_code": exc.code,
                "message": exc.message,
            }

        payload = {
            "model": effective_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": PROBE_MAX_TOKENS,
            "temperature": 0.0,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        try:
            status, body = self.http_post(
                f"{base_url.rstrip('/')}/chat/completions", payload, headers, self._timeout(llm)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {
                **base,
                "ok": False,
                "skipped": False,
                "latency_ms": int((self.clock() - started) * 1000),
                "error_code": "SENATE_UNREACHABLE",
                "message": _redact(f"请求失败：{exc}", key),
            }
        latency = int((self.clock() - started) * 1000)
        if 200 <= status < 300:
            return {
                **base,
                "ok": True,
                "skipped": False,
                "latency_ms": latency,
                "error_code": None,
                "message": f"连通成功（HTTP {status}；key 来源：{key_source}）",
            }
        if status in (401, 403):
            code, message = "SENATE_NETWORK_DENIED", f"provider 拒绝了密钥（HTTP {status}）"
        elif status == 429:
            code, message = "SENATE_UNREACHABLE", "provider 限流（HTTP 429），稍后重试"
        else:
            code, message = "SENATE_UNREACHABLE", f"provider 返回 HTTP {status}"
        return {
            **base,
            "ok": False,
            "skipped": False,
            "latency_ms": latency,
            "error_code": code,
            "message": _redact(message, key, body),
        }

    # ------------------------------------------------------------ 内部工具

    def _api_key(self, provider: str, env_name: str) -> tuple[str, str]:
        names = [name for name in (env_name, "STOCKSENATE_LLM_API_KEY") if name]
        default_name = PROVIDER_KEY_ENV.get(provider)
        if default_name:
            names.append(default_name)
        for name in dict.fromkeys(names):
            value = (self.environ.get(name) or "").strip()
            if value:
                return value, f"环境变量 {name}"
        try:
            file_values = self.env_reader(self.ctl) or {}
        except Exception:  # noqa: BLE001 - dotenv 读不出来只能当「没有 key」
            file_values = {}
        for name in dict.fromkeys(names):
            value = str(file_values.get(name) or "").strip()
            if value:
                return value, f"{self.env_path.name} 中的 {name}"
        return "", ""

    def _base_url(self, contract: Contract, llm: dict[str, Any], provider: str) -> str:
        backend_url = str(llm.get("backend_url") or "").strip()
        if backend_url:
            return backend_url
        return contract.default_base_urls.get(provider) or DEFAULT_BASE_URLS.get(provider) or ""

    def _timeout(self, llm: dict[str, Any]) -> float:
        raw = llm.get("llm_timeout")
        try:
            value = float(raw) if isinstance(raw, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            value = DEFAULT_PROBE_TIMEOUT_SEC
        if not math.isfinite(value) or value <= 0:
            value = DEFAULT_PROBE_TIMEOUT_SEC
        return min(value, 60.0)

    def _guard_url(self, base_url: str, *, carry_secret: bool) -> None:
        import ipaddress
        import urllib.parse

        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise SenateError("SENATE_INVALID_VALUE", "backend_url 必须是 http(s) 地址")
        host = (parsed.hostname or "").strip()
        if not host:
            raise SenateError("SENATE_INVALID_VALUE", "backend_url 缺少主机名")
        try:
            addresses = [str(ip) for ip in self.resolve(host)]
        except (OSError, UnicodeError) as exc:
            raise SenateError(
                "SENATE_UNREACHABLE", f"backend_url 主机无法解析：{host}（{exc}）"
            ) from None
        if not addresses:
            raise SenateError("SENATE_UNREACHABLE", f"backend_url 主机无法解析：{host}")
        for raw in addresses:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if self.is_forbidden(raw):
                raise SenateError(
                    "SENATE_NETWORK_DENIED",
                    f"{host} 解析到内网/保留地址（{ip}），带密钥的探测不予放行",
                )
            if carry_secret and ip.is_loopback:
                raise SenateError(
                    "SENATE_NETWORK_DENIED",
                    f"{host} 指向本机地址（{ip}）：密钥只允许发往外网 provider",
                )


def local_provider() -> str:
    return LOCAL_PROVIDER


def _looks_like_secret(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(
        marker in lowered
        for marker in (
            "apikey",
            "api_key",
            "key",
            "token",
            "secret",
            "password",
            "credential",
            "sk-",
        )
    )


def _redact(text: str, key: str, body: str | None = None) -> str:
    out = text
    if key and key in out:
        out = out.replace(key, _mask(key))
    if body and key and key in body:
        body = body.replace(key, _mask(key))
    if body:
        snippet = body.strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        out = f"{out}；响应：{snippet}"
    return out


def _mask(key: str) -> str:
    tail = key[-4:] if len(key) >= 4 else ""
    return f"****{tail}" if tail else "****"


__all__ = [
    "DEFAULT_BASE_URLS",
    "GLOBAL_WRITABLE_KEYS",
    "LLM_CONFIG_FILE",
    "MODEL_SUGGESTIONS",
    "PROVIDER_KEY_ENV",
    "PROVIDERS",
    "ROLE_ALIASES",
    "ROLE_LLM_KEYS",
    "ROLE_MODELS_SECTION",
    "SenateError",
    "SenateRoleModels",
    "load_contract",
    "read_toml",
    "revision_of",
]
