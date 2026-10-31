# DEC-031 后端写通道探针：同路径补 GET 回 405，不新增读取 conditions 的 GET

- 日期：2026-10-31
- 提出：后端架构师（QUOT-287，方案来自父 issue QUOT-189 的拍板 A）
- 状态：生效（随 QUOT-287 实现落地）
- 关联：DEC-014（控制面 / 数据面分离、关闭态不暴露）、DEC-016（验证条件写路径）、
  DEC-030（HTTP 层只投影、口径只有一份）、`frontend/src/api.ts:validationWriteProbe`
  （探针契约）、`frontend/src/screens/ValidateScreen.tsx`（「后端」Tab 的可用性判据）

## 1. 背景

`/validate` 屏的「后端」Tab 靠一次写通道探针决定能不能用：`GET /api/control/validation/conditions`
回 **405** 才算「PUT 路由已注册 → 就绪」，回 **404** 被判成「控制面未启用或端点未合入」并置灰。
QUOT-189 在真实部署实测到的是 404，于是后端模式永远打不开，console 每条 `404 GET /api/control/validation/conditions`。

根因不在闸门，在 Starlette 的匹配优先级：`web/server.py:896` 的 SPA 兜底是
`@app.get("/{full_path:path}")`，对 `GET /api/control/validation/conditions` 是 **FULL** 匹配；
`PUT /validation/conditions`（QUOT-197）对同一个 GET 请求只是 **PARTIAL** 匹配。
FULL 优先于 PARTIAL，所以 GET 在路由层就落进兜底回 404，「PUT-only 路由自然产生 405」这个
契约里的默认前提从未成立过——单测当时只断言了 PUT 与闸门，没断言过 GET。

## 2. 决定

### 2.1 只动后端：`build_router()` 里同路径补一个 GET 探针（方案 A）

`web/control.py:2559` 新增 `@router.get("/validation/conditions")`，不进 `_guard` 之外的任何逻辑，
唯一行为是 `raise HTTPException(405, headers={"Allow": "PUT"})`。它**不返回数据**，
前端契约（`api.ts:861`：405 → 就绪、404 → 未就绪）与 `ValidateScreen` 因此零改动。

路由留在控制面 router 内，闸门与其它控制面接口完全一致，不另起一套：

| 场景 | 探针返回 | 由谁决定 |
| --- | --- | --- |
| 回环 + `LDST_CONTROL_ENABLED=1` + Host 合法 | **405** + `Allow: PUT` | 探针处理体 |
| `LDST_CONTROL_ENABLED=0`（含 HEAD / OPTIONS） | **404**，且不带 `Allow` | `server.py` 的方法无关中间件短路 |
| 非回环来源 / Host 不在白名单（DNS rebinding） | **403** | `_guard` |
| 缺写来源证据 | 不影响（`GET` 不在 `WRITE_METHODS`） | `_guard` 只对写方法查证据 |

### 2.2 不选方案 B：不补「读 conditions」的 GET

条件已经能通过数据面 `GET /api/validation?date=` 的 `payload.conditions` 读到，
`ValidateScreen.tsx:244` 的冲突提示用的就是这份数据。再开一个 GET 读同一份条件是**第二个数据面**：
多一套投影、多一套闸门语义（读写面分离是 DEC-014 的边界）、多一处口径漂移的地方（同 DEC-030
「口径只有一份」）。探针要回答的只有「写通道在不在」，不需要顺带把数据搬出来。

## 3. 副作用与残留

- **HEAD 从 404 变 405**：Starlette 给含 GET 的路由自动带上 HEAD，控制面**开启**时
  `HEAD /api/control/validation/conditions` 现在回 405 + `Allow: PUT`。开启态下控制面本就
  只对回环 + 合法 Host 可见，这不是新增暴露；关闭态仍由中间件短路成 404，DEC-014 §1
  「不暴露控制面存在」的不变量由 `test_probe_control_disabled_is_404_without_allow` 钉住。
- **`Allow` 只含 PUT** 依赖 PUT 路由在源码里注册在 GET 之前：Starlette 取**第一个** PARTIAL
  匹配的路由方法集合作为 `Allow`。改动这个顺序会让 OPTIONS 的 `Allow` 变成 `PUT, GET, HEAD`。
- 前端 `api.ts` 探针注释里「在路由层就被拦下，不进入 `_guard`」这句已与实现不符（现在会进
  `_guard`，也正是闸门语义的来源），归前端小队顺手订正，不影响行为。

## 4. 验收

- `pytest tests/unit/web/test_validation_conditions_endpoint.py -o addopts=""` 全绿（新增 5 条探针用例，均断言 `state/` 无落盘）。
- 真实后端 `ldst run --port 5952`：`curl -i http://127.0.0.1:5952/api/control/validation/conditions` → `405` + `allow: PUT`。
- 复测归口 QUOT-189：`/validate` 徽标与「后端」Tab 恢复可用、console 的 `404 GET /api/control/validation/conditions` 归零。
