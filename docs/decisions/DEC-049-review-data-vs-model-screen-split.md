# DEC-049 复盘两屏分工：数据面 `/review` 与模型面 `/review/ai` 的内容切分、四态口径同源、「我的判断」归属

- 日期：2026-11-03
- 提出：老板（QUOT-344 S2 的屏边界拍板；单描述明写「别再自创」）
- 状态：生效
- 关联：DEC-046（复盘三入口的路由与导航，本决策填它留给 S2 的正文结构）、DEC-045（编号与变更记录规则）、
  DEC-040（UI-SPEC 屏数口径）、QUOT-344、QUOT-353（本单，S2 实现）、QUOT-355（S1）、QUOT-319（缺数显示 `—`）、
  `frontend/src/screens/ReviewScreen.tsx`、`frontend/src/screens/AiReviewScreen.tsx`、
  `frontend/src/screens/review/parse.ts`、`frontend/src/screens/review/SectionMetrics.tsx`、
  `frontend/src/screens/review/useReviewView.ts`、`frontend/src/screens/review/demo.ts`、
  `stores/reviewDraft.ts`、`docs/UI-SPEC.md` §3.1/§3.19/§10/§11.1/§11.20

## 1. 背景

DEC-046 的 S1 只拆了导航与路由：`/review/ai` 是占位壳，五段 AI 叙述、段级 `status`、`ai_failures[]`、
「重新生成 / 重试失败段」全部留在 `/review` 一屏里；同时 `sections[].metrics`（`review/pipeline.collect_metrics`
的程序直出指标）**没有任何屏渲染**——数据面缺正文，模型面超载。

S1 收尾还留下两处口径没定义，S2 动手前必须一次性裁定，否则两屏会各写一半：

1. 「我的判断」草稿（`stores/reviewDraft.ts`，IndexedDB 分片 `review_draft`）算数据还是算模型产物？
   两屏都想要它，写两份存储必然漂移。
2. 「这一屏没有 AI 正文」有三种完全不同的成因，此前的单一空态把它们混成一句话，
   其中把「模型未配」显示成「数据缺失」会把用户引去跑 `ldst sync`，而真正该做的是接入模型凭据。

## 2. 决定

1. **一屏一面，内容不重叠**（验收项「三屏内容互不重叠」的条文版）：

   | 屏 | 面 | 有的内容 | 绝不出现 |
   |---|---|---|---|
   | `/review` 每日复盘 | 数据面 | 三张指标卡（`/api/sentiment`）、五段 `sections[].metrics` 程序指标、数据来源/质量标记、`data_source_status`、「我的判断」五段草稿 | AI `narrative`、模型名、段级 `status`/`ai_failures[]`、「重新生成 / 重试失败段」 |
   | `/review/ai` AI复盘 | 模型面 | 五段 `narrative`、段级 `status` + `ai_failures[]` + `degraded`、`ai.model` / `ai.degraded`、控制面动作 `POST /control/ai/generate` 与按段 `/control/ai/retry`（含 `control.job` 轮询） | 三张指标卡、逐段 `metrics`、「我的判断」编辑框 |
   | `/weekly` AI周报 | — | 本决策零变更（DEC-046 §6：只改侧栏标签，屏内 `h1` 仍「周度复盘」） | — |

2. **同源解析只有一处**：`frontend/src/screens/review/parse.ts`。两屏读的都是同一份
   `review/<date>.json`（`/api/review`），各取一半。段名映射（草稿键 `dragonTiger` ↔ payload `dragon_tiger`）、
   段状态表、缺失口径、三-case 判定都在这一个文件里；屏侧不得各自 `if (section.status === …)`。
   取数层同理共用 `review/useReviewView.ts`：`useReviewView` → 数据面（review + sentiment），
   `useAiReviewView` → 模型面（只有 review，不发 `/api/sentiment`——那是数据面的读数）。

3. **「我的判断」归数据面，全仓只有一份存储**。理由是它的性质而不是位置：它由人写、按段对齐程序指标
   （「这段数据我怎么看」），与模型是否接入无关；放模型面会让人以为它是 AI 产物的附件。
   模型面只读同一份产物里的 `narrative`，**不写第二份草稿存储**，也不接 `reviewDraft` 的 setter。
   模型面的交易日因此只跟 URL `?date=`（`AiReviewScreen` 不写 `reviewDraft.tradeDate`）——
   两处各存一份「当前交易日」必然会漂。

