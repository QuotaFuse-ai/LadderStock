# DEC-074 回测触发面为参数化写接口单独开例外（QUOT-535）

日期：2026-11-10 · 状态：生效（随 QUOT-535 实现落地） · 关联：QUOT-535、QUOT-500、QUOT-534、
DEC-014（控制面 / 数据面分离、写接口 argv 只读白名单）、DEC-049（数据面与模型面拆分）、
`src/ladderstock/web/backtest.py`、`src/ladderstock/backtest/runner.py`

## 1. 背景

DEC-014 的边界是「控制面写接口不接受请求参数」：控制面只暴露**常量 argv** 的动作
（安装 / 启停 / 配置），避免网页把任意子命令或任意参数拼给本机 CLI。QUOT-500 因此只开放了
6 个只读运行卡端点，写面留给本机 CLI `ldst backtest run`。

QUOT-534（回测页面）验收时实测到契约缺口：页面必须能**用表单收集的策略 / 标的 / 区间 / 成本**
触发一次回测，只读端点无法满足；而 `backtest/__init__.py` 的设计注释本来就写了
「`ldst backtest run` 与 `POST /api/backtest/run` 都走 `run_backtest`」。用户已拍板补后端端点。

## 2. 决策

给回测**单独**开一条有参写面，其余控制面写接口的边界不变：

| 端点 | 语义 |
|---|---|
| `GET /api/backtest/strategies` | 策略清单 + 成本默认值 + 默认标的（纯读，数据源是后端常量） |
| `POST /api/backtest/run` | 提交一次回测，`202 {job_id, status:"queued"}`，异步执行 |
| `GET /api/backtest/jobs/{job_id}` | `{status, run_id, gate_passed, exit_code}`；`status ∈ queued/running/ok/failed` |

三条约束让「有参」不突破 DEC-014 的原始目的：

1. **参数不拼命令行**。请求体是结构化字段（策略名 / 策略参数 / 标的 / 日期 / 成本覆盖），
   进程内直接调用 `run_backtest(...)`，**不经过 shell、不拼接 argv、不引入新的子命令**；
   执行路径与 CLI 完全同源（同一 runner / 同一 guard / 同一运行卡落盘）。
2. **取值域有白名单**。策略名必须在 `strategy.BUILTIN` 内；策略参数先构造策略对象校验；
   标的必须过 `universe.classify`（M1 只允许主板 / 创业板普通股）；日期必须 `YYYY-MM-DD`
   且 `start <= end`；成本覆盖走 `BacktestConfig.with_overrides` 的严格校验。
   任一不合法 → **400 + 结构化 `{error, message}`**，不入队；请求体形状不对 → 422。
3. **闸门与现有写面同口径**。回环来源 + `Host` 白名单 + 正向来源证据（本机页面 `Origin`
   或 `X-LDST-Client: 1`）+ `Sec-Fetch-Site ∈ {same-origin, none}`；不开放公网。
   写面只落本机 `data/backtests/<run_id>/`，不触碰 peer / 生产端口。

**退出码映射**沿用 CLI：`0` 校验门通过、`8` 校验门未通过（任务仍算成功，运行卡标红）、
`2` 参数非法、`3` 配置非法、`4` 本机数据缺失。异步任务把这些码放进
`jobs/{id}.exit_code`，失败原因放 `error`。

## 3. 为什么不选其它方案

| 方案 | 否决理由 |
|---|---|
| 前端直接调 CLI 子进程 | 等于把任意 argv 暴露给网页，正是 DEC-014 要禁止的 |
| 新增 `ldst backtest run --json-body` 之类通用透传 | 命令白名单被扩成「任意参数」，边界更差；且依然要过 shell |
| 只读端点 + 前端预先枚举参数组合 | 参数组合是连续空间（策略参数 × 区间 × 成本），枚举不现实 |
| 同步 HTTP 跑完再回（不开异步） | 一次回测要重跑样本内外 + 三档成本，秒级到十秒级；会占满请求线程 |

## 4. 未改动的边界

- 其余 `/api/control/*` 写接口仍是常量 argv，不接受请求参数（DEC-014 原样）。
- `POST /api/backtest/run` 不提供「任意命令 / 任意脚本 / 任意配置文件路径」入口；
  配置文件路径由服务端按 `config_dir()` 解析，请求体只能覆盖白名单字段。
- 没有新增 CLI 命令，`tests/fixtures/command_matrix.py` 的 `LEAF_PATHS` / `JSON_PATHS` /
  `DOCUMENTED_GROUPS` 不变。
- 页面仍只读运行卡，不重算任何指标；本次只补「触发 + 查状态」。
