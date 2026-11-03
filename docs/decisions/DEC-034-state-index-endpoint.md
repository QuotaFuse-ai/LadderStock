# DEC-034 生产态状态索引端点 `/__state/index`：语义照抄 Vite 中间件、落盘原子写、写方法要来源证据

- 日期：2026-11-01
- 提出：后端架构师（QUOT-288）
- 状态：生效（随 QUOT-288 实现落地）
- 关联：DEC-001（Python 不读 IndexedDB）、DEC-004（回环 5950）、DEC-014（控制面 / 数据面分离）、
  DEC-033 §2.5（写方法来源证据的判据）、`docs/UI-SPEC.md` §12.2 第 2 条、
  `frontend/vite.config.ts`（开发态实现，口径的唯一参照）、`frontend/persist/indexReporter.ts`

## 1. 背景

`ldst run` 生产态（托管 `frontend/dist`）7/7 路由的 console 各有一条
`405 POST /__state/index`（QUOT-189 审计）。原因很直白：那个端点只活在 Vite 插件里，
静态产物不含中间件，Python 侧没有同名路由，POST 落进 SPA 兜底（`@app.get("/{full_path:path}")`）
被自动回 405。副作用不止是脏日志：

- `ldst state inspect` 在生产态永远看不到前端分片（文件没人写）；
- `ldst state reset` 的 `pending_reset` → 前端清除 → `ack_reset` 回执这条链断了。

前端不改字段、不改路径就能从开发态切生产态，是本单的前提。

## 2. 决定

### 2.1 两条路由，路径与开发态逐字一致

| 方法 / 路径 | 语义 |
| --- | --- |
| `GET /__state/index` | 返回当前索引 + `pending_reset`，`Cache-Control: no-store` |
| `POST /__state/index` | 接收一次上报，合并后原子落盘 `<data_root>/state/frontend-index.json` |

其余方法（`PUT/PATCH/DELETE/OPTIONS`）显式回 `405` + `Allow: GET, POST`，与开发态中间件
同形——不交给路由层的自动 405：那条只报第一条部分匹配的方法（`Allow: GET`），把 POST 藏了。

路径**不进 `/api/*`**：它是本机状态面，不是数据面接口。因此必须注册在 SPA 兜底之前，
否则 GET 会被兜底当成前端路由回一段 `index.html`。实现落在独立模块
`src/ladderstock/web/state_index.py`（`StateIndexPlane` + `build_router`），
`create_app` 只加一行注册——与控制面 / 研究面 / 产业面 / 雷达面同一个组织方式。

### 2.2 载荷：五字段白名单，缺字段保留上一份

落盘文件只留 `backend` / `schema_version` / `generated_at` / `stores` / `pending_reset`，
其余键丢掉。合并规则照抄 Vite：某字段本次没带（或类型不对）就**沿用上一份**，不补空值。

理由不是省事：`syncPendingResets()` 的回执上报只带 `backend` + `ack_reset`，
若按「没带就是空」处理，`state inspect` 会以为分片全消失了。
白名单同时是拒绝清单——将来前端多带字段不会静默写进本机文件。

`pending_reset` 条目要求 `store` 为字符串，缺 `requested_at` 补当前时间，`note` 保留：
`ldst state reset` 写的格式与这里读的格式是同一份，两侧不会互相看不懂。

### 2.3 原子写：随机名临时文件 + `os.replace`

`ldst state inspect` 与前端 GET 都可能撞上写入中，所以必须整份替换。
CLI 侧（`cli/state.py`、`core/validation.write_document`）用的是固定的 `<name>.tmp`；
服务端**改用随机后缀**，否则两条通道同时写会共用同一个临时文件、把对方写成半截再 replace，
产出一个坏文件——那正是原子写要消灭的东西。坏文件读出来按空索引降级（同 Vite），不抛。

写入失败（磁盘 / 权限 / Windows 上目标被占用）→ 500 + 原因，**不冒充 200**：
前端 `markIndexReported` 只认 `response.ok`，回 200 就是把失败说成成功。

