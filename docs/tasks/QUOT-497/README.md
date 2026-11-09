# QUOT-497 main push 事件 frontend 持续红：page-command-bar 点击后 dialog 不出现（时序竞态）

## 问题

main push 事件的 `frontend (tsc / vitest / build)` 连续两次红（`c7b4031e`、`3da032c1`），同一套检查在 PR run 全绿。失败点是 `tests/page-command-bar.test.tsx` 用例 1：`Unable to find role="dialog" and name "本页命令"`，其余 868 个用例全过。

## 根因

`frontend/src/components/PageCommandBar.tsx` 的路由复位 effect 原来是：

```ts
useEffect(() => {
  setJob(command?.last_job ?? null)
  setError(null)
  setOpen(false)
}, [command?.key, pathname])
```

组件挂载时 `commands` 为空数组，`command?.key` 为 `undefined`；API 返回后 key 变为实际值，effect 因此执行一次 `setOpen(false)`。CI runner 更慢，`commands` 的 promise resolve 更容易落在用户（测试）点击之后：点击 `setOpen(true)` → effect 紧随 `setOpen(false)`，面板一闪即收。本地快机上 effect 通常先于点击完成，所以本地偶发不复现。原用例用 `waitFor` 轮询掩盖，但 effect 若在点击后再关面板，轮询多少次也等不到 dialog。

## 修复（组件行为，非测试宽限）

- `PageCommandBar.tsx`：effect 只在「路由态真正变化」时收起面板。用 `routeKeyRef` 记录上一次 `pathname|command.key`，仅当变化且非首次时 `setOpen(false)`；commands 首次到达（key 从 undefined 到实际值）不再关闭面板。切页/换命令时收起的行为不变。
- `tests/page-command-bar.test.tsx` 用例 1：click 前先 `waitFor(commands 已调用)` 与 `waitFor(trigger 渲染)` 把异步态收敛到确定，click 后改为同步断言 dialog 存在，去掉「竞态注释 + waitFor 轮询」的时序依赖。

## 验证

- `npx vitest run tests/page-command-bar.test.tsx` 连续 20 次（`CI=true`）全绿：20/20 `Tests 4 passed (4)`，以 vitest 退出码判定。
- 全量 `npx vitest run`：87 个文件 / 869 个用例全过（与 CI 日志中的用例总数一致）。
- `npx tsc --noEmit` 通过；`npx vite build` 通过。
- 禁项自查：无 skip、无 `continue-on-error`、无删用例、无 `--retry`、无超时拉长。
