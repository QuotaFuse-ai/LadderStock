"""助手通道的跨会话记忆（QUOT-487）：进程内 mem0 SDK 读写、写入门槛与注入分段。

QUOT-611 起改为 **进程内 mem0 SDK**（不再依赖 Docker / 127.0.0.1:9100 REST 实例）：

- 向量库：qdrant-client 本地模式（`path=` 落盘，portalocker 独占锁 → 全应用唯一
  Memory 单例，懒加载 + 锁双检）。
- Embedder：BAAI/bge-small-zh-v1.5（512 维），唯一运行时为 BAAI 原版 safetensors
  （sentence-transformers/torch），模型预置在安装包 `file/models/`，全程离线
  （`HF_HUB_OFFLINE=1` 等），绝不运行时触网。fastembed/ONNX 副本仅作为分发资产
  保留在包内，不是运行时路径（两栈向量不在同一空间，不并列回落）。
  加载失败（torch 缺失 / 模型目录缺失 / 校验不过）→ `MemoryUnavailable`
  明确报错，绝不静默降级。embedder 详情写进 `engine_health()`（/api/health）。
- LLM：本地网关（openai 兼容，`LDST_LLM_*` / `config/ai.env`），`infer=True`
  保持既有行为：写入的是消息，记忆是 mem0 抽取产物。
- 落盘路径按实例维度隔离（沿用现有 LDST_ENV，不新写隔离规则）：
  `state_dir/<env>/{qdrant/, history.db}`，prod / dev 物理隔离。

通道是为 **LadderStock 助手**（`web/assistant.py` 的一问一答）服务的：提问前按
命名空间召回记忆，回复后按策略异步写入；mem0 侧的任何失败都只降级不拖垮回答。

红线（改动前先读，写错会污染别的系统）：

1. **绝不直连 Multica 自己的 mem0**（D:\\mem0，9000）——本模块是进程内 SDK，
   不向任何 mem0 实例发网络请求。
2. **只在声明的命名空间内读/写**：助手通道跟随 QUOT-498 模块命名空间
   （`agent_id` + `kb_scope` 两维同时到位，载荷经 `module_memory` 校验）；
   独立调用用 `user_id + agent_id = ladderstock-assistant`。不跨用户检索；
   user_id 从配置读（环境变量，缺省落本机持久标识），不写死成 `default`。
3. **只写通过重要性门槛的用户消息**，且不含密钥（正则族直接拒绝）、不含未经验证
   的数字（估值 / 涨跌幅，需回查数据源）、不含逐轮对话原文、不落结构化标的档案
   （那是 QUOT-488 的职责，带 `as_of` 与来源，与本通道的模糊线索不同源）。
4. 召回以「线索」身份注入，与知识库（QUOT-488）物理分段并各自标注来源；
   冲突时以档案为准，回答里须说明「记忆里是旧说法」。

所有 mem0 侧失败（引擎初始化失败 / SDK 异常）统一抛 `MemoryUnavailable`：
召回失败 → 回答不带记忆照常完成；写入失败 → 只记日志。两者都带上下文与耗时，
绝不只写一个 `error`。日志只出现指纹（短哈希），绝不出原文。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ladderstock.ai import module_memory
from ladderstock.ai.fallback import MemoryScope

logger = logging.getLogger("ladderstock.ai.memory")

#: 独立调用 / CLI memory-clear 用的 agent 身份；助手问答通道走 QUOT-498 模块命名空间
AGENT_ID = "ladderstock-assistant"

#: 本条记忆的来源页面标记，写进 metadata.source
SOURCE = "assistant"

DEFAULT_TOP_K = 5
DEFAULT_SCORE_THRESHOLD = 0.55
DEFAULT_TIMEOUT_SEC = 5.0

#: 重要性打分低于该值的候选直接丢弃；用户显式说「记住」时 bypass 本门槛（红线仍生效）
WRITE_THRESHOLD = 0.5

#: 召回加权：命中当前 symbol 的记忆加分（top_k 截断前生效）
SYMBOL_BOOST = 0.05

#: mem0 连续失败后的静默冷却：避免每次提问都重试一次失败路径（按实例落盘分桶）
COOLDOWN_SEC = 30.0

#: 注入提示的单条记忆截断与整块上限（防止把 prompt 撑爆）
MEMORY_TEXT_MAX = 120
MEMORY_BLOCK_MAX = 800

ENV_USER_ID = "LDST_MEMORY_USER_ID"
ENV_TOP_K = "LDST_MEMORY_TOP_K"
ENV_SCORE_THRESHOLD = "LDST_MEMORY_SCORE_THRESHOLD"
ENV_TIMEOUT_SEC = "LDST_MEMORY_TIMEOUT_SEC"
ENV_ENABLED = "LDST_MEMORY_ENABLED"
#: 模型根目录（安装包 file/models/）；缺省由 install_root 推导
ENV_MODELS_DIR = "LDST_MEMORY_MODELS_DIR"
#: qdrant / history db 落盘根目录；缺省 data_dir()/mem0
ENV_STATE_DIR = "LDST_MEMORY_STATE_DIR"

#: QUOT-611 兼容哨兵：显式设为空串 → 关闭整个通道（测试隔离沿用此口径）；
#: 设了非空值（旧的 9100 地址等）→ 忽略该值，通道照开（进程内不再联网）。
LEGACY_ENV_BASE_URL = "LDST_MEMORY_BASE_URL"

_FALSEY = {"", "0", "false", "no", "off"}

# --------------------------------------------------------------------------- #
# 进程内引擎路径布局
# --------------------------------------------------------------------------- #

STATE_DIRNAME = "mem0"
QDRANT_DIRNAME = "qdrant"
HISTORY_DB_FILENAME = "history.db"
COLLECTION_NAME = "ladderstock_memories"

#: bge-small-zh-v1.5 = 512 维
EMBEDDING_DIMS = 512
#: BAAI 原版模型在 HF 上的标识（仅用于文档/日志展示，运行时按本地目录加载）
EMBEDDER_MODEL = "BAAI/bge-small-zh-v1.5"

MODELS_DIRNAME = "models"
#: BAAI 原版（safetensors，走 sentence_transformers）目录名
BAAI_MODEL_DIRNAME = "bge-small-zh-v1.5-baai"


def _install_models_dir() -> Path:
    """安装包内预置模型根：install_root/file/models/。LDST_MEMORY_MODELS_DIR 可覆盖。"""
    from ladderstock.cli.paths import data_dir
    from ladderstock.core.runtime import install_root

    env = os.environ.get(ENV_MODELS_DIR, "").strip()
    if env:
        return Path(env)
    candidate = install_root() / "file" / MODELS_DIRNAME
    if candidate.is_dir():
        return candidate
    # 兜底：数据目录旁的 models/（便携布局 / 测试）
    return data_dir().parent / MODELS_DIRNAME


def _default_state_dir() -> Path:
    """qdrant / history db 落盘根：data_dir()/mem0。实例维度（LDST_ENV）再分一层。"""
    from ladderstock.cli.paths import data_dir

    env = os.environ.get(ENV_STATE_DIR, "").strip()
    if env:
        return Path(env)
    return data_dir() / STATE_DIRNAME


def _env_instance_name() -> str:
    """实例维度（双实例隔离沿用现有 LDST_ENV，不新写规则）。"""
    from ladderstock.supervisor.modules import current_env

    return current_env()


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemorySettings:
    """记忆通道配置。`enabled=False` 时整个通道跳过（不建库、不注入、不写日志风暴）。"""

    user_id: str
    top_k: int
    score_threshold: float
    timeout_sec: float
    enabled: bool
    models_dir: Path
    state_dir: Path

    def env_state_dir(self) -> Path:
        """本实例（LDST_ENV）专属落盘目录：state_dir/<env>/，prod / dev 物理隔离。"""
        return self.state_dir / _env_instance_name()

    def qdrant_path(self) -> Path:
        return self.env_state_dir() / QDRANT_DIRNAME

    def history_db_path(self) -> Path:
        return self.env_state_dir() / HISTORY_DB_FILENAME


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("memory_config_invalid env=%s value=%r fallback=%s", name, raw, default)
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("memory_config_invalid env=%s value=%r fallback=%s", name, raw, default)
        return default


def _persistent_user_id() -> str:
    """env 未指定 user_id 时，用 data/memory/user_id 的持久本机标识兜底。

    固定单用户、跨重启不变；首次生成。绝不使用 `default`——那会让任何一台
    装上本产品的机器共享同一段记忆空间。
    """
    from ladderstock.cli.paths import data_dir

    path = data_dir() / "memory" / "user_id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
    except OSError as exc:
        # 只读文件系统之类：退回一次性 id（仅本进程内一致），并明确记录
        logger.warning(
            "memory_user_id_persist_failed path=%s error=%s using ephemeral_id", path, exc
        )
        return value
    return value


def load_settings() -> MemorySettings:
    """从环境变量读记忆配置；缺失项取文档默认值（`.env.example` 有说明）。

    enabled 口径（QUOT-611）：LDST_MEMORY_ENABLED falsey → 关；
    LDST_MEMORY_BASE_URL 显式设为空串 → 关（测试隔离沿用此口径）；
    其余一律开——进程内引擎没有「远端实例」概念，旧的非空 9100 值忽略。
    """
    enabled = os.environ.get(ENV_ENABLED, "1").strip().lower() not in _FALSEY
    if os.environ.get(LEGACY_ENV_BASE_URL, None) == "":
        enabled = False
    return MemorySettings(
        user_id=os.environ.get(ENV_USER_ID, "").strip() or _persistent_user_id(),
        top_k=max(1, _int_env(ENV_TOP_K, DEFAULT_TOP_K)),
        score_threshold=_float_env(ENV_SCORE_THRESHOLD, DEFAULT_SCORE_THRESHOLD),
        timeout_sec=max(0.1, _float_env(ENV_TIMEOUT_SEC, DEFAULT_TIMEOUT_SEC)),
        enabled=enabled,
        models_dir=_install_models_dir(),
        state_dir=_default_state_dir(),
    )


# --------------------------------------------------------------------------- #
# 进程内引擎（mem0 SDK）：懒加载单例
# --------------------------------------------------------------------------- #


class MemoryUnavailable(RuntimeError):
    """mem0 侧任何失败：引擎初始化失败、SDK 异常。调用方一律降级。"""


def _prepare_offline_env(models_dir: Path) -> None:
    """进程级离线开关（setdefault，不覆盖用户显式设置）。绝不运行时触网。"""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # mem0 的 FastEmbedEmbedding 不透传 cache_dir / specific_model_path，
    # 只能靠 fastembed 认的缓存根（model_management.py 认它 → local_files_only=True）
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(models_dir))
    # baai_local 分支的 HuggingFaceEmbedding（sentence-transformers）同样只认
    # 环境变量：本地模型目录当 home，杜绝任何落到 ~/.cache 的下载路径
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(models_dir))
    os.environ.setdefault("MEM0_TELEMETRY", "False")


def _embedder_config(model: str) -> Any:
    from mem0.configs.embeddings.base import BaseEmbedderConfig

    return BaseEmbedderConfig(model=model, embedding_dims=EMBEDDING_DIMS)


def _verify_model_integrity(models_dir: Path) -> None:
    """比对 SHA256SUMS 清单；损坏/被改的文件 → MemoryUnavailable，绝不带病加载。

    清单由开发机下载模型时生成（随包），覆盖两个模型目录的全部文件。
    清单缺失视为通过（旧包兼容），但包内标准布局必须带清单。
    """
    manifest = models_dir / "SHA256SUMS"
    if not manifest.is_file():
        logger.warning("model_integrity_manifest_missing path=%s", manifest)
        return
    import hashlib

    bad: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, rel = line.partition(" ")
        rel = rel.strip().lstrip("*").replace("/", os.sep)
        path = models_dir / rel
        if not path.is_file():
            bad.append(f"{rel}（文件缺失）")
            continue
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest.lower():
            bad.append(f"{rel}（哈希不一致）")
    if bad:
        raise MemoryUnavailable(
            "模型文件完整性校验失败：" + "；".join(bad)
            + f"。安装包 {models_dir} 内文件损坏或被改动，请重新安装（SHA256SUMS 为随包清单）。"
        )


def _load_embedding_engine(models_dir: Path) -> tuple[str, Any]:
    """唯一运行时（决策 A 定稿）：BAAI 原版 safetensors，sentence-transformers/torch。

    没有「回落 ONNX」这条并列路径——fastembed（ONNX）与 SentenceTransformer
    （safetensors）的向量不在同一空间，混用会让检索错乱。包内仍保留 ONNX 副本
    仅作分发资产，不是必达路径。加载失败 → MemoryUnavailable，绝不静默降级。
    """
    baai_dir = models_dir / BAAI_MODEL_DIRNAME
    # fastembed / hf_hub 的离线开关在这里就要就位：本函数可能被直接调用，
    # 不等 _build_memory 里的 setdefault（FASTEMBED_CACHE_PATH 决定缓存根）
    _prepare_offline_env(models_dir)
    _verify_model_integrity(models_dir)

    try:
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        raise MemoryUnavailable(
            "嵌入模型不可用：torch / sentence-transformers 未随包安装"
            "（决策 A 唯一运行时，无 ONNX 回落）。请完整安装应用。"
        ) from None
    if not baai_dir.is_dir():
        raise MemoryUnavailable(
            f"嵌入模型不可用：本地模型目录 {baai_dir} 不存在。"
            f"请确认安装包 {models_dir} 内预置了模型（LDST_MEMORY_MODELS_DIR 可覆盖）。"
        )
    from mem0.embeddings.huggingface import HuggingFaceEmbedding

    return "baai_local", HuggingFaceEmbedding(_embedder_config(model=str(baai_dir)))


def _build_llm(llm: Any) -> Any:
    from mem0.configs.llms.openai import OpenAIConfig
    from mem0.llms.openai import OpenAILLM

    return OpenAILLM(
        OpenAIConfig(
            model=llm.model,
            openai_base_url=llm.base_url,
            api_key=llm.api_key,
        )
    )


def _build_history_db(history_db_path: str) -> Any:
    from mem0.memory.storage import SQLiteManager

    return SQLiteManager(history_db_path)


@dataclass
class _LlmEndpoint:
    model: str
    base_url: str
    api_key: str


def _build_memory(models_dir: Path, state_dir: Path) -> Any:
    """装配进程内 mem0.Memory。LLM 未配置或装配失败 → MemoryUnavailable。

    绕过 `Memory.__init__` 的 EmbedderFactory（它会重新走 fastembed 默认下载路径），
    手动注入 `_load_embedding_engine` 产出的本地 embedder——模型预置离线的关键。
    """
    from ladderstock.ai.fallback import load_config

    config = load_config()
    target = next(
        (t for t in config.targets if t.backend.configured), None
    )
    if target is None:
        raise MemoryUnavailable(
            "记忆引擎初始化失败：本地 LLM 网关未配置"
            "（LDST_LLM_BASE_URL / LDST_LLM_API_KEY 或 config/ai.env）。"
            "infer=True 的记忆抽取依赖它。"
        )
    llm = _LlmEndpoint(
        model=target.model,
        base_url=target.backend.base_url,
        api_key=target.backend.api_key,
    )

    qdrant_path = state_dir / QDRANT_DIRNAME
    qdrant_path.mkdir(parents=True, exist_ok=True)
    history_db_path = state_dir / HISTORY_DB_FILENAME

    _prepare_offline_env(models_dir)

    try:
        from mem0 import Memory
        from mem0.configs.base import MemoryConfig
        from mem0.vector_stores.configs import VectorStoreConfig
        from mem0.vector_stores.qdrant import Qdrant

        branch, embedder = _load_embedding_engine(models_dir)
        vector_store = Qdrant(
            collection_name=COLLECTION_NAME,
            embedding_model_dims=EMBEDDING_DIMS,
            path=str(qdrant_path),
        )
    except MemoryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - qdrant 本地锁冲突等，统一显式失败
        raise MemoryUnavailable(
            f"记忆引擎初始化失败：{exc.__class__.__name__}: {exc}"
            f"（qdrant 路径 {qdrant_path}）"
        ) from None

    config = MemoryConfig(
        vector_store=VectorStoreConfig(
            provider="qdrant",
            config={
                "collection_name": COLLECTION_NAME,
                "embedding_model_dims": EMBEDDING_DIMS,
                "path": str(qdrant_path),
            },
        ),
        llm={
            "provider": "openai",
            "config": {
                "model": llm.model,
                "openai_base_url": llm.base_url,
                "api_key": llm.api_key,
            },
        },
        history_db_path=str(history_db_path),
        version="v1.1",
    )

    memory = Memory.__new__(Memory)
    memory.config = config
    memory.vector_store = vector_store
    memory.embedding_model = embedder
    memory.llm = _build_llm(llm)
    memory.db = _build_history_db(str(history_db_path))
    memory.collection_name = COLLECTION_NAME
    memory.api_version = config.version
    memory.custom_instructions = config.custom_instructions
    # 非标准属性：实际加载分支，写进 engine_health() 与启动日志
    memory.engine_branch = branch
    memory.embedder_info = {
        "engine": "sentence-transformers",
        "model": str(models_dir / BAAI_MODEL_DIRNAME),
        "dim": EMBEDDING_DIMS,
    }
    logger.info("memory_engine_ready branch=%s qdrant=%s", branch, qdrant_path)
    return memory


#: 模块级单例（懒加载 + 锁双检）。qdrant local 是 portalocker 独占锁：
#: 全应用只允许一个 Memory 实例，第二个实例会锁冲突。
_engine_lock = threading.Lock()
_engine: Any | None = None
_engine_error: MemoryUnavailable | None = None


def get_engine() -> Any:
    """取进程内 mem0.Memory 单例；初始化失败抛 MemoryUnavailable。

    失败不永久缓存：冷却期内调用方根本不会进来（见 _note_failure），
    冷却过后允许重试（比如 LLM 网关起来了）。
    """
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            settings = load_settings()
            try:
                _engine = _build_memory(settings.models_dir, settings.env_state_dir())
                _engine_error = None
            except MemoryUnavailable as exc:
                _engine_error = exc
                raise
    return _engine


def engine_health() -> dict[str, Any]:
    """/api/health 的 memory 段：引擎状态与实际加载分支（不触发加载）。"""
    if _engine is not None:
        return {
            "engine": "in-process",
            "branch": getattr(_engine, "engine_branch", "unknown"),
            "embedder": getattr(
                _engine, "embedder_info", {"engine": "sentence-transformers"}
            ),
            "ok": True,
        }
    if _engine_error is not None:
        return {
            "engine": "in-process",
            "branch": None,
            "ok": False,
            "error": _engine_error.__class__.__name__,
        }
    return {"engine": "in-process", "branch": "not_loaded", "ok": None}


def reset_runtime_state() -> None:
    """清空进程内缓存（单例、冷却窗口、一次性标志）。仅测试与手工重置使用。"""
    global _engine, _engine_error, _disabled_notified
    with _engine_lock:
        _engine = None
        _engine_error = None
    _cooldown_until.clear()
    _disabled_notified = False


# --------------------------------------------------------------------------- #
# 冷却（按实例落盘路径分桶）
# --------------------------------------------------------------------------- #

_cooldown_until: dict[str, float] = {}


def _cooldown_key(settings: MemorySettings) -> str:
    return str(settings.env_state_dir())


def _cooldown_active(settings: MemorySettings) -> bool:
    return time.monotonic() < _cooldown_until.get(_cooldown_key(settings), 0.0)


def _note_failure(settings: MemorySettings) -> None:
    _cooldown_until[_cooldown_key(settings)] = time.monotonic() + COOLDOWN_SEC


# --------------------------------------------------------------------------- #
# 客户端：对外接口（recall / store / clear 调用方不改）
# --------------------------------------------------------------------------- #


class MemoryClient:
    """进程内 mem0 引擎的薄包装。构造绝不触发引擎加载（懒加载，冷启动不卡 UI）。

    `engine=` 供测试注入假引擎；生产路径留空，首次调用时经 `get_engine()` 懒加载。
    """

    def __init__(
        self,
        settings: MemorySettings,
        *,
        engine: Any | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.settings = settings
        self._engine = engine

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        self._engine = get_engine()
        return self._engine

    # -- 写操作 -------------------------------------------------------------- #

    def add(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """add 载荷（module_memory.store_params 产出）→ SDK `Memory.add(infer=True)`。"""
        engine = self._get_engine()
        started = time.monotonic()
        try:
            result = engine.add(
                payload["messages"],
                user_id=payload["user_id"],
                agent_id=payload.get("agent_id"),
                run_id=payload.get("run_id"),
                metadata=payload.get("metadata") or {},
                infer=True,
            )
        except MemoryUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK 侧任何异常统一显式抛出
            raise MemoryUnavailable(
                f"add 失败：{exc.__class__.__name__}: {exc}"
                f"（耗时 {time.monotonic() - started:.2f}s）"
            ) from None
        return result if isinstance(result, dict) else None

    def clear(self, *, user_id: str, agent_id: str) -> int:
        """清空本命名空间全部记忆。SDK 不回条数：先 get_all 计数，再 delete_all。

        拿不到计数返回 -1；调用方以「再检索为空」作为清空生效的判据。
        """
        engine = self._get_engine()
        started = time.monotonic()
        count = -1
        try:
            existing = engine.get_all(
                filters={"user_id": user_id, "agent_id": agent_id}, top_k=10000
            )
            items = existing.get("results", []) if isinstance(existing, dict) else []
            count = len(items)
            engine.delete_all(user_id=user_id, agent_id=agent_id)
        except MemoryUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MemoryUnavailable(
                f"clear 失败：{exc.__class__.__name__}: {exc}"
                f"（耗时 {time.monotonic() - started:.2f}s）"
            ) from None
        logger.info("memory_cleared user=%s agent=%s count=%d", user_id, agent_id, count)
        return count if count else -1

    # -- 读操作 -------------------------------------------------------------- #

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """search 载荷（module_memory.recall_params / 独立调用 dict）→ SDK `Memory.search`。"""
        engine = self._get_engine()
        kb_scope = payload.get(module_memory.KB_SCOPE_KEY)
        filters: dict[str, Any] = {
            "user_id": payload["user_id"],
            "agent_id": payload.get("agent_id"),
        }
        if kb_scope:
            filters[module_memory.KB_SCOPE_KEY] = kb_scope
        started = time.monotonic()
        try:
            result = engine.search(
                payload["query"],
                top_k=int(payload.get("top_k") or DEFAULT_TOP_K),
                filters=filters,
            )
        except MemoryUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MemoryUnavailable(
                f"search 失败：{exc.__class__.__name__}: {exc}"
                f"（耗时 {time.monotonic() - started:.2f}s）"
            ) from None
        return _extract_results(result)


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    """mem0 SDK 返回在不同版本间有 `results` / `items` / 裸数组三种形态，统一抽列表。"""
    items: Any = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("results", "items", "memories"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


#: 已报过一次「记忆通道未启用」，避免每个请求刷同一条日志
_disabled_notified = False


def _note_disabled_once() -> None:
    """「记忆通道未启用」整个进程只提示一次，避免每个请求刷同一条日志。"""
    global _disabled_notified
    if not _disabled_notified:
        _disabled_notified = True
        logger.info(
            "memory_disabled 未启用（LDST_MEMORY_ENABLED=falsey 或 LDST_MEMORY_BASE_URL=''）"
        )


_client: MemoryClient | None = None
_client_lock = threading.Lock()


def shared_client(settings: MemorySettings) -> MemoryClient:
    """按 settings 取共享客户端单例（引擎在首次调用时懒加载，构造不触模型）。"""
    global _client
    if _client is not None and _client.settings == settings:
        return _client
    with _client_lock:
        if _client is None or _client.settings != settings:
            _client = MemoryClient(settings)
    return _client


# --------------------------------------------------------------------------- #
# 写入门槛：红线（密钥 / 未验证数字）与重要性打分
# --------------------------------------------------------------------------- #

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}\b")),
    (
        "credential_pair",
        re.compile(r"(?i)\b(pass(word|wd)?|secret|token|api[_-]?key)\b\s*[=:]\s*\S{6,}"),
    ),
    (
        "connection_string_secret",
        re.compile(r"(?i)\b(postgres(ql)?|mysql|mariadb|mongodb(\+srv)?|redis|amqp)://\S+:\S+@"),
    ),
    ("tokenish_blob", re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b")),
    ("hex_blob", re.compile(r"\b(?:[0-9a-fA-F]{2}){20,}\b")),
)

_NUMBER_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent_claim", re.compile(r"\d+(?:\.\d+)?\s*%")),
    ("price_claim", re.compile(r"\d+(?:\.\d+)?\s*(?:元|块钱|美元|美金)")),
    ("cap_claim", re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万)")),
    (
        "ratio_claim",
        re.compile(r"(?i)(?:市盈率|市净率|\bpe\b|\bpb\b|\broe\b)\s*[约是:=在]?\s*\d+(?:\.\d+)?"),
    ),
)

_REMEMBER_RE = re.compile(r"(记住|记一下|记下|别忘|别忘了|帮我记|标注一下)")
_PREFERENCE_RE = re.compile(
    r"(我喜欢|我不喜欢|我偏好|我倾向|我习惯|我只看|我不看|别给我|不要给我|"
    r"我从不|我一般不跟|我不跟|我不碰|别再给我|少推|只推|用不惯|用惯)"
)
_FOLLOW_RE = re.compile(
    r"(长期关注|长期跟踪|一直关注|重点跟踪|重点盯|一直盯|自选里|自选中|持仓里|"
    r"重仓|纳入观察|移出观察|不再跟踪|不再关注|拉黑)"
)


def secret_rule(text: str) -> str | None:
    """命中疑似密钥的规则名（写入前直接拒绝）；正常文本返回 None。"""
    for name, pattern in _SECRET_RULES:
        if pattern.search(text):
            return name
    return None


def unverified_number_rule(text: str) -> str | None:
    """命中未经验证数字（估值 / 涨跌幅）的规则名——必须回查数据源，不写入。"""
    for name, pattern in _NUMBER_RULES:
        if pattern.search(text):
            return name
    return None


def importance_score(text: str) -> tuple[float, str]:
    """用户消息的重要性打分，返回 `(score, reason)`。

    - 显式要求记住（「记住」「别忘」…）→ 1.0，必写（不受 WRITE_THRESHOLD 限制）；
    - 明确偏好（喜欢 / 只看 / 不跟…）→ 0.6；叠加长期关注语义 → 0.9；
    - 纯提问 / 闲聊 / 无信号短句 → 0.1。

    重复出现 ≥2 次的规则不在这里数：mem0 写入侧对重复事实做去重更新
    （add 语义），本地再维护计数只会双份状态。
    """
    if _REMEMBER_RE.search(text):
        return 1.0, "explicit_remember"
    score = 0.1
    reasons: list[str] = []
    if _PREFERENCE_RE.search(text):
        score += 0.6
        reasons.append("preference")
    if _FOLLOW_RE.search(text):
        score += 0.3
        reasons.append("follow")
    if not reasons:
        return 0.1, "chitchat"
    return min(score, 1.0), "+".join(reasons)


def _digest(text: str) -> str:
    """可定位但不可还原的指纹：进日志的是短哈希，不是原文（原文可能已被脱敏）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 召回（read）与写入（write）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemoryHit:
    """一条召回记忆。`score` 是 mem0 的相似度（阈值过滤与加权后的值对上注入）。"""

    text: str
    score: float
    metadata: dict[str, Any]


