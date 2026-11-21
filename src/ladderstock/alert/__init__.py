"""提醒引擎（DEC-087）：自定义规则 × 出站渠道 × 收件箱留痕。

模块划分（三层，边界一句话）：

- `rules` —— 规则类型与参数口径（纯函数）；
- `store` —— `data/alerts.sqlite` 的全部读写（rules / state / history / channels）；
- `channels` —— 出站投递（console / desktop / email / webhook / 三家 IM）；
- `evaluate` —— 求值与「越线报一次」状态机（纯函数）；
- `run` —— 一次执行体（CLI / Web / 调度三面共享）。
"""

from ladderstock.alert import channels, evaluate, rules, run, store

__all__ = ["channels", "evaluate", "rules", "run", "store"]
