"""R5 产业研究：申万一级行业五维快照（QUOT-143）。

包内分工：

- `catalog.py`：`config/industry.yaml` 的行业清单与口径参数；
- `metrics.py`：**纯函数**公式层（无 I/O，可手算复核，金标准夹具在
  `tests/fixtures/industry/`）；
- `snapshot.py`：`industry_snapshot()` 装配层——只读本机缓存回放
  （`data/archive.py`），不发起任何网络请求，与 `core/pool.py` 同一条读取路径。

口径文件：`docs/specs/SPEC-INDUSTRY-METRICS.md`。缺数据一律显式 `null` +
`unverified`，不估算、不插值、不用模型补（任务硬约束）。
"""

from ladderstock.industry.catalog import IndustryCatalog, IndustryEntry, load_catalog
from ladderstock.industry.snapshot import DIMENSIONS, industry_snapshot

__all__ = [
    "DIMENSIONS",
    "IndustryCatalog",
    "IndustryEntry",
    "industry_snapshot",
    "load_catalog",
]
