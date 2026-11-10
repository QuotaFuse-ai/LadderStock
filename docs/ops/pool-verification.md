# 实机验证入口 pool-verification（QUOT-363）

打榜评分的 `capital` / `theme` 两个分项一直空着（QUOT-360），两位开发要在实机上取证，
但「实机是哪台、`/api/pool` 的 base URL 是什么、数据目录在哪、当日分区到底落没落盘」
此前没有被任何一份文档说清——`D:\workplace\LadderStock` 那棵脏树的 `data/archive/` 是空的，
按它找数会误判成「上游没数」。本文是这个入口的唯一权威说明。

> **结论更正（2026-11-03 16:4，先读这条再读其余）**：资金侧的口径**不是**「只认 `meta.source = eastmoney`」——
> 那条写法已作废，`eastmoney` 单股实时通道返回的 `price` 是**分**、`main_net` **不是元**，禁止直灌 `raw.net_inflow`；
> 现网正在喂 `parts.capital` 的日线 `stock_fund_flow` 才是**元**。完整证据与更正后的 6 条裁定在 **§3「⛔ 更正」**。
> 另外 `capital` 自 16:22 起**已非 null**（当日 `fund_flow` 分区落盘），本文各处「两个分项一直空着」指的是 11:46 那次采集，见 §2。

- **本文件是 source of truth**（仓库这份）。运行时数据不在仓库里，见下。
- 数据目录：`D:\QuotaFuseModules\LadderStock\data`（由 `LDST_DATA_DIR` 指向，`/api/health` 可自证）
- 配置目录：`D:\QuotaFuseModules\LadderStock\config`（改 `sources.yaml` / `sync.yaml` / `schedule.yaml` 要改这份，不是仓库里那份）
- 日志：`D:\QuotaFuseModules\LadderStock\logs\` ＋ `C:\Users\Administrator\AppData\Local\TickHub\logs\mcp-8000.log`

> 本文只记录**变量名与路径**，不含任何 token、代理串或环境变量值。
> 本任务不改任何产品代码；文中引用的 `core/pool.py`、`adapters/tickhub.py` 行号只用于定位口径，未做任何修改。

## 1. 实机在哪

**就是这台 Windows 主机**（10.0.19042，局域网地址 192.168.1.138），不是 WSL，也不是远端 peer 机。
WSL `Ubuntu-24.04` 只跑了 Redis（docker-proxy → 宿主机 `127.0.0.1:6379`），是 TickHub 的缓存后端，不是应用宿主。

| 组件 | 监听 | 启动方式 |
|---|---|---|
| LadderStock 看板 + 只读 API | `http://127.0.0.1:5950` | 计划任务 `LadderStock UI 5950` → `serve-5950.ps1`（supervisor 循环：子进程退出后 10s 重启） |
| TickHub-CN MCP peer | `http://127.0.0.1:8000/mcp` | `C:\Users\Administrator\.tickhub\start_peer.cmd`（先注入 `proxy.env`，再 `th-cn start-all`） |
| Redis（TickHub 缓存） | `127.0.0.1:6379` | WSL 内 docker |
| LadderStock 自身 MCP | `5951`（`/api/state/config` 声明）**未监听** | — |

⚠️ 上面那句「supervisor 循环」只有在 `schtasks /run /tn "LadderStock UI 5950"` **手动拉起之后**才成立：
该计划任务的 `<Triggers />` 是空的，没有任何触发器，永远不会自己起（见 §5）。

**当前 5950 不是那个计划任务起的。** 16:15 复核进程树：

```
21356 "D:\workplace\ruanjian\python312\python.exe" "D:\workplace\LadderStock\.venv\Scripts\ldst.exe" run --port 5950 --host 127.0.0.1
  └ 15436 "D:\workplace\LadderStock\.venv\Scripts\ldst.exe" run ...
       └ 6800  ← 真正在监听 127.0.0.1:5950 的进程，创建时间 2026-11-03 15:20:51
```

⇒ 代码基线是 **`D:\workplace\LadderStock`（脏树，落后 `origin/main` 198 提交）**，数据是中心库。
`ldst-5950.pid` 里的 25888 是 11-02 那一次的残留，和现在的进程无关，**不要拿它找进程**。
（这一条纠正本文早先写的「11:42 手工启动」——那是上一个已经死掉的实例。）

`/api/pool` 的实际 base URL：**`http://127.0.0.1:5950`**。
`LDST_TICKHUB_MCP_URL=http://127.0.0.1:8000/mcp` 由 `serve-5950.ps1` 注入，与 QUOT-360 要求一致。

