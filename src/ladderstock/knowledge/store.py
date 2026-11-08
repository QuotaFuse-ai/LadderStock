"""标的档案的存储层：路径安全、manifest 索引、归档、冲突裁决。

数据落 `<data_dir>/knowledge/`：

```
manifest.json                    key = market:symbol → {dir, as_of, status}
<market>_<symbol>/profile.md     档案正文（5 节结构，见 research 模板）
<market>_<symbol>/latest.md      最近一次归档的档案快照
<market>_<symbol>/runs/<id>.md   每次 run 的归档留档
```

三条硬口径（照参考实现 orchestrator/README.md 的契约）：

- **safePath 口径**：词法前缀 + 逐级禁符号链接 + realpath 仍在 root 内
  （`resolve()` 在 Windows 上顺带消化 `..`，所以先 resolve 再比对前缀）；
- **归档只在 run 非 failed 且产出 report 时发生**；归档失败只记事件，
  不改 run 状态（`archive_run` 永不抛出给管线）;
- **冲突裁决**：实时数据优先，档案里追加一条反证记录（保留旧结论，
  标 refuted + 依据 + 日期），绝不静默覆盖历史。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.knowledge.gate import GateViolation, gate_payload
from ladderstock.knowledge.model import (
    SCHEMA_VERSION,
    Dossier,
    KnowledgeError,
    dossier_from_text,
    normalize_market,
    normalize_symbol,
    sh_today,
)

MANIFEST_NAME = "manifest.json"

#: 召回注入上限（字符），超长按章节截断。
KNOWLEDGE_MAX_CHARS = 12000


class PathEscape(KnowledgeError):
    """路径越出 knowledge 数据区，或数据区内存在符号链接。"""


# --------------------------------------------------------------------------- #
# 路径与安全
# --------------------------------------------------------------------------- #


def knowledge_root(data_root: Any) -> Path:
    return Path(data_root) / "knowledge"


def dossier_dir(data_root: Any, market: Any, symbol: Any) -> Path:
    return knowledge_root(data_root) / f"{normalize_market(market)}_{normalize_symbol(symbol)}"


def profile_path(data_root: Any, market: Any, symbol: Any) -> Path:
    return dossier_dir(data_root, market, symbol) / "profile.md"


def latest_path(data_root: Any, market: Any, symbol: Any) -> Path:
    return dossier_dir(data_root, market, symbol) / "latest.md"


def runs_dir(data_root: Any, market: Any, symbol: Any) -> Path:
    return dossier_dir(data_root, market, symbol) / "runs"


def manifest_path(data_root: Any) -> Path:
    return knowledge_root(data_root) / MANIFEST_NAME


def safe_path(data_root: Any, *segments: str) -> Path:
    """safePath 口径（参考实现 service.ts 同款）：

    1. resolve 后逐字符比对前缀——词法层拒绝 `..` 越界；
    2. 从 root 往下的每一级 lstat，任何一级是符号链接即拒绝；
    3. realpath 仍须落在 root 的 realpath 之内。
    """
    root = Path(data_root).resolve()
    if not segments:
        return root
    abs_target = root.joinpath(*segments).resolve()
    root_text = str(root)
    target_text = str(abs_target)
    inside = target_text == root_text or target_text.startswith(
        root_text + "/"
    ) or target_text.startswith(root_text + "\\")
    if not inside:
        raise PathEscape(f"路径越出数据区：{segments!r}")
    # 逐级禁符号链接：只走 root 之下的词法层级
    rel_parts = abs_target.relative_to(root).parts
    cur = root
    for part in rel_parts:
        cur = cur / part
        if cur.is_symlink():
            raise PathEscape(f"数据区内存在符号链接，拒绝访问：{part}")
    real_root = Path(str(root)).resolve()
    real_target = Path(str(abs_target)).resolve()
    if real_target != real_root and not str(real_target).startswith(str(real_root)):
        raise PathEscape(f"路径 realpath 越出数据区：{segments!r}")
    return abs_target


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


def load_manifest(data_root: Any) -> dict[str, Any]:
    path = manifest_path(data_root)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "companies": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "companies": {}}
    if not isinstance(data, dict) or not isinstance(data.get("companies"), dict):
        return {"schema_version": SCHEMA_VERSION, "companies": {}}
    return data


def save_manifest(data_root: Any, manifest: dict[str, Any]) -> Path:
    manifest.setdefault("schema_version", SCHEMA_VERSION)
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    return atomic_write(manifest_path(data_root), text)


def manifest_entry(manifest: dict[str, Any], market: str, symbol: str) -> dict[str, Any] | None:
    return manifest.get("companies", {}).get(f"{market}:{symbol}")


def upsert_manifest(manifest: dict[str, Any], dossier: Dossier, *, dir_name: str) -> dict[str, Any]:
    companies = manifest.setdefault("companies", {})
    companies[f"{dossier.market}:{dossier.symbol}"] = {
        "dir": dir_name,
        "as_of": dossier.as_of,
        "status": dossier.status,
    }
    return manifest


# --------------------------------------------------------------------------- #
# 档案读写
# --------------------------------------------------------------------------- #


def load_profile(data_root: Any, market: Any, symbol: Any) -> Dossier | None:
    """读 profile.md；不存在或格式损坏返回 None（不伪造空档案）。"""
    path = profile_path(data_root, market, symbol)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return dossier_from_text(text)
    except KnowledgeError:
        return None


def write_profile(data_root: Any, dossier: Dossier) -> Path:
    """写 profile.md（原子写）并同步 manifest 索引。"""
    gate_payload([], dossier.render())
    path = profile_path(data_root, dossier.market, dossier.symbol)
    written = atomic_write(path, dossier.render())
    manifest = load_manifest(data_root)
    upsert_manifest(
        manifest,
        dossier,
        dir_name=f"{dossier.market}_{dossier.symbol}",
    )
    save_manifest(data_root, manifest)
    return written


# --------------------------------------------------------------------------- #
# 归档（run → knowledge）
# --------------------------------------------------------------------------- #


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ArchiveOutcome:
    """归档结果：`archived=False` 时带 `event`（事件名 + 说明），不抛出。"""

    def __init__(self, archived: bool, event: str, detail: str = "", path: str = "") -> None:
        self.archived = archived
        self.event = event
        self.detail = detail
        self.path = path

    def as_dict(self) -> dict[str, Any]:
        return {
            "archived": self.archived,
            "event": self.event,
            "detail": self.detail,
            "path": self.path,
        }


def should_archive(run_result: Any) -> bool:
    """归档门槛：run 状态非 failed 且产物含 report 阶段。"""
    failed = list(getattr(run_result, "failed", None) or [])
    if "report" in failed:
        return False
    status = str(getattr(run_result, "code", "") or "")
    if status == "failed":
        return False
    generated = list(getattr(run_result, "generated", None) or [])
    return "report" in generated


def render_run_archive(run_result: Any, stages_payload: dict[str, Any] | None = None) -> str:
    """run 归档正文：只含阶段 JSON 的结构化字段（叙述并入，不发明新数字）。"""
    code = getattr(run_result, "code", "")
    day = getattr(run_result, "date", "")
    lines = [
        "---",
        f'symbol: "{code}"',
        f"as_of: {day}",
        f"run_archived_at: {_utc_now()}",
        "---",
        "",
        f"## 研究运行 {day}",
        "",
    ]
    for stage in getattr(run_result, "stages", None) or []:
        name = stage.get("name")
        if not name:
            continue
        lines.append(f"### {name}（{stage.get('status', '')}）")
        for item in stage.get("facts", {}) or {}:
            lines.append(f"- {item}: {stage['facts'][item]}")
        lines.append("")
    if stages_payload:
        lines.append("```json")
        lines.append(json.dumps(stages_payload, ensure_ascii=False, indent=2))
        lines.append("```")
    return "\n".join(lines)


def archive_run(
    data_root: Any,
    market: Any,
    run_result: Any,
    *,
    run_id: str,
    valid_days: int | None = None,
    name: str = "",
) -> ArchiveOutcome:
    """run 归档：写 runs/<run-id>.md + latest.md + 更新 manifest。

    - 只在 `should_archive()` 通过时发生；
    - 正文先过合规与隐私 gate，命中即拒绝并记 `archive.gate_rejected`；
    - 任何写盘失败只返回失败事件，**不抛出、不改 run 状态**。
    """
    market_norm = normalize_market(market)
    if not should_archive(run_result):
        return ArchiveOutcome(False, "archive.skipped", "run 失败或无 report 产物，不归档")
    text = render_run_archive(run_result)
    try:
        gate_payload(None, text)
    except GateViolation as exc:
        return ArchiveOutcome(False, "archive.gate_rejected", exc.reason)
    code = normalize_symbol(getattr(run_result, "code", ""))
    as_of = str(getattr(run_result, "date", "") or sh_today().isoformat())
    dir_name = f"{market_norm}_{code}"
    dossier = Dossier(
        symbol=code,
        market=market_norm,
        name=name or code,
        as_of=as_of,
        status="fresh",
        valid_days=valid_days or 90,
        sources=[],
        body=text,
    )
    try:
        base = safe_path(knowledge_root(data_root), dir_name)
        run_file = base / "runs" / f"{run_id}.md"
        if not str(run_file).startswith(str(base)):
            raise PathEscape(f"run 归档路径越界：{run_id!r}")
        atomic_write(run_file, text)
        atomic_write(latest_path(data_root, market_norm, code), text)
        manifest = load_manifest(data_root)
        upsert_manifest(manifest, dossier, dir_name=dir_name)
        save_manifest(data_root, manifest)
    except (OSError, KnowledgeError) as exc:
        return ArchiveOutcome(False, "archive.failed", str(exc))
    ok = ArchiveOutcome(
        True, "archive.completed", path=str(latest_path(data_root, market_norm, code))
    )
    return ok


# --------------------------------------------------------------------------- #
# 冲突裁决
# --------------------------------------------------------------------------- #


def record_refutation(
    data_root: Any,
    market: Any,
    symbol: Any,
    *,
    old_conclusion: str,
    realtime_value: str,
    evidence: str,
    day: str | None = None,
) -> Path:
    """冲突裁决：实时优先，档案「历史结论」表追加一条反证记录。

    旧结论**保留**（表格里标 refuted + 依据 + 日期），绝不静默覆盖。
    档案级 `status` 同步标 `refuted`——它是显式反证的唯一写入点。
    """
    dossier = load_profile(data_root, market, symbol)
    if dossier is None:
        raise KnowledgeError(f"档案不存在，无法记录反证：{market}:{symbol}")
    day = day or sh_today().isoformat()
    lines = dossier.body.rstrip("\n").splitlines()
    header = "| as_of | 结论论 | 依据 | 有效期(天) | 状态(fresh / stale / refuted) |"
    sep = "|---|---|---|---|---|"
    row = (
        f"| {day} | 实时数据推翻旧结论：{old_conclusion} → 现为 {realtime_value} "
        f"| {evidence} | 90 | refuted |"
    )
    if header in lines:
        idx = lines.index(header)
        insert = idx + 1
        while insert < len(lines) and lines[insert].startswith("|"):
            insert += 1
        lines[insert:insert] = [row]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("## 3. 历史结论(每条必带 as_of;与实时数据冲突时以实时为准并记录反证)")
        lines.append(header)
        lines.append(sep)
        lines.append(row)
    dossier.body = "\n".join(lines) + "\n"
    dossier.status = "refuted"
    dossier.as_of = day
    return write_profile(data_root, dossier)


__all__ = [
    "ArchiveOutcome",
    "KNOWLEDGE_MAX_CHARS",
    "MANIFEST_NAME",
    "PathEscape",
    "archive_run",
    "dossier_dir",
    "knowledge_root",
    "latest_path",
    "load_manifest",
    "load_profile",
    "manifest_entry",
    "manifest_path",
    "profile_path",
    "record_refutation",
    "runs_dir",
    "safe_path",
    "save_manifest",
    "should_archive",
    "upsert_manifest",
    "write_profile",
]
