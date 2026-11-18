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

    需要记忆行为的测试自行注入 `MemoryClient(settings, engine=...)`，或覆盖回测试开关。
    """
    from ladderstock.ai import memory as memory_module

    monkeypatch.setenv(memory_module.LEGACY_ENV_BASE_URL, "")
    memory_module.reset_runtime_state()
    yield
    memory_module.reset_runtime_state()


@pytest.fixture(autouse=True)
def _isolate_active_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个用例都从「未指定环境」开始，退出时也清空。

    `_active_env` 优先于 `LDST_ENV`，所以既不能把上个用例 select_env 的结果
    带进来，也不能在本用例结束时把自己的改动带出去——否则
    `test_registry_rejects_prod_adapters_under_test_env` 这类
    「setenv test 后必须拒 prod 端口表」的用例会被上一个用例遗留的
    `_active_env="prod"` 静默放行（全量顺序下 DID NOT RAISE ValueError）。
    """
    from ladderstock.supervisor import modules as modules_module

    modules_module.reset_env()
    monkeypatch.delenv("LDST_ENV", raising=False)
    yield
    modules_module.reset_env()


#: 会改变配置解析结果的进程环境变量。开发机上常被 shell 导出（例如把面板
#: 指向本地 8317 代理的 `LDST_LLM_MODEL`），而 `fallback._env_value` 的设计是
#: 「进程环境优先于 ai.env」，于是断言里的 `model-a` 会变成真实模型名。
_CONFIG_ENV_VARS = (
    "LDST_LLM_MODEL",
    "LDST_LLM_API_KEY",
    "LDST_LLM_BASE_URL",
    "LDST_CONFIG_DIR",
    "LDST_DATA_DIR",
    "LDST_PEER_ROOT",
)


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个用例都从一个没有外部配置注入的进程环境开始。

    只摘「配置解析」读的那几个变量；端口/环境相关的 `LDST_PORT_OFFSET`、
    `LDST_SUPERVISOR_OWN_PROCESSES` 保留，因为 `tests/ops` 需要它们反映真实部署。
    """
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
