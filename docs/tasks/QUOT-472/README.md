# QUOT-472 首页 B 块 AI 研究入口：交付与验收取证

- 日期：2026-11-07
- 分支：`agent/agent/38a9d432025c`
- 决策：`docs/decisions/DEC-067-home-block-b-ai-research-entry.md`
- 规格：`docs/specs/SPEC-HOME-V2.md` v1.1 §3 / §9 / §12-L01·L08

## 1. 交付内容

首页 B 块从「聚合搜索入口」升级为 AI 研究对话入口：四条起步问题、多行输入
（Enter 发送 / Shift+Enter 换行）、发送 / 停止 / 清空、回答下「本轮来源」（来源名 + 项数 +
取数时刻 + 页面链接），无来源时明确写「本轮未取数」。通道复用 QUOT-454 的
`POST /api/research/{scope}/assistant`，未新增依赖、未改后端协议；原聚合搜索完整保留在
「不经过 AI，直接搜」分区。

### 1.1 改动文件

| 文件 | 行 | 内容 |
|---|---|---|
| `frontend/src/screens/home/homeAssistant.ts` | 1–201 | 新增。首页上下文白名单（指数四格 / 自选 ≤20 / 盯盘 ≤10 / 验证仅条数；持仓永不进入）、`buildHomeAssistant()`（`:93`）、`serializeHomeAssistantContext()`（`:199`）、「本轮来源」构建 |
| `frontend/src/screens/home/HomeResearchChat.tsx` | 1–339 | 新增。对话 UI：`SUGGESTIONS`（`:53`）、`ask()`（`:116`，含快照、Abort、停止/重试）、`SourceList`（`:60`）、Enter/Shift+Enter（`:187`）、清空（`:176`） |
| `frontend/src/screens/home/HomeSearch.tsx` | 1–120 | 重写装配：对话在上（`:70`），原搜索入口 + 三行映射 + 最近搜索在「不经过 AI，直接搜」分区（`:72–115`），`?search=` 深链消费保持（`:54–60`） |
| `frontend/src/screens/HomeScreen.tsx` | 30, 122–129, 278 | 传入四块视图，`buildHomeAssistant(tradeDate, {...})` memo 后交给 `HomeSearch` |
| `frontend/src/api/research.ts` | 941–976 | 新增 `HOME_ASSISTANT_SCOPE = 'home'` 与 `askHomeAssistant()`，内部走既有 `askResearchAssistant` |
| `frontend/tests/home-research-chat.test.tsx` | 1–349 | 新增 8 条测试：建议填充、Enter 发送与上下文键白名单（无持仓字段）、来源清单、未取数、503 无 Key 重试、断连、停止、Shift+Enter、清空 |
| `frontend/tests/home-search.test.tsx` | — | 深链用例改为注入 `HomeAssistantBundle` 夹具，断言不变 |

### 1.2 明确没做（需要新接口/新能力）

1. **后台研究任务的「起前确认 + 取消写原因」**：本仓没有 `pending_research` 类接口，
   `POST /api/research/{code}/run` 只能对已存在的 `(code, day)` 续跑单段，前端无法凭一句话起任务。
   已在 SPEC §12-L08 上报，不造一个不会发生的任务。
2. **联网搜索**：通道只把页面上下文发给模型，不做检索；文案不出现「联网 / 实时抓取」。

## 2. 验收取证

### 2.1 截图（1440×900，深浅各一组）

截图随 QUOT-472 的交付评论以附件形式提供（本目录 `shots/` 下是同一批 PNG 的本地副本，
按仓库 `.gitignore` 不入库；`entry-1280-light.png` 为 1280 宽版本）。

