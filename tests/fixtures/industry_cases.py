"""R5 产业研究夹具（`tests/fixtures/industry/I*.json`）的读取与注入，供单测共用。

夹具三段：`config`（可落盘的 `industry.yaml` 文档）、`datasets`（六个输入数据集的缓存
记录）、`expected`（按 `docs/specs/SPEC-INDUSTRY-METRICS.md` 手算的期望值）。

写库走 `duckdb_store.write_dataset`（与 `fixtures/pool` 同一做法），模式由
`boundary.DATASET_CATALOG` 决定：`fund_flow` / `industry_fund_flow` 是 replace，
其余 append——登记不匹配时 store 会直接拒绝，所以这里不传 `mode`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ladderstock.data import duckdb_store

FIXTURES = Path(__file__).resolve().parent / "industry"
CASES = sorted(FIXTURES.glob("I*.json"))
IDS = [path.stem for path in CASES]

#: 板块资金流的上游标签：缓存行里的 `_source` 就是它，板块口径名由它拼出来
CACHE_SOURCE = {"industry_fund_flow": "eastmoney"}
DEFAULT_SOURCE = "golden"


def load_case(name: str) -> dict[str, Any]:
    """按文件名读一份夹具，并钉住「数据集集合 = 快照输入集合」。"""
    case: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    from ladderstock.industry import snapshot as snapshot_module

    assert set(case["datasets"]) == set(snapshot_module.SNAPSHOT_DATASETS), (
        f"{name}：夹具数据集必须与快照输入数据集一一对应"
    )
    return case


def write_cache(case: dict[str, Any], root: Path) -> None:
    """把夹具写进本机缓存；空记录不落库，保持「缓存缺失」语义。"""
    for dataset, rows in case["datasets"].items():
        if not rows:
            continue
        duckdb_store.write_dataset(
            dataset,
            rows,
            source=CACHE_SOURCE.get(dataset, DEFAULT_SOURCE),
            root=root,
        )


def write_config(case: dict[str, Any], root: Path) -> None:
    """夹具里的 `config` 段原样落成 `industry.yaml`，交给真加载器校验。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "industry.yaml").write_text(
        yaml.safe_dump(case["config"], allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def prepare(case: dict[str, Any], tmp_path: Path) -> tuple[Path, Path]:
    """返回 `(数据目录, 配置目录)`，两者都落在本次测试的 tmp 里。"""
    data = tmp_path / "data"
    config = tmp_path / "config"
    write_cache(case, data)
    write_config(case, config)
    return data, config


def dimensions_leaf(payload: dict[str, Any], path: str) -> Any:
    """`notes` / `reasons` 的取值路径：行业与归属段从 payload 根走，其余进 `dimensions`。"""
    parts = path.split(".")
    node: Any = payload if parts[0] in ("industry", "membership") else payload["dimensions"]
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise AssertionError(f"快照里没有这条路径：{path}")
        node = node[part]
    return node
