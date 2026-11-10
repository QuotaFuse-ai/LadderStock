# QUOT-531 首页 B 块「研究工具快捷入口」：交付与验收取证

- 日期：2026-11-10
- 分支：`agent/agent/efe6b9fbb279`
- 规格：`docs/specs/SPEC-HOME-V2.md` v1.3 §3 / §9（QUOT-531 修订）
- 基线：`origin/main` @ `2f29337`

## 1. 交付内容

首页 B 块「今天，想研究什么？」在 AI 对话与四条起步问题**下方**新增独立区块
「研究工具快捷入口」：从 `content/skills/catalog.json` 按组随机抽 4 条可点条目，
2×2 卡片（复用起步问题卡的版式与 token），点一下直达 `/skills/:id` 或已承载页面
（`/limit-up`、`/watch`）；「换一批」重抽，每次进入首页重新挂载即换一批。
对话通道、「本轮来源」口径、起步问题与直接搜全部零回退。

### 1.1 改动文件

| 文件 | 行 | 内容 |
|---|---|---|
| `frontend/src/screens/home/homeShortcuts.ts` | 1–111 | 新增。`shortcutHref()`（`:27`，唯一落点判定）、`pickHomeShortcuts()`（`:65`，按组抽样：一组最多一条、组内优先 `ready` / `existing_page`、组数不足才补齐）、`HOME_SHORTCUT_COUNT = 4`（`:21`） |
| `frontend/src/screens/home/HomeResearchShortcuts.tsx` | 1–113 | 新增。区块 UI（`:38`）：2×2 卡片、分组/状态副标题、`existing_page` 用外链图标、「换一批」（`:44`）；随机只在挂载与按钮点击时发生 |
| `frontend/src/screens/home/HomeSearch.tsx` | 9、24–27、76 | 在对话与直接搜之间装配快捷入口；文件头注释同步 |
| `frontend/tests/home-research-shortcuts.test.ts` | 1–164 | 新增 7 条：4 条 / 跨组 / 可点优先 / 排除 planned·not_supported / 不足补齐 / 同 seed 稳定与换批重抽 / 落点 |
| `frontend/tests/home-research-shortcuts.test.tsx` | 1–97 | 新增 4 条：4 条渲染与落点合法、换一批重抽、点击真跳转、重新挂载换批 |
| `docs/specs/SPEC-HOME-V2.md` | 头部、§3.1、§9 | 版本 v1.3：线框与边界补快捷入口，验收加一条 |
| `docs/tasks/QUOT-531/shots/` | — | 本地取证副本（PNG + `evidence.json`，按 `.gitignore` 不入库） |

### 1.2 抽样口径（纯前端）

- 只读 `catalog.json`（`readCatalog()`），不写第二份清单、不编演示数据，不引后端。
- 可点判定只有一处 `shortcutHref()`：`existing_page` → `existingPageTarget(item)`；
  `ready` / `partial` / `unconnected` 且有包 → `/skills/:id`；`planned` / `not_supported` 无落点、不参与。
- 分组：一组最多一条；组内先取 `ready` / `existing_page`，没有才退到 `partial` / `unconnected`；
  组数不足 4 时从剩余可点条目补齐。随机源可注入（测试用），页面默认 `Math.random`。

### 1.3 明确没做

- 不新增后端接口、不做「按用户偏好排序 / 学习点击」——没有这类数据源，不造。
- 不在快捷入口上起研究任务：只做页面跳转，与 §12-L08（无 research-start 接口）一致。

## 2. 验收取证

### 2.1 门禁（本地原样输出）

```
npm run typecheck  → exit 0
npm test           → Test Files 92 passed (92) / Tests 908 passed (908)
npm run build      → ✓ built in 8.26s
```

### 2.2 浏览器实机（headless Chrome + CDP，dev server `127.0.0.1:5173`）

21/21 断言通过（原样见 `shots/evidence.json`）：

| 检查 | 结果 |
|---|---|
| B 块标题 =「研究工具快捷入口」，卡片 4 条 | 4 条 / 4 个不同分组 |
| 落点合法 | `/skills/13`、`/skills/35`、`/limit-up`、`/skills/3` |
| 「换一批」×3 | 每批仍 4 条 4 组，组合发生变化（见 `evidence.batches`） |
| 刷新首页 | 组合变化（4 条全换） |
| 点详情类卡片 | `/skills/5`（机构预期追踪） |
| 点「已在页面」卡片 | `/limit-up`（涨停样本统计） |
| AI 对话 / 四条起步问题 | 输入框与 4 个问题按钮均在 |
| 1440 / 1280 宽 | 无横向溢出（1440=1440、1280=1280） |

截图（本地 `shots/`，随评论附件提供）：

| 文件 | 内容 |
|---|---|
| `quot-531-home-light.png` | 浅色：起步问题 + 快捷入口 2×2 + 直接搜 |
| `quot-531-home-light-refreshed.png` | 浅色：换一批之后 |
| `quot-531-home-dark.png` | 深色：`existing_page` 卡片带外链图标与「已在页面」 |
| `quot-531-jump-detail.png` | 点详情卡后的 `/skills/:id` |
| `quot-531-jump-existing-page.png` | 点「涨停样本统计」后的 `/limit-up` |

> 复现脚本为本次取证用的临时 CDP 脚本（工作目录 `quot-531-verify.mjs`，不入库）；
> 断言与截图均由真实页面 DOM 产生，未注入前端代码。

## 3. 风险与后续

- 合并需 CI 双绿 + 证据齐全 + 用户批准；本任务只开 PR，不自行合并。
- 快捷入口完全由 `catalog.json` 派生：内容侧改清单后无需改前端；若新增 `existing_page`
  条目，需同步 `lib/skills.ts` 的 `EXISTING_PAGE_TARGET`（既有口径，未新增第二份表）。
