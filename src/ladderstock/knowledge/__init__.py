"""标的档案知识库（QUOT-488）：档案层 + 状态判定 + 召回边界。

分层：

- `model`：frontmatter / 状态判定（fresh | stale | refuted）/ valid_days 归一；
- `gate`：写入前的合规与隐私 gate（疑似密钥、隐私字段命中即拒）；
- `store`：路径安全（safePath 口径）、manifest 索引、归档、冲突裁决；
- `recall`：latest.md 召回、12000 字按章节截断、`KNOWLEDGE_BEGIN` 边界。

对外接口：`GET /api/knowledge/:market/:symbol`（`web/knowledge.py`）。
向量存取层（Qdrant）独立部署，本包不依赖。
"""

from ladderstock.knowledge.gate import GateViolation, gate_content, gate_payload
from ladderstock.knowledge.model import (
    DEFAULT_VALID_DAYS,
    MARKETS,
    STATUSES,
    Dossier,
    KnowledgeError,
    normalize_market,
    normalize_symbol,
    normalize_valid_days,
    sh_today,
)
from ladderstock.knowledge.recall import (
    KNOWLEDGE_BEGIN,
    KNOWLEDGE_DISCLAIMER,
    KNOWLEDGE_END,
    RecallResult,
    recall,
    recall_wrapped,
    truncate_by_section,
    wrap_for_prompt,
)
from ladderstock.knowledge.store import (
    ArchiveOutcome,
    PathEscape,
    archive_run,
    dossier_dir,
    knowledge_root,
    latest_path,
    load_manifest,
    load_profile,
    profile_path,
    record_refutation,
    should_archive,
    write_profile,
)

__all__ = [
    "DEFAULT_VALID_DAYS",
    "ArchiveOutcome",
    "Dossier",
    "GateViolation",
    "KNOWLEDGE_BEGIN",
    "KNOWLEDGE_DISCLAIMER",
    "KNOWLEDGE_END",
    "KnowledgeError",
    "MARKETS",
    "PathEscape",
    "RecallResult",
    "STATUSES",
    "archive_run",
    "dossier_dir",
    "gate_content",
    "gate_payload",
    "knowledge_root",
    "latest_path",
    "load_manifest",
    "load_profile",
    "normalize_market",
    "normalize_symbol",
    "normalize_valid_days",
    "profile_path",
    "recall",
    "recall_wrapped",
    "record_refutation",
    "should_archive",
    "sh_today",
    "truncate_by_section",
    "wrap_for_prompt",
    "write_profile",
]
