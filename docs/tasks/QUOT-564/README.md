# QUOT-564：粒子化工作流健康监控组件（第一批 采集→入库→辩论）

状态：待验收（PR + CI 证据见 PR 评论）
范围：前端 only——新增 1 个组件 + 1 个契约 lib + 2 个测试文件；`SettingsScreen.tsx` 仅 2 行挂载、
`styles/index.css` 追加粒子 keyframes；不改 backend/，不改 S5（QUOT-558）/S6（QUOT-559）/S8（QUOT-561）任何文件。

## 契约（B1/QUOT-562 定义，B2/QUOT-563 复用，前端只消费）

统一阶段事件：`{ module, module_class: "data"|"cog", stage, health: "ok"|"warn"|"down", reason, as_of, phase }`。
本批阶段：collect_script/process/store/success、ingest_watermark/persist/idempotent、debate_speak/clash/converge。

- `frontend/src/lib/workflowStages.ts`：契约类型 + 纯函数。`extractStageEvents(payload: unknown)`
  容忍两种信封——`stage_events: []` 平铺数组，或 `events: [{type:'stage', ...}]`（字段平铺或嵌套 `payload`）；
  严格校验枚举与非空字段，非法条目丢弃，module+stage 去重。B1/B2 落地时若信封不同，只改这一个函数。
- 健康直渲：`stageHealthView` 只做 ok→正常 / warn→降级 / down→异常 的文案映射，**不重算、不推断**
  （QUOT-500）；as_of 再旧也按后端原样显示，不做陈旧推断。
- 粒子唯一驱动：`hasOkEvent(events)`——该模块有后端报 ok 的阶段才流动，是后端 ok 的纯映射，不是前端编状态。

## 组件（`frontend/src/components/supervisor/WorkflowHealthMonitor.tsx`）

- 管线：采集 → 入库 → 辩论（P1 实色，可点按钮）→ 召回 → 结论（P2 虚线占位 DIV，「P2 · 规划中」，本批不接事件）。
- 配色全走既有 token：蓝 `info` = 数据管线，紫 `chart-series-2` = 认知层；健康弧 ok/warn/danger 三色。
  未新增 token（`docs/tasks/TASK-UI-REDESIGN/tokens.json` 不在本 checkout，gen-tokens 无源可改）。
- 健康环：SVG `pathLength=100`，每阶段一段弧，颜色 = 该阶段后端 health；无事件 → 中性虚线环 + 「等待事件」。
- 粒子：CSS keyframes（`ldst-wf-flow-x/y`，1800ms，环境动画豁免时长预算），每边 3 粒；
  `prefers-reduced-motion` 时 JS matchMedia 换静态点（25/50/75% 位置），CSS media 兜底 `animation: none`。
- 详情区：`role="region" aria-live="polite"`，逐阶段 白话 reason 原文 + as_of（title 带原始 ISO）+ stage/phase 小字；
  节点为原生 button（aria-pressed / aria-controls），sr-only 健康摘要，键盘 Enter/Space 可达。
- 取数：`api.supervisor.state` + `useAsync` + `usePolling`（5s，指数退避，后台标签页跳拍），与 S5 弹窗同通道。
- 诚实降级：通道未接通（data=null 且非首载）→「未接入」横幅 + describeApiError 原文；
  通道通但无 stage 事件 →「等待事件」横幅；两种态都零粒子、零健康弧、不编 ok，不回落演示数据。
- 布局：≥640px 横排，<640px 竖排（连接线对齐环心）；无 position:fixed、无视口高度、组件内部不滚动。

## 挂载点（前端架构师决策）

设置 → 子模块（`/settings?tab=peers`）分区顶部。理由：与「模块注册表 / 子模块状态」同 IA 语义；
组件独立取数、自带降级，对 SettingsScreen 仅 2 行 diff，与 S5/S6/S8 零文件冲突。

## 验证

- 新增测试 24 例全绿：`quot-564-workflow-stages.test.ts`（11：双信封解析 / 非法丢弃 / 去重 /
  phase 自然序 / 健康映射 / hasOkEvent / 标签回退 / 管线定义）；
  `quot-564-workflow-health.test.tsx`（13：弧色直渲 / reason 原文 / as_of / 粒子开关 / 旧 as_of 不推断 /
  部分事件降级 / waiting / offline / events 信封 / 键盘与 aria / P2 占位 / reduced-motion / 5s 轮询 / 设置页挂载）。
- 全量 `npx vitest run`：105 files / 1047 passed；`npx tsc --noEmit` 0 错；`npx vite build` ok。
- 浏览器实测（headless Chrome CDP，mock API 注入事件；5950 被真实后端占用，取证走 5965 mock +
  scratch vite 代理，未动真实后端）：**15/15 通过**——5 节点语义、10 弧 ok/warn/down 直渲、
  P1 两边 6 粒流动 / P2 边静止、点击与键盘 Enter 切换详情 + aria-pressed、aria-live、亮色主题、
  320px 竖排零溢出零重叠、reduced-motion 6 静态点 animation:none、waiting/offline 零粒子零弧不编 ok、
  全程无 JS 异常无控制台错误。截图 7 张在 `docs/screenshots/quot-564-workflow-*.png`（随 PR）。
- `check:ui`：本单新增/改动文件零违规；仓库存量违规（PortfolioReplayTab 等）与本单无关，未触碰。

## 红线核对

- 前端不重算指标 / 不推断健康态；只渲染后端 stage 事件的 health/reason/as_of。
- 未编数；live 未接通如实「未接入 / 等待事件」，不伪造粒子流动 / 健康 ok / 辩论进度；不回落演示数据。
- 不改 S5/S6/S8 代码文件；不删断言；不 sleep 掩盖偶发；无整文件 skip；不改 backend/；不硬编码模型清单。
- 密钥 / 连接串零入库；粒子尊重 prefers-reduced-motion；无 position:fixed / 视口高度布局 / 组件内部滚动。
- 取证用 mock / vite scratch 配置 / CDP 脚本均在 workdir（不进仓库）；scratch vite 配置验证后已删除。