日志位置：

| 用途 | 路径 |
|---|---|
| 5950 应用 stdout / stderr | `D:\QuotaFuseModules\LadderStock\logs\ldst-5950.out.log` / `ldst-5950.err.log` |
| 5950 supervisor | `D:\QuotaFuseModules\LadderStock\logs\ldst-5950.supervisor.log` |
| 5950 PID 文件 | `D:\QuotaFuseModules\LadderStock\logs\ldst-5950.pid`（**已过期**，见 §5） |
| 盯盘扫描 | `D:\QuotaFuseModules\LadderStock\logs\watch-scan.log` |
| MCP peer（含代理轮换记录） | `C:\Users\Administrator\AppData\Local\TickHub\logs\mcp-8000.log` |

### ⚠️ 别连这两个（QUOT-360 踩过的坑）

- `http://127.0.0.1:5957` —— 另一个 `ldst run` 实例（`D:\workplace\ls-quot-325`），它的数据目录下**根本没有 `archive/` 分区**，`/api/pool` 返回 `available=false`、三个数据集全 0 条。取证连它会得出「上游没数」的错误结论。
- `D:\workplace\LadderStock`（脏树，`feat/quot-231-ladder`，落后 `origin/main` 198 提交）—— `data/archive/` 为空、也没有 `market.duckdb`，在这里找 `*/archive/2026/11/*` 永远找不到。**实机读的不是这个目录。**（注意：脏树的**代码**确实在给 5950 供服务，见上；空的只是它**自己那份 `data/`**。）

这个坑的准确指纹，今天在本机用空闲端口 + 空数据目录复现过一次，认 signature 比认端口可靠：

```
/api/pool?date=2026-11-03  →  available=false
                              data_source_status="missing"
                              reason="涨停池没有可解析的候选（可能尚未同步）"
                              source_detail="limit_pool: none（0 条）; concept_board: none（0 条）; ..."
```

看到 `missing` + 全 `none（0 条）`＝**连错实例 / 错数据目录**，不是上游没数；`ok` + `fund_flow: none（0 条）` 才是 §2 说的当日资金口径缺数。

## 2. 数据口径

⚠️ **这台机器上现在有两套 runtime × 两个 data 目录，只有一个是取证入口**（2026-11-03 16:15 复核）：

