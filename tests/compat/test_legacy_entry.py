"""旧入口兼容：`ladderstock` 长别名、`python -m ladderstock`、旧启动脚本与旧端口。

文档要求（`docs/CLI.md` §F / §G、`docs/PACKAGES.md` §6）：

- `ladderstock` 是 `ldst` 的长别名，指向同一个 Typer app，命令面不得分叉；
- `python -m ladderstock` 等价于 `ldst`（`compatibility/start.py` 只做参数翻译与委托）；
- 旧启动脚本 `scripts/start`、`scripts\\start.cmd` 委托 `ldst run` 并打印迁移提示；
- 旧端口 5940 / 8770 **只打印迁移提示**，绝不接管、不回退（DEC-004）；
- 旧配置/数据目录通过 `config migrate` 只读迁移（`docs/CLI.md` §G 最后一行）。

仓库尚未实现的兼容件用 `xfail(strict=False)` 标出：现在是「已知缺口」，
实现了自动转为通过，不需要再改测试。缺陷明细写在 `docs/tasks/TASK-VERIFY/report.md`。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import DEFAULT_MCP_PORT, DEFAULT_PORT, app

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
SRC = ROOT / "src" / "ladderstock"
CLI_DOC = ROOT / "docs" / "CLI.md"
PACKAGES_DOC = ROOT / "docs" / "PACKAGES.md"

runner = CliRunner()

#: `docs/CLI.md` §0 的 11 个分组，加上 `run` / `export` / `doctor` 三个顶层命令，
#: 再加 R1-1（QUOT-190）新增的 `research`（见 docs/CLI.md §0「R1-1 增量」行），
#: 以及 R7 增量两个（`pool` 候选池、`position` 我的持仓，QUOT-145 / §E″），
#: 再加 R5 增量一个（`industry` 产业研究，QUOT-143 / §D‴）。
DOCUMENTED_GROUPS = {
    "run",
    "review",
    "validate",
    "sentiment",
    "ladder",
    "pool",
    "position",
    "industry",
    "sync",
    "sources",
    "watch",
    "notify",
    "journal",
    "export",
    "state",
    "ai",
    "research",
    "mcp",
    "config",
    "schedule",
    "doctor",
}

#: 文档承诺「只打印迁移提示」的旧端口（DEC-004）。
LEGACY_PORTS = ("5940", "8770")


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


#: rich 在 CI（非 Windows 的管道输出）上仍会插入 ANSI 样式，解析前先剥掉。
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _python_module(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 固定颜色与宽度：否则同一份帮助在不同 runner 上渲染不同，解析结果不可复现
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["COLUMNS"] = "120"
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "ladderstock", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


# --------------------------------------------------------------------------- #
# 1. 别名与入口
# --------------------------------------------------------------------------- #


def test_pyproject_declares_both_console_scripts() -> None:
    scripts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["ldst"] == "ladderstock.cli.main:main"
    assert scripts["ladderstock"] == "ladderstock.cli.main:main", (
        "`ladderstock` 必须是 `ldst` 的长别名，指向同一个 main"
    )


def test_python_dash_m_runs_the_same_app(data_dir: Path) -> None:
    """`python -m ladderstock` 与 `ldst` 共用入口：随便挑一条只读命令验证。"""
    completed = _python_module("state", "inspect", "--output", "json")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["backend"] == "indexeddb"
    assert payload["schema_version"] == 2


def test_alias_does_not_change_the_command_surface(data_dir: Path) -> None:
    """`ldst` 与 `ladderstock` 是同一个 app 对象，帮助里的分组必然一致。"""
    completed = _python_module("--help")
    assert completed.returncode == 0, completed.stderr
    missing = {name for name in DOCUMENTED_GROUPS if name not in completed.stdout}
    assert not missing, f"顶层命令缺失：{sorted(missing)}"


def test_help_lists_no_undocumented_top_level_command(data_dir: Path) -> None:
    """反向断言：帮助里出现的命令组必须都在文档 §0 的分组内。"""
    completed = _python_module("--help")
    assert completed.returncode == 0, completed.stderr
    stdout = ANSI_ESCAPE.sub("", completed.stdout)
    # Rich 用制表框渲染 Commands 段落：`│ run   启动本地工作台…`
    listed = set(re.findall(r"^\s*[│|]?\s*([a-z][a-z-]+)\s{2,}\S", stdout, re.M))
    assert listed, f"没解析到命令列表：\n{stdout}"
    assert listed <= DOCUMENTED_GROUPS, f"出现了未登记的命令：{sorted(listed - DOCUMENTED_GROUPS)}"


# --------------------------------------------------------------------------- #
# 2. 端口策略：新端口固定，旧端口只提示
# --------------------------------------------------------------------------- #


def test_default_ports_are_the_documented_loopback_ports() -> None:
    assert DEFAULT_PORT == 5950
    assert DEFAULT_MCP_PORT == 5951


#: 唯一允许出现旧端口字面量的运行时模块：`ldst: … 是旧端口 …` 的提示文案。
LEGACY_PORT_HINT_MODULE = SRC / "cli" / "legacy_ports.py"


def test_legacy_ports_never_leak_into_runtime_code() -> None:
    """旧端口 5940 / 8770 只允许出现在 `cli/legacy_ports.py` 的提示文案里。

    别的运行时代码出现旧端口字面量，通常意味着有人在「接管旧端口」或
    「失败后回退旧端口」——两者都被 DEC-004 明令禁止。提示模块自身也只许提示：
    不 bind、不 listen、不重试。
    """
    hint_module = LEGACY_PORT_HINT_MODULE.resolve()
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_sources()
        if path.resolve() != hint_module
        and any(port in path.read_text(encoding="utf-8") for port in LEGACY_PORTS)
    ]
    assert not offenders, f"运行时代码里出现了旧端口：{offenders}"

    assert LEGACY_PORT_HINT_MODULE.is_file(), "旧端口提示模块缺失：cli/legacy_ports.py"
    hint = LEGACY_PORT_HINT_MODULE.read_text(encoding="utf-8")
    for forbidden in ("bind(", "listen(", "socket", "uvicorn", "serve(", "retry"):
        assert forbidden not in hint, (
            f"旧端口提示模块不得包含 {forbidden!r}（DEC-004：只提示，不接管、不回退）"
        )

    doc = CLI_DOC.read_text(encoding="utf-8")
    for port in LEGACY_PORTS:
        assert port in doc, f"CLI.md 必须写明旧端口 {port} 只打印迁移提示"


def test_occupied_port_exits_with_config_error_not_a_silent_switch() -> None:
    source = (SRC / "cli" / "run.py").read_text(encoding="utf-8")
    assert "不会自动换端口" in source
    assert "EXIT_CONFIG" in source


# --------------------------------------------------------------------------- #
# 3. `config migrate`：旧数据目录过渡（当前唯一占位命令）
# --------------------------------------------------------------------------- #


def test_config_migrate_honours_the_contract_in_either_state(data_dir: Path) -> None:
    """契约：要么是施工期占位（70 + stderr 说明、stdout 为空），
    要么已实现（0 + JSON 计划，且**不**改动旧目录）。"""
    result = runner.invoke(app, ["config", "migrate", "--dry-run", "--output", "json"])

    if result.exit_code == 70:
        assert result.stdout.strip() == "", "占位命令不得产生结构化输出"
        assert "config migrate" in result.stderr
        assert "尚未实现" in result.stderr
        return

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict) and payload, "实现后必须输出结构化迁移计划"
    assert "dry_run" in payload or "plan" in payload


def test_config_validate_still_passes(data_dir: Path) -> None:
    """迁移命令的占位不能影响配置校验（CI 门禁里也是这条）。"""
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 0, result.stderr


# --------------------------------------------------------------------------- #
# 4. 已知缺口（未实现 → xfail；实现后自动转绿）
# --------------------------------------------------------------------------- #


def _missing(reason: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=False, reason=f"未实现（{reason}）")


def _find_legacy_script() -> Path | None:
    for candidate in ("start", "start.cmd", "start.ps1"):
        path = ROOT / "scripts" / candidate
        if path.is_file():
            return path
    return None


@_missing("docs/CLI.md §F：scripts/start 与 scripts\\start.cmd 应委托 ldst run")
def test_legacy_start_script_delegates_to_ldst_run() -> None:
    path = _find_legacy_script()
    assert path is not None, "旧启动脚本缺失：scripts/start / scripts/start.cmd"
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "ldst" in text or "ladderstock" in text
    assert "run" in text
    assert "迁移" in text, "旧脚本必须打印迁移提示，不能静默等价"


@_missing("docs/PACKAGES.md §6：compatibility/start.py 只做参数翻译与委托")
def test_compatibility_entry_shim_exists() -> None:
    shim = ROOT / "compatibility" / "start.py"
    assert shim.is_file(), "兼容入口缺失：compatibility/start.py"
    text = shim.read_text(encoding="utf-8")
    assert "ladderstock" in text


@_missing("源文档目录树：src/ladderstock/config/migrate.py 承载旧配置只读迁移")
def test_config_migration_module_exists() -> None:
    module = SRC / "config" / "migrate.py"
    assert module.is_file(), "缺少 config/migrate.py（config migrate 的实现落脚点）"


@_missing("docs/PACKAGES.md §6：兼容读取旧 LDST_ 前缀，新前缀优先")
def test_legacy_env_prefix_is_read_but_never_overrides() -> None:
    text = (SRC / "cli" / "paths.py").read_text(encoding="utf-8")
    assert "LDST_DATA_DIR" in text
    assert "兼容" in text, "paths.py 需要说明旧前缀（如 VIBE_ASTOCK_*）的兼容读取顺序"
