## 说明

QUOT-354（[344-S3] 三屏拆分独立验收）的取证入库分支。base 为 `agent/agent/ec41d096769b`（QUOT-353 S2 tip，含 `origin/main`）——**S2 合入 main 后本 PR 的净 diff 只剩 `docs/screenshots/` 22 张 after 截图**；若先合本 PR，会连带把 S2 的拆屏改动一起带进 main，请按 S2 → 本 PR 的顺序合。

## 内容

`docs/screenshots/`：
- `review-after-354s3-{dark,light}.png` / `review-after-354s3-1024-{dark,light}.png`（11-03 有产物，1280 与 1024 双视口）
- `review-ai-after-354s3-{dark,light}.png` / `review-ai-after-354s3-1024-{dark,light}.png`
- `weekly-after-354s3-{dark,light}.png` / `weekly-after-354s3-1024-{dark,light}.png`
- 四态补充：`{review,review-ai}-after-354s3-{noartifact,error,400,demo}-dark.png`、`review-ai-after-354s3-demo-partial-1280-dark.png`、`weekly-after-354s3-demo-dark.png`
- 缺陷直证：`weekly-after-354s3-buge-dark.png`（每日序列缺数显示 0 + 屏内 h1「周度复盘」）、`weekly-after-354s3-500transient-dark.png`（瞬时 HTTP 500 的兜底态）

由 `frontend/scripts/visual-shot.mjs` 与同一 CDP 取证脚本产出，1280 / 1024 两档**横向溢出 0 项**。

## 验收结论摘要（完整清单与 bug 复现见 QUOT-354 评论）

- 通过：3、4、6、7、9（两屏内容零重叠；四态齐；演示态点「重新生成 / 重试失败段 / 重跑该段」全部 `disabled` 且 0 个非 GET 请求；`npm run typecheck` / `npm run test` 399 passed / `npm run check:ui` 6/6 退出码均 0）。
- 不通过：1（AI周报屏内 h1 仍「周度复盘」，UI-SPEC §3.7 写明改文案归 S2）、2（`/review?date=` 被本机草稿日期覆盖，P1）、5（草稿全局单槽、跨交易日串台，P2）、8（`/weekly` 每日序列 11-03～11-06 涨停/炸板两列把缺数显示成 0，P2）、10（代码与测试 17 处引用 `DEC-047`，实际决策是 `DEC-049`）。
- 发布风险：不建议带着 BUG-B 发布——用户改一次「交易日」后，深链与刷新就会永久串日，两屏同日对照失效。

## 环境

Windows 10 19042 / Node 22.17.1 / Chrome headless（CDP）/ 前端 `vite.shot.config.ts` 5199 → 真实后端 `ldst run` 5950（`data_dir=D:\QuotaFuseModules\LadderStock\data`）。
