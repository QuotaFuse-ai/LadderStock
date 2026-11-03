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

## industry 2 屏 + limit-up 精修对比（QUOT-311，2026-11-01 夜）

`<route>-<before|after>-311-<demo|partial>-<dark|light>.png` 是 QUOT-306 阶段二（industry 矩阵 /
industry 详情 / limit-up 三屏精修）的对比组，**不覆盖**阶段一与 QUOT-312 / QUOT-320 的证据。
`before` 拍在合并基线 `5e2314c`（三屏改动前），`after` 拍在本轨分支，同一台机、同一份
`vite.shot.config.ts`、同一查询串，只差代码：

```bash
cd frontend && npm run dev -- --config vite.shot.config.ts   # http://127.0.0.1:5199
node scripts/visual-shot.mjs --routes industry,industry-name,limit-up --query "demo=1"                  --suffix after-311-demo
node scripts/visual-shot.mjs --routes industry,limit-up              --query "demo=1&state=partial"     --suffix after-311-partial
```

本机没有 `ldst run`（行情数据集为空），所以这一组取的是 `?demo=1` 演示源，`?demo=1` 与 `?state=partial`
两枚标识都由 `PageShell` 的 `DemoStateBadge` 自动上屏（QUOT-320 / R-18）。`--suffix before-311-*` 的两组
在 `git checkout 5e2314c` 之后重拍，用来做同口径对比。

## 原四屏 §11 走查对比（QUOT-315，2026-11-01 深夜 → 11-02）

`<route>-<before|after>-315-<dark|light>.png` 与 `<route>-<before|after>-315-offline-<dark|light>.png`
是 QUOT-306 阶段三（复盘 / 盯盘 / 验证 / 日志 四屏按 `docs/UI-SPEC.md` §11 v1.1 逐条走查后落地）的对比组，
**不覆盖**阶段一、QUOT-312 / QUOT-320 / QUOT-311 的证据。`before` 拍在合并基线 `44dcb7b`（四屏改动前），
`after` 拍在本轨分支，同一台机、同一个 dev server、同一份脚本，只差代码：

```bash
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173      # 无 ldst run，后端 5950 未起
node scripts/visual-shot.mjs --routes review,watch,validate,journal --suffix after-315 --out <dir> --settle 1800
node scripts/visual-shot.mjs --routes review,watch,validate,journal --suffix after-315-offline --query state=offline --out <dir> --settle 1800
```

本机没有 `ldst run`（5950 无监听），所以这一组拍的是各屏**真实的「未同步 / 空 / 降级」态**（第一组）与
`?state=offline` 强制态（第二组，能同时看到 `DemoStateBadge` 的「演示态：离线」水印、§4.2 旧值隐藏条、
页脚溯源行与 `—` 缺失纪律）。**没有用 `?demo=1`**：本轮改动里有三项（盯盘徽标同源、§4.2 四类原因文案、
剩余 TTL 的 `—`）只在「无信封 / 无快照」路径上才判定得出来，演示源会把它们一并遮掉；且 §5.6 要求演示源
与真源不混拍。逐条判定与证据见 `docs/tasks/TASK-QUOT-306/ui-spec-11-walkthrough-315.md`。

## QUOT-348 首页搜索面板规格图（2026-11-03）

`home-search-<state>-<dark|light>.png` 十张**是规格图（设计稿渲染），不是实机截图**：搜索面板尚未实装，
`visual-shot.mjs` 拍不到它（脚本按路由表打真页面）。这批图由 `docs/specs/home-search-prototype.html`
经 Chrome 无头渲染产出，用来给 `docs/specs/home-search.md` 的每个状态留像素证据。命名沿用本目录
`<route>-…-<dark|light>.png` 的骨架（`<route>` = `home`），中间段换成状态名而不是 `before|after`——
没有改动前后对照可拍，实装后再补 `home-search-*-after-*` 对比组。

```bash
# 在仓库根目录执行；chrome=0 隐藏原型自带的状态切换条（不属于设计体系）
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
P="file:///$(cygpath -w docs/specs/home-search-prototype.html | tr '\\' '/')"
shot() { "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size="$2" \
  --virtual-time-budget=2500 --screenshot="$PWD/docs/screenshots/$1.png" "$P?chrome=0&$3" >/dev/null 2>&1; }

shot home-search-entry-states-dark        1440,900 "state=entry-states"
shot home-search-panel-empty-dark         1440,900 "state=empty"
shot home-search-panel-results-dark       1440,900 "state=results"
shot home-search-panel-results-scrolled-dark 1440,900 "state=results&scroll=bottom"
shot home-search-panel-loading-dark       1440,900 "state=loading"
shot home-search-panel-no-result-dark     1440,900 "state=noresult"
shot home-search-panel-group-error-dark   1440,900 "state=grouperror"
shot home-search-panel-offline-dark       1440,900 "state=offline"
shot home-search-panel-results-light      1440,900 "state=results&theme=light"
shot home-search-panel-results-1024-dark  1024,900 "state=results&vw=rail"
```

`--window-size` 取 1440×900（本目录基准，同 `visual-shot.mjs:12-18`），最后一张 1024×900 是 rail 断点。
逐态对应的规格章节见 `docs/specs/home-search.md` §11。
