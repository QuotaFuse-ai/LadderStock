# DEC-083：首页研究对话的通道归属、会话语义与「研究台」定义（QUOT-573）

日期：2026-11-13
关联：QUOT-573（本单）、QUOT-572（总经理统筹父单）、《对话模式与研究台-设计与开发任务书》§3.1 / §4.3 / 阶段 B、
DEC-067（首页 B 块复用 QUOT-454 通道）、DEC-067 对话通道（`chat.py`，QUOT-473）、
SPEC-HOME-V2 §3.1 / §12-L08、SPEC-AI-CHAT（v0.2）、SPEC-RESEARCH-PAGES §1
状态：已定稿（「研究台」一项为**需确认**，见 §6）

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| 首页对话通道 | **方案 A：走专用页面助手通道 `POST /api/page/assistant`**（`assistant.py:441` `PAGE_PREFIX`）。否决方案 B（`/api/control/ai/chat`） |
| 与任务书的关系 | 任务书阶段 B 只要求「改 `assistant.py` 三个路由追加 `blocks`」；首页 = 页面助手 = 进阶段 B 范围，**不需要新增任何路由** |
| 会话语义 | **单轮无状态**。前端不新增 `session_id`，后端不落会话；会话记忆由任务书阶段 C 的 `chat_session / chat_turn` 承接，不在本通道上做 |
| `pending_research` | 本通道**不返回**该字段；SPEC-HOME-V2 §12-L08「不做」裁定维持。未来任何通道若返回，**允许不渲染，但禁止渲染成「起任务」按钮** |
| 「研究台」 | **需确认**（§6）。正文措辞最支持「首页 E 研究面板」；任务书内部又有两处与 `/research` 个股深挖冲突，二选一需老板拍板 |

## 2. 现状实证（全部实读，非推断）

### 2.1 前端当前打的是哪条路

| 事实 | 位置 |
|---|---|
| 首页对话 scope 常量 | `frontend/src/api/research.ts:952` `HOME_ASSISTANT_SCOPE = 'home'` |
| 首页入口复用个股深挖接口 | `frontend/src/api/research.ts:1025-1030` `askHomeAssistant` → `askResearchAssistant('home', …)` |
| 结果：`code === 'home'` 进研究助手 | `src/ladderstock/web/assistant.py:379-397` `research_assistant(code, body)` |

### 2.2 后端三条现役助手路由的差异（`server.py:1617-1632`，均实读）

| 路由 | 挂载 | `module` | `page` | `system_prompt` | `symbol` |
|---|---|---|---|---|---|
| `POST /api/research/{code}/assistant` | `server.py:1620` `build_assistant_router()` | **硬编码 `"senate"`**（`assistant.py:393`） | `"research"` | `SYSTEM_PROMPT`（个股深挖预设） | `= code`（即 `'home'`） |
| `POST /api/skills/assistant` | `server.py:1626` `build_skills_router()` | `module_for(page_key)` | `"skills"` | `SKILL_SYSTEM_PROMPT` | 无 |
| `POST /api/page/assistant` | `server.py:1633` `build_page_router()` | `module_for(page_key)` | 无（`page_assistant` 不传 `page`，见 `assistant.py:454-467`） | `PAGE_SYSTEM_PROMPT` | 无 |

即：首页走现役路径时，系统拿到的是「senate 模块 + 个股深挖提示词 + symbol='home'」。`symbol` 会进 mem0 召回（`assistant.py:281-293`），首页的行情/自选问答会被套上「分析个股 home」的语义。这是语义错配，不是崩溃。

### 2.3 方案 A 的页面键行为（实证）

- `assistant.py:50-74`：`PAGE_KEY_MODULES` 无 `"/"` 键 → `module_for('/')` 回落 `DEFAULT_MODULE = "tickhub"`（`assistant.py:75`）。
- `tickhub` 在降级白名单内：`src/ladderstock/ai/fallback.py:82` `MODULE_KEYS = ("tickhub", "tickhub_us", "wire", "senate")`。
- 语义正确：首页取的就是行情 / 自选 / 指数这一层数据。

### 2.4 方案 B 的通达性（为何否）

| 事实 | 位置 |
|---|---|
| 端点只在控制面下 | `src/ladderstock/web/control.py:4373` `@router.post("/ai/chat")` |
| 控制面总开关默认开、可关 | `control.py:101` `CONTROL_ENABLED_ENV = "LDST_CONTROL_ENABLED"`；`:250` 默认开启，显式 `0/false/no/off` 才关闭 |
| 关闭时整族 404（方法无关中间件短路） | `server.py:1033` `control_switch`（`@app.middleware("http")`） |
| 数据面不受影响 | `server.py:1618` 注释、`research.py:15` 同口径 |
| 首页本身是数据面 | `server.py:1617-1619`：`/api/research/*` 与页级助手「不受 `LDST_CONTROL_ENABLED` 影响——读取属于数据面」 |

