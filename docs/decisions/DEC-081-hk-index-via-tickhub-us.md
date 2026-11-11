# DEC-081：港股指数两格经 TickHub-US 别名表供数——逐 symbol 取、零 key、Tier C 非商用（QUOT-560）

日期：2026-11-11
关联：QUOT-560（本单）、QUOT-519（父单）、QUOT-471（美股两格先例）、
DEC-006（生态边界：app 只经 peer MCP）、DEC-008（sources/sync ↔ 目录双向一致）、
HOME-V2-DATA-SURFACE §3 C-1（「港股无源，建议砍掉」——本决策推翻其前提）、
SPEC-HOME-V2 §2.3（`ok` 态「留着给未来港股接入」——本决策使其可达）
状态：已定稿

编号扫描（DEC-045 纪律）：2026-11-11 `git ls-tree origin/main docs/decisions/`
扫到的最大号 = **080**（`DEC-080-daily-wire-news-collection-job.md` /
`DEC-080-ensure-on-open.md` / `DEC-080-peer-health-events-confirmed-restart.md`），
本决策取 **081**。

## 背景

首页指数条自 QUOT-471 起恒渲染 8 格：A 股 4 + 美股 2 有真实读数，港股 2 格
（恒生指数 / 恒生科技）因「无合规源」恒为 `available=false`「源未接入」占位。
HOME-V2-DATA-SURFACE（2026-11-03 实测）的结论是 `th_us_get_quote` 对
`HSI.HK` / `HSTECH.HK` 一律 `isError`（`invalid literal for int() with base 10:
'HSI'`），建议砍掉港股两格。

QUOT-560 复核推翻了这个前提：**不是无源，是 peer 的港股支路只认数字个股代码**。
TickHub-US 的 `quote_hk` 链 [tencent, sina, eastmoney] 背后，腾讯 `qt.gtimg.cn/q=r_hkHSI`
与新浪 `rt_hkHSI` 对港股指数**免费公开、零 key** 地回完整行情（点位 / 昨收 / 涨跌额 /
涨跌幅，实测 2026-11-11：HSI 24315.92 / −0.98%，HSTECH 4027.06 / −1.41%）。
缺的只是 ticker 规范化层把 `HSI` 这类别名拦截下来、映射成 `r_hkHSI` / `rt_hkHSI`
之前，就先掉进了按 `int()` 解析的个股支路。

## 决策

1. **源在 peer 侧补，不在 app 侧自采**（DEC-006 闸门 4 原样遵守）。
   TickHub-US `core/ticker.py` 新增港股指数别名表（`HSI` / `HSTECH`，裸名 / `^` 前缀 /
   `.HK` 后缀三种写法 → 规范形 `HSI.HK` / `HSTECH.HK`），拦截点在美股正则之前
   （`.HK` 形似美股 `BRK.B` 类股后缀，先判港股）；`tencent.py` / `sina.py` 的
   `quote_hk` 支路对指数代码走 `r_hk{code}` / `rt_hk{code}`，字段数门槛不放宽
   （腾讯 76、新浪 15，指数报文实测 78 / 25 字段，达标）。
   eastmoney 支路对港股指数**显式抛 `DataNotAvailable`**（其 secid 只映射数字个股），
   不静默给错票。
2. **app 侧新增数据集 `index_quote_hk`**（`DATASET_CATALOG` + `sources.yaml` 双登记，
   DEC-008）：peer `tickhub_us`、主工具 `th_us_get_quote`、`fallback=[]`、
   `replace` / 水位 `timestamp`、**不进 `sync.yaml`**（与 `index_quote_us` 同构：
   实时报价无「一天一条」水位，走 `fetch_live`）。
   **备源为空是刻意的**：`th_us_get_quote` 内部已带 tencent→sina 的同口径兜底，
   app 侧再挂一条同 peer 同工具的备源只是重复，违反「主备同口径」的登记语义。
