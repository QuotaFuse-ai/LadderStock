"""生态入站适配器：把三个 peer 的返回统一映射为 `LadderEnvelope`。

约定：**只有这里能把外部数据变成 LadderStock 的内部结构**。
新增数据入口必须落在这个包里，并登记到 `core.boundary.DATASET_CATALOG`。
"""

from ladderstock.data.adapters.senate import from_data_envelope
from ladderstock.data.adapters.tickhub import from_tool_response
from ladderstock.data.adapters.wire import from_wire_envelope

__all__ = ["from_tool_response", "from_wire_envelope", "from_data_envelope"]
