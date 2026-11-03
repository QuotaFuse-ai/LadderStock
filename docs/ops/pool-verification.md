# 实机验证入口 pool-verification（QUOT-363）

打榜评分的 `capital` / `theme` 两个分项一直空着（QUOT-360），两位开发要在实机上取证，
但「实机是哪台、`/api/pool` 的 base URL 是什么、数据目录在哪、当日分区到底落没落盘」
此前没有被任何一份文档说清——`D:\workplace\LadderStock` 那棵脏树的 `data/archive/` 是空的，
按它找数会误判成「上游没数」。本文是这个入口的唯一权威说明。

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
- `D:\workplace\LadderStock`（脏树，`feat/quot-231-ladder`，落后 `origin/main` 198 提交）—— `data/archive/` 为空、也没有 `market.duckdb`，在这里找 `*/archive/2026/11/*` 永远找不到。**实机读的不是这个目录。**

## 2. 数据口径

变量名（值不贴）：`LDST_DATA_DIR`、`LDST_CONFIG_DIR`，由 `serve-5950.ps1` 注入；
另有 `LDST_PEER_DIR_TICKHUB` / `LDST_PEER_DIR_TICKHUB_US` / `LDST_PEER_DIR_WIRE` / `LDST_PEER_DIR_SENATE`、
`LDST_TICKHUB_MCP_URL` / `LDST_TICKHUB_US_MCP_URL` / `LDST_WIRE_MCP_URL`。解析优先级见
`src/ladderstock/cli/paths.py`（`LDST_*` > 旧前缀 `VRA_*` / `VIBE_ASTOCK_*` > `cwd/data` > `~/.ladderstock/data`）。

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

| 数据集 | 11-03 parquet | 11-03 duckdb | 最近一个可用交易日 | 水位（`data/state/watermark.json`，11:46 更新） |
|---|---|---|---|---|
| `limit_pool` | 52 行 | 52 行 | 11-02：125 行 | `2026-11-03` / 52 行 / `tickhub_limit_pool` / ok |
| `concept_board` | 100 行 | 100 行 | 11-02：100 行 | `2026-11-03` / 100 行 / `tickhub_concept` / ok |
| `fund_flow` | **无文件** | **0 行** | **11-02：30 行**（表内共 1806 行，逐日 30） | **`2026-11-02`** / 1800 行 / `tickhub_fund_flow` / ok |

**当日 `fund_flow` 两条读法都没数。** 不是水位问题也不是代理问题——日线主力净额当日盘后才发布，
而 15:35 那条导出链压根没进 `origin/main`（`git grep "15:35" origin/main` 0 命中），也没接进这台机器（见 §5）。

取证读法二选一，**不要去改同步逻辑**（归 QUOT-347 / QUOT-349）：

1. **回落最近交易日**：读 `2026-11-02` 那 30 行，`data_as_of` 明确写出资金口径日期；
2. **当日盘中口径**：`realtime_fund_flow(code)`，已实测可达且真打到上游（§3）。

`concept_board` 当日 100 行在库，题材侧缺的只是「代码 → 板块」归属，`stock_concept_blocks` 可直接给出。

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
| `eastmoney` | `1236.0` | `3.0` | null | null | `11.0132121781` | null |
| `sina` | `12.36` | `380599473.38` | `328248414.78` | `18725719.8` | null | `9.9644` |

**已裁定（QUOT-360）：资金分项只认 `meta.source = eastmoney`，其它源（含 `sina`）按「取不到」处理**，
走现有 null + 剔除权重的机制，**不做单位换算兜底**——`price` 差 100 倍、`main_net` 差数量级目前只是观察到的现象，
没有经过验证的换算表，换算写错是**静默错**，`parts.capital` 会给出看起来合理的假排名，比 null 坏得多。
过滤点现成：`adapters/tickhub.py:187` 已把 `meta.source` 落成 `upstream`。
该口径由 QUOT-361 写进 DEC-046 与 `docs/specs/SPEC-CANDIDATE-POOL.md` 资金口径小节（**DEC-046 已预留给 361**，按 DEC-045 取号规则，其他任务别占这个号）。

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

# 1) 打榜池取证（今日）
curl -s "http://127.0.0.1:5950/api/pool?date=2026-11-03" | python -m json.tool

# 期望看到：
#   payload.available = true
#   payload.data_source_status = "ok"
#   payload.source_detail 含 "limit_pool: parquet（52 条）"、"concept_board: parquet（100 条）"、
#                          改动前 "fund_flow: none（0 条）"
#   payload.candidate_count = 30，counts.limit_pool_rows = 52
#   改动前：每个 candidate 的 parts.capital / parts.theme = null，
#           absent_parts = ["capital","theme"]，raw.net_inflow = null，raw.theme_tags = []，
#           counts.theme_boards = 0
#   QUOT-361 完成后：parts.capital 非 null、raw.net_inflow 有值、data_as_of 写明资金口径日期
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

## 5. 运维风险（本文只报告，未动任何配置）

1. **`LadderStock UI 5950` 计划任务的 `<Triggers />` 是空的** —— 没有任何触发器，永远不会自己起；
   11-02 22:37 那次是手动 `schtasks /run`。当前监听 5950 的进程是 11-03 11:42 从 `D:\workplace\LadderStock`
   **手工**启动的，父进程是一次性 shell，随它退出就没了；`ldst-5950.pid`（25888）已过期。
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
   **属于服务器配置变更，等人点头，未执行。**
3. **没有任何计划任务在写这台机器的 `archive/` 分区。** 11-03 的 52 / 100 行是 11:43–11:46 一次手工 `ldst sync` 的产物；
   部署态 `schedule.yaml` 里只有 09:05 提醒、6 个盘中快照、15:05 收盘对账，**没有 15:35 的日线导出**，
   且 `git grep "15:35" origin/main` 0 命中 —— 那条链没进 main，也没接进这台机器（转回 QUOT-347 验收意见处理）。
4. `D:\QuotaFuseModules\LadderStock` 不是 git 仓库（只有 `config/ data/ logs/`），改这里**不会**进版本控制。
5. `/api/state/config` 声明 `mcp=5951`，但 5951 未监听。
6. 本机 Redis 在 WSL 的 docker 里，WSL 一停 TickHub 缓存层就退化；`th-cn doctor` 的 `cache_backend` 会立刻变红。
7. 本文所有「当前值」（§2 快照、5950/8000 的 PID、水位日期）都是 2026-11-03 一次采集，会过期；复核一律先跑 §4 第 0 步。

## 6. 清理

本任务没有改任何配置、没停任何进程、没动 `market.duckdb`（全程 `read_only=True`）。

```bash
# 中间产物（本地工作区，非仓库）
rm -f q363-probe.ps1 probe_mcp.py q363-ev/*.json
# 进仓库的只有本文件；evidence.py / evidence.txt 属复核证据与复跑脚本，按 DEC-0026 留本地
git status --porcelain   # 除本文件外应为空
```

- `q363-ev/` 里的 JSON 含东财上游响应，虽然 CLI 侧已对凭据脱敏，仍不属于长期留存物，跑完即删。
- 复核用的 duckdb 连接一律 `read_only=True`；**不要**用写模式打开 `market.duckdb`，它与 5950 服务抢锁（见 `data/db_lock.py`）。