def _boost(metadata: dict[str, Any], symbol: str | None) -> float:
    """按 symbol 加分：命中当前标的的记忆加权；两边都空的通用记忆微加成。"""
    memory_symbol = str(metadata.get("symbol") or "")
    if symbol and memory_symbol:
        return SYMBOL_BOOST if memory_symbol == symbol else 0.0
    if not symbol and not memory_symbol:
        return SYMBOL_BOOST / 2
    return 0.0


def _note_disabled_once() -> None:
    """「记忆通道未启用」整个进程只提示一次，避免每个请求刷同一条日志。"""
    global _disabled_notified  # noqa: PLW0602 - 进程内一次性标志
    if not _disabled_notified:
        _disabled_notified = True
        logger.info(
            "memory_disabled 未启用（LDST_MEMORY_ENABLED=falsey 或 LDST_MEMORY_BASE_URL=''）"
        )


def recall(
    question: str,
    *,
    symbol: str | None,
    settings: MemorySettings,
    client: MemoryClient,
    scope: MemoryScope | None = None,
) -> list[MemoryHit]:
    """召回记忆：引擎 search → 本地阈值过滤 → symbol 加权 → 取 top-k。

    失败抛 `MemoryUnavailable`（调用方降级）。未启用 / 冷却期内直接返回 []。
    给了 `scope`（QUOT-498 模块命名空间）时载荷经 `module_memory.recall_params`
    校验，`agent_id` + `kb_scope` 两维同时发出；未给时落独立调用的固定身份。
    """
    if not settings.enabled:
        _note_disabled_once()
        return []
    if _cooldown_active(settings):
        return []
    started = time.monotonic()
    # 预取 2 倍余量：本地阈值过滤与加权后再截 top_k，不为了凑数塞低分记忆
    if scope is not None:
        payload = module_memory.recall_params(
            scope, question, user_id=settings.user_id, top_k=settings.top_k * 2
        )
    else:
        payload = {
            "query": question,
            "user_id": settings.user_id,
            "agent_id": AGENT_ID,
            "top_k": settings.top_k * 2,
        }
    raw = client.search(payload)
    threshold = settings.score_threshold
    hits: list[MemoryHit] = []
    for item in raw:
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        text = str(item.get("memory") or item.get("text") or "").strip()
        if not text or score < threshold:
            continue
        raw_metadata = item.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        # 命名空间二次校验：引擎侧若没按 user/agent 过滤，这里兜底挡住跨空间记忆
        owner = str(metadata.get("user_id") or "")
        if owner and owner != settings.user_id:
            continue
        if scope is not None:
            hit_agent = str(item.get("agent_id") or metadata.get("agent_id") or "")
            hit_kb = str(metadata.get(module_memory.KB_SCOPE_KEY) or "")
            if hit_agent and hit_agent != scope.agent_id:
                continue
            if hit_kb and hit_kb != scope.kb_scope:
                continue
        boosted = min(score + _boost(metadata, symbol), 1.0)
        hits.append(MemoryHit(text=text, score=boosted, metadata=metadata))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    logger.info(
        "memory_search ok hits=%d kept=%d top_k=%d threshold=%.2f symbol=%s elapsed=%.2fs",
        len(raw),
        min(len(hits), settings.top_k),
        settings.top_k,
        threshold,
        symbol or "-",
        time.monotonic() - started,
    )
    return hits[: settings.top_k]


