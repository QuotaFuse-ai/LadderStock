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

依赖：Node 22+（内置 fetch / 全局 WebSocket）+ 本机 Chrome 或 Edge headless，不引第三方包。

## before 基线的取数环境（2026-11-01）

本机 `ldst run` 在跑，但**未执行过 `ldst sync`**，行情类数据集为空；IndexedDB 用的是全新浏览器 profile，
本机私有数据（日志 / 持仓 / 候选池）也为空。因此 before 图拍到的是各屏真实的「未同步 / 空 / 降级」态，
不是设计稿、也不是 `?demo=1` 演示态（演示态带水印，不代表用户实际所见）。
后续若要拍到有数据的 before/after，先跑 `ldst sync --date <交易日>` 再重新执行上面的命令。

## QUOT-320 共享层 after（`--query`，2026-11-01 夜）

`visual-shot.mjs` 加了 `--query <str>`：把查询串追加到路由上，配合 `--suffix` 拍演示态与缺失态
（文件名仍是 `<route>-<suffix>-<theme>.png`）。

```bash
cd frontend && npm run dev -- --port 5173     # 另开终端
node scripts/visual-shot.mjs --routes news     --query "demo=1"                    --suffix after-320-demo    # 水印上屏
node scripts/visual-shot.mjs --routes news     --query "demo=1&variant=partial"    --suffix after-320-missing # 部分缺失=danger 橙红
node scripts/visual-shot.mjs --routes settings --query "tab=ports&state=offline"   --suffix after-320-ports   # 端口未取到=—
```

`after-320-*` 六张（暗 + 亮）证明三件事：`?demo=1` 单独出现时水印也上屏、
`source_status` 的色序按 §5.2（`partial` 橙红、`degraded` 黄）、离线时端口列写 `—` 而不是默认 5950/5951。

## radar 四屏阶段二对比（QUOT-312，2026-11-01）

`<route>-<before|after>-s2-<dark|light>.png` 是 QUOT-306 阶段二（radar 4 屏精修）的对比组，
**不覆盖**上面阶段一的 `before` / `after` 证据。与阶段一不同，这一组拍的是**本机真实数据态**：
`ldst run`（5950）+ `vite.shot.config.ts` 代理，取数窗为 `/pool?date=2026-10-30`（47 只候选 / ok）、
`/news?date=2026-10-30&until=2026-10-30`（59 条 / partial）、`/calendar?month=2026-10&day=2026-10-15`
（业绩预告小节 113 条 / 其余四节 `no_source`）、`/positions`（本机 SQLite 为空 → 诚实空态，不伪造持仓）。
`pool-*` 两张是点开第一行「展开」后的分项抽屉——被精修的裸表落在这里，主屏截图看不到。

`frontend/scripts/visual-shot.mjs` 的路由表写死不带查询串，这一组用同一份脚本的临时副本拍摄
（路由带日期 + 抽屉先点击「展开」+ `--settle 6000`），当时 `visual-shot.mjs` 还没有 `--query`。