| 谁 | 代码基线（`LDST_RUNTIME` / 启动路径） | 读的 data 目录 | 监听 | 在跑吗 |
|---|---|---|---|---|
| 取证入口（本文） | `D:\workplace\LadderStock`（脏树） | **`D:\QuotaFuseModules\LadderStock\data`** ✅ 有 `archive/2026/11/{01,02,03}` | 5950 | 是（pid 6800，15:20:51 起） |
| QUOT-350 部署态调度 | `D:\workplace\ls-quot-325` | `D:\workplace\ls-quot-325\data` ❌ **只有 `intraday/` + `state/`，没有 `archive/`** | 5957（当前未监听） | `ldst schedule run` 在跑（15:31 起） |
| QUOT-350 新落的常驻层 | `ldst.env` 写 `LDST_RUNTIME=D:\workplace\ls-quot-325`、`LDST_DATA_DIR=D:\QuotaFuseModules\LadderStock\data`（**与本文一致**） | 中心库 | — | 16:12–16:16 正在写入 `D:\QuotaFuseModules\LadderStock\ops\`（`ldst.env` / `schedule-ops.cmd` / `schedule_supervisor.py`） |

⇒ **给开发的结论**：旧调度（`run-service.ps1`，`LDST_DATA_DIR` 指仓库内 `ls-quot-325\data`）产出的分区**不会进 5950 读的中心库**。
在它被 `ldst.env` 那一版接管之前，中心 `archive/` 不会自己长出新交易日——今天 11-03 的分区是手工 sync 的产物（§5 第 3 条）。
所以 §4 的「取证前先手工 sync 并记时间」**在新层接管前一直是前置步骤**，别指望明天有 11-04 的分区。
这一条不在本任务范围内动（属 QUOT-347 / QUOT-349 / QUOT-350），已在评论里 @ 协调人。


变量名（值不贴）：`LDST_DATA_DIR`、`LDST_CONFIG_DIR`，由 `serve-5950.ps1` 注入；
另有 `LDST_PEER_DIR_TICKHUB` / `LDST_PEER_DIR_TICKHUB_US` / `LDST_PEER_DIR_WIRE` / `LDST_PEER_DIR_SENATE`、
`LDST_TICKHUB_MCP_URL` / `LDST_TICKHUB_US_MCP_URL` / `LDST_WIRE_MCP_URL`。解析优先级见
`src/ladderstock/cli/paths.py`（`LDST_*` > 旧前缀 `VRA_*` / `VIBE_ASTOCK_*` > `cwd/data` > platformdirs 用户数据目录 `data/`，DEC-071）。

⚠️ **不要按 cwd 推断数据目录**——5950 当前进程从 `D:\workplace\LadderStock` 启动，却读中心库。
用 API 自证，这是唯一可信的口径来源：

```bash
curl -s http://127.0.0.1:5950/api/health
# {"ok":true,"data_dir":"D:\\QuotaFuseModules\\LadderStock\\data", ...}
curl -s http://127.0.0.1:5950/api/state/config
# config_dir / data_dir / ports(web=5950, mcp=5951)
```

### 读法顺序

`data/archive.py::snapshot()`（`core/pool.py` 经 `core/ladder.py::dataset_snapshot()` 调用）：

1. 命中 `data/archive/<Y>/<M>/<D>/<dataset>.parquet` → `backend="parquet"`；
2. 否则查 `data/market.duckdb` 里**该日期**的表 → `backend="duckdb"`；
3. 都没有 → `backend="none"`，`source_detail` 显示「none（0 条）」。

**第 2 步是按日期过滤的**，所以「DuckDB 兜底」不会自动把最近交易日补上——当日无分区且当日无 DuckDB 行时结果仍是 0 条，`missing` 里就会出现该数据集。这正是 `fund_flow` 现在的表现。

### 2026-11-03 取证快照（非常态水位，勿当基线）

| 数据集 | 11-03 parquet | 11-03 duckdb | 最近一个可用交易日 | 水位（`data/state/watermark.json`） |
|---|---|---|---|---|
| `limit_pool` | 11:46 读为 52 行，**15:21:52 被再次 sync 重写为 48 行**（`source_detail` 同步变为「48 条」） | 与 parquet 同源 | 11-02：125 行 | `2026-11-03` / **48 行** / `tickhub_limit_pool` / ok（updated_at 15:21:52） |
| `concept_board` | 100 行 | 100 行 | 11-02：100 行 | `2026-11-03` / 100 行 / `tickhub_concept` / ok（updated_at 11:46:08） |
| `fund_flow` | **11:46 无文件 → 16:22 已有 `fund_flow.parquet` 40 行**（见下方「结论已作废」） | 表内 1806 行 → **2400 行** | 11-02：30 行 | **11:46 是 `2026-11-02`／1800 行；16:22:23 变成 `2026-11-03`／2400 行** / `tickhub_fund_flow` |

⚠️ **当日行数不是基线，不能写进断言。** 同一个 `limit_pool` 当日分区在 11:46 是 52 行、15:21 是 48 行——
盘中/盘后每跑一次 `ldst sync` 就会覆盖当日 parquet 并改写水位。取证时**同时记下 `source_detail` 原文和采集时刻**。
连「`parts.capital` 是不是 null」都不能断言：11:46 那次它是 null，16:22 已经变成 `84.6`（见下）。
（15:21 那次改写在中心 `logs/` 里没有任何对应记录，见 §5 第 8 条。）

#### ⛔ 本文早先「当日 `fund_flow` 两条读法都没数」的结论，已于 2026-11-03 16:22 作废

16:22 中心库被新一轮 sync 写入（`limit_pool` 16:22:00 / `fund_flow` 16:22:23 / `concept_board` 16:22:24，
紧接着 16:21:48 有人跑了 `schedule_supervisor.py stop` —— 属 QUOT-350 的换层动作）。同一分钟 `/api/pool` 实测：

```
source_detail = "limit_pool: parquet（48 条）; concept_board: parquet（100 条）; fund_flow: parquet（40 条）"
counts        = {limit_pool_rows:48, fund_flow_rows:40, theme_boards:0, ...}
candidates[0] = 002696 百洋股份 parts={strength:100.0, turnover:100.0, standing:100.0, capital:84.6, theme:null}
                absent_parts=["theme"]   raw.net_inflow=417850659.38
