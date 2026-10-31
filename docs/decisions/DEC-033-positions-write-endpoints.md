# DEC-033 持仓写面照 vibe-astock：全 POST、回环独占、六字段白名单、硬删除

- 日期：2026-11-01
- 提出：项目领导人（QUOT-285）；口径由老板 2026-10-31 10:39 拍板
- 状态：生效（随 QUOT-285 实现落地）
- 关联：DEC-006（数据只落本机）、DEC-004（回环 5950）、DEC-014（控制面 / 数据面分离）、
  DEC-015 Tier 1（隐私库不进信封与 MCP）、DEC-020（隐私库 v2 与持仓边界）、
  DEC-030（雷达四接口，本轮写面的前置）、`SPEC-RADAR-PAGES` §6.2、`SPEC-MY-POSITION`

## 1. 背景

`GET /api/positions` 已由 QUOT-279 交付，页面要能增删改就必须有一条本机写路径。
老板已定：**照参考仓库 vibe-astock 的做法做**，不再在「HTTP 写 / 只走 CLI」之间选方案。

参考仓库（`simonlin1212/vibe-astock`）的实测口径：

- 没有「改持仓」的资源接口，持仓由台账流水重放算出；写面只有 `POST /ledger` 与
  `POST /ledger/{kind}/delete`，**删除也不用 `DELETE`**；
- 写方法必须是 JSON，并带同源 `Origin` / `Sec-Fetch-Site` 证据——换 `DELETE` 会绕过
  「无预检的简单请求」这道防线；
- API 只绑回环，token 文件 `0600`；批量导入全写或全不写、写前备份；
- 删除不留审计链：台账 JSON 就是记录本身，`audit_chain` 只登记研究证据。

我们的差异只有一处：**不引入 trade 流水 + 重放**。那张表是为了结算已实现盈亏 / 分红；
本产品只展示浮动盈亏，`positions` 表就是唯一真相，不存在「两份真相」。

## 2. 决定

### 2.1 只有两条写路由，且都是 `POST`

| 方法 / 路径 | 语义 |
| --- | --- |
| `POST /api/positions` | 一条持仓按 `code` upsert |
| `POST /api/positions/{code}/delete` | 硬删除 |

不提供 `PUT` / `PATCH` / `DELETE`。理由不是风格：现有写方法来源判据
（`web/control.py::write_source_allowed`）挂在 `WRITE_METHODS` 上，换动词就等于换一条
没被覆盖的路径；全 POST 让「同源 Origin 或 `X-LDST-Client: 1`」这一条判据覆盖全部写操作。

### 2.2 六字段白名单，白名单就是拒绝清单

`code` / `cost` / `qty` / `entry_date` / `industry` / `note`，多一个键就 `400`。
缺三个必填（`code` / `cost` / `qty`）`400`；`cost` / `qty` 非数值或为负 `400`；
`entry_date` 非 `YYYY-MM-DD` `400`。券商账号、资金密码类字段没有入口，不做模糊匹配。

### 2.3 不新增存储、不改表结构

落库仍走 `journal.position.add_position(upsert=True)`：一条 SQLite UPSERT，`positions`
就是唯一副本。`entry_date` 省略时新增取今天、覆盖保留原建仓日（不把「没填」当成「改到今天」）。

### 2.4 删除是硬删除，不留审计链

直接删行：没有软删除标记、没有审计表、没有备份目录。要留底由用户自己导出
（`ldst position list --export <path>`）。本机 SQLite 是唯一副本，为隐私数据再存一份
「已删除的副本」反而扩大暴露面。

### 2.5 闸门复用，不新增第二种鉴权

回环独占 + `Host` 白名单（同 `GET /api/positions`），写方法额外要求同源 `Origin` 或
显式头 `X-LDST-Client: 1`，`Sec-Fetch-Site` 只认 `same-origin` / `none`。
`LDST_CONTROL_ENABLED=0` 关的是控制面，不连带关持仓写面（与 `_journal_guard` 一致）。

## 3. 明确不做

- 不引入 `trade` 流水 + 重放（我们只算浮动盈亏，没有「两份真相」）；
- 不做 `PUT` / `PATCH` / `DELETE` 动词；
- 不做跨站 / 远程写入口，不回环之外绑定；
- 不做批量导入的「全写或全不写 + 写前备份」——本轮没有批量写接口，单条 upsert 出错即整体不落库。

## 4. 影响

- 新增：`tests/unit/web/test_positions_write_endpoints.py`（校验 / 闸门 / upsert / 硬删除 / 平面 JSON）。
- 改动：`journal/position.py`（`add_position(upsert=True)`，默认行为不变）、
  `web/radar.py`（两条写路由 + 写闸门 + 校验）、`web/server.py`（模块注释补写面）、
  `docs/specs/SPEC-RADAR-PAGES.md` §6–§8、`frontend/src/api/radar.ts`（
  写调用改 POST，删除改 `POST …/delete`）、`frontend/src/screens/radar/*`（编辑与新增共用 upsert）。
- `GET /api/positions` 实现不动（QUOT-279）。
