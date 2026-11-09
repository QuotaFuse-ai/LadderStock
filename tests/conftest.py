from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

# 本仓源码优先于环境里的 editable 安装（共享环境里可能装着别的任务的副本）
for path in (ROOT / "src", ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> Path:
    """把工作目录切到仓库根，让 `config/` 被 ldst 找到。"""
    monkeypatch.chdir(ROOT)
    return ROOT


@pytest.fixture(autouse=True)
def _isolate_memory_channel(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """默认关掉跨会话记忆通道（QUOT-487）：单测不连本机 mem0 实例（9100/9000）。

    需要真实记忆行为的测试自行 `monkeypatch.setenv("LDST_MEMORY_BASE_URL", ...)` 打开，
    或直接注入 `MemoryClient`。
    """
    from ladderstock.ai import memory as memory_module

    monkeypatch.setenv(memory_module.ENV_BASE_URL, "")
    memory_module.reset_runtime_state()
    yield
    memory_module.reset_runtime_state()
