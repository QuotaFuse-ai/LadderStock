# SPEC-INTRADAY-UI 盯盘 · 竞价与盘中（前端）

- 版本：v0.1（2026-11-01 首版，QUOT-302）
- 日期：2026-11-01
- 归属：前端架构（盯盘第二入口）
- 实现：`frontend/src/screens/intraday/`、`frontend/src/api/intraday.ts`
- 数据口径（**本文件不改口径**）：`SPEC-INTRADAY-READ`（QUOT-301 读接口）、
  `SPEC-INTRADAY-SNAPSHOT`（QUOT-286 落盘结构）；视觉与五态：`docs/UI-SPEC.md`
- 关联决策：DEC-035 §2.3（窗口外不补跑）、§5（页面另行立项）、DEC-028（null 不当 0）、
  DEC-039（回环独占）、DEC-034（API 路由先于 SPA 兜底）

## 1. 路由与导航挂载

| 路由 | 页面 | 一级导航 | 二级入口 | 深链 |
| --- | --- | --- | --- | --- |
| `/intraday` | 竞价与盘中 | 盯盘 | 第二个入口（`Timer` 图标） | `?date=YYYY-MM-DD&slot=HH:MM` |

- 挂在「盯盘」组下，不新增顶级导航项；该组从 1 段变 2 段，`Layout.tsx` 既有的
  `pages.length > 1` 判定自动展开为二级，组件不改。
- `Layout.tsx` 里「竞价与盘中还没立项」的过期注释随本屏删除。
- 与 QUOT-300（导航重构）的关系：QUOT-300 的 `NAV_SECTIONS` 不动 `watch` 分组，
  两侧只在 `App.tsx` 的路由表上重叠一行。QUOT-300 的 `routes.ts` 落地后，
  本屏需要做的只是把 `'/intraday'` 加进 `ROUTE_PATHS`、路由元素加进 `ROUTE_ELEMENTS`。

## 2. 信息架构

```
页头（标题 + 日期 · snapshot_count/slot_count 个时点有快照 · 来源 + 刷新）
├─ partial 提示条（列出没有落盘的时点及各自状态）
├─ 当日时点条（SlotRail：后端 slots[] 一格一渲染，含状态、读数、说明脚注）
└─ 时点快照节选（SnapshotDetail）
   ├─ 口径行：price_field 说明 / 取数工具 / 快照时刻 / 时点记录 detail 原样
   ├─ 三个指标卡：昨日涨停池 count / 今日仍封板 / 平均涨跌幅
   ├─ 情绪面块（6 格 + 1进2 一行）
   ├─ 分板位 badges
   └─ 逐只读数表（载荷顺序，页面不排序、不评分、不推荐）
```

选中时点决定节选内容：默认第一个 `available` 的时点，`?slot=` 深链优先，
都没有可用时点时选第一格。换日清空手动选择。

## 3. 时点状态的六态映射（文字 + 图形双通道）

UI-SPEC §9：状态不得只靠颜色。每格 = 图标 + 文案 + 边框样式，缺一不成立。

| 后端 `status` | 文案 | 图标 | 边框 | 是否算失败 |
| --- | --- | --- | --- | --- |
| `ok` | 已写入 | `CheckCircle2` | `border-ok` | 否 |
| `degraded` | 降级写入 | `AlertTriangle` | `border-warn` | 否 |
| `already` | 已存在·未覆盖 | `CircleDot` | `border-accent` | 否 |
| `skipped` | 已过窗口，不补跑 | `CircleSlash2` | 虚线 `border-border-strong` | **否** |
| `missing` | 未取到 | `MinusCircle` | 虚线 `border-border` | 否 |
| `failed` | 抓取失败 | `XCircle` | `border-danger` | 是（仅这一格） |

两条正交于状态的补充判定：

- `recorded === false`（`status.json` 里没这一行）→ 文案「当天没跑过」，与
  「跑过但没读数」分开说；
- 未知状态值 → 原样透出该值，不折叠成「未取到」（后端加状态不需要改前端）。

