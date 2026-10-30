"""R3 审计命令（QUOT-141 要求 5）：`ldst research <code> --check` 只读、不重跑模型。

对应验收：「构造一份含伪造数字的报告 → `--check` 失败并指出位置」。
退出码：0 通过 / 6 产物不存在 / 8 有不合格数字（复用 docs/CLI.md §F 既有码，不新增）。

命令写成 `--check` 开关而不是 `ldst research report <code>` 子命令，原因写在
`cli/research.py` 的模块文档里：`research` 是带必填位置参数的叶子命令，
再挂子命令会让 click 的组回调先撞「Missing argument CODE」，
且会破坏 docs/CLI.md §0 的 39 叶子 / 38 argv 计数契约。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.ai import fallback
from ladderstock.cli.main import app
from ladderstock.core.boundary import Provenance, assert_provenance
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_OK, EXIT_PARTIAL, EXIT_USAGE
from ladderstock.research import artifacts, checkpoint
from ladderstock.research.pipeline import ResearchPipeline

runner = CliRunner()

CODE = "600000"
DAY = "2026-10-27"

#: `--check --output json` 的载荷键（R8 / 页面只消费这一份）
CHECK_KEYS = {
    "status",
    "code",
    "date",
    "stage",
    "json",
    "md",
    "ok",
    "reason",
    "counts",
    "exempt_hits",
    "unused_sources",
    "findings",
    "problems",
}
FINDING_KEYS = {"line", "column", "number", "reason", "detail", "excerpt"}


class ForbiddenClient:
    """`--check` 一次模型调用都不许发：谁调它，测试立刻炸。"""

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        raise AssertionError("--check 不许调用模型")


def record(metric: str, value: float, *, unit: str = "倍", verified: bool = True) -> Provenance:
    return Provenance(
        value=value,
        unit=unit,
        source="tickhub-cn",
        endpoint=f"{metric}_endpoint",
        fetched_at="2026-10-31T09:00:00+08:00",
        as_of="2026-09-30",
        url="http://127.0.0.1:8000/mcp",
        verified=verified,
        metric=metric,
    )


def write_report(root: Path, narrative: list[str], stage: str = "report") -> tuple[Path, Path]:
    """按 R3 的真实形状写一段产物：出处来自 `assert_provenance`，Markdown 由程序渲染。"""
    metrics = {"pe": 18.4, "revenue_yoy": 37.2}
    records = {"pe": record("pe", 18.4), "revenue_yoy": record("revenue_yoy", 37.2, unit="%")}
    slot = artifacts.provenance_slot(assert_provenance(metrics, records), stage=stage)
    document = artifacts.build_document(
        code=CODE,
        day=DAY,
        stage=stage,
        status="ok",
        mode="structured",
        model="fake-model",
        degraded=False,
        narrative=narrative,
        notes=[],
        facts=metrics,
        ai={"narrative": narrative, "degraded": False},
        provenance=slot,
    )
    paths = artifacts.write_stage(document, root)
    return Path(paths["json"]), Path(paths["md"])


def rewrite(md_path: Path, text: str) -> int:
    """把人读件正文换成「人 / 模型自己写的」叙述，结构化件不动——这正是审计要抓的场景。

    返回替换后那一行的行号（从 1 起），用来核对审计有没有指对位置。
    """
    lines = md_path.read_text(encoding="utf-8").split("\n")
    start = lines.index("## 叙述") + 2
    tail = next(index for index, line in enumerate(lines) if line.startswith("## 指标"))
    lines[start:tail] = [text, ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return start + 1


def check(root: Path, *extra: str) -> Any:
    return runner.invoke(
        app,
        ["research", CODE, "--date", DAY, "--check", "--output", "json", *extra],
    )


def payload(result: Any) -> dict[str, Any]:
    assert result.exit_code in {EXIT_OK, EXIT_NOT_FOUND, EXIT_PARTIAL}, (
        f"{result.exit_code}\n{result.stdout}\n{result.stderr}"
    )
    return json.loads(result.stdout)


def digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """数据目录切到 tmp（产物不落仓库 `data/`），cwd 也挪出仓库，让 `config/ai.yaml` 不可见。"""
    root = tmp_path / "data"
    monkeypatch.setenv("LDST_DATA_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    return root


# --------------------------------------------------------------------------- #
# 1. 合格的报告
# --------------------------------------------------------------------------- #


def test_check_passes_on_a_fully_cited_report(data_dir: Path) -> None:
    write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    result = check(data_dir)
    body = payload(result)

    assert result.exit_code == EXIT_OK, body["findings"]
    assert set(body) == CHECK_KEYS
    assert body["status"] == "audited" and body["ok"] is True and body["reason"] is None
    assert body["stage"] == "report" and body["code"] == CODE and body["date"] == DAY
    assert body["findings"] == [] and body["problems"] == []
    assert body["counts"] == {
        "numbers": 4,
        "cited": 4,
        "unverified": 0,
        "exempt": 8,
        "sources": 2,
    }
    # 叙述 2 个数字 + `## 指标` 2 个数字全部带角标；豁免吃掉页头日期/代码与角标自身的数字
    assert body["exempt_hits"] == {
        "日期": 3,
        "角标本身": 4,
        "证券代码（标识符不是指标）": 1,
    }
    assert body["unused_sources"] == []


def test_check_passes_on_every_stage_via_only(data_dir: Path) -> None:
    write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"], stage="valuation")
    result = check(data_dir, "--only", "valuation")
    assert result.exit_code == EXIT_OK, result.stdout
    assert payload(result)["stage"] == "valuation"


def test_check_needs_no_config_and_never_reruns_model(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd 已挪出仓库（没有 `config/ai.yaml`）也能审计，且 `run` 被换成必然抛错的实现。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("--check 不许重跑管线")

    monkeypatch.setattr(ResearchPipeline, "run", boom)
    write_report(data_dir, ["市盈率 18.4 倍。"])
    assert check(data_dir).exit_code == EXIT_OK


def test_check_is_read_only(data_dir: Path) -> None:
    write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    before = digest(data_dir)
    check(data_dir)
    check(data_dir)
    assert digest(data_dir) == before, "--check 是只读工具，不得产生任何写操作"
    assert not list(data_dir.rglob("*.tmp"))


# --------------------------------------------------------------------------- #
# 2. 产物不存在 → 6
# --------------------------------------------------------------------------- #


def test_check_missing_artifact_exits_6(data_dir: Path) -> None:
    result = check(data_dir)
    assert result.exit_code == EXIT_NOT_FOUND
    body = payload(result)
    assert set(body) == CHECK_KEYS, "缺产物分支的键集合与审计通过时一致"
    assert body["status"] == "missing" and body["ok"] is False
    assert "产物不存在" in body["reason"]
    assert Path(body["json"]).parent.as_posix().endswith(f"{CODE}/{DAY}")
    assert body["findings"] == [] and body["problems"] == []


def test_check_missing_markdown_half_pair_exits_6(data_dir: Path) -> None:
    json_path, md_path = write_report(data_dir, ["市盈率 18.4 倍。"])
    md_path.unlink()
    body = payload(check(data_dir))
    assert body["status"] == "missing"
    assert json_path.is_file(), "结构化件还在，只是人读件缺失"


# --------------------------------------------------------------------------- #
# 3. 伪造数字 → 8，且指出位置
# --------------------------------------------------------------------------- #


def test_check_fails_on_a_bare_number_and_points_at_it(data_dir: Path) -> None:
    _json_path, md_path = write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    line = rewrite(md_path, "本季度营收增速为 51.9%，超出市场预期。")

    result = check(data_dir)
    body = payload(result)
    assert result.exit_code == EXIT_PARTIAL
    assert body["ok"] is False and body["status"] == "audited"
    finding = body["findings"][0]
    assert set(finding) == FINDING_KEYS
    assert finding["number"] == "51.9" and finding["reason"] == "无角标的数字"
    assert finding["line"] == line, "审计必须指到那一行"
    assert finding["column"] == len("本季度营收增速为 ") + 1
    assert finding["excerpt"].startswith("本季度")


def test_check_fails_when_a_real_citation_carries_a_forged_value(data_dir: Path) -> None:
    """借真引用报假数：`[1]` 确实存在，但它记的是 18.4，正文写 99.9。"""
    _json_path, md_path = write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    line = rewrite(md_path, "市盈率 99.9[1] 倍，营收增速 37.2%[2]。")

    result = check(data_dir)
    body = payload(result)
    assert result.exit_code == EXIT_PARTIAL
    assert body["findings"][0]["number"] == "99.9"
    assert body["findings"][0]["line"] == line
    assert body["findings"][0]["reason"] == "数字与角标记录的来源值不符"


def test_check_fails_on_a_dangling_superscript(data_dir: Path) -> None:
    _json_path, md_path = write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    line = rewrite(md_path, "市盈率 18.4[9] 倍。")

    result = check(data_dir)
    body = payload(result)
    assert result.exit_code == EXIT_PARTIAL
    assert body["findings"][0]["reason"] == "角标指向不存在的来源"
    assert body["findings"][0]["line"] == line


def test_check_fails_when_structured_doc_claims_verified_without_backing(data_dir: Path) -> None:
    """结构化件自洽：`values` 声称已核实但来源 `verified=false` → 也判不合格。"""
    json_path, _md_path = write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    document = json.loads(json_path.read_text(encoding="utf-8"))
    document["provenance"]["sources"][0]["verified"] = False
    json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    body = payload(check(data_dir))
    assert body["problems"], body
    assert any("verified=false" in item for item in body["problems"])


def test_check_fails_when_a_numeric_metric_has_no_provenance(data_dir: Path) -> None:
    """没有出处的数值字段进了产物，而整件标成已核实 → 结构化件不自洽。"""
    json_path, _md_path = write_report(data_dir, ["市盈率 18.4 倍，营收增速 37.2%。"])
    document = json.loads(json_path.read_text(encoding="utf-8"))
    document["facts"]["net_margin"] = 22.8
    json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    result = check(data_dir)
    assert result.exit_code == EXIT_PARTIAL
    assert any("net_margin" in item for item in payload(result)["problems"])


# --------------------------------------------------------------------------- #
# 4. 用法错误与其他形态
# --------------------------------------------------------------------------- #


def test_check_with_unknown_stage_exits_2(data_dir: Path) -> None:
    result = check(data_dir, "--only", "notes")
    assert result.exit_code == EXIT_USAGE
    assert "report" in result.stderr and "notes" in result.stderr


def test_placeholder_report_still_audits_clean(data_dir: Path) -> None:
    """未配置凭据的占位产物：没有引用来源，正文数字一律〔未核实〕，审计照样通过。"""
    config = fallback.AiConfig(
        default_model="m",
        fallback_chain=(),
        base_url_env="LDST_CHECK_NEVER_SET_URL",
        api_key_env="LDST_CHECK_NEVER_SET_KEY",
        structured_output="prefer",
        on_all_failed="degrade_to_text",
        sections={},
    )
    run = ResearchPipeline(
        CODE, config=config, client=ForbiddenClient(), root=data_dir
    ).run(DAY)
    assert run.exit_code == 9 and run.degraded is True

    result = check(data_dir)
    body = payload(result)
    assert result.exit_code == EXIT_OK, body["findings"]
    assert body["counts"]["cited"] == 0 and body["counts"]["sources"] == 0
    assert body["unused_sources"] == []
    assert checkpoint.REPORT_STAGE == "report"