进程内用一把锁串行化「读—改—写」。跨进程（CLI 与 server 同时改 `pending_reset`）
仍是「谁后写谁赢」的窗口，最坏后果是一条重置登记延到前端下次启动处理，不丢数据。
为这个窗口上文件锁不划算。

### 2.4 上限 2 MB，超限 413

先认 `Content-Length`（超了直接不回读 body），再逐块累加挡住分块传输——
只认头字段等于给 `Transfer-Encoding: chunked` 留了后门。超限不写盘。
坏 JSON / 空 body / 顶层不是对象 → 400，带原因。

上限值与开发态 `MAX_BODY_BYTES` 同档（2 MB）：一次真实上报约 2 KB，2 MB 是「正常值的千倍」，
既能挡住把本机磁盘当垃圾桶的玩法，又不会挡住清单将来长到几百个分片。

### 2.5 闸门：回环 + `Host` 白名单，写方法另需正向来源证据

- 非回环来源 403；`Host` 不在白名单 403（挡 DNS rebinding）——与控制面 / 日志面同一套函数。
- **不看 `LDST_CONTROL_ENABLED`**：那个开关关的是写交易数据的控制面。这个文件里只有
  分片名、版本、体积；关掉控制面的运维仍要 `state inspect` 出得来东西。
- **POST 额外要求同源 `Origin` 或 `X-LDST-Client: 1`**（DEC-033 §2.5 同一判据，不新增第二种鉴权）。

第三条比原单要求（「回环 + `Host`」）多一步，是因为它是一个**会落盘的写接口**：
跨站页面用 `fetch` + `text/plain` 包一段 JSON 属于「无预检的简单请求」，回环 + `Host`
挡不住它（浏览器自己会带本机 `Host`），只有 `Origin` 判据能挡。挡住的东西不敏感
（一个本机文件的当前内容），但这道判据本来就在，不用才是漏洞。
本机页面的同源 `Origin` 天然满足，前端零改动。

### 2.6 响应形状照抄开发态

`{"ok": true, "path": "data/state/frontend-index.json", "cleared_reset": [...]}`。
`path` 是与开发态逐字一致的常量：前端不看它，但文档与排查看得见，两种运行态出现两个
字符串只会让人以为路径真的不同。

## 3. 不做什么

- **不读 IndexedDB、不碰前端存储**（DEC-001 不变）：这个端点只是把前端报上来的清单存盘。
- **不新增第三种持久化**：文件还是 `data/state/frontend-index.json`，`state inspect` 的读法一行没改。
- **不给 `/__state/*` 加别的子路径**：`ldst state reset` 继续走 CLI，不在这上面开写交易的口子。
- **不改前端**（`indexReporter.ts` / `vite.config.ts` 一行没动）：开发态与生产态现在是同一份语义，
  不需要在代码里区分运行态。

## 4. 验证

`tests/unit/web/test_state_index_endpoint.py`（24 例）钉住：POST→落盘、GET 读回一致、
损坏文件降级为空、`ldst state inspect` 读得到上报的分片（`LDST_DATA_DIR` 指过去直接跑
`build_report()`）、其余方法 405 + `Allow: GET, POST`、GET 不被 SPA 兜底答成 HTML、
超限 413（含无 `Content-Length` 的分块一支）、坏 JSON / 空 body / 顶层非对象 400、
非回环 403、非法 `Host` 403、缺来源证据的 POST 403、`Sec-Fetch-Site: cross-site` 403、
`pending_reset` 不被普通上报抹掉、`ack_reset` 只清对应那一条、部分上报不清空概况、
白名单外的键不落盘、`LDST_CONTROL_ENABLED=0` 不影响这两个路由。

生产态验收（QUOT-189 的 console 归零）：`ldst run` 起在 5952 后，
`POST /__state/index` 回 200 而不是 405，`data/state/frontend-index.json` 随持久化更新。

## 5. 遗留

- 前端 `usePersistenceStatus.setIndexEndpoint()` 现在在生产态会真的变绿；
  UI-SPEC §12.2 第 2 条可以销账（该文档由前端架构师维护，本单不代改）。
- 若将来要把 `pending_reset` 做成即时推送（而不是等前端下次启动），需要一条 SSE / 轮询通道，
  那是另一个决策，不要塞进这个落盘端点里。
