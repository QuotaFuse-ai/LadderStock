# DEC-055 自选写面：`POST /api/watchlist` 两条写路由（修订 DEC-052 §2.3）

- 日期：2026-11-05
- 提出：项目领导人（QUOT-419，承接总经理派活）
- **状态：生效（QUOT-419 交付即生效）**
- 编号：`DEC-055`。按 DEC-045 §2，落笔前扫 `docs/decisions/`，已占用的最大号为 054
  （`DEC-054-intraday-batch-slot-store.md`），本单取 055。
- 关联：DEC-052（自选读面与 §2.3「写面不经 HTTP」，**本单修订的原文**）/
  DEC-033（持仓读写面，本单照抄其闸门与出口形态）/ DEC-015 Tier 1（隐私库不进信封、MCP、AI）/
  DEC-004（只绑回环）/ DEC-006（数据只落本机）/ DEC-044（我的股票分组）/ DEC-048（页面一键命令，本单移除 `/positions` 一条）

## 1. 背景与为什么要改

DEC-052 §2.3 的原话是：**「不加写路由。自选的增 / 删 / 改不经 HTTP……写面若要开是再一次独立决定，不与本单捆绑」**，
当时只批了 `GET /api/watchlist` 一条读路由，增删走本机 CLI `ldst watch add|rm`。

QUOT-419 的需求把「自选」做成和「持仓」并列的独立页面：首页两张卡各自直达自己的页面，
进页面就能新增，建完回首页自动出现，**不再要求用户回终端跑 CLI**。这条体验在
「写面只走 CLI」的约束下无法实现——前端没有可调用的写通道，页面只能做成只读。

所以本单**正式修订 DEC-052 §2.3**：把自选的写入口从「仅 CLI」扩到「本机页面 + CLI」。
这不是把隐私数据放出本机：新增的两条路由与持仓写面（DEC-033）同形，回环独占、
`Host` 白名单、来源证据、平面 JSON、`no-store`，一条都不少。

## 2. 决定

### 2.1 只开两条写路由，与持仓写面同形

| 路由 | 语义 | 落库函数 |
| --- | --- | --- |
| `POST /api/watchlist` | 加一条自选，**幂等**（已存在原样返回，不刷新 `created_at`） | `journal.watchlist.add_watch()` |
| `POST /api/watchlist/{code}/delete` | 硬删除一条自选；不在清单里 **404**（不静默成功） | `journal.watchlist.remove_watch()` |

- 删除也走 `POST`，不提供 `PUT` / `PATCH` / `DELETE` 动词——换动词会绕开
  「写方法必须有来源证据」这条防线（DEC-033 §2.2 的同一条）。
- 请求体只认 `code` 一个键（**多一个就 400**）；值接受 6 位数字的字符串或 JSON 整数，
  `sh` / `sz` / `bj` 前缀由 `journal.watchlist.clean_code()` 归一化成裸 6 位，
  HTTP 层不另写一套代码规则。
- 响应是**平面 JSON**：`{ok:true, local_only:true, store:"local_sqlite", watch:{code, created_at}}`，
  删除回 `removed:{code}`。`Cache-Control: no-store`。

### 2.2 闸门：`_watchlist_write_guard` = 读闸门 + 来源证据

1. **回环独占**：`client_host(request.client)` 必须过 `is_loopback_host()`，否则 403；
2. **`Host` 白名单**：非 `127.0.0.1` / `localhost` / `::1`（端口须与监听端口一致）→ 403；
3. **来源证据**：同源 `Origin` 或显式头 `X-LDST-Client: 1`（复用控制面 `write_source_allowed`），
   二选一即可；
4. **跨站元数据**：带 `Sec-Fetch-Site` 时只接受 `same-origin` / `none`。

**不放宽任何一条**：不新增第二种鉴权、不因 `LDST_CONTROL_ENABLED=0` 连带关闭
（关的是控制面，不是本机数据面），不看远程访问 Token（Token 管「谁连得进来」，
不管「隐私数据该不该出去」）。

### 2.3 出口约束不变

- **不进 `LadderEnvelope`**：载荷顶层没有 `payload` / `domain` / `fetched_at`；
  字段名不叫 `watchlist` / `自选`（这两个词在 `core/privacy.py` 的 `PRIVATE_KEYS` 里，
  进信封出口会被 `strip_private()` 静默删字段），保持 `watch` / `removed`。
- **`note` 仍不出**（DEC-052 §2.2 的拍板不动）：写接口也不接受 `note`，
  自由文本连入口都没有；`journal.watchlist` 的投影仍然不读这一列。
- **不落第二份存储**：仍然只有 `data/journal.sqlite` 的 `watchlist` 表；
  前端只把结果留在 React 内存里。
- **非回环路径 0 条**：新写面与读面共用同一套回环判据，`check_privacy.py` 第 3 组白名单
  继续按「`_watchlist_guard` + `is_loopback_host(` + `host_header_allowed(` + `no-store`」四项校验。

## 3. 同时决定：`/positions` 页面命令条移除（R3 的配套）

DEC-048 的 `PAGE_COMMANDS` 里有一条 `positions → position list`。QUOT-419 R3 要求
「持仓页自动加载本机持仓，不再靠 CLI 查询」，所以：

- 从 `PAGE_COMMANDS` 移除 `positions` 一条，`/positions` 顶部不再渲染命令条；
- CLI `ldst position list` 本身保留（导出 / 对账仍有用），只是不再占据页面入口；
- 页面空态与表单错误里指向终端的文案一并去掉，新增入口只留页面表单。

这是一次**降级而非删功能**：命令仍可手工执行，页面的主路径改成
`GET /api/positions` 自动加载 + `POST /api/positions` 提交。

## 4. 权衡

| 选项 | 取舍 |
| --- | --- |
| 维持「写面只走 CLI」，页面只读 | 否。QUOT-419 的验收就是「进页面能创建、回首页能回显」，只读页面做不到 |
| 新开一条「自选专用」写通道 / 新鉴权 | 否。与持仓写面同形复用 `write_source_allowed`，全仓只有一套来源证据 |
| 把自选写面塞进 `LadderEnvelope` | 否。结构性出口会把隐私数据带进 export / MCP / AI，DEC-015 的底线 |
| 写接口接受 `note` | 否。DEC-052 §2.2 已拍板「自由文本不出」，写面也不开口子 |
| 保留 `/positions` 命令条并排展示 | 否。命令条是「本页主操作」的位置，持仓页的主操作现在是表单，不是查询命令 |

## 5. 落地与验证（QUOT-419 已完成）

- 代码：`src/ladderstock/web/radar.py`（`watch_write_code()`、`RadarPlane.watch_add()` /
  `watch_delete()`、`_watchlist_write_guard()`、两条路由）、`src/ladderstock/web/control.py`
  （移除 `positions` 页面命令）；
- 测试：`tests/unit/web/test_watchlist_write_endpoints.py`（语义 / 校验 / 闸门 / 出口 / 闭环），
  `tests/unit/web/test_watchlist_read_endpoint.py` 的「写面只走 GET」断言同步改为「只有 GET + POST」；
- 隐私：`scripts/check_privacy.py` 5/5 通过；
- 实机：5950 重启后 `POST /api/watchlist` → `GET /api/watchlist` 的 `watch_count` 0→1，
  三条守卫负向用例（无来源证据 / 跨站 `Sec-Fetch-Site` / 坏 `Host`）全部 403。
  证据见 `docs/tasks/TASK-QUOT-419/`。