`skipped` 的节选面板走 `EmptyState` 并复述后端的窗口原因 + 「窗口外不补跑：用当天
之后的价格回填过去的时点会永久污染数据」（DEC-035 §2.3），**不**渲染成加载失败、
也**不**渲染成空数据。

## 4. 不重算边界

页面只显示，不计算：`count / fresh / stale / avg_change_pct / by_board / 封板率 /
炸板率 / 1进2 / 逐只读数` 全部取 `payload.snapshot`（即 `<HHMM>.json` 原文）里的值，
与候选池屏同口径。快照原文里契约未列出的键收进 `extra` 原样保留，不丢信息也不解读。

时点表同样来自后端：`slots[]` 给几格就渲染几格、给什么时刻就显示什么时刻，
前端不持有 `09:25…15:00` 常量（`demo.ts` 里的 `DEMO_SLOTS` 只在 `?demo=1` 用）。

## 5. 五种界面状态

优先级沿用 `lib/pageState.ts`：`error > offline > partial > loading > empty > normal`。

| 状态 | 触发（真实） | 触发（演示） | 表现 |
| --- | --- | --- | --- |
| normal | `available` 且 `snapshot_count === slot_count` | `?demo=1&variant=full` | 满条 + 节选 |
| partial | `available` 且 `snapshot_count < slot_count` | `?state=partial` / `?demo=1&state=partial`（2 格 missing） | 提示条 + 六格各显示自身状态 |
| empty | `available === false`；或 404（接口未接入） | `?state=empty` | 后端 `reason` 原文 + 时点条仍在 |
| loading | 请求中 | `?state=loading` | 骨架 |
| error | 非 404 / 非断连的取数失败 | `?state=error` | `ErrorBlock` + 重试 |
| offline | `not-connected` | `?state=offline` | 离线条 + 不显示任何读数 |

`?state=` 强制出来的空态会明说「这一天实际有 N/M 个时点落盘，只是把空态摆出来」，
不反过来否认已经取到的数据。

## 6. 视觉与无障碍不变量

- 所有数字进 `.ldst-num`（`tabular-nums`），涨跌幅红涨绿跌带 `↑` / `↓`；
- 缺值是「—」，不是 0（DEC-028）；`at_limit_up === null` 说「不按涨幅阈值猜」；
- 时点格是真正的 `<button>`，`aria-pressed` + `aria-label="${slot} ${状态文案}"`，
  键盘可达、不依赖 hover；
- 读数表有 `caption`、表头 `scope="col"`，数值列右对齐；
- 1280 起内容列不横向溢出；`<1280` 侧栏收成 56px 图标栏时二级展平，入口仍可达。

## 7. 验收对照（本机 `ldst run` 生产态）

| 要求 | 证据 |
| --- | --- |
| 六个时点状态各一格 | `data/intraday/2026-10-28`（采集口自己跑出 ok/degraded/already/skipped/missing/failed）→ 截图 `08-six-states` |
| 不补跑不等于失败 | `09-skipped-detail`（11:30 走 EmptyState + 窗口原因） |
| 未取到显示「—」不是 0 | `10-missing-detail`、`12-demo-partial`（2 格未取到） |
| 真实空态 | `11-no-data-day`（`2026-11-11` 无 status.json，后端 reason 原文 + 六格「当天没跑过」） |
| 五态可复现 | `02`–`06` 截图，均 `?state=` 强制 |
| 1280 无横向溢出 / console 0 报错 | 11 张截图全部 `scrollWidth === innerWidth`、溢出元素 0、console 0 条 |
| 六态 / 五态 / 契约回归 | `tests/intraday-screen.test.tsx`（16 例）、`tests/intraday-api.test.ts`（12 例） |

## 8. 已知边界

- 不做历史日期回填选择器（本屏只读，日期靠 `?date=`）；
- 不新增全局状态库，取数沿用 `useAsync` + 每屏自己的 source；
- 快照接口是回环独占（DEC-039），从非回环地址访问会拿到 403，页面按错误态显示后端
  原文，不降级成空数据；
- 单包 646 kB 的构建告警是全仓既有问题，不在本单内处理。