def store(
    question: str,
    *,
    symbol: str | None,
    page: str,
    run_id: str,
    settings: MemorySettings,
    client: MemoryClient,
    scope: MemoryScope | None = None,
) -> None:
    """对话结束后把「值得记的用户消息」写入 mem0。任何失败只记日志。

    管线：密钥红线 → 未验证数字红线 → 重要性门槛 → add(infer=True)。mem0 抽取
    事实后落库，不会把提问原文原样存成记忆（add 的消息是输入，记忆是抽取产物）。
    给了 `scope` 时写入与召回同一对命名空间字段（QUOT-498，kb_scope 进 metadata）。
    """
    if not settings.enabled:
        return
    if _cooldown_active(settings):
        return
    rule = secret_rule(question)
    if rule is not None:
        # 告警只带规则名与指纹：原文可能含密钥，绝不出现在日志
        logger.warning(
            "memory_secret_rejected rule=%s sha=%s page=%s run_id=%s",
            rule,
            _digest(question),
            page,
            run_id,
        )
        return
    number_rule = unverified_number_rule(question)
    if number_rule is not None:
        logger.info(
            "memory_number_rejected rule=%s sha=%s page=%s run_id=%s",
            number_rule,
            _digest(question),
            page,
            run_id,
        )
        return
    score, reason = importance_score(question)
    if score < WRITE_THRESHOLD:
        logger.debug(
            "memory_write_skipped score=%.2f reason=%s sha=%s", score, reason, _digest(question)
        )
        return
    metadata = {"page": page, "symbol": symbol or "", "source": SOURCE}
    if scope is not None:
        payload = module_memory.store_params(
            scope,
            [{"role": "user", "content": question}],
            user_id=settings.user_id,
            run_id=run_id,
            metadata=metadata,
        )
    else:
        payload = {
            "messages": [{"role": "user", "content": question}],
            "user_id": settings.user_id,
            "agent_id": AGENT_ID,
            "run_id": run_id,
            "metadata": metadata,
        }
    started = time.monotonic()
    try:
        client.add(payload)
    except MemoryUnavailable as exc:
        _note_failure(settings)
        logger.warning("memory_write_failed run_id=%s error=%s", run_id, exc)
        return
    except Exception as exc:  # noqa: BLE001 - 写入是附加能力，兜底也绝不阻断回复
        _note_failure(settings)
        logger.warning(
            "memory_write_failed run_id=%s error=%s (%s)",
            run_id,
            exc.__class__.__name__,
            exc,
        )
        return
    logger.info(
        "memory_written run_id=%s page=%s symbol=%s reason=%s elapsed=%.2fs",
        run_id,
        page,
        symbol or "-",
        reason,
        time.monotonic() - started,
    )


