"""便携包启动期的端口自动选择（用户零配置）。

正式 / 测试双环境并行靠固定端口段区分（5950 系 · 18000 系，见
`ladderstock.core.boundary.PORTS_OFFSET_ENV`），那是**开发机**的约定；
便携包在用户机器上只有一个实例，不该让用户因为「端口被占」去改配置。

启动器的做法：先探测一个空闲基端口，再用 `offset_for()` 算出
`LDST_PORT_OFFSET`，由既有平移机制一次性派生全部 peer 端口——用户只需要
双击，不需要知道任何端口号。

只做端口探测，不负责启动进程：把选择结果交给 `LDST_PORT_OFFSET` 与
`--port <base>` 即可，正式服行为不受影响（offset=0 时全链路原样）。
"""

from __future__ import annotations

import socket
from collections.abc import Callable

#: 基端口首选值（与正式服默认一致）；被占时按步长后移。
DEFAULT_BASE_PORT = 5950

#: 后移步长：留出间隙，避免与同机其他服务贴在一起。
PORT_STEP = 10

#: 最多探测多少个候选端口，超过就报错让人看见（不静默乱选）。
MAX_ATTEMPTS = 60

#: 偏移的参考端口：`LDST_PORT_OFFSET` 是相对它算的（正式服 offset=0）。
REFERENCE_PORT = 5950


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """能否在本机 bind 上该端口。

    只做 bind 探测，不 listen、不留监听态；**不设 SO_REUSEADDR**——在 Windows 上
    该选项会让「已别进程占用」的端口也绑定成功，探测就失效了。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_base_port(
    preferred: int = DEFAULT_BASE_PORT,
    *,
    probe: Callable[[int], bool] | None = None,
    step: int = PORT_STEP,
    attempts: int = MAX_ATTEMPTS,
) -> int:
    """从 `preferred` 起按 `step` 后移，返回第一个空闲的基端口。

    `probe` 可注入（单测与「让用户手工钉住端口」的路径都用它）；默认走真实 bind。
    候选全部被占时抛 `RuntimeError` 而不是回落到 0——静默选一个不可用端口
    只会把问题推到几分钟后的启动失败。
    """
    check = probe or port_is_free
    last = preferred + step * (attempts - 1)
    for port in range(preferred, last + 1, step):
        if check(port):
            return port
    raise RuntimeError(
        f"端口 {preferred}–{last}（每 {step} 一个）全部被占用，无法为便携包选基端口"
    )


def offset_for(base_port: int, *, reference: int = REFERENCE_PORT) -> int:
    """基端口 → `LDST_PORT_OFFSET`；小于参考端口时钳到 0（与 `port_offset()` 同语义）。"""
    delta = base_port - reference
    return delta if delta > 0 else 0
