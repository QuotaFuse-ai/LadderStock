# QUOT-526：自愈冷启动改用 bundle 命令表

关联：QUOT-519（伞单第 5 根柱子）、QUOT-491（482-C 自愈）、QUOT-523（单入口）、
DEC-069（崩溃自愈）、DEC-071（页面自启 / peer adopt-only）、DEC-075（本单决策）
状态：已交付，待验收

## 1. 目标

`SelfHealEngine` 冷启动能从 **bundle 自带命令表**拉起未 ledger 模块（原状：空的
`restart-commands.json` 只能重拉已 ledger、且实际无通道），与 supervisor 账本一致。
验收：全停后冷启自愈把自有模块全拉起；peer 不碰；生命周期测试。

## 2. 实现

| 改动 | 文件 | 说明 |
|---|---|---|
| 认领既有实例 | `supervisor/engine.py` | 新增 `adopt_module()`：适配器 `identify()` 命中才记账（`origin=adopted`，pid 不变），失败关闭、不 spawn / 不 kill |
| bundle 命令表重拉 | `supervisor/restart.py` | 新增 `AdapterRestarter`：先认领、再按适配器启动面拉起；`spawn=False` 时只认领（附加模式） |
| 冷启动 | `supervisor/selfheal.py` | 新增 `modules=` / `cold_start=`；账本无 pid 的托管模块立刻拉一次（delay 0），失败后走 5s→15s→45s；`RestartOutcome.adopted` 不记重拉次数 |
| 主动停语义 | `supervisor/engine.py` | `_stop_module` 落 `stopping_by_user=True`，显式 `start` 清除——接上真通道后停止不会被 5s 后自愈撤销 |
| 生产装配 | `web/supervisor_api.py` | 用 `AdapterRestarter` 取代 `CommandTableRestarter`；`LDST_SUPERVISOR_OWN_PROCESSES=1` 时 `modules=MODULE_ORDER + cold_start=True`，默认附加模式只认领 |
| 共用账本 | `web/server.py` | `build_page_boot(root, ledger=engines.ledger)`，避免 PageBoot 与自愈两份内存视图互相覆盖 |

## 3. 边界

- **默认附加模式 peer 仍 adopt-only**：`AdapterRestarter(spawn=False)`，已在跑的
  认领、没跑的不拉起、外部占口不终止；
- 端口被非本模块进程占用 → 启动失败关闭（DEC-004 / DEC-014），连续 3 次进
  `needs_human`，等人工 reset；
- 主动 `stop_all` / 面板停止不会被自愈撤销；
- 冷启动首轮 0s，首轮失败后的第二次等待按「第 2 次尝试」为 15s（退避按尝试次数计）。

## 4. 验收证据

| 检查 | 结果 |
|---|---|
| `tests/unit/supervisor/test_cold_start.py`（9 项） | 全过 |
| `tests/unit/supervisor/test_adopt_and_restarter.py`（10 项） | 全过 |
| `tests/unit/web/test_supervisor_engines_wiring.py`（3 项） | 全过 |
| `tests/unit/supervisor` + `tests/unit/web` + `tests/unit/cli` 回归 | 全过 |
| `ruff check src tests` / `mypy src` | 干净 |
| `tests/ops/test_lifecycle_selfheal_cold_start.py`（真实宿主） | 本机触发生产端口红线，未执行；需 `LDST_SELFHEAL_COLD_START=1` 在批准的已全停部署机运行 |

## 5. 已知取舍

- 真实生命周期用例默认不跑：它会起停 8000 等模块端口，必须由运维在批准的切换窗口
  显式开启；本单只在单元层证明冷启动 / 认领 / 退避 / 端口归属闸门。
- `CommandTableRestarter` 保留但不再接线；如后续有部署侧自定义命令表需求，可继续
  通过 `SelfHealEngine(restarter=...)` 注入。
