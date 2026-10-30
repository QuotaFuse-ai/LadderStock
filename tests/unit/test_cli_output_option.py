"""全局 `--output` 契约：31 条基线命令 + R1-1 `research` + R7 增量 5 条（共 39 个叶子路径）都不许把这个选项丢掉。

契约来自 docs/CLI.md §F：

- 每条命令都接受 `--output json|table|csv`；
- 不传时交互终端默认 table、管道默认 json；
- 每条命令实现后，`--output` 必须产出该格式的结构化结果（不再有「尚未实现」桩）。

反向验证：删掉任意一条命令的 `--output` 声明，`test_every_leaf_command_declares_output`
立即变红并点名路径（`test_checker_flags_missing_output` 用合成 app 固化这一行为）。
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import pytest
import typer
from fixtures.command_matrix import (
    JSON_PATHS,
    LEAF_PATHS,
    OUTPUT_FORMATS,
    OUTPUT_MATRIX,
)
from fixtures.fake_peers import FakeTransport
from typer.main import get_command
from typer.testing import CliRunner

from ladderstock.cli import output
from ladderstock.cli.main import app
from ladderstock.cli.output import OutputOption

runner = CliRunner()


# --------------------------------------------------------------------------- #
# 注册表遍历
# --------------------------------------------------------------------------- #

def leaves(cmd: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """遍历 typer 注册表，产出「真正可执行」的路径。

    `review` / `validate` / `sync` 三个组带 `invoke_without_command`：组本身
    也是叶子（`ldst review --date ...`），子命令另算一条。
    """
    subs = dict(getattr(cmd, "commands", None) or {})
    if not subs or getattr(cmd, "invoke_without_command", False):
        yield path, cmd
    for name, sub in subs.items():
        yield from leaves(sub, (*path, name))


def output_param(cmd: Any) -> Any | None:
    for param in cmd.params:
        if "--output" in getattr(param, "opts", ()):
            return param
    return None


def missing_output(registered: dict[tuple[str, ...], Any]) -> list[str]:
    """漏声明 `--output` 的命令路径（空列表 = 契约成立）。"""
    return [
        " ".join(path)
        for path, cmd in registered.items()
        if output_param(cmd) is None
    ]


LEAVES = dict(leaves(get_command(app)))


@pytest.fixture(autouse=True)
def _offline_data_layer(repo, tmp_path, monkeypatch):
    """数据目录钉在 tmp、传输层换成空桩：不拨真 peer，也不写仓库 `data/`。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport",
        lambda _peers: FakeTransport(),
    )


# --------------------------------------------------------------------------- #
# 1. 矩阵：每条叶子命令都声明了 --output
# --------------------------------------------------------------------------- #

def test_registered_leaves_match_baseline() -> None:
    """注册表与基线矩阵必须一一对应（多一条/少一条都要有人来补契约）。"""
    registered = set(LEAVES)
    baseline = set(LEAF_PATHS)
    extra = ", ".join(" ".join(p) or "<root>" for p in sorted(registered - baseline))
    gone = ", ".join(" ".join(p) for p in sorted(baseline - registered))
    assert registered == baseline, (
        f"未登记进 LEAF_PATHS 的新命令：{extra or '-'}\n基线里已不存在的命令：{gone or '-'}"
    )


@pytest.mark.parametrize("path", LEAF_PATHS, ids=lambda p: " ".join(p))
def test_every_leaf_command_declares_output(path: tuple[str, ...]) -> None:
    cmd = LEAVES.get(path)
    assert cmd is not None, f"注册表里找不到 {' '.join(path)}"
    assert output_param(cmd) is not None, (
        f"{' '.join(path)} 缺少全局 --output（docs/CLI.md §F）；"
        "补上 `output: OutputOption = None` 再跑一次"
    )


@pytest.mark.parametrize("path", LEAF_PATHS, ids=lambda p: " ".join(p))
def test_output_option_offers_documented_formats(path: tuple[str, ...]) -> None:
    param = output_param(LEAVES[path])
    assert param is not None, f"{' '.join(path)} 缺少 --output"
    assert tuple(getattr(param.type, "choices", ())) == OUTPUT_FORMATS, (
        f"{' '.join(path)} 的 --output 可选值不是 {OUTPUT_FORMATS}"
    )


