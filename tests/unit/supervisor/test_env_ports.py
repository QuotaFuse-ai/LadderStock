"""QUOT-593：MODULE_PORTS 环境维度测试。

覆盖：
- prod / test 两张端口表的内容正确性（用户批准的 +10000 口径）；
- 环境检测：LDST_ENV 优先、config/env.yaml 兜底、默认 prod、非法值拒；
- select_env / reset_env 进程内切换与恢复；
- prod/test 端口零碰撞（双环境可并行）；
- module_registry 按当前环境校验（test 表下 prod 适配器被拒，反之亦然）。
"""

from __future__ import annotations

import pytest
import yaml

from ladderstock.supervisor import adapters as adapters_module
from ladderstock.supervisor import modules as modules_module
from ladderstock.supervisor.adapters import MODULE_ORDER, module_registry
from ladderstock.supervisor.modules import (
    MODULE_PORTS,
    current_env,
    module_ports,
    reset_env,
    select_env,
)

_PROD_EXPECTED = {
    "tickhub": (8000, 8787),
    "tickhub_us": (8002,),
    "wire": (8790, 8791),
    "senate": (8765, 8786),
    "financecoffer": (8100,),
    "proxyfleet": (8788, 8789),
    # QUOT-589 重修：看门狗无端口化（全机只留 5950/5951），不占 5952。
    "watch": (),
    "proxypool": (),
}
_TEST_EXPECTED = {
    "tickhub": (18000, 18787),
    "tickhub_us": (18002,),
    "wire": (18790, 18791),
    "senate": (18765, 18786),
    "financecoffer": (18100,),
    "proxyfleet": (18788, 18789),
    # 同上：测试环境亦无端口，不占 15952。
    "watch": (),
    "proxypool": (),
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LDST_ENV", raising=False)
    reset_env()
    yield
    reset_env()


def _fake_adapters(ports_table: dict[str, tuple[int, ...]]):
    class _A:
        def __init__(self, name: str, ports: tuple[int, ...]) -> None:
            self.name = name
            self.ports = ports

    return {
        name: _A(name, ports_table[name])  # type: ignore[misc]
        for name in MODULE_ORDER
        if name != "watch"
    } | {"watch": _A("watch", ports_table["watch"])}


class TestEnvTables:
    def test_prod_table_matches_approved_ports(self):
        assert module_ports("prod") == _PROD_EXPECTED

    def test_test_table_matches_approved_ports(self):
        assert module_ports("test") == _TEST_EXPECTED

    def test_test_is_prod_plus_10000_for_business_modules(self):
        for name in ("tickhub", "tickhub_us", "wire", "senate", "financecoffer", "proxyfleet"):
            assert module_ports("test")[name] == tuple(
                p + 10000 for p in module_ports("prod")[name]
            )

    def test_no_cross_env_collision(self):
        prod_ports = {p for ports in module_ports("prod").values() for p in ports}
        test_ports = {p for ports in module_ports("test").values() for p in ports}
        assert not prod_ports & test_ports

    def test_module_keys_identical_across_envs(self):
        assert set(module_ports("prod")) == set(module_ports("test")) == set(MODULE_PORTS)

    def test_returned_dict_is_copy(self):
        table = module_ports("prod")
        table["tickhub"] = (1,)
        assert module_ports("prod")["tickhub"] == (8000, 8787)

    def test_module_order_unchanged(self):
        assert MODULE_ORDER == (
            "tickhub",
            "tickhub_us",
            "wire",
            "senate",
            "financecoffer",
            "proxyfleet",
            "watch",
        )


class TestEnvDetection:
    def test_default_prod(self):
        assert current_env() == "prod"
        assert module_ports()["tickhub"] == (8000, 8787)

    def test_ldst_env_test(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LDST_ENV", "test")
        assert current_env() == "test"
        assert module_ports()["tickhub"] == (18000, 18787)

    def test_ldst_env_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("LDST_ENV", "staging")
        monkeypatch.setattr(modules_module, "config_dir", lambda: tmp_path, raising=False)
        assert current_env() == "prod"

    def test_ldst_env_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LDST_ENV", " TEST ")
        assert current_env() == "test"

    def test_env_yaml_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("LDST_ENV", raising=False)
        (tmp_path / "env.yaml").write_text(
            yaml.safe_dump({"env": "test"}), encoding="utf-8"
        )
        monkeypatch.setattr(modules_module, "config_dir", lambda: tmp_path, raising=False)
        assert current_env() == "test"

    def test_env_yaml_broken_falls_back_prod(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("LDST_ENV", raising=False)
        (tmp_path / "env.yaml").write_text("env: [unclosed", encoding="utf-8")
        monkeypatch.setattr(modules_module, "config_dir", lambda: tmp_path, raising=False)
        assert current_env() == "prod"

    def test_ldst_env_beats_env_yaml(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("LDST_ENV", "prod")
        (tmp_path / "env.yaml").write_text(
            yaml.safe_dump({"env": "test"}), encoding="utf-8"
        )
        monkeypatch.setattr(modules_module, "config_dir", lambda: tmp_path, raising=False)
        assert current_env() == "prod"


class TestSelectEnv:
    def test_select_env_switches_table(self):
        select_env("test")
        assert module_ports()["tickhub"] == (18000, 18787)
        assert current_env() == "test"

    def test_select_env_rejects_unknown(self):
        with pytest.raises(ValueError):
            select_env("staging")

    def test_reset_env_returns_to_detection(self):
        select_env("test")
        reset_env()
        assert current_env() == "prod"

    def test_module_ports_env_arg_wins_over_selected(self):
        select_env("test")
        assert module_ports("prod")["tickhub"] == (8000, 8787)

    def test_legacy_proxy_mapping_follows_env(self):
        select_env("test")
        assert MODULE_PORTS["tickhub"] == (18000, 18787)
        select_env("prod")
        assert MODULE_PORTS["tickhub"] == (8000, 8787)


class TestRegistryValidation:
    def test_registry_accepts_current_env_ports(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LDST_ENV", "prod")
        module_registry(_fake_adapters(module_ports("prod")))  # type: ignore[arg-type]
        monkeypatch.setenv("LDST_ENV", "test")
        module_registry(_fake_adapters(module_ports("test")))  # type: ignore[arg-type]

    def test_registry_rejects_prod_adapters_under_test_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LDST_ENV", "test")
        with pytest.raises(ValueError, match="端口"):
            module_registry(_fake_adapters(module_ports("prod")))  # type: ignore[arg-type]

    def test_registry_rejects_test_adapters_under_prod_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("LDST_ENV", "prod")
        with pytest.raises(ValueError, match="端口"):
            module_registry(_fake_adapters(module_ports("test")))  # type: ignore[arg-type]

    def test_registry_env_attr_resolves_current_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LDST_ENV", "test")
        assert adapters_module.MODULE_PORTS["tickhub"] == (18000, 18787)
        monkeypatch.setenv("LDST_ENV", "prod")
        assert adapters_module.MODULE_PORTS["tickhub"] == (8000, 8787)