首页对话是读数据、不是改配置。把首页接到控制面意味着：用户在受管环境把控制面关掉后，**首页对话整块 404**，而首页其余数据照常出——这正是 `research.py:15` 明令避免的「跟着关会让页面永久空态」。

## 3. 被否方案的代价

### 3.1 否决 B（`POST /api/control/ai/chat`）要付出的代价

**放弃的东西**（真实存在，要写清楚）：

| 能力 | `chat.py` 现状 | 首页若走 A 就**没有** |
|---|---|---|
| 会话记忆 | 进程内 `OrderedDict`，TTL 30 min、最多 64 会话、每会话最近 6 轮（`chat.py:66-68`、§4 SPEC-AI-CHAT） | 无会话内上下文 |
| 来源校验与 `used_at` | `ChatSource` + 时间/链接校验 + `_source_note`（`chat.py:434`） | 只有 `assistant.sources` 前端快照 |
| `pending_research[]` | `chat.py:447` / `:863` 返回建议 | 不返回（§12-L08 本就不做） |
| 可中止 | `ChatAborted` → 499（`chat.py:899-915`） | `assistant.py` 有 `_guard` + 前端 `AbortController`，行为不同但可用 |
| 错误码表 | `ai_no_channel` / `ai_timeout` / … 状态码区分 | `describeAssistantError` 一套，已在用 |

**如何回补**（不新起第二套实现）：任务书 D3 定稿「能力迁进现役、`chat.py` 保留不删」，阶段 B/C 把 `ChatSource` / `ChatRequestContext` / `ChatFailure` / `_source_note` 迁进 `assistant.py` 链路，会话交给阶段 C 的 `chat.sqlite` 落库。也就是说，B 方案里值得要的能力，**在 A 通道路径上会由 B+C 阶段补齐**，不需要为它把首页挂到控制面。

**第二个代价**：放弃 B 意味着 `chat.py` 本体继续作为「写好了没接线」的对照实现存在（D3 已裁定不删）。这是已知技术债，不新增。

### 3.2 不选「继续用 `/api/research/home/assistant`」的代价

如果为了省事维持现状，代价是：

1. 首页语义错配：`module="senate"` + 个股深挖提示词 + `symbol='home'` 进 mem0 召回，回答会被引向「分析个股」，且 `home` 这个伪代码会污染记忆检索（`assistant.py:281-293`）。
2. SPEC-HOME-V2 §3.1「通道只有一条 = `/api/research/{scope}/assistant`」与新语义继续不一致，后续每个碰到首页对话的人都要重新判断一次。
3. 任务书阶段 B 要改的「三个路由」里，`/api/research/{code}/assistant` 是为个股深挖设计的；首页塞进去会让阶段 B 的「旧形态 `reply` 逐字不变」快照测试被迫覆盖一个本来不该在这里的调用方。

## 4. 会话语义：单轮无状态（定稿）

**结论：首页对话是单轮无状态。**

理由（按优先级）：

1. `assistant.py` 的 `ask()` 只有跨会话 mem0 召回，**没有会话内上下文**；要在 A 通道上加多轮，等于在 `assistant.py` 里再起一套会话，和任务书阶段 C（`chat_session` / `chat_turn` + `chat.sqlite`，D1 定稿）重复。
2. 任务书阶段 C 已经定义了正式会话模型（`session_id`、`title`、`seq` 递增、淘汰最旧）。首页多轮的**正确落点**是阶段 F 的 `ChatPane` 调阶段 C 的会话接口，不是在 `assistant.py` 上加临时内存。
3. 阶段 C 的会话存储（`chat.sqlite`）在服务重启后仍在；`chat.py` 的内存 TTL 30 min 做不到这点，若首页先上内存 TTL，等阶段 C 上线会出现「两套会话寿命」（总经理在父单评论里已点出这条风险）。

**约束（写进 SPEC）**：

- 本通道**请求体不带 `session_id`**；前端不生成、后端不校验、不返回。
- 若未来首页需要多轮，走阶段 C 的会话接口，**不在 `ask()` 上加内存会话**。
- mem0 跨会话召回（`assistant.py:13-15`）保持现状，不算「会话记忆」，不需要关。

## 5. `pending_research` 边界（定稿）

