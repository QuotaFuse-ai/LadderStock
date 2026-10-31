"""AI 周报（R11）：把一周的每日复盘产物聚合成周度事实包，再让 AI 只写叙述。

- `week.py`     —— ISO 周标签 `YYYY-Www` 的解析与日期换算（纯函数，无时钟）；
- `facts.py`    —— 确定性周度事实包（涨跌分布 / 情绪温度序列 / 连板高度变化 /
                    主线题材持续天数 / 晋级率趋势 / 待验证项结算），全部复用每日复盘
                    那一套计算函数，不复制公式；
- `checkpoint.py` —— 周度五段编排的断点续跑状态：复用 `ai/checkpoint.py` 状态机，只换落盘布局；
- `pipeline.py` —— 事实包 → 五段 AI 叙述（`core/boundary.merge_narrative`）→
                    `data/weekly/<week>.json` + `<week>.md`；模型失败时降级为事实包 + 模板。

边界与每日复盘同一条（DEC-006）：数字只由程序算，AI 只追加叙述，数据只落本机。
口径文档：`docs/specs/SPEC-WEEKLY.md`。
"""