def test_checker_flags_missing_output() -> None:
    """反向验证：漏声明的命令必须被点名，否则矩阵测试形同虚设。"""
    probe = typer.Typer()

    @probe.command("ok")
    def _ok(output: OutputOption = None) -> None:  # noqa: ARG001 - 只为被检查
        ...

    @probe.command("forgot")
    def _forgot(date: str = "2026-10-22") -> None:  # noqa: ARG001 - 故意漏 --output
        ...

    assert missing_output(dict(leaves(get_command(probe)))) == ["forgot"]


# --------------------------------------------------------------------------- #
# 2. 可解析性：已实现命令的 stdout
# --------------------------------------------------------------------------- #

def _ids(path: tuple[str, ...]) -> str:
    return " ".join(path)


def _argv_id(value: Any) -> str:
    return " ".join(value) if isinstance(value, tuple) else ""


@pytest.mark.parametrize(("argv", "expected"), OUTPUT_MATRIX, ids=_argv_id)
def test_output_json_is_never_a_usage_error(
    argv: tuple[str, ...], expected: set[int]
) -> None:
    """31 条命令逐条带 `--output json`：退出码按各自语义，绝不是 2。"""
    result = runner.invoke(app, list(argv))
    assert "No such option" not in result.stderr, (
        f"{' '.join(argv)} 仍在报 No such option\nstderr={result.stderr}"
    )
    assert result.exit_code != 2, (
        f"{' '.join(argv)} 退出码 2（参数错误）\nstderr={result.stderr}"
    )
    assert result.exit_code in expected, (
        f"{' '.join(argv)} 退出码 {result.exit_code}，期望 {sorted(expected)}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.mark.parametrize("argv", JSON_PATHS, ids=_ids)
def test_implemented_commands_emit_parseable_json(argv: tuple[str, ...]) -> None:
    result = runner.invoke(app, [*argv, "--output", "json"])
    assert result.exit_code in (0, 3), (
        f"{' '.join(argv)} 退出码 {result.exit_code}\nstderr={result.stderr}"
    )
    payload = json.loads(result.stdout)  # 抛异常即失败：stdout 必须是纯 JSON
    assert isinstance(payload, (dict, list))


@pytest.mark.parametrize("argv", JSON_PATHS, ids=_ids)
def test_implemented_commands_accept_csv(argv: tuple[str, ...]) -> None:
    result = runner.invoke(app, [*argv, "--output", "csv"])
    assert result.exit_code in (0, 3), (
        f"{' '.join(argv)} --output csv 退出码 {result.exit_code}\nstderr={result.stderr}"
    )
    assert "No such option" not in result.stderr
    assert result.stdout.strip(), f"{' '.join(argv)} --output csv 没有任何表头/行"


# --------------------------------------------------------------------------- #
# 3. 默认值：交互 table、管道 json（docs/CLI.md §F）
# --------------------------------------------------------------------------- #

class _Tty(io.StringIO):
    """伪装成交互终端。"""

    def isatty(self) -> bool:
        return True


def test_default_format_is_json_when_piped(monkeypatch) -> None:
    monkeypatch.setattr(output.sys, "stdout", io.StringIO())
    assert output.resolve_format(None) == "json"


def test_default_format_is_table_on_interactive_terminal(monkeypatch) -> None:
    monkeypatch.setattr(output.sys, "stdout", _Tty())
    assert output.resolve_format(None) == "table"


@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_explicit_format_wins_over_default(monkeypatch, fmt: str) -> None:
    monkeypatch.setattr(output.sys, "stdout", _Tty())
    assert output.resolve_format(fmt) == fmt


def test_doctor_defaults_to_json_when_captured() -> None:
    """无 `--output` 且 stdout 不是终端（管道）时默认 json。"""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 3)
    assert json.loads(result.stdout)["checks"]


# --------------------------------------------------------------------------- #
# 4. 已实现命令：`--output` 三档都要出结构化结果
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_migrate_emits_structured_plan_in_every_format(fmt: str) -> None:
    """`config migrate` 已实现：三种格式都必须产出结构化结果（不再是「尚未实现」）。"""
    result = runner.invoke(app, ["config", "migrate", "--dry-run", "--output", fmt])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip(), "实现后必须产出结构化结果"
    assert "尚未实现" not in result.stderr