```

⇒ **资金侧已经有数了**，`core/pool.py` 本来就会把 `fund_flow` 里的 `net_inflow` 吃进 `parts.capital`——
之前 `capital=null` 是**缺数据**，不是缺代码。上面「二选一取证读法」那段（回落 11-02 / 走 `realtime_fund_flow`）
在当日分区存在时不再需要，**但别删**：当日分区会不会有，取决于 sync 有没有跑（§5 第 3、8 条）。

🔴 **量纲这件事已经有了判定，别按本文早先的写法办**（2026-11-03 16:4 由项目领导人更正，证据见 §3）：
中心库这份 `fund_flow` 的 `net_inflow`（`002696` → `417850659.38`）是**人民币元**，来自日线 `stock_fund_flow` 通道，
是 361 可以直接用的主源之一；反而是 `realtime_fund_flow` 的 **`eastmoney` 单股实时通道不是元**（`price` 是分、`main_net` 是未标明的 push2 标度），
**禁止把它的 `f62` 原值直灌 `raw.net_inflow`**。本文在此之前写的「只认 eastmoney」是错的，已在 §3 更正。

`concept_board` 当日 100 行在库，题材侧缺的只是「代码 → 板块」归属，`stock_concept_blocks` 可直接给出（`theme` 至今仍 null、`theme_boards=0`）。

## 3. 两个上游工具的可达性（已实测）

代理：`PROXY_PROVIDER=kuaidaili`、`PROXY_POOL_ENABLE=true`，由 `C:\Users\Administrator\.tickhub\proxy.env` 注入
（凭据键名 `KDL_*`，本文不贴值）。`mcp-8000.log` 持续出现 `proxy provider refreshed` / `proxy pool checked`，`detail.nodes=5`。

```bash
th-cn proxy list  -o json   # enabled=true, count=5
th-cn proxy check -o json   # 5/5 可达，延迟 468–2265 ms
```

⚠️ **直接跑 `th-cn` CLI 会看到 `proxy: 0 node(s) / enabled=false`** —— CLI 进程不加载 `proxy.env`，只有 `start_peer.cmd` 会。
要么照 `start_peer.cmd` 的方式先注入环境变量，要么走 §4 的 MCP 通道（8000 服务进程已带代理），否则测的是没代理通路。

经 `http://127.0.0.1:8000/mcp` 实调，两个工具都在已注册的 149 个工具里，`ok=true`、`cache_hit=false`（真打上游）。
入参只有 `code` / `data_as_of` / `fields` / `tier`，**没有 cache 开关**，绕缓存只能靠 CLI 的 `--no-cache`。

### `realtime_fund_flow(code)`

字段：`code,name,price,main_net,main_net_pct,super_large_net,large_net,medium_net,small_net,change_pct`（部分随源为 null）。

同一只 `002696`，两次调用（间隔约 3 分钟，均绕过缓存）：

| meta.source | `price` | `main_net` | `super_large_net` | `large_net` | `main_net_pct` | `change_pct` |
|---|---|---|---|---|---|---|
| `eastmoney` | `1236.0`（**分**） | `3.0`（**不是元**） | null | null | `11.0132121781` | null |
| `sina` | `12.36`（元） | `380599473.38`（**元**） | `328248414.78` | `18725719.8` | null | `9.9644` |

#### ⛔ 更正：本文此前写的「资金分项只认 `meta.source = eastmoney`、其它源按取不到处理」是错的（2026-11-03 16:4，项目领导人）

错在把「不做单位换算兜底」当成了「选 eastmoney」。量纲逐条查过代码（`D:\workplace\TickHub-CN\src\tickhub_cn\datasources\funds.py`，本机 10-25 版与安装态同源；下列行号我逐条复验）：

| 通道 | 取数与是否换算 | 量纲 |
|---|---|---|
| `realtime_fund_flow` **eastmoney** | 请求 `funds.py:392-398` 只带 `secid` + `fields`，**没有 `fltt` / `invt`**；`price=f43`（`:408`）、`main_net=f62`（`:409`）经 `to_float` 原样透传（`datasources/util.py:17-29` 无任何缩放） | `price` = **分**（1236 = 12.36 元）；`main_net` = push2 默认标度，**不是元**。具体倍数**不要靠猜**——同一只 `002696` 与当日日线实测差约 8 个数量级，够判禁用了 |
| `realtime_fund_flow` **sina** | `funds.py:442-443` `trade` / `netamount`，同样不换算 | **元**。有回归夹具：`netamount="12345678"` → `main_net == 12345678.0`（`tests/regression/test_push2_fallbacks.py:58,95,121`） |
| `stock_fund_flow`（日线，**现网正在喂 `parts.capital`**） | `funds.py:59` `main_net = p[1]`；sina 兜底 `:122` `netamount` | **元** |
| `fund_flow_ranking`（批量） | `funds.py:164-165` **带 `fltt: 2, invt: 2`**，`:187,:189` 原样读 `f62` | **元** |

