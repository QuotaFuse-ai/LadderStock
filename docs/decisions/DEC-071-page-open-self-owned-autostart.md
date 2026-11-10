# DEC-071: 页面打开即自启自有子进程（QUOT-522）

日期：2026-11-10
关联：QUOT-519（拆单来源）、QUOT-482（supervisor 内核）、DEC-069（崩溃自愈口径）
状态：已定稿

## 背景

用户要求「打开网页，盯盘 / 取数就自动在跑」，不依赖 CLI 或计划任务；
同时所有 peer 必须维持 adopt-only。需要复用既有 supervisor 基础设施，而非新建一套自启机制。

## 决策

1. **自启挂在看板进程的 startup 上，不新建 autostart**。`web/server.py` 用
   `mount_page_boot(app, boot)` 在 FastAPI startup 调 `PageBoot.ensure()`，
   shutdown 调 `stop_children()`。没有 Run 键、计划任务、启动文件夹项。
   开关 `LDST_PAGE_AUTOSTART`（默认开，`0` 关）。
2. **自有子进程只有两个，且只认账本归属**：`watch_guard`（`__watch-guard`，周期跑
   `watch start`）与 `scheduler`（`schedule run`）。二次打开 `ensure()` 幂等：
   同命令行的进程已在跑 → `already_running`，pid 不变。停止只处理
   `origin == "spawned"` 记录，peer 永不自动终止（延续 DEC-069 / 482 红线）。
3. **Windows 无控制台子进程一律走退出标记协作停止**。实证：`CREATE_NO_WINDOW`
   拉起的 python 收到 `taskkill /T`（不带 `/F`）只会被拒（rc 128）。
   看板关闭时写 `logs/<module>.stop`，子进程各自在空档检查后正常退出：
   `schedule run` 用专用线程 `scheduler.shutdown(wait=False)`（job 线程内 shutdown 会
   `cannot join current thread`），guard 在 0.2s 粒度的等待里检查。看板最多等 5s，
   等不到才 `/F` 连根兜底。标记路径经环境变量传入子进程，不写死。
4. **不把退出标记当作「用户主动停止」信号**，仅用于看板生命周期收尾；自愈引擎
   （DEC-069 `stopping_by_user`）语义不变。

## 后果

- 打开 / 刷新页面即拉齐自有子进程；无需用户理解 CLI。
- 看板被强杀时子进程会残留，但下次打开会被 `_find_by_cmdline` 认领，不产生双实例。
- 若将来要把 `schedule run` 的停止信号推到更底层（例如 Windows job object），
  可在不改 CLI 契约的前提下平替 `_stop_when_marked`。
