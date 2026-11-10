# DEC-073: 研究方向目录分页 + 「只看可用」过滤的 URL 语义（QUOT-530）

日期：2026-11-10 · 状态：已定稿 · 关联：QUOT-530

## 1. 决策

`/skills` 目录固定 **每页 10 条**，页码与过滤条件都以 URL 查询参数为唯一真源：

| 参数 | 取值 | 说明 |
|---|---|---|
| `group` | 分组名（缺省 = 全部） | 既有参数，含义不变 |
| `page` | `2..N` | 第 1 页**不写** `page`；越界按末页渲染并 `replace` 收敛 |
| `usable` | `1` | 只在开启时出现 |

排序优先级：`group` → `usable` → `page`。切换分组或开关过滤时删除 `page`，
即回到第 1 页；任何时刻地址栏与屏内实际页码一致。

## 2. 「可用」的判定口径

可用 = `ready` / `partial` / `existing_page` 三种目录状态（70 → 45 条），
也就是「现在点进去能看到步骤或已有页面」的条目。排除 `unconnected` / `planned` /
`not_supported`。

判定只写在 `lib/skills.ts` 的 `USABLE_CATALOG_STATUSES` + `isUsableCatalogItem()`，
屏幕只消费函数，不复制状态清单——以后新增目录状态时只需改这一处。

## 3. 分页条形态

- 新增通用组件 C-23 `Pagination`：上一页 / 页码 / 下一页 + 「第 X / Y 页 · 共 N 条 ·
  每页 M 条」摘要。`≤7` 页给全量页码；更多页给首末页 + 当前页 ±2，省略处显示 `…`。
- 当前页用三重信号：实心 accent 底、`font-semibold`、`aria-current="page"`；
  「上一页 / 下一页」用 `disabled` 而不是只变颜色（UI-SPEC §9）。
- 组件受控、无请求、无业务判断，符合 §6 组件契约。

## 4. 为什么不把「每页条数」做成可选项

本单只要求「每页条数显示」，不要求用户切换。目录 70 条、10 条/页已是 7 页的
可扫读规模，加一个页容量选择器会引入额外状态与 URL 参数，与本次验收无关。
若以后目录数量级变化，再按 `?size=` 扩展，`Pagination` 的 `pageSize` 已是纯展示 prop。

## 5. 未改动的边界

`content/skills/catalog.json`、`readCatalog()`、六个状态徽标、分组 chips、
`/skills/:id`、`/skills/pkg/:name` 与 `existing_page` 外链全部原样；没有新增数据源。