TickHub 自己也把这件事写进了产品文档：`skills/tickhub-cn/references/sources.md` 的「Units differ per source」一节 —— Units are **not** unified across the product，**报数必须把量纲写在数旁边**。
消费侧 `core/pool.py:74-75` 那句注释「只用于排名，所以不做单位换算——元/万元的歧义不影响名次」**只在单源时成立**：
一旦「当日源 → 回落最近交易日」把两种量纲喂进同一次 `rank_percentiles()`，名次就静默错。

**更正后的裁定（正文由 QUOT-361 落进 DEC-046 与 `docs/specs/SPEC-CANDIDATE-POOL.md` 资金口径小节）：**

1. `raw.net_inflow` 的语义**固定为人民币元**；任何通道写入该字段前必须先**证明**量纲（代码位置或回归夹具，不接受「看起来对」）。
2. **禁止**把 `realtime_fund_flow` eastmoney 单股实时通道的 `f62` 原值直灌 `raw.net_inflow`。
   要它当主源，先在 TickHub-CN 给该请求补 `fltt=2` / `invt=2` 并加回归 —— **跨仓改动，另开任务，不塞进 361**。
3. 361 可用主源二选一：**sina 实时（元）** 或 **日线 `stock_fund_flow`（元，现网已在喂）**；批量备选 `fund_flow_ranking`（已带 `fltt=2`，元）。
4. **一次排名只允许一种量纲**：混源必须先归一到元，否则本轮全用同一源。`price` 同理（eastmoney 是分，用到就 `/100` 或换源）。
5. 加一条对照测试：同一只股票「日线 vs 实时」的 `main_net` 比值应 ≈ 1，显著偏离即判量纲回归破，不许放行。
6. 过滤点现成：`adapters/tickhub.py:187` 已把 `meta.source` 落成 `upstream`，按上面 1–4 在此分流。

（**DEC-046 已预留给 361**，按 DEC-045 取号规则，其他任务别占这个号。）

### `stock_concept_blocks(code)`

- 结构：`data = {code, total, boards[], concept_tags[]}`，`boards[] = {name, code(BKxxxx), change_pct, lead_stock}`
- `002696`：`total=23`，前几项 `农林牧渔 BK0433 +1.75` / `水产饲料 BK1509 +3.55` / `饲料 BK1258 +2.33`
- `600261`：`total=24`，前几项 `家用电器 BK0456 +0.2` / `照明设备Ⅲ BK1460 +0.05` / `照明设备Ⅱ BK1245 +0.05`

**验收项（QUOT-362）**：`boards[].code` 是东财 BK 码，**先实测与 `concept_board` 主键同构再 join**；
`boards[].change_pct` 是板块自身涨幅，不要和 `concept_boards` 当日涨幅当成同一件事复用。

## 4. 取证命令

```bash
# 0) 先确认连的是有数的实例，并看清它的数据目录
curl -s http://127.0.0.1:5950/api/health

# 0b) 再看这份数是几点落盘的——行数和分区会被后来的 sync 覆盖，取证记录里必须带这个时刻
python -c "import json;w=json.load(open(r'D:/QuotaFuseModules/LadderStock/data/state/watermark.json',encoding='utf-8'))['watermarks'];\
[print(k,w.get(k,{}).get('value'),w.get(k,{}).get('rows'),w.get(k,{}).get('updated_at')) for k in ('limit_pool','concept_board','fund_flow')]"
ls /d/QuotaFuseModules/LadderStock/data/archive/2026/11/03/

# 1) 打榜池取证（今日）
curl -s "http://127.0.0.1:5950/api/pool?date=2026-11-03" | python -m json.tool

# 期望看到（行数以当次 source_detail 为准，勿写死，见 §2 警告）：
#   payload.available = true
#   payload.data_source_status = "ok"
#   payload.source_detail 含 "limit_pool: parquet（N 条）"、"concept_board: parquet（100 条）"、
#                          11:46 那次是 "fund_flow: none（0 条）"，16:22 起是 "fund_flow: parquet（40 条）"
#   payload.candidate_count = 30，counts.limit_pool_rows 与 source_detail 里的 N 一致
#   11:46 采集：parts.capital / parts.theme = null，absent_parts = ["capital","theme"]，raw.net_inflow = null，
#                counts.theme_boards = 0
#   16:22 采集：parts.capital 已非 null（002696 → 84.6，raw.net_inflow=4.18e8），absent_parts 只剩 ["theme"]，
#                counts.theme_boards 仍 0，raw.theme_tags 仍 []  ← 题材侧一点没变
#   ⇒ 资金侧的 null 是「数据没落盘」造成的，不是代码；16:22 起 `raw.net_inflow=4.18e8` 是**人民币元**
#     （日线 `stock_fund_flow` 通道），量纲判定见 §3「⛔ 更正」——361 的候选源按那里，别按 eastmoney 单股实时
#   QUOT-361 完成后：parts.capital 非 null、raw.net_inflow 有值且量纲已按 §3 更正选定，
#                data_as_of 写明资金口径来自哪个通道（日线 or 实时，两者不能混一轮）
#   QUOT-362 完成后：parts.theme 非 null、raw.theme_tags 非空、counts.theme_boards > 0

# 断言式取证（只看关键位）
curl -s "http://127.0.0.1:5950/api/pool?date=2026-11-03" \
  | python -c "import sys,json;p=json.load(sys.stdin)['payload'];\
c=p['candidates'][0];print(p['available'],p['data_source_status'],p['counts'],c['parts'],c['raw'])"
```