| 状态 | 浅色 | 深色 | 说明 |
|---|---|---|---|
| 正常 · 入口 | `shots/entry-light.png` | `shots/entry-dark.png` | 标题、副标题、四条起步问题、多行输入、直接搜分区 |
| 正常 · 一问一答 | `shots/ask-light.png` | `shots/ask-dark.png` | 真实请求经本机后端 → 本机 stub 模型；回答 + 「本轮来源」 |
| 无 Key | `shots/nokey-light.png` | `shots/nokey-dark.png` | 真实后端 503 `ai_not_configured`：「研究助手未配置模型」+ 后端原文 + 重试 |
| 断连 | `shots/offline-light.png` | `shots/offline-dark.png` | CDP 在浏览器网络层拒绝 `assistant` 请求（`ConnectionRefused`），前端走真实 `not-connected` 分支：「本机服务未连接」+ 重试；页面数据不受影响 |
| 深链搜索 | `shots/panel-light.png` | — | `/?search=600519` 打开「聚合搜索」面板并出行（600519 → 个股深挖） |
| 1280 宽 | `shots-1280/entry-light.png` | — | `scrollWidth = innerWidth = 1280`，无横向溢出 |

> **模型通道标注**：本机没有可用模型 Key（`claude` CLI 路径失效、`codex` 订阅分组 403、
> 无 `LDST_LLM_*` 环境或 `ai.env`）。`ask-*` 两张是**链路验证**：前端 → 本机 FastAPI →
> 本机回环 OpenAI 兼容 stub（固定文本，只为证明请求/响应/来源渲染整条链路通），
> **不代表模型能力**。真实模型调用待 Key 可用后按 §4 复跑。

### 2.2 请求 / 响应（`assistant-call.json` 摘要）

```
POST http://127.0.0.1:5951/api/research/home/assistant
headers: accept=application/json, content-type=application/json, x-ldst-client=1
body: { question, date, context }
context keys: date, market, page, unavailable, validation, watch, watchlist   ← 无 positions
响应: 200 { reply, model: "openai:user-model", fetched_at: "2026-11-07T09:44:23+00:00" }
```

`context.market.indices[0]` 为 `{ code: "sh000001", name: "上证指数", price: 3649.18, changePct: -1.1, asOf: "2026-11-06T16:14:01" }`；
本轮该环境只有指数数据，`watchlist / watch / validation` 为空，所以「本轮来源」只出现
「大盘与全球指数 · 4 项 · 取数 11-06 16:14 · 首页 A 块」——与截图一致。

### 2.3 门禁原样输出

- `npm run typecheck` → 无输出（`tsc --noEmit`，exit 0）
- `npm run test` → `Test Files 72 passed (72)` / `Tests 666 passed (666)`
- `npm run build` → `✓ built in 12.77s`
- `npm run check:ui` → `5/6 项通过`；`arbitrary` 一项 FAIL 的三处均为**既有**违规
  （`ResearchSymbolBar.tsx:219`、`AiDock.tsx:465`、`EventSection.tsx:474`），
  本次改动文件零新增。

原始日志：`gate-logs/typecheck.log`、`gate-logs/test.log`、`gate-logs/build.log`、`gate-logs/check-ui.log`；
请求 / 响应原样：`gate-logs/assistant-call.json`。

## 3. 回归确认

- `?search=600519` 深链打开面板并出结果（`shots/panel-light.png`）；
- 深链用例、`/` 快捷键、Esc 焦点回归用例保持绿色（`frontend/tests/home-search.test.tsx`）；
- `useHomeSearch` / `SearchPanel` / `lib/searchCore.ts` / `searchIntent.ts` / `searchRoutes.ts`
  未改一行。

## 4. 复跑步骤

```bash
# 1) 后端（必须从本仓 src 起；PATH 里的 python 指向另一个 worktree 的 editable 安装）
cd <repo> && PYTHONPATH=src python -m ladderstock run --port 5951 --output json

# 2) 前端构建产物由后端同源托管（写请求的来源校验要求端口一致）
cd frontend && npm run build

# 3) 配好模型 Key（或本机兼容端点）后，正常态改用真实模型复拍：
LDST_LLM_BASE_URL=... LDST_LLM_API_KEY=... PYTHONPATH=src python -m ladderstock run --port 5951

# 4) 门禁
cd frontend && npm run typecheck && npm run test && npm run build && npm run check:ui
```