def dispatch_store(
    question: str,
    *,
    symbol: str | None,
    page: str,
    run_id: str,
    settings: MemorySettings,
    client: MemoryClient,
    sync: bool = False,
    scope: MemoryScope | None = None,
) -> None:
    """异步落库（守护线程，失败只记日志）；`sync=True` 供测试与 CLI 同步等待。"""
    if sync:
        store(
            question,
            symbol=symbol,
            page=page,
            run_id=run_id,
            settings=settings,
            client=client,
            scope=scope,
        )
        return
    if not settings.enabled:
        return
    threading.Thread(
        target=lambda: store(
            question,
            symbol=symbol,
            page=page,
            run_id=run_id,
            settings=settings,
            client=client,
            scope=scope,
        ),
        name=f"memory-write-{run_id}",
        daemon=True,
    ).start()


# --------------------------------------------------------------------------- #
# 注入分段（对话记忆与标的档案物理分段，冲突以档案为准）
# --------------------------------------------------------------------------- #

KNOWLEDGE_BEGIN = "<<<KNOWLEDGE_BEGIN 不可信数据>>>"
KNOWLEDGE_END = "<<<KNOWLEDGE_END>>>"

MEMORY_SECTION_HEADER = "【对话记忆（可能过时，仅作线索）】"
KNOWLEDGE_SECTION_HEADER = "【标的档案（带 as_of，优先于对话记忆）】"
EMPTY_KNOWLEDGE_NOTE = (
    "（该标的没有可用档案；档案缺失时以页级数据为准，不要引用记忆补全。）"
)