`evidence.py`（一次把 `/api/pool` + 两个上游工具打全的复跑脚本）按 DEC-0026 **不入库**，
随 QUOT-363 的评论附件交付，放到本机任意目录后运行：

```bash
D:/workplace/_q249_venv/Scripts/python.exe evidence.py
```

```bash
# 2) 需要绕过缓存时（先注入 proxy.env，照 start_peer.cmd 的做法）
th-cn tool call realtime_fund_flow   --params '{\"code\":\"002696\"}' --no-cache -o json
th-cn tool call stock_concept_blocks --params '{\"code\":\"002696\"}' --no-cache -o json

# 3) 行数复核（parquet 与 duckdb 两条读法分别看）
D:/workplace/LadderStock/.venv/Scripts/python.exe -c "import duckdb;c=duckdb.connect(r'D:/QuotaFuseModules/LadderStock/data/market.duckdb',read_only=True);\
[print(t,[r for r in c.execute(f'select trade_date,count(*) from \"{t}\" group by 1 order by 1 desc limit 3').fetchall()]) for t in ('limit_pool','concept_board','fund_flow')]"
ls /d/QuotaFuseModules/LadderStock/data/archive/2026/11/03/
```

`/api/pool` 是只读缓存回放，**不会自己打上游**——分区不落盘，接口就永远是 0 条。改代码前先手工出数：

```bash
ldst sync --dataset limit_pool    --output json
ldst sync --dataset concept_board --output json
ldst sync --dataset fund_flow --codes <当日30只> --output json
# 跑完复核 data/state/watermark.json 的 value / rows / status，并在取证记录里写上同步时间
```

跑这些命令时必须带上 §2 那三个环境变量（`LDST_DATA_DIR` / `LDST_CONFIG_DIR` / `LDST_TICKHUB_MCP_URL`），
否则读到的是 `cwd/data`，等于对着空库取证。

### 入口可用性复测（2026-11-03 15:55 实测，非基线）

第一次取证之后复跑一遍，确认「入口本身可用」这件事有可核对的原样输出。以下三段就是上面命令的实际返回，
开发开工前照此对照，**对不上就是入口断了，不是上游没数**。

```text
GET http://127.0.0.1:5950/api/health
{"ok":true,"name":"ladderstock","version":"0.1.0.dev0","time":"2026-11-03T07:55:43+00:00",
 "data_dir":"D:\\QuotaFuseModules\\LadderStock\\data","read_only":true}

GET http://127.0.0.1:5950/api/pool?date=2026-11-03
data_as_of=2026-11-03  available=true  data_source_status=ok  height=18  candidate_count=30
source_detail = "limit_pool: parquet（48 条）; concept_board: parquet（100 条）; fund_flow: none（0 条）"
counts        = {limit_pool_rows:48, dropped_duplicates:0, unknown_code:0, theme_boards:0, fund_flow_rows:0}
candidates[0] = 002696 百洋股份 score=100.0
                parts={strength:100.0, turnover:100.0, standing:100.0, capital:null, theme:null}
                absent_parts=["capital","theme"]
                raw={boards:18, turnover_rate:24.26, height:18, net_inflow:null,
                     theme_tags:[], theme_board_change_pct:null}
```

两个上游工具同批复测（走 `http://127.0.0.1:8000/mcp` 的 MCP 通道，无需自己注入代理；`isError=false`）：

```text
realtime_fund_flow("002696")   ok=true  cache_hit=false  meta.source=eastmoney
  data keys: code,name,price,main_net,main_net_pct,super_large_net,large_net,medium_net,small_net
  price=1236.0  main_net=3.0  main_net_pct=11.0132121781  super_large_net=null

stock_concept_blocks("002696") ok=true  cache_hit=false  meta.source=eastmoney
  data keys: code,total,boards,concept_tags
  total=23  boards[0]={name:"农林牧渔", code:"BK0433", change_pct:1.2, lead_stock:"..."}
```

