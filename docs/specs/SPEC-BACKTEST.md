# SPEC-BACKTEST 回测 M1 口径（A 股日频策略回测最小闭环）

- 日期：2026-11-10
- 归属：QUOT-500（M1，单标的日频，最小闭环）
- 实现：`src/ladderstock/backtest/`
  - `feed.py`（读本机 `daily_k` → `BarSeries`）、`universe.py`（主板 / 创业板与涨跌停幅度）、
    `strategy.py`（3 个内置策略）、`match.py`（A 股撮合引擎）、`score.py`（指标，调 calc 层）、
    `guard.py`（校验门）、`card.py`（运行卡渲染）、`config.py`（配置加载与覆盖）、
    `runner.py`（唯一入口：CLI 与 Web 共用）
  - CLI：`src/ladderstock/cli/backtest.py`（`ldst backtest run|list|show`，见 `docs/CLI.md` §M）
  - HTTP：`src/ladderstock/web/backtest.py`（`/api/backtest/*`：只读运行卡 + QUOT-535 触发面）
- 配置：`config/backtest.yaml`（成本 / 校验门默认值）+ `config/schema/backtest.schema.json`（形状）
- 测试：`tests/unit/backtest/`（A 股规则 / 无未来函数 / run_id 复现 / 成本三档 / 配置校验 / 运行卡契约）
- 红线：不复制参考库代码；默认成本非 0；**不允许未来函数**；校验门失败必须标红

## 0. 一句话

给定「一个标的 + 一段时间 + 一个策略 + 一套成本」，按 A 股的成交规则逐日撮合，
产出一张**可复现**、**每项指标都带 `calc_id`**、**校验门不过就标红**的运行卡
（JSON / Markdown / HTML），前端只读这张卡、不重算。

## 1. 输入

### 1.1 数据

- 只读本机历史库 `<data_dir>/market.duckdb` 的 `daily_k`（`adjust=qfq`），不联网、不采集。
- 读入后形成 `BarSeries`：`bars` / `data_fp` / `first_date` / `last_date` / `adjust`。
- M1 单标的：默认真机验证用 `600000`，≥2 年日线（641 根，2024-03-14 ~ 2026-11-05）。

### 1.2 策略（信号只看到 `t` 及之前）

| 名称 | 参数 | 规则 |
|---|---|---|
| `ma_cross` | `short_window=5`、`long_window=20` | 短均线上穿长均线 → 1，下穿 → 0 |
| `momentum` | `lookback=20`、`threshold=0.0` | `close[t]/close[t−N]−1 > threshold` → 1，否则 0 |
| `breakout` | `lookback=20` | `close[t]` 超过**不含 t** 的前 N 日最高 → 1；跌破前 N 日最低 → 0 |

**协议**：策略只输出**目标持仓** `[0,1]`，`history = bars[:t+1]`；不给成交价、不给成交时点。
历史不足窗口时一律空仓（不用未来数据补）。

### 1.3 成本与参数（默认值在配置文件，不在代码常量）

`config/backtest.yaml`（QUOT-535 起对接真实券商，单位写进字段名）：

```yaml
costs:
  commission_rate_wan: 0.9        # 佣金率（万分之），双向
  commission_floor_yuan: 0.01     # 单笔最低佣金（元）：5=不免五；0.01/1/0=免五或自定义起收
  transfer_fee_wan: 0.1           # 过户费（万分之），双向（现行官方口径）
  stamp_duty_wan: 5               # 印花税（万分之），仅卖出（2023-08-28 起千1减半）
  slippage: 0.001                 # 滑点（千1）
  tiers: [0.0002, 0.0005, 0.001]  # 成本敏感性三档（小数口径，只替换佣金率）
  presets:                        # 用户券商三档（均不含过户费，过户费另加）
    - {key: wan09_free5_001, label: 万0.9 免五 0.01 元起收, commission_rate_wan: 0.9,  commission_floor_yuan: 0.01}
    - {key: wan085_free5_1,  label: 万0.85 免五 1 元起收,    commission_rate_wan: 0.85, commission_floor_yuan: 1}
    - {key: wan065_min5,     label: 万0.65 不免五 5 元起收,  commission_rate_wan: 0.65, commission_floor_yuan: 5}
gate:
  min_trades: 20            # 校验门：最小完整交易次数
  in_sample_ratio: 0.7      # 校验门：样本内占比（按时间顺序）
```

