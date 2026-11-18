"""知识库向量层：档案切块写入 Qdrant + 按问题语义检索（QUOT-488 接线）。

三条硬口径（与 `recall.py` 的文件通道并存，不是替代）：

1. **向量层是加速器，不是依赖**：模型缺失、Qdrant 不通、维度不匹配，一律抛
   `VectorsUnavailable`，调用方退回「按 market:symbol 精确读文件」那条路。
   向量库挂了不能让人答不出话。
2. **重跑覆盖，不堆垃圾**：point id = uuid5(命名空间, "market:symbol:as_of:section:序")，
   同一份档案重跑写入同一批 id；换 `as_of` 前先按 `market+symbol` 删旧点。
3. **切块按章节**：`## ` 切节，单块超过上限再按段落二次切；块内保留 `as_of/status`，
   检索结果能判「这份结论还成不成立」。

编码模型：fastembed `BAAI/bge-small-zh-v1.5`（512 维，与部署侧 `fast-bge-small-zh-v1.5`
同一空间，可直接用既有 collection）。模型文件落 `LDST_FASTEMBED_CACHE`（安装包内
`file/models`），首次可用 `LDST_VECTORS_ALLOW_DOWNLOAD=1` 拉一次，之后离线。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:6333"
DEFAULT_COLLECTION = "ladderstock_knowledge"
DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_TIMEOUT = 2.0
# 固定命名空间：同一份内容在任何一台机器上都得到同一个 point id（重跑幂等）
POINT_NAMESPACE = uuid.UUID("2f7d3a1c-7b64-4a1e-9d38-1a2b3c4d5e6f")

MIN_CHUNK_CHARS = 40
MAX_CHUNK_CHARS = 1200
DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = 0.35

_MODEL: Any = None
_MODEL_FAILED: str | None = None
#: 模型加载互斥锁。工具会被并发调用（同一轮里「公告检索」和「通用取数」并行），
#: 两个线程同时进 TextEmbedding 会互相踩 ONNX session，出一个成功一个失败，
#: 失败的还会把 `_MODEL_FAILED` 永久毒化。加锁 + 双检，只加载一次。
_MODEL_LOCK = threading.Lock()


class VectorsUnavailable(RuntimeError):
    """向量层任何环节不可用：调用方一律退回文件通道。"""


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def _env(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw or default


def qdrant_url() -> str:
    """qdrant REST 地址：显式环境变量优先，否则按实例端口偏移推导。

    正式服 6333、测试服 16333（+10000），与 `services._offset()` 同一口径：
    测试写入不能落进正式集合，两边必须是两个独立实例、独立卷。
    """

    explicit = (os.environ.get("LDST_QDRANT_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    offset = 0
    try:
        from ladderstock.core.boundary import port_offset

        offset = int(port_offset())
    except Exception:  # pragma: no cover - 取不到偏移按正式服算
        offset = 0
    return f"http://127.0.0.1:{6333 + offset}"


def collection_name() -> str:
    return _env("LDST_QDRANT_COLLECTION", DEFAULT_COLLECTION)


def model_name() -> str:
    return _env("LDST_FASTEMBED_MODEL", DEFAULT_MODEL)


def _has_model(directory: Path) -> bool:
    """目录里是否真有 fastembed 能直接加载的模型。"""

    try:
        return any(directory.glob("fast-*")) or any(directory.glob("models--*"))
    except OSError:
        return False


def cache_dir() -> Path:
    """模型缓存根：优先带模型的那一份，最后才回安装根的空目录。

    开发态模型常在仓库的 `file/models/fastembed`，安装根下那个目录往往是空的
    （安装器只建目录不搬 94MB 模型），直接按安装根返回会让 fastembed 去连
    HuggingFace 然后超时。这里按「目录里真有 fast-* 模型」挑一份，都没有才
    返回安装根，让调用方看到明确路径去补模型。
    """

    explicit = _env("LDST_FASTEMBED_CACHE", "")
    if explicit:
        return Path(explicit)
    inherited = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    # 记忆通道也会 setdefault 抢这个变量（指向 `<repo>/models`），那份目录里
    # 没有 fastembed 能直接加载的模型。只有目录真带着模型才认，否则按候选链挑。
    if inherited and _has_model(Path(inherited)):
        return Path(inherited)

    from ladderstock.cli.paths import data_dir

    candidates: list[Path] = [data_dir().parent / "file" / "models" / "fastembed"]
    bundle = (os.environ.get("LDST_MODELS_DIR") or "").strip()
    if bundle:
        candidates.append(Path(bundle))
    try:
        repo_root = Path(__file__).resolve().parents[3]
        candidates.append(repo_root / "file" / "models" / "fastembed")
    except (IndexError, OSError):  # pragma: no cover - 打包后 __file__ 不可靠
        pass
    for candidate in candidates:
        try:
            if candidate.is_dir() and any(candidate.glob("fast-*")):
                return candidate
        except OSError:
            continue
    return candidates[0]


def _timeout() -> float:
    try:
        return max(0.3, float(_env("LDST_QDRANT_TIMEOUT", str(DEFAULT_TIMEOUT))))
    except ValueError:
        return DEFAULT_TIMEOUT


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #


def _embedder() -> Any:
    """懒加载 fastembed 单例；任何失败记一条日志并抛 VectorsUnavailable。

    外面套互斥锁：agent 一轮里会并行调多个工具，两个线程同时首次加载同一个
    ONNX session 会互相打断并留下一条假失败。锁内双检，保证只加载一次。
    """
    global _MODEL, _MODEL_FAILED
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if _MODEL_FAILED is not None:
            raise VectorsUnavailable(_MODEL_FAILED)
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - 安装包缺依赖时
            _MODEL_FAILED = f"fastembed 未安装：{exc}"
            raise VectorsUnavailable(_MODEL_FAILED) from exc

        cache = cache_dir()
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        allow_download = _env("LDST_VECTORS_ALLOW_DOWNLOAD", "0") == "1"
        # 这里必须覆盖而不是 setdefault：记忆通道先跑，会把 FASTEMBED_CACHE_PATH
        # 指到 `<repo>/models`（那边没有可加载的模型），setdefault 留下旧值就会
        # 让 fastembed 报 "Could not find model in cache_dir"。
        os.environ["FASTEMBED_CACHE_PATH"] = str(cache)
        # 缓存里已有模型就直接用：默认不联网。开着 allow_download 也没用——fastembed
        # 会先连 HuggingFace 试一次，内网/无外网时白等十来秒超时再回退本地，agent 的
        # 工具调用会因此判超时（踩过）。真有缺模型且允许下载，才放开 local_files_only。
        has_local = _has_model(cache)
        local_only = not (allow_download and not has_local)
        try:
            import warnings

            with warnings.catch_warnings():
                # fastembed 对 GCS 旧布局发一条 FutureWarning，模型本身能用；
                # 留在 stderr 会污染 CLI 输出（PowerShell 直接判成失败）。
                warnings.simplefilter("ignore", FutureWarning)
                _MODEL = TextEmbedding(
                    model_name=model_name(),
                    cache_dir=str(cache),
                    local_files_only=local_only,
                )
        except Exception as exc:  # noqa: BLE001 - 模型加载是外部依赖，一律降级
            _MODEL_FAILED = f"嵌入模型不可用（{model_name()}，缓存 {cache}）：{exc}"
            logger.warning("knowledge_vectors_model_unavailable detail=%s", exc)
            raise VectorsUnavailable(_MODEL_FAILED) from exc
        return _MODEL


def embed(texts: list[str]) -> list[list[float]]:
    """批量编码；空列表返回空。"""
    if not texts:
        return []
    model = _embedder()
    try:
        return [[float(x) for x in vector] for vector in model.embed(texts, batch_size=8)]
    except Exception as exc:  # noqa: BLE001
        raise VectorsUnavailable(f"编码失败：{exc}") from exc


def embed_one(text: str) -> list[float]:
    vectors = embed([text])
    if not vectors:
        raise VectorsUnavailable("编码返回空向量")
    return vectors[0]


# --------------------------------------------------------------------------- #
# HTTP（只用 httpx，不引 qdrant-client：一个 REST 面就够，少一层版本耦合）
# --------------------------------------------------------------------------- #


def _http() -> Any:
    import httpx

    return httpx.Client(timeout=_timeout())


def _request(method: str, path: str, *, json_body: Any = None) -> Any:
    url = f"{qdrant_url()}{path}"
    try:
        import httpx

        with httpx.Client(timeout=_timeout()) as client:
            response = client.request(method, url, json=json_body)
    except Exception as exc:  # noqa: BLE001 - 连接失败一律当不可用
        raise VectorsUnavailable(f"Qdrant 不可达（{url}）：{exc}") from exc
    if response.status_code >= 400:
        raise VectorsUnavailable(
            f"Qdrant {method} {path} 返回 {response.status_code}：{response.text[:200]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise VectorsUnavailable(f"Qdrant 返回非 JSON：{response.text[:120]}") from exc


def _collection_state() -> dict[str, Any] | None:
    """读集合配置：不存在返回 None。"""
    try:
        import httpx

        url = f"{qdrant_url()}/collections/{collection_name()}"
        with httpx.Client(timeout=_timeout()) as client:
            response = client.get(url)
    except Exception as exc:  # noqa: BLE001
        raise VectorsUnavailable(f"Qdrant 不可达：{exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise VectorsUnavailable(f"读集合失败 HTTP {response.status_code}")
    return (response.json() or {}).get("result") or {}


def _vector_spec(state: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """返回 (具名向量名, 维度)；匿名单向量时名字为 None。"""
    if not state:
        return None, None
    params = (state.get("config") or {}).get("params") or {}
    vectors = params.get("vectors")
    if isinstance(vectors, dict):
        if "size" in vectors:
            return None, int(vectors["size"])
        for name, spec in vectors.items():
            if isinstance(spec, dict) and "size" in spec:
                return str(name), int(spec["size"])
        return None, None
    return None, None


def ensure_collection(dim: int) -> str | None:
    """集合不存在就建；存在但维度不符直接报错（绝不能混写）。返回具名向量名。"""
    state = _collection_state()
    named, size = _vector_spec(state)
    if state is not None:
        if size is not None and size != dim:
            raise VectorsUnavailable(
                f"集合 {collection_name()} 维度 {size} 与模型 {model_name()} 的 {dim} 维不一致；"
                "请换 LDST_QDRANT_COLLECTION 或重建集合"
            )
        return named
    _request(
        "PUT",
        f"/collections/{collection_name()}",
        json_body={"vectors": {"size": dim, "distance": "Cosine"}},
    )
    logger.info("knowledge_vectors_collection_created name=%s dim=%s", collection_name(), dim)
    return None


# --------------------------------------------------------------------------- #
# 切块
# --------------------------------------------------------------------------- #


def chunk_sections(text: str) -> list[tuple[str, str]]:
    """按 `## ` 切节，单节过长再按空行切段。返回 [(小节标题, 块文本)]。"""
    body = text.strip()
    if not body:
        return []
    parts: list[tuple[str, str]] = []
    current_title = "正文"
    current_lines: list[str] = []

    def flush() -> None:
        if current_lines:
            chunk = "\n".join(current_lines).strip()
            if chunk:
                parts.extend(_split_long(current_title, chunk))
            current_lines.clear()

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip() or "正文"
            current_lines.append(line)
        else:
            current_lines.append(line)
    flush()
    return [(title, chunk) for title, chunk in parts if len(chunk) >= MIN_CHUNK_CHARS]


def _split_long(title: str, chunk: str) -> list[tuple[str, str]]:
    if len(chunk) <= MAX_CHUNK_CHARS:
        return [(title, chunk)]
    out: list[tuple[str, str]] = []
    buffer: list[str] = []
    size = 0
    for para in chunk.split("\n\n"):
        if size + len(para) > MAX_CHUNK_CHARS and buffer:
            out.append((title, "\n\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(para)
        size += len(para) + 2
    if buffer:
        out.append((title, "\n\n".join(buffer)))
    return out


def point_id(market: str, symbol: str, as_of: str, section: str, index: int) -> str:
    key = f"{market}:{symbol}:{as_of}:{section}:{index}"
    return str(uuid.uuid5(POINT_NAMESPACE, hashlib.sha256(key.encode("utf-8")).hexdigest()))


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #


@dataclass
class SyncResult:
    market: str
    symbol: str
    as_of: str
    chunks: int
    deleted: int


def _dossier_text(data_root: Any, market: str, symbol: str) -> tuple[str, str, str] | None:
    """取档案正文：优先 profile.md（主档案），退回 latest.md（归档快照）。"""
    from ladderstock.knowledge.model import (
        KnowledgeError,
        dossier_from_text,
        normalize_market,
        normalize_symbol,
        sh_today,
    )
    from ladderstock.knowledge.store import knowledge_root

    market_norm = normalize_market(market)
    symbol_norm = normalize_symbol(symbol)
    root = knowledge_root(data_root)
    for filename in ("profile.md", "latest.md"):
        path = root / f"{market_norm}_{symbol_norm}" / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            dossier = dossier_from_text(text)
        except KnowledgeError:
            continue
        status = dossier.computed_status(sh_today())
        if status == "refuted":
            return None
        return dossier.body, dossier.as_of, status
    return None


def delete_dossier(market: str, symbol: str) -> int:
    """按 payload 过滤删除此前的点（换 as_of 前先清旧，避免同名档案两份并存）。"""
    body = {
        "filter": {
            "must": [
                {"key": "market", "match": {"value": market}},
                {"key": "symbol", "match": {"value": symbol}},
            ]
        }
    }
    result = _request("POST", f"/collections/{collection_name()}/points/delete", json_body=body)
    return int(((result or {}).get("result") or {}).get("deleted") or 0)


def sync_dossier(data_root: Any, market: str, symbol: str, *, purge: bool = True) -> SyncResult | None:
    """把一份档案切块写入 Qdrant。档案缺失 / refuted / 向量层不可用 → None（调用方降级）。"""
    payload = _dossier_text(data_root, market, symbol)
    if payload is None:
        return None
    body, as_of, status = payload
    pieces = chunk_sections(body)
    if not pieces:
        return None
    texts = [chunk for _, chunk in pieces]
    vectors = embed(texts)
    dim = len(vectors[0])
    named = ensure_collection(dim)
    deleted = delete_dossier(market, symbol) if purge else 0

    points = []
    for index, ((section, chunk), vector) in enumerate(zip(pieces, vectors)):
        points.append(
            {
                "id": point_id(market, symbol, as_of, section, index),
                "vector": {named: vector} if named else vector,
                "payload": {
                    "market": market,
                    "symbol": symbol,
                    "as_of": as_of,
                    "status": status,
                    "section": section,
                    "source": "knowledge/profile",
                    "text": chunk,
                },
            }
        )
    _request(
        "PUT",
        f"/collections/{collection_name()}/points",
        json_body={"points": points, "wait": True},
    )
    logger.info(
        "knowledge_vectors_synced market=%s symbol=%s as_of=%s chunks=%s deleted=%s",
        market,
        symbol,
        as_of,
        len(points),
        deleted,
    )
    return SyncResult(market=market, symbol=symbol, as_of=as_of, chunks=len(points), deleted=deleted)


def sync_all(data_root: Any) -> list[SyncResult]:
    """全量重建：按 manifest 索引逐份同步；单份失败不影响其余。"""
    from ladderstock.knowledge.store import load_manifest

    manifest = load_manifest(data_root)
    results: list[SyncResult] = []
    for key in (manifest.get("companies") or {}):
        market, _, symbol = str(key).partition(":")
        if not market or not symbol:
            continue
        try:
            result = sync_dossier(data_root, market, symbol, purge=False)
        except VectorsUnavailable as exc:
            logger.warning("knowledge_vectors_sync_failed key=%s detail=%s", key, exc)
            continue
        if result is not None:
            results.append(result)
    return results


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #


@dataclass
class VectorHit:
    score: float
    market: str
    symbol: str
    as_of: str
    status: str
    section: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "market": self.market,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "status": self.status,
            "section": self.section,
            "text": self.text,
        }


def search(
    query: str,
    *,
    market: str | None = None,
    symbol: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[VectorHit]:
    """语义检索档案片段；`refuted` 不返回。向量层不可用抛 VectorsUnavailable。"""
    text = (query or "").strip()
    if not text:
        return []
    vector = embed_one(text)
    named = ensure_collection(len(vector))

    must: list[dict[str, Any]] = []
    if market:
        must.append({"key": "market", "match": {"value": market}})
    if symbol:
        must.append({"key": "symbol", "match": {"value": symbol}})

    body: dict[str, Any] = {
        "vector": {"name": named, "vector": vector} if named else vector,
        "limit": max(1, top_k),
        "with_payload": True,
        "score_threshold": min_score,
    }
    condition: dict[str, Any] = {"must_not": [{"key": "status", "match": {"value": "refuted"}}]}
    if must:
        condition["must"] = must
    body["filter"] = condition

    result = _request(
        "POST",
        f"/collections/{collection_name()}/points/search",
        json_body=body,
    )
    hits: list[VectorHit] = []
    for item in ((result or {}).get("result") or []):
        payload = item.get("payload") or {}
        hits.append(
            VectorHit(
                score=float(item.get("score") or 0.0),
                market=str(payload.get("market") or ""),
                symbol=str(payload.get("symbol") or ""),
                as_of=str(payload.get("as_of") or ""),
                status=str(payload.get("status") or "fresh"),
                section=str(payload.get("section") or ""),
                text=str(payload.get("text") or ""),
            )
        )
    return hits


def render_block(hits: list[VectorHit], *, budget: int = 4000) -> str | None:
    """把命中片段拼成注入块（带 as_of/status 头 + 不可信数据边界）。"""
    if not hits:
        return None
    from ladderstock.knowledge.recall import KNOWLEDGE_DISCLAIMER, wrap_for_prompt

    lines: list[str] = []
    used = 0
    for hit in hits:
        head = f"· {hit.market}:{hit.symbol} · {hit.section} · as_of={hit.as_of} · status={hit.status}"
        if used + len(head) > budget:
            break
        lines.append(head)
        lines.append(hit.text.strip())
        used += len(head) + len(hit.text)
    if not lines:
        return None
    body = "\n".join(lines)
    header = f"[标的档案语义召回 · {len(hits)} 段 · 模型 {model_name()}]\n{KNOWLEDGE_DISCLAIMER}\n"
    return header + wrap_for_prompt(body)


def available() -> dict[str, Any]:
    """给页面/接口用的健康摘要：集合、点数、模型是否加载。"""
    info: dict[str, Any] = {
        "ok": False,
        "url": qdrant_url(),
        "collection": collection_name(),
        "model": model_name(),
        "cache": str(cache_dir()),
        "points": None,
        "dim": None,
        "error": None,
    }
    try:
        state = _collection_state()
    except VectorsUnavailable as exc:
        info["error"] = str(exc)
        return info
    if state is None:
        info["error"] = f"集合不存在：{collection_name()}"
        return info
    named, size = _vector_spec(state)
    info["points"] = state.get("points_count")
    info["dim"] = size
    info["named_vector"] = named
    try:
        _embedder()
        info["ok"] = True
    except VectorsUnavailable as exc:
        info["error"] = str(exc)
    return info


__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_MODEL",
    "DEFAULT_TOP_K",
    "SyncResult",
    "VectorHit",
    "VectorsUnavailable",
    "available",
    "cache_dir",
    "chunk_sections",
    "collection_name",
    "delete_dossier",
    "embed",
    "embed_one",
    "ensure_collection",
    "model_name",
    "point_id",
    "qdrant_url",
    "render_block",
    "search",
    "sync_all",
    "sync_dossier",
]