MEMORY_CONFLICT_RULE = (
    "若【对话记忆】与【标的档案】或【页级上下文】冲突，以档案与页级数据为准，"
    "并说明「记忆里是旧说法」，不许静默采用记忆。"
)


def render_memory_block(hits: list[MemoryHit], *, knowledge: str | None = None) -> str:
    """把召回记忆渲染成注入段；与知识库（QUOT-488）物理分段、各自标注来源。

    无记忆且无档案正文时返回空串（提示词保持干净）。有记忆时档案段的
    `<<<KNOWLEDGE_BEGIN 不可信数据>>>` / `<<<KNOWLEDGE_END>>>` 边界恒定出现。

    `knowledge` 传 `recall_wrapped()` 的结果：它自带 as_of 头、声明与边界，
    原样放在自己的 `【标的档案】` 段里**不二次包裹、不截断**（12000 字上限由
    `recall.py` 判）；800 字预算只约束记忆段（QUOT-566）。
    """
    knowledge_text = (knowledge or "").strip()
    if not hits and not knowledge_text:
        return ""
    parts: list[str] = []
    if hits:
        lines = [MEMORY_SECTION_HEADER]
        for hit in hits:
            text = hit.text.strip()
            if len(text) > MEMORY_TEXT_MAX:
                text = text[: MEMORY_TEXT_MAX - 1] + "…"
            lines.append(f"- (score {hit.score:.2f}) {text}")
        memory_block = "\n".join(lines)
        if len(memory_block) > MEMORY_BLOCK_MAX:
            memory_block = memory_block[: MEMORY_BLOCK_MAX - 1] + "…"
        parts.append(memory_block)
    if knowledge_text:
        parts.append(f"{KNOWLEDGE_SECTION_HEADER}\n{knowledge_text}")
    else:
        parts.append(
            f"{KNOWLEDGE_SECTION_HEADER}\n{KNOWLEDGE_BEGIN}\n{EMPTY_KNOWLEDGE_NOTE}\n{KNOWLEDGE_END}"
        )
    return "\n\n".join(parts)


__all__ = [
    "AGENT_ID",
    "KNOWLEDGE_BEGIN",
    "KNOWLEDGE_END",
    "MEMORY_TEXT_MAX",
    "MemoryClient",
    "MemoryHit",
    "MemorySettings",
    "MemoryUnavailable",
    "SOURCE",
    "dispatch_store",
    "engine_health",
    "importance_score",
    "load_settings",
    "recall",
    "render_memory_block",
    "reset_runtime_state",
    "secret_rule",
    "shared_client",
    "store",
    "unverified_number_rule",
]