- 单笔佣金 = `max(成交额 × commission_rate, commission_floor_yuan)`；买入成本再叠加过户费，
  卖出成本再叠加过户费与印花税。印花税**只对卖出**、过户费**双边**。
- **默认成本必须非 0**；佣金 / 滑点 / 印花税为 0 → 配置错误（退出码 2），不静默用默认值顶上。
- CLI / HTTP 覆盖只做**覆盖**；运行卡 `config` 记「本次实际生效值」，`config_sources` 逐项记来源
  （`yaml` / `cli` / `fallback`）。旧小数口径键（`commission` / `stamp_duty`）仍可读，
  按 `×10000` 换算为万分之口径，最低佣金与过户费记为 0（旧撮合口径）。
- 非法配置（负数、滑点 0、`in_sample_ratio` 不在 `(0,1)`、三档不升序或不足 2 档、
  `min_trades` 非正整数、未知覆盖键、NaN/inf、bool 冒充数值、presets 缺字段）**一律报错**。

## 2. 撮合（`match.py`）

| 项 | 规则 |
|---|---|
| 信号 / 成交时点 | 信号在 `t` 收盘产生，**`t+1` 开盘价 ± 滑点**成交 |
| 买入价 / 卖出价 | `open·(1+slippage)` / `open·(1−slippage)` |
| 佣金 | 双边，单笔 `max(notional·commission, commission_floor)` |
| 过户费 | 双边，`notional·transfer_fee`（默认万 0.1） |
| 印花税 | 仅卖出，`notional·stamp_duty`（默认万 5） |
| 涨停买不进 | `round(open, 2) >= limit_up` → 当日买单拒绝 |
| 跌停卖不出 | `round(open, 2) <= limit_down` → 当日卖单拒绝 |
| 停牌跳过 | `volume <= 0` 当日不成交 |
| T+1 | 当日买入份额当日不可卖；`want_buy` / `want_sell` 互斥（同根不做来回） |
| 涨跌停幅度 | 主板 10%、创业板 20%；基准价 `prev_close`，限价四舍五入到 0.01 |
| 最小单位 | 100 股整手 |

无未来函数由 `tests/unit/backtest/test_lookahead.py` 证伪：`TomorrowOracle`（偷看明天）
在真实成本下累计收益为负、且跑输买入持有；零成本下也 `<= 0`。信号与成交至少隔一根 K 线。

每条 A 股规则一条测试（`tests/unit/backtest/test_ashare_rules.py`）：
涨停买不进 / 跌停卖不出 / 停牌跳过 / T+1（另含「未触限可成交」「停牌不动持仓」「创业板 20%」）。

## 3. 指标（`score.py`，调 calc 层）

11 项：`cumulative_return`、`annualized_return`、`max_drawdown`、`sharpe`、`calmar`、
`win_rate`、`profit_loss_ratio`、`trade_count`、`avg_turnover`、`avg_holding_days`、`exposure_ratio`。

- 每项指标 id = `calc_id("backtest_<name>", {data_fp, calc_version, start, end, trading_days, ...spec}, data_fp=data_fp)`，
  逐项落在 `metrics_calc_ids`。
- 另有整卡级 `calc_ids`：`run_card` / `data` / `gate` / `benchmark`。
- 年化交易日 `TRADING_DAYS = 252`。

## 4. 校验门（`guard.py`）

三项按序检查，任一失败 → `gate.passed=false`：

