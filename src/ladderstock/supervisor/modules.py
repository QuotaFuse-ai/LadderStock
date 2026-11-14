"""托管模块清单：模块名 → 端口，按环境（prod / test）分表（QUOT-593）。

正式环境端口取自 QUOT-482 派单正文的本机实测表（TickHub 8000/8787、Wire 8790/8791
等）；测试环境 = 正式 + 10000（18000 系），用户批准（QUOT-593 A 方案块2）。
正式/测试两套 supervisor 可并行运行，端口互不干扰。

`watch`（看门狗）不在用户批准的两张表里，但引擎与 MODULE_ORDER 依赖它：
QUOT-589 重修起无端口化——空元组，probe 靠 cmdline 扫描（WatchdogAdapter），
不占任何 HTTP 端口（用户裁决全机只留 5950/5951）。`proxypool` 是按需模块：
监听端口由
宿主配置 `Mappings` 决定，不在静态白名单里，登记为空元组（动态端口），
实际端口以 `ProxyPoolAdapter.ports` 为准，registry 对按需模块不做端口等值校验。

环境检测（`current_env`）：`LDST_ENV` 环境变量（prod|test，默认 prod）优先，
其次安装根 `config/env.yaml` 的 `env:` 键。对外统一走 `module_ports(env=None)`，
`MODULE_PORTS` 保留为 prod 表别名（向后兼容）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping

from ladderstock.cli.paths import config_dir

_ENV_PORTS: dict[str, dict[str, tuple[int, ...]]] = {
    "prod": {
        "tickhub": (8000, 8787),
        "tickhub_us": (8002,),
        "wire": (8790, 8791),
        "senate": (8765, 8786),
        "financecoffer": (8100,),
        "proxyfleet": (8788, 8789),
        # QUOT-589 重修：看门狗无端口化（用户裁决全机只留 5950/5951），
        # 不再监听 5952——空元组 = 无端口模块，probe 靠 cmdline 扫描
        # （WatchdogAdapter）。
        "watch": (),
        # 空元组 = 动态端口：按配置 Mappings 拉起，不在静态白名单内。
        "proxypool": (),
    },
    # QUOT-593：测试环境 = 正式 + 10000（用户批准）。
    "test": {
        "tickhub": (18000, 18787),
        "tickhub_us": (18002,),
        "wire": (18790, 18791),
        "senate": (18765, 18786),
        "financecoffer": (18100,),
        "proxyfleet": (18788, 18789),
        # QUOT-589 重修：同样无端口化，不占 15952。
        "watch": (),
        "proxypool": (),
    },
}

_VALID_ENVS = ("prod", "test")

class _EnvPortsMapping(Mapping[str, tuple[int, ...]]):
    """按当前环境实时解析的只读端口表（QUOT-593）。

    `MODULE_PORTS` 曾经是静态 dict；改成代理后，既有 `MODULE_PORTS[name]` /
    `.items()` / `in` 的调用方无需改动即拿到当前环境的表。注意 monkeypatch
    它不会生效——测试要覆盖端口请改用 `select_env()` 或 patch
    `modules._ENV_PORTS`。
    """

    def __getitem__(self, key: str) -> tuple[int, ...]:
        return module_ports()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(module_ports())

    def __len__(self) -> int:
        return len(module_ports())

    def __repr__(self) -> str:
        return f"<EnvPortsMapping env={self.env} {dict(self)}>"

    @property
    def env(self) -> str:
        return _active_env or current_env()


#: 向后兼容别名（QUOT-482 prod 口径 → QUOT-593 起按当前环境实时解析）。
MODULE_PORTS: Mapping[str, tuple[int, ...]] = _EnvPortsMapping()

#: 当前生效环境；经 select_env 切换（进程内默认按 current_env() 解析一次）。
_active_env: str = ""


def current_env() -> str:
    """检测当前环境：`LDST_ENV`（prod|test）优先，其次安装根 `config/env.yaml`。

    显式 `select_env()` 优先，其次 `LDST_ENV`，再次 env.yaml，默认 prod。
    配置读取失败（无 config 目录 / YAML 损坏）同样回落 prod。
    """
    if _active_env:
        return _active_env
    value = os.environ.get("LDST_ENV", "").strip().lower()
    if value in _VALID_ENVS:
        return value
    try:
        import yaml

        path = config_dir() / "env.yaml"
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            env = str(data.get("env", "")).strip().lower()
            if env in _VALID_ENVS:
                return env
    except Exception:
        pass
    return "prod"


def select_env(env: str) -> None:
    """进程内显式指定当前环境（优先于自动检测；测试注入用）。"""
    global _active_env
    normalized = env.strip().lower()
    if normalized not in _VALID_ENVS:
        raise ValueError(f"未知环境 {env!r}，仅支持 {'/'.join(_VALID_ENVS)}")
    _active_env = normalized


def reset_env() -> None:
    """清除进程内显式指定，回到自动检测（测试隔离用）。"""
    global _active_env
    _active_env = ""


def module_ports(env: str | None = None) -> dict[str, tuple[int, ...]]:
    """返回指定环境的端口表；缺省 = 当前环境。

    返回副本——调用方改它不会污染内部表。
    """
    resolved = (env or _active_env or current_env()).strip().lower()
    if resolved not in _VALID_ENVS:
        raise ValueError(f"未知环境 {env!r}，仅支持 {'/'.join(_VALID_ENVS)}")
    return dict(_ENV_PORTS[resolved])


#: 看板（主服务）端口，用户批准的双端口裁决（QUOT-589 第二轮）：prod=5950 /
#: test=5951。与 `_ENV_PORTS` 同处登记，是面板/看门狗/自愈引擎唯一的端口事实源。
_PANEL_PORTS: dict[str, int] = {"prod": 5950, "test": 5951}


def panel_port(env: str | None = None) -> int:
    """返回指定环境的看板端口（prod=5950 / test=5951）；缺省 = 当前环境。"""
    resolved = (env or _active_env or current_env()).strip().lower()
    if resolved not in _VALID_ENVS:
        raise ValueError(f"未知环境 {env!r}，仅支持 {'/'.join(_VALID_ENVS)}")
    return _PANEL_PORTS[resolved]


__all__ = [
    "MODULE_PORTS",
    "module_ports",
    "panel_port",
    "current_env",
    "select_env",
    "reset_env",
]