**这两个读数的正确读法（2026-11-03 16:4 更正后）：** `meta.source = eastmoney` 只证明**通道可达**，
**不**等于「这路的 `main_net` 能当元用」——恰恰相反，`price=1236.0` 是**分**、`main_net=3.0` 是 push2 未标明标度，
**都不是元**（逐条代码证据见 §3「⛔ 更正」那张表）。当时我据此写过的「资金口径只认 eastmoney」是错的，已作废。
取证时要判的是「工具能不能返回」，量纲按 §3；要拿 `main_net` 当 `raw.net_inflow` 用，必须走 sina 实时或日线 `stock_fund_flow`。
`boards[].change_pct` 会随盘中变化（11:46 读到 `BK0433 +1.75`，15:55 读到 `+1.2`），
所以它同样不是取证基线。

## 5. 运维风险（本文只报告，未动任何配置）

1. **`LadderStock UI 5950` 计划任务的 `<Triggers />` 是空的** —— 没有任何触发器，永远不会自己起；
   11-02 22:37 那次是手动 `schtasks /run`。当前监听 5950 的进程是 11-03 **15:20:51** 从 `D:\workplace\LadderStock`
   **手工**启动的（进程树见 §1），父进程是一次性 shell，随它退出就没了；`ldst-5950.pid`（25888）是 11-02 那一次的残留，别拿它找进程。
   **取证前先 `curl /api/health` 确认进程还活着。** 这比「supervisor 掉了」更糟：它是**一次性**的。
