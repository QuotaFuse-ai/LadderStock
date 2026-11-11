# QUOT-545：stop_all 误杀 adopted peer 的停止边界修复

关联：QUOT-528（peer adopt-only 四路径验收，钉死本缺陷）、QUOT-519 / 523 / 527
（单入口与退出只停自有子进程）、DEC-014（只终止账本可证明的进程）、
DEC-078（本单决策：adopted_peer 停止边界）
状态：已交付，待验收

## 1. 目标

`SupervisorEngine.stop_all()` / `stop_module()` / 适配器 `stop()` 一律**不终止
外部 peer 认领的进程**，同时**保留崩溃恢复语义**——上一任 supervisor 遗留的
自有进程仍必须能被下一任 `stop_all` 停掉。缺陷来自 QUOT-528：两条证据链
（外部 peer 认领 vs 前任遗留自有进程）都落在 `origin=adopted`，停止路径无法区分。

验收：`tests/integration/test_peer_adopt_only.py` 全绿（去掉 `xfail(strict=True)`），
新增「前任 supervisor 进程仍可被 stop_all 停止」回归断言，PR + CI 双绿。

## 2. 实现

核心：给账本记录加一个**持久化布尔标记 `adopted_peer`**，把「账本证明不了是本
控制面拉的、按识别规则认领的既有实例」与「对账续托管的前任自有进程」分开。
停止路径只认这个标记，不看 `origin`、不看模块名。

| 改动 | 文件 | 说明 |
|---|---|---|
| 账本标记 | `supervisor/ledger.py` | `ModuleRecord` 新增 `adopted_peer: bool = False`；`from_dict` 读取（缺省 False，兼容旧账本）；`record_restart` 成功后清除（重拉 = 换绑到新 spawn 进程） |
| 停止闸门 | `supervisor/engine.py` | `_stop_module` 首行检查 `record.adopted_peer` → 直接返回原记录：不写停止意图、不改状态、不调适配器。`stop_all` 汇总里给被跳过的 peer 记 `skipped="adopted_peer"` |
| 标记置位 | `supervisor/engine.py` | `adopt_module` 证据认领分支置 `adopted_peer=True`；`_start_module` 真正 spawn 时清除。对账续托管（ledger-alive）分支不动 |
| 页面自启 | `supervisor/page_boot.py` | `_record_peer` 认领 peer 时置 `adopted_peer=True` |
| HTTP 汇总 | `web/supervisor_api.py` | `_stop_result` 把 `skipped` / `adopted_peer` 归入 `skipped` 而非 `stopped`；策略性跳过不算失败（`ok` 只在真异常时为 False） |

单一闸门落在 `_stop_module`，覆盖 CLI / HTTP / 退出钩子 / restart 全部停止路径；
适配器保持账本无感知（`_stop_targets` 机制不动，引擎层保证 peer 根本不会走到它）。

## 3. 边界

- **只跳过外部 peer 认领**，不是「跳过所有 adopted」：前任 supervisor 遗留的
  自有进程对账后 `origin=adopted` 但 `adopted_peer=False`，`stop_all` 照常终止；
- 不改端口白名单（`MODULE_PORTS` / `MODULE_ORDER` 原样）；
- 不改自愈 `stopping_by_user` 语义（peer 被跳过时根本不写停止意图）；
- 不碰生产 5950 / 8000 / 9100 / 9000 / 8100；
- 旧账本无 `adopted_peer` 字段 → 读为 False，不做启发式回填（宁可保守不误标）。

## 4. 验收证据

| 检查 | 结果 |
|---|---|
| `tests/integration/test_peer_adopt_only.py`（6 项，已去 xfail） | 全过 |
| `tests/unit/supervisor/test_adopted_peer_stop.py`（11 项，新增） | 全过 |
| `tests/integration/test_stop_all_ownership.py`（2 项，新增，真实进程） | 全过（Windows） |
| supervisor / web / cli 单元回归 | 全过 |
| `ruff check .` / `mypy` | 干净 |
| 全量 `pytest --ignore=tests/ops` | 3881 过 / 13 skip / 4 xpass（既有非严格兼容缺口）/ 1 失败（见下） |
| `tests/ops/*`（真实宿主生命周期） | 本机触发生产端口红线，未执行（沿用 QUOT-526 先例，CI ubuntu 亦 skip） |

全量唯一失败 `tests/test_senate.py::GetContractTest::test_status_lists_sixteen_roles_and_real_providers`
与本单无关：它断言 provider 表不含 `ollama`，而本机 `stock_senate` 解析到另一
workspace 的 editable 检出（`quot-536…/StockSenate`），其 `_DEFAULT_BASE_URLS` 含
`ollama`。本单未触碰任何 senate 文件；CI 从固定依赖安装、走内联镜像表，不受此
宿主环境污染影响。

关键回归断言（`test_stop_all_ownership.py`）：

- `test_previous_supervisor_spawned_process_is_still_stopped_by_stop_all`：
  engine_a spawn wire → 新会话 engine_b 对账为 `adopted`（无 peer 标记）→
  `stop_all` 把它停掉（state=stopped，stop_reason=stopped_by_request）；
- `test_stop_all_stops_owned_process_and_spares_adopted_peer`：
  外部 tickhub peer 认领 + wire 自有 spawn，一次 `stop_all`：wire 消失、
  peer 原样存活（pid 不变）、tickhub 记 `skipped`、账本仍 running/adopted_peer。

## 5. 已知取舍

- 选「持久化布尔标记」而非新增 `origin` 枚举或按模块名跳过：QUOT-528 钉死的
  验收要求 `origin=="adopted"` 保持不变，且 own-processes 模式下 peer 模块也可被
  本控制面 spawn（按模块名跳过会误伤崩溃恢复）。详见 DEC-078 的备选方案对比。
- 与 QUOT-527（PR #207，已合并）的 `stop_owned()` 分层共存：`stop_all` 跳 peer /
  停遗留 / 停 spawned；`stop_owned` 更严格（跳 peer / 跳遗留 / 只停本任 spawned）。
  本分支已合并 main，两个闸门落在不同 hunk，语义互不冲突。
- 真实生命周期用例（tests/ops）默认不跑：会起停生产端口，须运维在批准窗口显式
  开启；本单在单元 + 受控集成层证明停止边界与崩溃恢复。