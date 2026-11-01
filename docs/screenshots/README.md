# 视觉巡检截图

- 归属：QUOT-306（UI 精修）阶段一证据基线，由 QUOT-307 建立。
- 命名：`<route>-<before|after>-<dark|light>.png`，视口 1440×900。
  - `<route>` 取路由段小写连字符（`/limit-up` → `limit-up`，`/` → `home`）。
  - 带参数的路由用参数名，实参取脚本里的样例值：`/industry/:name` → `industry-name`（电子）、
    `/research/:code` → `research-code`（600519）、`/research/:code/report` → `research-code-report`。
- 主题切换走既有机制：`localStorage['ldst.theme']`（`frontend/src/lib/theme.ts`），脚本在页面脚本之前写入，
  不改任何屏幕代码。

## 重新生成

```bash
# 1) 起前端（推荐带 /api 代理的取证配置，指向本机已启动的 ldst run:5950）
cd frontend && npm install
npm run dev -- --config vite.shot.config.ts     # http://127.0.0.1:5199

# 2) 另开一个终端：18 屏 × 暗/亮 = 36 张
cd frontend && node scripts/visual-shot.mjs             # 或 npm run shots
```

改完视觉后拍 after：`node scripts/visual-shot.mjs --suffix after`。
只拍部分屏幕：`--routes home,pool --themes dark`。其余开关见脚本头注释。

## 演示态 / 强制态取图口径（QUOT-319）

脚本支持 `--query <str>`，把查询串拼到路由后面，用于拍 `?demo=1` 演示源与 `?state=` 强制态：

```bash
# 10 张能进演示态的屏 × 暗/亮 = 20 张 → <route>-after-demo-<dark|light>.png
node scripts/visual-shot.mjs --query demo=1 --suffix after-demo \
  --routes limit-up,industry,industry-name,signals,news,calendar,pool,positions,research-code,research-code-report

# 缺失态（B 族）：?state=offline 会让 header 走「取不到数」的分支
node scripts/visual-shot.mjs --query state=offline --suffix after-missing \
  --routes signals,industry,positions,settings,weekly,pool

# 两个参数可以叠：--query 'demo=1&state=offline'
```

- `--routes` 的取值以脚本内 `ROUTES` 的 `slug` 为准，radar 四屏挂在 `/news` `/calendar` `/pool` `/positions`，**不是 `/radar/...`**。
- 演示态图**必须带可区分的后缀**（约定 `after-demo`），否则会覆盖正常态的 before/after 基线。
  不带 `--suffix` 时脚本会自动把查询串的键名并进后缀（`--query demo=1` → `<route>-before-demo-<theme>.png`），基线不会被演示态图冲掉。
- `?demo=1` 是**唯一**的演示源开关（`screens/*/demo.ts`），纯前端渲染，不需要 `ldst run`；
  `?state=` 走 `lib/pageState.ts` 的强制态，同样不需要后端。所以这两组在 5173 上就能拍。
- 演示态是否合格，判定标准只有一条：**每张图页头都要有同一枚「演示数据」水印徽标**（UI-SPEC §8 / DEC-028）。
  各屏自写的行内「· 演示数据」文本不算（资讯 / 深挖 / 报告三屏此前各写一套）。
- 拍不到的态：需要交互或真实数据才能出现的，脚本无点击能力，只能人工构造后补图——
  候选池抽屉的权重列（`radar/PoolScreen.tsx:156`，要先点开行抽屉）、
  周度题材条形图的缺失槽位（`WeeklyScreen.tsx:543`，要有 `streak_days` 为 null 的真实周数据）、
  复盘页 `source_status` 的 partial / degraded 色序对照（`ReviewScreen.tsx:198`，`?state=` 强制的是页面态不是数据完整度，
  要后端返回 `data_source_status: partial` / `degraded` 两份 envelope）。这三处当前以 vitest 断言为准，图待补。

依赖：Node 22+（内置 fetch / 全局 WebSocket）+ 本机 Chrome 或 Edge headless，不引第三方包。

## before 基线的取数环境（2026-11-01）

本机 `ldst run` 在跑，但**未执行过 `ldst sync`**，行情类数据集为空；IndexedDB 用的是全新浏览器 profile，
本机私有数据（日志 / 持仓 / 候选池）也为空。因此 before 图拍到的是各屏真实的「未同步 / 空 / 降级」态，
不是设计稿、也不是 `?demo=1` 演示态（演示态带水印，不代表用户实际所见）。
后续若要拍到有数据的 before/after，先跑 `ldst sync --date <交易日>` 再重新执行上面的命令。