3. **逐 symbol 取，镜像 `_fetch_us`**：`core/indices.py` 的港股白名单
   （`HSI.HK` / `HSTECH.HK`，UI key 仍 `HSI` / `HSTECH`）逐格调
   `fetch_live("index_quote_hk", params={"symbol": ..., "commercial_use": False})`。
   每首页取数 = 1 次 CN 批量 + 2 次美股 + 2 次港股，共 5 次 peer 调用，预算不变。
   **单格失败只影响本格**：`_fetch_per_symbol` 逐格捕获异常、按 symbol 记录原因，
   投影层互不拖累（注册表层的同源 cooldown 是既有治理语义，与美股块共享，本单不改）。
4. **零 key、Tier C、非商用**：只走 sina / tencent 免费公开源；每次调用显式
   `commercial_use=False`；`compliance_tier=C` 与上游 `warnings` 原样落信封
   `payload` 并带进 `source_detail`（QUOT-471 C-2 第 3 条的既有口径，港股复用同一适配器）。
   密钥不进 yaml / 日志 / 提交——本源根本不需要密钥。
5. **如实降级**：取不到就是 `available=false` + 具体原因（`index_quote_hk 取数失败
   （HSI.HK）：…`），不编数、不补 0、不复用 A 股占位。8 格全有值时 `ok` 态首次可达
   （SPEC-HOME-V2 §2.3 的预留兑现）。

## 考虑过的替代方案

- **砍掉港股两格（HOME-V2-DATA-SURFACE C-1 原建议）**：被否——其前提「无源」是
  peer 解析缺口造成的假象，上游免费源实测存在且字段齐备；砍格是把 peer 的 bug
  当成生态约束。
- **app 侧自采 qt.gtimg.cn / hq.sinajs.cn**：被否——直接违反 DEC-006 闸门 4
  （app 只经 peer MCP，不自采行情站点）。
- **复用 `index_quote`（tickhub CN）扩港股**：被否——CN peer 的 `index_quote`
  只覆盖 SH/SZ/BSE，扩它要改 CN 侧源适配，且 CN/US 两 peer 的港股能力重复建设；
  US peer 已有 `quote_hk` 链，别名表是最小改动。
- **给 `index_quote_hk` 挂 app 侧备源**：被否——同 peer 同工具的「备源」不构成
  独立口径，peer 内部 tencent→sina 链已是兜底；登记空备源并在注释里写明原因。
- **批量一次取两格**：被否——`th_us_get_quote` 单 symbol 契约，批量要改 peer
  工具签名；逐 symbol 与美股块同构，单格失败隔离更简单。

## 验收

- peer：`tests/core/test_ticker_hk_index.py`（别名 / 映射 / 个股与美股回归）、
  `tests/contract/test_hk_index_quote.py`（2026-11-11 真实报文录制的 tencent / sina
  契约 + 链路由 + eastmoney 显式拒答）、`test_live_smoke.py` 新增 HSI / HSTECH 实测
  （`TICKHUB_US_LIVE=1` 下 4 项全过，零 key）；`ruff` / `compileall` / 非 live 全绿。
- app：`tests/unit/core/test_indices_hk_fetch.py`（单格隔离 / 规范入参 / 市场校验 /
  合规明细去重）、`tests/unit/web/test_indices_endpoint.py`（8 格顺序 / 全有值 `ok` /
  HK 完整行情 / 5 次调用预算 / 单格失败原因归属）、
  `tests/contract/test_tickhub_us_payload_mapping.py`（HK 行投影 + Tier C 落地）、
  `test_paths_seed.py`（首跑 seed 含 `tickhub_hk_index_quote`）、
  `test_sources_consistency.py`（双向一致自动覆盖新登记）。
- 浏览器实证：首页 8 格，港股 2 格由「源未接入」变为真实读数（前后对照截图随任务报告）。
- 美股 2 格与 A 股 4 格回归不变；单格港股失败只降级本格，诚实标注原因。