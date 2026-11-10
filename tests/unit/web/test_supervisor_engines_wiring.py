"""生产装配（QUOT-526）：冷启动只在单入口模式开启，默认附加模式保持 adopt-only。

- `LDST_SUPERVISOR_OWN_PROCESSES` 未设 / 0：自愈只看账本旧口径（`modules=None`），
  restarter 不 spawn —— peer 绝不被自动拉起；
- 设为 1：托管 `MODULE_ORDER` 全表 + `cold_start=True`，restarter 可按 bundle
  命令表拉起缺的模块；
- 自愈与顺序启停共用同一本账。

适配器装配整体打桩：不碰真实进程 / 端口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER, MODULE_PORTS, module_registry
from ladderstock.supervisor.modules import MODULE_PORTS as PORTS
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.web import supervisor_api


class _NullAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ports = PORTS.get(name, ())

    def start(self):  # pragma: no cover - 装配测试不应拉起
        raise AssertionError(f"{self.name} 不应被启动")

    def stop(self) -> None:  # pragma: no cover
        pass

    def kill(self) -> None:  # pragma: no cover
        pass

    def probe(self) -> bool:
        return False


@pytest.fixture
def stub_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = module_registry(
        {name: _NullAdapter(name) for name in (*MODULE_ORDER, "proxypool")}  # type: ignore[dict-item]
    )
    monkeypatch.setattr(supervisor_api, "build_default_adapters", lambda **_kwargs: registry)


def test_attach_mode_keeps_ledger_only_self_heal(tmp_path: Path, stub_adapters: None) -> None:
    engines = supervisor_api.build_supervisor_engines(
        tmp_path, panel_url="http://127.0.0.1:5950/", env={}
    )

    heal = engines.heal
    assert heal.modules is None
    assert heal.cold_start is False
    assert isinstance(heal.restarter, AdapterRestarter)
    assert heal.restarter.spawn is False
    assert heal.ledger is engines.ledger


def test_single_entry_mode_enables_cold_start(tmp_path: Path, stub_adapters: None) -> None:
    engines = supervisor_api.build_supervisor_engines(
        tmp_path,
        panel_url="http://127.0.0.1:5950/",
        env={supervisor_api.OWN_PROCESSES_ENV: "1"},
    )

    heal = engines.heal
    assert heal.modules == MODULE_ORDER
    assert heal.cold_start is True
    assert isinstance(heal.restarter, AdapterRestarter)
    assert heal.restarter.spawn is True
    assert engines.processes._ledger is engines.ledger


def test_port_table_matches_registered_modules() -> None:
    assert set(MODULE_PORTS) >= set(MODULE_ORDER)
