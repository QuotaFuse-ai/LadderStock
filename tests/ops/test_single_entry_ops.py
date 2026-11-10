"""单入口运维脚本验收（QUOT-523）：包装器只转发、回执只读、解析器认全入口形态。

被测对象是 `ops/host/` 下两个 PowerShell 脚本：

1. `Invoke-SingleEntry.ps1`——计划任务 / 桌面唯一该调用的启停入口。`-DryRun`
   必须只打印将执行的命令行（不拉起、不停止）；`-Action stop` 不带 `-Force`
   必须落到 supervisor 自己的预演（`dry_run: true`，不动进程）。
2. `Get-LegacyTaskReceipt.ps1`——散落计划任务只读回执。本文件比对本机真实现状
   （LadderStock / TickHub / QF-Peer 相关任务）与清单是否对得上；`-Apply` 只导出
   XML，任何任务状态都不许变。

平台门：仅 Windows + Windows PowerShell 5.1（CI ubuntu 无 PowerShell / schtasks）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

IS_WINDOWS = os.name == "nt"
WINDOWS_ONLY = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="ops/host 脚本只在 Windows 部署机执行（CI ubuntu 无 PowerShell / 计划任务）",
)

pytestmark = WINDOWS_ONLY

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_DIR = REPO_ROOT / "ops" / "host"
WRAPPER = HOST_DIR / "Invoke-SingleEntry.ps1"
RECEIPT = HOST_DIR / "Get-LegacyTaskReceipt.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe")


def _ps(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    assert POWERSHELL, "找不到 powershell.exe"
    flags = [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    return subprocess.run(
        flags,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )


def _envelope(script: Path) -> str:
    raw = script.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        f"{script.name} 缺 UTF-8 BOM（Windows PowerShell 5.1 会按 GBK 解析乱码）"
    )


def test_ops_scripts_carry_bom_for_powershell_51() -> None:
    _envelope(WRAPPER)
    _envelope(RECEIPT)


def test_wrapper_dry_run_only_prints_and_stop_defaults_to_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 显式 launcher 原样使用（等价 ldst 入口点）：空文件足以证明干跑不执行它
    launcher = tmp_path / "ldst.exe"
    launcher.write_bytes(b"")
    env = {**os.environ, "LDST_LAUNCHER": str(launcher), "PYTHONPATH": str(REPO_ROOT / "src")}
    monkeypatch.setenv("LDST_LAUNCHER", str(launcher))

    plan = _ps(WRAPPER, "-Action", "start", "-DryRun", env=env)
    assert plan.returncode == 0, plan.stderr
    assert str(launcher) in plan.stdout and "supervisor start" in plan.stdout
    assert "Traceback" not in plan.stderr, "start -DryRun 不许真的拉起进程"

    # 预演必须走真实 supervisor：换成源码树解释器（python -m ...），且不带 --force
    python_env = {k: v for k, v in env.items() if k != "LDST_LAUNCHER"}
    python_env["LDST_LAUNCHER"] = sys.executable
    preview = _ps(WRAPPER, "-Action", "stop", env=python_env)
    assert preview.returncode == 0, preview.stderr
    payload = json.loads(preview.stdout)
    assert payload["dry_run"] is True
    assert payload["would_stop"] == [] or isinstance(payload["would_stop"], list)
    assert "order" in payload


def test_receipt_list_matches_machine_and_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}

    before = _ps(RECEIPT, "-Json", env=env)
    assert before.returncode == 0, before.stderr
    before_rows = {row["Task"]: row for row in json.loads(before.stdout)}
    assert not (tmp_path / "LadderStock" / "task-receipts").exists(), "不带 -Apply 不许写回执目录"

    for name in (
        "LadderStock API",  # 红线：4950/5950 看板自循环必须被识别为保留，不许归到收编清单
        "LadderStock Modules",
        "LadderStock Watchdog",
    ):
        assert name in before_rows, f"回执清单漏了本机存在的计划任务 {name}"
    assert before_rows["LadderStock API"]["Kind"] == "keep-board-loop"
    assert before_rows["LadderStock Modules"]["Kind"] == "legacy-autostart"
    assert before_rows["LadderStock Watchdog"]["Kind"] == "legacy-autostart"
    kinds = {row["Kind"] for row in before_rows.values()}
    assert kinds <= {"legacy-autostart", "stat-monitor", "keep-board-loop", "peer-peer", "external"}

    after = _ps(RECEIPT, "-Apply", "-Json", env=env)
    assert after.returncode == 0, after.stderr
    after_rows = {row["Task"]: row for row in json.loads(after.stdout)}
    assert {name: row["State"] for name, row in after_rows.items()} == {
        name: row["State"] for name, row in before_rows.items()
    }, "-Apply 只导出 XML，不许改变任何任务状态"

    receipts = tmp_path / "LadderStock" / "task-receipts"
    exported = [item for item in receipts.glob("*.xml")] if receipts.is_dir() else []
    assert exported, "-Apply 必须留下可复现的 XML 回执"