- 本通道（`/api/page/assistant`）**不返回 `pending_research`**——`assistant.py` 的返回体里本来就没有这个字段。
- SPEC-HOME-V2 §12-L08 的「不做」裁定**维持不变**：本仓没有「一句话起研究任务」的接口，真正的研究任务入口保持在个股深挖页。
- 未来任何通道（含 B 方案若复活）返回 `pending_research[]` 时：**允许前端不渲染该字段；如果渲染，禁止渲染成「起任务」按钮或任何触发后台任务的控件**。§12-L08 的理由是「前端无法凭一句话起任务」，这条理由今天依然成立。

## 6. 「研究台」定义：需确认

全仓 grep（`docs/`、`frontend/src`、`src/`）无「研究台」字样，任务书以外没有权威来源。任务书内部证据互相冲突：

| 证据 | 指向 | 位置 |
|---|---|---|
| §1「对话态 / 研究台态」，研究台态 = 「现有那 16 个屏」 | **两屏都要**（首页 = 现有 `HomeScreen`，另有 15 个业务屏） | 任务书 §1 / §4.2 |
| D6 定稿「研究台态落点 = 现有 `HomeScreen` 原样内嵌」 | **首页 E 研究面板**（`HomeScreen` 内嵌） | 任务书 §12-D6 |
| QUOT-552 v2.0 把首页改成「一页工作台」，E 研究面板是三页签（快捷入口 / 研究股票 / 搜索历史） | 首页 E 面板 | SPEC-HOME-V2 §3.1 / §5 |
| §5.5「对话里正在聊 `600036`，切到研究台 → `navigate('/research/600036')`，**不要回 `/`**」 | **`/research` 个股深挖页** | 任务书 §5.5 |
| SPEC-RESEARCH-PAGES §1：`/research/:code` 是个股深挖（公司画像 / 财务趋势 / 估值分位…），与首页 E 面板无关 | `/research` | SPEC-RESEARCH-PAGES §1 |

**二选一的依据**：

- 若「研究台 = 首页 E 研究面板」：§5.5「聊到 `600036` 就 navigate 到 `/research/600036`」是**跳转行为**而非落点定义，可以解释为「切到研究台态后，若对话里有具体标的，则再跳个股深挖页」。
- 若「研究台 = `/research` 个股深挖页」：D6「现有 `HomeScreen` 原样内嵌」就说不通——那是首页，不是 `/research`。

**推荐（未拍板，供老板选择）**：按 D6 的落槌，**研究台 = 首页的研究形态，主体是现有 `HomeScreen`（包含 E 研究面板），不等于 `/research`**。§5.5 的「带标的时跳 `/research/:code`」是研究台态内部的一条导航规则，不改变落点定义。QUOT-573 不阻塞在这一点上：无论哪种定义，都不影响首页对话通道的归属决策（§1）。

**确认路径**：由老板在 QUOT-572 评论区拍板，或在 QUOT-582（阶段 F 首页双模式外壳）派单时明确。在拍板前，实现侧**以 D6 为准**（内嵌 `HomeScreen`），不要单独为 `/research` 建第二个入口。

## 7. 边界与红线

- 本单**只改文档**：新增本决策文件；`SPEC-HOME-V2.md` §3.1 / §12-L08 表述对齐 + 变更记录；`SPEC-AI-CHAT.md` 补对首页的适用性说明 + 变更记录。**不改业务代码、不改 `frontend/src`、不改 `sources.yaml`、不碰指数源。**
- 不新增任何模型名 / 密钥 / 连接串。
- 通道常量约定：前端首页 scope 常量 `HOME_ASSISTANT_SCOPE = 'home'` 的**语义在本次变更后不再指向 `/api/research/{scope}`**，而是指向页面助手 `POST /api/page/assistant`。这是一条「接口常量约定」层面的事实，API 侧命名不变，避免前端改动。真正把前端从 `askResearchAssistant('home', …)` 切到页面助手，属于 QUOT-582（阶段 F）/ QUOT-578（阶段 E）的实现范围，**不在本单**。

## 8. 未做 / 留给后续

| 事项 | 归属 |
|---|---|
| 前端把首页请求从 `/api/research/home/assistant` 切到 `/api/page/assistant`，并在 payload 里带 `page_key: '/'` | QUOT-582（阶段 F） |
| `ChatSource` / `ChatRequestContext` / `ChatFailure` / `_source_note` 迁进 `assistant.py`（D3） | QUOT-579（阶段 B）/ QUOT-580（阶段 C） |
| 会话落库 `chat.sqlite`（D1） | QUOT-580（阶段 C） |
| 首页多轮若启用，走何种会话接口 | 阶段 C 完成后由后续单决定；本单已定「本通道单轮」 |
| 研究台定义拍板 | 老板（§6） |