2. **整套服务守护层住在一个 agent 任务工作区里**：`serve-5950.ps1` 所在目录
   （`C:\Users\Administrator\multica_workspaces\...\quot-200-baf27edac135\workdir\run-logs\`）还有 8 个
   `serve-*.ps1`（tickhub-cn / tickhub-us / wire-mcp / wire-web / senate-mcp / senate-webui / tickhub-cn-admin）
   加 `watch-scan.ps1`，共 **9 个守护脚本**。而 main 上 `docs/ops/disk-guard.sh` 的回收规则是：
   `multica_workspaces/quot-*` 对应 issue 一旦 `done` / `cancelled` 就 `rm -rf`。
   QUOT-200 目前 in_progress 所以没被回收；**它一结案，这 9 个脚本和计划任务同一天消失**，
   5950 与 TickHub peer 一起失联——而那正是 QUOT-361 / QUOT-362 的取证入口。
   处置方案（照 disk-guard 先例）：脚本迁到 `D:\QuotaFuseModules\LadderStock\ops\`（D 盘，不在 disk-guard 的
   C 盘回收范围内）+ 计划任务改指新路径 + **补上真实触发器**（开机触发 + 每 5 分钟端口巡检）。
   **2026-11-03 16:12 进展**：备份已做——9 个任务的 XML 原样导出 + 10 个脚本逐字副本 + 一份加了端口守卫的参考实现，
   全部放在 `D:\QuotaFuseModules\LadderStock\ops\quot-363-backup\`（原件未动，`cp -p` 保持 mtime；回滚命令见该目录 README）。
   **改任务 / 补触发器仍然没做**，原因变了：`ops\` 目录当时正被 **QUOT-350** 实时写入（`ldst.env` 16:12、
   `schedule-ops.cmd` + `schedule_supervisor.py` 16:16，随后 16:2x 又落了 `register-autostart.ps1` / `start-now.ps1`），
   同一台机器的同一层常驻服务已有人在落。再注册一套 supervisor＝两个守护层抢同一个 `data/`，比原问题更糟。
   **但要看清他们落的是哪一层**：`register-autostart.ps1` 注册的是 **`LadderStock Schedule`**（调度控制器，
   默认 `-Method Logon` 只写当前用户的启动文件夹快捷方式；`-Method Task` 那种计划任务承载，脚本自己写了「需项目领导人/老板在 issue 里明确批准后再执行」），
   `start-now.ps1` 拉的是 `schedule_supervisor.py run`。
   ⇒ **`LadderStock UI 5950` 这条不在他们的覆盖范围内**：5950 仍然是 15:20:51 那个手工进程（第 1 条），
   没有任何常驻层管它，QUOT-361 / QUOT-362 的取证入口照旧会在它退出后起不来。
   这一格还空着，`quot-363-backup\serve-5950.ps1` 就是为它准备的（照 QUOT-350 的写法：端口占用就 skip，不自旋重启）。
   **归属已定（2026-11-03 17:0，项目领导人）：5950 守护这一格归 QUOT-350，不落在 QUOT-363**——本文只交付备份、
   端口守卫参考实现和这一条缺口，注册与「杀掉手工进程看 5950 是否自愈」的自测由 350 执行；接管时把第 8 条的
   `data/` 多写入者审计一并计入 350 的最小可观测性。
   顺带查出一个真 bug：`serve-*.ps1` 原件的 `while($true)` **不看端口就重启**，端口若被别的进程占着（现在 5950 就是手工进程），
   子进程秒退→**每 10s 重启一次停不下来**；参考实现里加了「端口已占用就 skip 退出」的守卫，接管的人照这个写。
3. **没有任何计划任务在写这台机器的 `archive/` 分区。** 11-03 的分区来自 11:43–11:46 一次手工 `ldst sync`（`limit_pool` 当时 52 行，后被 15:21 那次重写，见第 8 条）；
   部署态 `schedule.yaml` 里只有 09:05 提醒、6 个盘中快照、15:05 收盘对账，**没有 15:35 的日线导出**，
   且 `git grep "15:35" origin/main` 0 命中 —— 那条链没进 main，也没接进这台机器（转回 QUOT-347 验收意见处理）。
4. `D:\QuotaFuseModules\LadderStock` 不是 git 仓库（只有 `config/ data/ logs/`），改这里**不会**进版本控制。
5. `/api/state/config` 声明 `mcp=5951`，但 5951 未监听。
6. 本机 Redis 在 WSL 的 docker 里，WSL 一停 TickHub 缓存层就退化；`th-cn doctor` 的 `cache_backend` 会立刻变红。
7. 本文所有「当前值」（§2 快照、5950/8000 的 PID、水位日期）都是 2026-11-03 一次采集，会过期；复核一律先跑 §4 第 0 步。
8. **中心 `data/` 目录有多个写入者，而且没有审计。** 15:21:52 那次 sync 重写了 `archive/2026/11/03/limit_pool.parquet`、
   `market.duckdb`、`state/watermark.json`，把当日 `limit_pool` 从 52 行改成 48 行，但中心 `logs/`
   （`ldst-5950.*.log` / `watch-scan.log`）与 `D:\workplace\ls-quot-325-ops\logs\` 里**都没有 15:21 的任何记录**。
   时间上它紧跟 15:20:51 那次 5950 重启（§1），像是在同一个临时 shell 里「重启 + sync」连着做的；5950 本身 `read_only=true` 不会写。
   后果直接落在取证上：**你读到的当日行数取决于谁最后一次跑了 sync**。
   处置：「取证前先看 `watermark.json` 的 `updated_at`」已写成 §4 第 0b 步硬性前置；给 sync 加一行带 PID/调用者的落地日志
   属配置/代码变更，**没做**，等 QUOT-347 / QUOT-350 认领。

## 6. 清理

本任务没有改任何计划任务、没有停任何进程、没有写 `market.duckdb`，也没往仓库里放过第二个文件。

**在本机写过的东西（只有这一处，且不在仓库、不在会被回收的工作区）**：

```
D:\QuotaFuseModules\LadderStock\ops\quot-363-backup\      ← 见该目录 README.md
├── backup-20261103\   9 个计划任务的原样 XML（回滚依据）
├── orig-20261103\     10 个守护脚本的逐字副本（cp -p，源文件与 mtime 未动）
├── serve-5950.ps1     带端口守卫的参考实现——未注册、无任务引用它
└── watch-scan.ps1     同上
```

为验证这个参考实现，用空闲端口 5960 + 一次性空数据目录跑过一遍 supervisor（起得来、端口被占会 skip、子进程被杀会 10s 后重启），
测完**已把测试实例与测试目录整个删掉**，`5960` 无残留监听，全程没碰 5950 / 8000 的现有进程。

```bash
# 中间产物（本地工作区，非仓库）
rm -f q363-probe.ps1 probe_mcp.py q363-ev/*.json
# 进仓库的只有本文件；evidence.py / evidence.txt 属复核证据与复跑脚本，按 DEC-0026 留本地
git status --porcelain   # 除本文件外应为空
```

- `q363-ev/` 里的 JSON 含东财上游响应，虽然 CLI 侧已对凭据脱敏，仍不属于长期留存物，跑完即删。
- 复核用的 duckdb 连接一律 `read_only=True`；**不要**用写模式打开 `market.duckdb`，它与 5950 服务抢锁（见 `data/db_lock.py`）。
