# QUOT-551 · 首页 B 块三路径 clarity（拆自 QUOT-519）

只改**呈现 / 文案 / 视觉区分**，让普通人一眼分清三条研究路径，并让两组卡片不再同款；
**不删任何能力、不改任何行为**。

## 做了什么

1. **三路径序号标签**（新件 `frontend/src/screens/home/HomePathLabel.tsx`，纯呈现无行为）：
   B 块自上而下摆明主次——
   - ① 问 AI · 自由提问（主，`tone=primary`）
   - ② 开现成工具 · 点一下直达（次，`tone=secondary`）
   - ③ 直接搜 · 不经过 AI（末，`tone=tertiary`）
   徽标恒为 `bg-accent-soft` 底，读作「同一类路径标记」；主次靠 `tone` + 各自标题字号
   （h2 subtitle > h3 13px > caption）区分。
2. **两组卡片视觉区分**：
   - **起步问题**：从与工具卡同款的 2×2 矩形卡改为 tinted **问句 pill**
     （`rounded-pill` + `bg-accent-soft` + `TextCursorInput` 填入图标，`flex flex-wrap`），
     暗示「点一下填入输入框」。文案改为「不知道问什么？点一条填进输入框，改完再发：」。
   - **工具卡**：保留带箭头（`ArrowRight` / `ExternalLink`）的矩形卡，暗示「点一下跳转」。
   - hover / focus 反馈保留（`hover:border-accent` / `focus-visible:ring-2`）。
3. **文案白话化**：工具区副标题加「不想提问？」前缀；直接搜标题「直接搜 · 不经过 AI」。

## 零回退（红线守住）

- 起步问题按钮 `aria-label={question}` 钉死不变 → 点一下仍只填输入框 + 聚焦。
- 工具卡 `span` 结构、`getByRole('heading'/'region', { name: '研究工具快捷入口' })`、
  「换一批」按钮名不变 → 仍跳 `/skills/:id` 或 `existingPageTarget(item)`，「换一批」仍只换工具。
- 搜索状态机与呈现件入参 / 返回形状零改动：`useHomeSearch` / `SearchPanel` /
  `lib/searchCore.ts` / `searchIntent.ts` / `searchRoutes.ts` 未碰；结果模态仍一份实现。
- 对话通道与「本轮来源」口径（`POST /api/research/{scope}/assistant` / `describeAssistantError`）未碰。
- 快捷入口抽样口径（`homeShortcuts.ts`）未碰，不编演示数据。
- 零新增 token、零新依赖、零新断点；只改 4 个文件（3 改 1 新）。

## 改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/screens/home/HomePathLabel.tsx` | 新增 | 三路径统一序号标签（纯呈现） |
| `frontend/src/screens/home/HomeResearchChat.tsx` | 改 | ① 标签 + 起步问题 pill 化 |
| `frontend/src/screens/home/HomeResearchShortcuts.tsx` | 改 | ② 标签 + 副标题白话化 |
| `frontend/src/screens/home/HomeSearch.tsx` | 改 | ③ 标签 + 顶部说明段 |
| `docs/specs/SPEC-HOME-V2.md` | 改 | v1.4：§3.1 线框 ①②③ + clarity 段、§9 验收项、变更记录 |

## 验证证据

**本地 CI 三门（与 `.github/workflows/ci.yml` frontend job 同命令）**
- `npx tsc --noEmit` → exit 0
- `npx vitest run` → **999 passed（101 files）**
- `npx vite build` → built ok
- `npm run check:ui` → 3 项 FAIL 全部落在**未触碰**的基线文件（ProxyPoolCard / PortfolioReplayTab /
  PageCommandBar 等），本次 4 个文件**零新增违规**；check:ui 非 CI 闸门。

**真机（5950）浏览器回归（CDP headless，10/10 通过）+ 截图**
- `home-after-dark.png` / `home-after-light.png`：深 / 浅色整页，三路径序号 + 两组卡片区分。
- `verify-path1-pills.png`：① 起步问题 pill 组。
- `verify-path2-tools.png`：② 工具卡（带箭头）组。
- `verify-path3-search.png`：③ 直接搜分区。
- `verify-pill-filled.png`：点 pill → 输入框被填 + 聚焦（未发送）。
- `verify-tool-navigated.png`：点工具卡 → 跳转生效。
- `verify-panel-open.png`：点直接搜 → 既有模态面板打开。
- `verify-deeplink-empty.png` / `verify-deeplink-results.png` / `verify-deeplink-noresult.png`：
  `?search=` 深链三态（展开 / 有结果 / 无结果）。

## 交付

PR + CI 双绿 + 上述证据；合并 / 关单报批（不自合）。