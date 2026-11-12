"""助手通道的跨会话记忆（QUOT-487）：mem0 REST 读写、写入门槛与注入分段。

通道是为 **LadderStock 助手**（`web/assistant.py` 的一问一答）服务的：提问前按
命名空间召回记忆，回复后按策略异步写入；mem0 侧的任何失败都只降级不拖垮回答。

红线（改动前先读，写错会污染别的系统）：

1. **只连 `LDST_MEMORY_BASE_URL` 声明的实例**（默认 QUOT-486 的 127.0.0.1:9100）。
   绝不直连 Multica 自己的 mem0（9000），那里是 agent 记忆后端，越界写入即污染。
2. **只在声明的命名空间内读/写**：助手通道跟随 QUOT-498 模块命名空间
   （`agent_id` + `kb_scope` 两维同时到位，载荷经 `module_memory` 校验）；
   独立调用用 `user_id + agent_id = ladderstock-assistant`。不跨用户检索；
   user_id 从配置读（环境变量，缺省落本机持久标识），不写死成 `default`。
3. **只写通过重要性门槛的用户消息**，且不含密钥（正则族直接拒绝）、不含未经验证
   的数字（估值 / 涨跌幅，需回查数据源）、不含逐轮对话原文、不落结构化标的档案
   （那是 QUOT-488 的职责，带 `as_of` 与来源，与本通道的模糊线索不同源）。
4. 召回以「线索」身份注入，与知识库（QUOT-488）物理分段并各自标注来源；
   冲突时以档案为准，回答里须说明「记忆里是旧说法」。

所有 mem0 侧失败（超时 / 断连 / 非 2xx / 非 JSON）统一抛 `MemoryUnavailable`：
召回失败 → 回答不带记忆照常完成；写入失败 → 只记日志。两者都带端点、状态码
与耗时，绝不只写一个 `error`。
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
from typing import Any

import httpx

from ladderstock.ai import module_memory
from ladderstock.ai.fallback import MemoryScope

logger = logging.getLogger("ladderstock.ai.memory")

#: 独立调用 / CLI memory-clear 用的 agent 身份；助手问答通道走 QUOT-498 模块命名空间
AGENT_ID = "ladderstock-assistant"

#: 本条记忆的来源页面标记，写进 metadata.source
SOURCE = "assistant"

DEFAULT_BASE_URL = "http://127.0.0.1:9100"
DEFAULT_TOP_K = 5
DEFAULT_SCORE_THRESHOLD = 0.55
DEFAULT_TIMEOUT_SEC = 5.0

#: 重要性打分低于该值的候选直接丢弃；用户显式说「记住」时 bypass 本门槛（红线仍生效）
WRITE_THRESHOLD = 0.5

#: 召回加权：命中当前 symbol 的记忆加分（top_k 截断前生效）
SYMBOL_BOOST = 0.05

#: mem0 连续失败后的静默冷却：避免每次提问都吃一次超时（最长 5s）
COOLDOWN_SEC = 30.0

#: 注入提示的单条记忆截断与整块上限（防止把 prompt 撑爆）
MEMORY_TEXT_MAX = 120
MEMORY_BLOCK_MAX = 800

ENV_BASE_URL = "LDST_MEMORY_BASE_URL"
ENV_USER_ID = "LDST_MEMORY_USER_ID"
ENV_TOP_K = "LDST_MEMORY_TOP_K"
ENV_SCORE_THRESHOLD = "LDST_MEMORY_SCORE_THRESHOLD"
ENV_TIMEOUT_SEC = "LDST_MEMORY_TIMEOUT_SEC"
ENV_ENABLED = "LDST_MEMORY_ENABLED"

_FALSEY = {"", "0", "false", "no", "off"}

#: QUOT-486 自建 mem0（127.0.0.1:9100）的真实路由：`/search`、`/memories`，
#: 没有托管平台那套 `/v1/memories/` 前缀（真机探测 2026-11-09，见 issue 取证）。
SEARCH_PATH = "/search"
ADD_PATH = "/memories"
DELETE_PATH = "/memories"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemorySettings:
    """记忆通道配置。`enabled=False` 时整个通道跳过（不联网、不注入、不写日志风暴）。"""

    base_url: str
    user_id: str
    top_k: int
    score_threshold: float
    timeout_sec: float
    enabled: bool


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
    """从环境变量读记忆配置；缺失项取文档默认值（`.env.example` 有说明）。"""
    base_url = os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/")
    enabled = base_url != "" and (os.environ.get(ENV_ENABLED, "1").strip().lower() not in _FALSEY)
    return MemorySettings(
        base_url=base_url,
        user_id=os.environ.get(ENV_USER_ID, "").strip() or _persistent_user_id(),
        top_k=max(1, _int_env(ENV_TOP_K, DEFAULT_TOP_K)),
        score_threshold=_float_env(ENV_SCORE_THRESHOLD, DEFAULT_SCORE_THRESHOLD),
        timeout_sec=max(0.1, _float_env(ENV_TIMEOUT_SEC, DEFAULT_TIMEOUT_SEC)),
        enabled=enabled,
    )


# --------------------------------------------------------------------------- #
# mem0 REST 客户端
# --------------------------------------------------------------------------- #


class MemoryUnavailable(RuntimeError):
    """mem0 侧任何失败：不可达、超时、非 2xx、非 JSON。调用方一律降级。"""


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    """mem0 响应在不同版本间有 `results` / `items` / 裸数组三种形态，统一抽列表。"""
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


#: 连续失败后的静默冷却窗口（按 base_url 分桶），进程内生效
_cooldown_until: dict[str, float] = {}


def _cooldown_active(base_url: str) -> bool:
    return time.monotonic() < _cooldown_until.get(base_url, 0.0)


def _note_failure(base_url: str) -> None:
    _cooldown_until[base_url] = time.monotonic() + COOLDOWN_SEC


def reset_runtime_state() -> None:
    """清空进程内缓存（冷却窗口、客户端池）。仅测试与手工重置使用。"""
    global _disabled_notified
    _cooldown_until.clear()
    _clients.clear()
    _disabled_notified = False


class MemoryClient:
    """mem0 REST 客户端（httpx 直连，不引 SDK：契约是纯 HTTP，依赖越少越好维护）。"""

    def __init__(
        self,
        base_url: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_sec, transport=transport)

    def __enter__(self) -> MemoryClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    # -- 写操作 -------------------------------------------------------------- #

    def add(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(ADD_PATH, payload)

    def clear(self, *, user_id: str, agent_id: str) -> int:
        """清空本命名空间全部记忆（`DELETE /memories?user_id&agent_id`）。

        自建实例只回 `{"message": "All relevant memories deleted"}`，不带条数，
        拿不到就返回 -1；调用方以「再检索为空」作为清空生效的判据。
        """
        started = time.monotonic()
        try:
            response = self._http.delete(
                DELETE_PATH, params={"user_id": user_id, "agent_id": agent_id}
            )
        except (httpx.HTTPError, OSError) as exc:
            _note_failure(self.base_url)
            raise MemoryUnavailable(f"clear 请求失败：{exc!r}（{self.base_url}）") from None
        if response.status_code >= 400:
            _note_failure(self.base_url)
            raise MemoryUnavailable(
                f"clear 被 mem0 拒绝：HTTP {response.status_code}"
                f"（{self.base_url}，耗时 {time.monotonic() - started:.2f}s）"
            )
        try:
            payload = response.json()
        except ValueError:
            return -1
        for key in ("deleted_ids", "deleted", "results"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(value, list):
                return len(value)
        return -1

    # -- 读操作 -------------------------------------------------------------- #

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._post(SEARCH_PATH, payload)
        if data is None:
            return []
        return _extract_results(data)

    # -- 公共 ---------------------------------------------------------------- #

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        started = time.monotonic()
        try:
            response = self._http.post(path, json=payload)
        except (httpx.HTTPError, OSError) as exc:
            _note_failure(self.base_url)
            raise MemoryUnavailable(
                f"{path} 请求失败：{exc.__class__.__name__}"
                f"（端点 {self.base_url}，耗时 {time.monotonic() - started:.2f}s）"
            ) from None
        elapsed = time.monotonic() - started
        if response.status_code >= 400:
            _note_failure(self.base_url)
            raise MemoryUnavailable(
                f"{path} 返回 HTTP {response.status_code}"
                f"（端点 {self.base_url}，耗时 {elapsed:.2f}s）"
            )
        try:
            payload_json = response.json()
        except ValueError as exc:
            _note_failure(self.base_url)
            raise MemoryUnavailable(
                f"{path} 响应不是 JSON：{exc}（端点 {self.base_url}，耗时 {elapsed:.2f}s）"
            ) from None
        return payload_json if isinstance(payload_json, dict) else None


#: 进程内 client 池（按 base_url + timeout 复用连接池；失败后由调用方自行关闭）
_clients: dict[str, MemoryClient] = {}

#: 已报过一次「记忆通道未启用」，避免每个请求刷同一条日志
_disabled_notified = False


def shared_client(settings: MemorySettings) -> MemoryClient | None:
    """按 settings 取进程内共享客户端；未启用时返回 None。"""
    if not settings.enabled:
        return None
    key = f"{settings.base_url}|{settings.timeout_sec}"
    client = _clients.get(key)
    if client is None:
        client = MemoryClient(settings.base_url, settings.timeout_sec)
        _clients[key] = client
    return client


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
    """「未配置 mem0」整个进程只提示一次，避免每个请求刷同一条日志。"""
    global _disabled_notified  # noqa: PLW0602 - 进程内一次性标志
    if not _disabled_notified:
        _disabled_notified = True
        logger.info("memory_disabled 未配置 mem0 实例（LDST_MEMORY_BASE_URL 为空），请求不带记忆")


def recall(
    question: str,
    *,
    symbol: str | None,
    settings: MemorySettings,
    client: MemoryClient,
    scope: MemoryScope | None = None,
) -> list[MemoryHit]:
    """召回记忆：mem0 search → 本地阈值过滤 → symbol 加权 → 取 top-k。

    失败抛 `MemoryUnavailable`（调用方降级）。未启用 / 冷却期内直接返回 []。
    给了 `scope`（QUOT-498 模块命名空间）时载荷经 `module_memory.recall_params`
    校验，`agent_id` + `kb_scope` 两维同时发出；未给时落独立调用的固定身份。
    """
    if not settings.enabled:
        _note_disabled_once()
        return []
    if _cooldown_active(settings.base_url):
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
        # 命名空间二次校验：服务端若没按 agent_id 过滤，这里兜底挡住跨空间记忆
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
    """对话结束后把「值得记的用户消息」异步写入 mem0。任何失败只记日志。

    管线：密钥红线 → 未验证数字红线 → 重要性门槛 → add。mem0 抽取事实后落库，
    不会把提问原文原样存成记忆（add 的消息是输入，记忆是抽取产物）。
    给了 `scope` 时写入与召回同一对命名空间字段（QUOT-498，kb_scope 进 metadata）。
    """
    if not settings.enabled:
        return
    if _cooldown_active(settings.base_url):
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
        logger.warning("memory_write_failed run_id=%s error=%s", run_id, exc)
        return
    except Exception as exc:  # noqa: BLE001 - 写入是附加能力，兜底也绝不阻断回复
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