4. **三种「没有 AI 正文」必须分开显示**，判定逻辑是 `parse.ts:classifyAiCase()`（纯函数，可单测），
   输入只取后端字段 `ai.model`、逐段 `model` / `status`、`ai_failures[]`，不解析提示文案：

   | case | 判据 | 屏上给什么 |
   |---|---|---|
   | `no-artifact` | 当天产物不存在（`payload.review === null`） | 引导 `ldst review --date=YYYY-MM-DD` + 复制命令 + 重新检查 |
   | `model-unconfigured` | 产物在、五段都在，但 `ai.model` 为 `null` 且逐段 `model` 全 `null`、`status` 无 `ok`、`ai_failures` 为空 | 「模型未接入：五段都没有跑过模型」横幅 + 「接入 AI」（`/settings?tab=ai`）+「接入后重新生成」 |
   | 段级失败 | `ai_failures[]` 非空或有段 `status: failed/degraded` | 逐段徽标 + 段内 `AiErrorNotice` + 按段「重跑该段」；页面级横幅给「5 段中 X 段失败、Y 段降级（网络 / 截断）」 |

   **不允许把「模型未配」显示成「数据缺失」，也不允许反过来**：case 2 的正文必须写「这里缺的是模型，
   不是数据」并指出五段程序指标在 `/review`；`model-unconfigured` 时不再叠一条泛化的「几段降级」横幅
   （两条横幅讲同一件事，用户读不出重点）。有 `ai_failures` 一律归 case 3——那时模型配过、请求发出去过，
   该给的是重试而不是接入。

5. **数据面的空态文案必须给生成路径**：`/review` 无产物时说明「跑完这条命令即有五段数据」，
   并明确「没有配置模型凭据只影响 AI复盘屏，不影响本屏」；缺数段落逐段标 `缺数` + 原因，
   段内计数按 `—` 显示（QUOT-319），`available: false` 段里的 `0` 一律不是读数。
   段级缺数的页面横幅取 `danger`（§5.2：数据集缺项 = `partial` → danger），
   顺带收口 R-12 记在旧 `ReviewScreen.tsx:198` 的色序反转。
   `loading` 用现成的 `ScreenSkeleton`，不造 spinner；徽标继续走 `SourceBadges` / 四态组件，不重写一套。

6. **程序直出的 `headline` 留在数据面，标签纠正为「程序直出 · 当日摘要」**。它是
   `review/pipeline._headline()` 用情绪温度与天梯拼出来的，不是模型产物；S1 前它挂着「AI 摘要」标签
   并带模型名，是错标。本决策顺带改正，并在屏上保留一处注释说明它为何不在模型面。

7. **演示态永不触发控制面**：`/review/ai` 的生成/重跑入口在 `?demo=1` 下禁用且原因可读
   （「演示态不触发 AI 生成：本页数据来自 demo.ts，不写后端也不建任务」）；演示态的「我的判断」
   只读、不预填、不写本机草稿，也不写离线回放缓存（`review_snapshot_cache_v1` 仍只有一个键）。
   真实请求 404 / 断连一律进空态 / 离线态，绝不回落到 `review/demo.ts`。

8. **不新增后端端点与字段**：AI 段与数据段同源（`review/<date>.json`），拆屏是纯前端读法。

## 3. 备选与取舍

| 方案 | 取舍 |
|---|---|
| `/review?tab=ai` 同屏页签 | 与 DEC-046 §2 的独立入口拍板相反，否决 |
| 「我的判断」挪到模型面 | 它是人按段对齐**数据**写的判断，与模型成败无关；挪过去会让人以为草稿是 AI 产物的一部分，否决 |
| 两屏各存一份草稿（`reviewDraft` + `aiDraft`） | 单描述明确禁止「两份存储」；两份必然漂移，否决 |
| 模型面也读 `/api/sentiment` 以便复用指标卡 | 等于把数据面内容复制进模型面，正是本决策要消除的重叠，且多一条无谓请求，否决 |
| 用 `notes` 文案关键字判「模型未配」 | 后端提示语是最容易改动的东西，判据必须落在 `ai.model` / 逐段 `model`+`status`+`ai_failures` 这些契约字段上，否决 |
| 在 `/review/ai` 也放只读指标（对齐参考站 vibe-astock） | 参考站的 AI 页确实带指标条，但那是同屏不分家的产物；本屏出口已链接回数据面，否决 |

## 4. 影响

- 两屏职责可测试化：`frontend/tests/review-split.test.tsx` 同时挂载两屏（各包一个容器），
  重叠内容会被数出来；`frontend/tests/quota-334-review-weekly.test.tsx` 按拆屏后的口径重写了
  `/review` 的断言（`h1` 是「每日复盘」，AI 段落与生成入口不再是它的期望）。
