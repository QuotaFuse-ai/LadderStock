# QUOT-542 page-command-bar 用例 flake 根治：click 前冲刷挂起的 passive effect

- 日期：2026-11-11
- 基线：`origin/main` @ `729fa8b`
- 范围：只改 `frontend/tests/page-command-bar.test.tsx`，组件与 CI 未动

## 1. 现象

main CI run `42888524661` 的 `frontend (tsc / vitest / build)` 失败 1 例：
`tests/page-command-bar.test.tsx` 用例 1「点击展开面板」报
`Unable to find an accessible element with the role "dialog" and name "本页命令"`，
此时按钮为 `aria-expanded="false"`。重跑 attempt 2 即绿，属 flake 而非回归。

## 2. 复现（本地）

单跑不复现。以 6 路并行 vitest 压满 CPU 后，原用例 150 次中失败 2 次（≈1.3%），
与 CI 症状一致。给组件临时加日志（定位后已还原）后抓到失败序列：

```
[ROUTE_EFFECT] null -> ""        # 挂载，commands 仍为空
[CLICK]                          # 测试点击按钮
[ROUTE_EFFECT] "" -> "sync"      # commands 首次到达那次提交的 passive effect 迟到了
[CLOSE_FROM_EFFECT]              # 它执行 setOpen(false)
[TOGGLE] false                   # click 的 setOpen(v => !v) 之后才被归约
```

用例侧再验：失败时 click 后哪怕等 50ms，dialog 也从未出现——
**不是「查询太早」，findByRole 等到 1000ms 超时同样等不到。**

## 3. 根因

`waitFor()` 轮询期间 React Testing Library 会关掉 `IS_REACT_ACT_ENVIRONMENT`，
`commands` 首次到达的那次提交是在 act 环境外提交的，其 passive effect
（`PageCommandBar` 的路由态复位 effect，routeKeyRef 从 `""` 变为 `"sync"` 时 `setOpen(false)`）
被挂在队列里没跑。测试紧接着 `fireEvent.click()`，React 在 click 的 `act` 内先应用
click 的 `setOpen(true)`，随后才冲刷这条挂起的 effect，于是补一条 `setOpen(false)`，
把刚打开的面板关掉。

真实浏览器里 React 会在派发离散事件前先冲刷 pending passive effect，
所以用户点击行为是正确的——这是 act 队列顺序造成的**测试侧时序缺陷**，不是组件 bug。
（`frontend/src` 一行未改；也没有 skip / 删用例 / 弱化断言 / 加 timeout / sleep / retry。）

## 4. 修复（测试侧）

1. 新增 `openPanel()`：`waitFor(trigger)` 之后先 `await act(async () => {})`
   把挂起的 passive effect 冲刷到点击之前，再 `fireEvent.click(trigger)`。
   三个用例统一走这个入口。修复后同样的日志顺序恒为
   `[CLOSE_FROM_EFFECT]` → `[CLICK]` → `[TOGGLE] false`。
2. 点击后的同步查询改为异步等待：`findByRole('dialog', { name: '本页命令' })`、
   `waitFor(textContent 含「已排队」)` 等；断言强度不变（仍要求 dialog 以正确 name 出现、
   文案齐全、`aria-expanded="true"`）。
3. 顺带修同文件的同类竞态：用例 2 的「已排队 / 执行中」来自 `runCommand()` 异步回包，
   原是 waitFor 之后同步断言，CI 慢机同样可能踩空；用例 3 的 Esc 收起也改为 waitFor 收敛。

## 5. 验证

- 目标文件：单跑 12/12 绿；6 路并行压测 270/270 绿（原用例同条件 148/150）。
- 全量 `CI=true npx vitest run`：101 文件 / 999 用例全过。
- `npx tsc --noEmit`、`npm run build` 通过。
- 禁项自查：无 skip、无删用例、无 `expect(true)`、无弱化断言、无 timeout 拉长、无 `--retry`、
  未改 `backend/` 与 CI workflow。
