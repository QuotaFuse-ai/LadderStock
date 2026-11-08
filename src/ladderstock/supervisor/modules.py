"""托管模块清单：模块名 → 端口（QUOT-491 / 482-C 对外接口的骨架来源）。

端口取自 QUOT-482 派单正文的本机实测表（TickHub 8000/8787、Wire 8790/8791 等）。
本清单**只描述「哪些模块归自愈管」**，不含任何启动/停止顺序——顺序是 482-A 的
职责；也不含启动命令——那是 482-B 的职责。
"""

from __future__ import annotations

# 声明顺序即面板展示顺序；这里刻意不表达依赖顺序。
MODULE_PORTS: dict[str, tuple[int, ...]] = {
    "tickhub": (8000, 8787),
    "tickhub_us": (8002,),
    "wire": (8790, 8791),
    "senate": (8765, 8786),
    "financecoffer": (8100,),
    "proxyfleet": (8788, 8789),
}

__all__ = ["MODULE_PORTS"]