1. `min_trades`：最小完整交易次数（太少 → 样本太小，收益是噪音）；
2. `out_of_sample`：按时间顺序切样本内 / 样本外（`split_index`），样本外累计收益必须 `> 0`；
3. `cost_sensitivity`：三档佣金率各重跑一次（只替换 `commission`），最终权益必须随佣金**非递增**。

失败时：运行卡 JSON `gate.passed=false` + `red_reasons`；Markdown 顶部写「校验门：未通过（红）」；
HTML 页头与横幅标红；CLI 打印红字并以 **8** 退出。**不允许只报好看的数字。**

## 5. 运行卡（`card.py` + `runner._build_card`）

- `run_id = "bt_" + sha256(canonical_params(参数) + "\x1f" + 数据指纹)[:8]`；
  同参数 + 同数据指纹 → 同 `run_id`、同卡（仅 `generated_at` 不同），重跑覆盖同一组产物。
- 产物：`<data_dir>/backtests/<run_id>/<run_id>.{json,md,html}`。
- JSON 严格序列化（`allow_nan=False`），任何 inf / NaN 直接报错而不是写出坏文件。
- 字段清单与类型见 `docs/CLI.md` §M 与运行卡 JSON 本体；前端据此只读渲染。

## 6. HTTP（`/api/backtest`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/backtest/runs?limit=` | 运行卡摘要列表（新的在前，`limit` 钳 1–200） |
| GET | `/api/backtest/latest` | 最新一张运行卡（无 → 404） |
| GET | `/api/backtest/runs/{run_id}` | 单张运行卡 JSON（契约本体） |
| GET | `/api/backtest/runs/{run_id}/export.json` | 下载 JSON |
| GET | `/api/backtest/runs/{run_id}/export.md` | 下载 Markdown |
| GET | `/api/backtest/runs/{run_id}/export.html` | 下载自包含 HTML（未过门标红） |
| GET | `/api/backtest/strategies` | 策略清单 + `config_defaults` + `symbol_default`（QUOT-535） |
| POST | `/api/backtest/run` | 提交回测，`202 {job_id, status:"queued"}`，异步执行（QUOT-535） |
| GET | `/api/backtest/jobs/{job_id}` | `{status, run_id, gate_passed, exit_code}`（QUOT-535） |

- `strategies` 数据源是后端常量 `strategy.BUILTIN` + `load_config()`：策略 name / label / params
  （key / label / type / default / min / max）与成本默认值（旧小数键 + 万分之键 + presets）
  都由后端给出，前端不硬编码。
- `run` 请求体：`{strategy, strategy_params, symbol, start, end, commission?, slippage?,
  stamp_duty?, min_trades?, in_sample_ratio?, cost_tiers?}`，并兼容
  `commission_rate_wan / commission_floor_yuan / transfer_fee_wan / stamp_duty_wan`。
  执行走与 CLI 相同的 `run_backtest`（同 runner / guard / 运行卡落盘），不另写撮合逻辑。
- 闸门同现有写面：回环独占 + `Host` 白名单 + 写方法来源证据（本机页面 `Origin` 或
  `X-LDST-Client: 1`）；非回环 / 坏 `Host` / 坏 `Origin` → 403。
- 参数非法 → **400** + `{error, message}`（未知策略 / 坏标的 / 坏日期 / 坏策略参数 / 坏成本）；
  请求体形状不对 → 422；`run_id` 非法 → 400；不存在 → 404；产物损坏 / 配置不可读 → 500。
- 参见 DEC-074：这是对 DEC-014「控制面写接口不接受参数」的单点例外——参数走结构化字段，
  不拼 argv、不进 shell。

## 7. 已知边界（M1）

- 单标的、日频；不做组合 / 仓位管理 / ATR 止损 / 有状态策略（M2）。
- 基准用该标的买入持有（本机 `index_daily` 只有 000300，不编造上证指数）。
- 成本敏感性只替换佣金率，最低佣金 / 过户费 / 滑点 / 印花税固定为配置值。
- 成本只覆盖股票：过户费按万 0.1 双边、印花税按万 5 卖出单边；债券 / 基金 / 可转债不参与 M1。