- 数据面新增此前不存在的正文层 `SectionMetrics.tsx`：指标 key 走登记表，未登记的 key 不渲染，
  后端 key 不直显。新增指标要改 `review/pipeline.collect_metrics` 与该表两处。
- `/review/ai` 从占位壳变成可用屏：控制面动作、任务轮询、段级重试都在这一屏；`/review` 上
  原有的这些入口随之移除（不是并存），旧深链 `/review` 仍然直达数据面，无 URL 破坏。
- `docs/UI-SPEC.md`：§3.1 改写为数据面口径、新增 §3.19「AI复盘（模型面）」、§10 两行映射落定、
  §11.1 重写 + 新增 §11.20、§12.5 补 R-27（页面导出缺口）；变更记录按 DEC-045 §4 合并进 **v1.6 同一条**（S1+S2），不另起一行。
- **编号占用**：开单时按 DEC-045 §1 扫 `origin/main` 最大号 = 045，本单先取了 046，与 S1（QUOT-355）
  已合入的同名文件撞号；那次撞号已由人类在提交 6be168c「让号 DEC-047/048」里收口
  （`DEC-046-review-nav-three-independent-entries.md` 保留 046，QUOT-347 的调度那份改为
  `DEC-047-schedule-close-sync-job.md`，QUOT-349 的限流那份为 `DEC-048-…`）。
  本决策原先按扫描结果写成 DEC-047，在 S2 进行期间 047 被 6be168c 占走，
  于是合并 `origin/main` 后重扫取 **DEC-049**（当前 `origin/main` 最大号 048 + 1）。
  这正是 DEC-045 §1「以合入 main 的最大号 +1 为准，分支上的预留号不算」要处理的情形，
  留此记录以免后来者误以为 047 是本单。
- 回滚方式：还原两屏文件 + 删除 `review/parse.ts`、`review/SectionMetrics.tsx`、
  `tests/review-split.test.tsx` 与本文件；`routes.ts` / `App.tsx` / 导航零变更，故回滚不碰 DEC-046 的三入口。

## 5. 证据

- `frontend/src/screens/ReviewScreen.tsx`（数据面：指标卡 + `SectionMetrics` + 五段草稿 + 无 AI 文案）
- `frontend/src/screens/AiReviewScreen.tsx`（模型面：三-case 分支 + 控制面 + 轮询 + 段级重跑）
- `frontend/src/screens/review/parse.ts`（`classifyAiCase` / `SECTION_STATUS` / 段名映射，两屏同源）
- `frontend/src/screens/review/SectionMetrics.tsx`（指标 key 登记表 + `—` 不补 0 的段级缺数口径）
- `frontend/src/screens/review/useReviewView.ts`（`useReviewView` / `useAiReviewView` / `useReviewSource` 单一演示源选择点）
- `frontend/tests/review-split.test.tsx`、`frontend/tests/quota-334-review-weekly.test.tsx`
- 截图（双视口 1440 / 1024，均零横向溢出）：
  `docs/screenshots/review-after-344s2-dark.png` 与 `review-ai-after-344s2-dark.png`（2026-11-03 有产物：数据面指标 + 模型面正文，
  模型面外壳徽标「缓存 / 数据完整」来自 `SourceBadges`）、
  `review-after-344s2-noartifact-dark.png` 与 `review-ai-after-344s2-noartifact-dark.png`（2026-10-30 无产物：数据面给生成路径，模型面 case 1 引导 `ldst review`）、
  `review-after-344s2-demo-partial-dark.png`（数据面「五段中 1 段数据缺失」= `danger`）、
  `review-ai-after-344s2-demo-partial-dark.png`（模型面 case 3：段级失败/降级 + 段级重跑）、
  `review-ai-after-344s2-demo-empty-dark.png` / `-demo-loading-dark.png`（六态）
- case 2「模型未接入」没有真机截图：本机 5950 后端 2026-11-03 的产物 `ai.model = "openai:deepseek-chat"`（五段皆 `ok`），
  10-30 / 10-29 则根本没有产物，所以「有产物但模型未配」这一态在真机上不可复现。它的证据是
  `tests/review-split.test.tsx` 第二个用例——按 `ai/orchestrator.py:_generate` 未配置分支的真实形状
  （五段 `status:'degraded'` + 逐段 `model:null` + `ai.model:null` + `ai_failures:[]` + `metrics` 齐）桩出响应，
  断言模型面出现 case 2 横幅与「接入 AI」、且全页不出现「数据缺失 / 还没有复盘产物 / 待生成」，数据面同刻仍显示指标读数。
- 闸门：`npm run typecheck` / `npm run test`（399 例）/ `npm run check:ui`（6/6）
