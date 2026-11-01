# 磁盘护栏 disk-guard（QUOT-329）

C 盘写满会在同一分钟打掉多条 agent run（2026-11-01 08:47 就是 4 条）。本护栏把「清盘」
从一次性人工救火变成每天自动执行的回收 + 巡检，范围完全沿用 QUOT-327 验收过的口径。

- 脚本：[`disk-guard.sh`](./disk-guard.sh)（本文件是 source of truth）
- 运行时副本：`C:/Users/Administrator/bin/disk-guard.sh`（调度器实际执行的是这份）
- 日志：`C:/Users/Administrator/logs/disk-guard/disk-guard.log`

## 安装 / 更新

改完本文件后同步到运行时路径，不要反向编辑运行时副本：

```bash
cp docs/ops/disk-guard.sh /c/Users/Administrator/bin/disk-guard.sh
bash -n /c/Users/Administrator/bin/disk-guard.sh   # 语法自检
```

## 触发条件与目标

| 项 | 值 | 说明 |
| --- | --- | --- |
| `TRIGGER_FREE_GB` | 6 | `C:` free 低于此值才动手，否则直接退出 |
| `TARGET_FREE_GB` | 12 | 回收目标；达不到就在日志里列出剩余的 out-of-scope 大户 |
| `ACTIVE_WINDOW_MIN` | 60 | 60 分钟内被写过的 task 目录一律跳过 |
| free < 3 GB | 退出码 2 | 回收失败的硬告警 |

四个值都可用环境变量覆盖，验收时就是靠 `TRIGGER_FREE_GB=14 bash disk-guard.sh` 制造真实触发的。

## 回收范围（白名单，写死在脚本里）

1. **已终态任务目录**里 issue 为 `done` / `cancelled` 的 `node_modules/`、`dist/`、
   `__pycache__/`、`.venv/`。终态判定不是靠目录名猜的：脚本分页调
   `multica issue list --status done|cancelled` 取全量 issue number，只有命中的
   `quot-<N>-<hash>` 目录才进入回收集合。集合之外的目录一律不动。
2. **纯缓存**，只走各自的官方命令，不手删缓存树：
   `npm cache clean --force` / `yarn cache clean` / `pip cache purge`。

每一笔删除在动手前先写一行审计日志，格式固定：

```
时间 | 路径 | 体积 | 判定依据
```

## 红线（护栏永不触碰）

- `in_progress` / `in_review` / `todo` / `blocked` 单对应的 task 目录
- 60 分钟内有写入活动的目录 —— 终态单也可能被 reopen 或被 wakeup 拉起新 run
- `docs/decisions/`、截图资产、`.qoder-cn` 里的配置与凭据
- **disk-warden**（QUOT-329 点火评论明确排除，早先版本里的「遗留 checkout 回收」一轮已删除）
- `C:/ProgramData` —— 需要动它必须回来找人类授权

判定只增不减：脚本对「要不要删」永远取保守值。探测命令本身报错时按「活跃」处理并跳过，
而不是当作「不活跃」继续删。

## 调度

`multica autopilot`（run_only，指派给运维工程师）：

| cron | 时区 | 说明 |
| --- | --- | --- |
| `30 7,21 * * *` | Asia/Shanghai | 每日 07:30 / 21:30 CST 两次 |

避开 09:00–15:30 交易时段，也避开 06:00 的时点快照采集窗口。

每次 run 的职责：执行脚本 → 读日志尾部取 `free_before` / `free_after` / 本次回收量 →
在 QUOT-329 评论里报告。出现非 0 退出码、回收后 free 仍 < 3 GB、或日志里出现
`TARGET NOT MET` / `CRITICAL` / `DELETE FAILED` 时，必须同时写清是哪一类目录占的、
为什么清不掉 —— 不允许静默。

查询排期是否还在：

```bash
multica autopilot list
multica autopilot trigger-list <autopilot-id>
```

## 一个会反复骗人的坑：Windows junction 让体积统计失真

`du` 会穿过 junction 重复计数，实测能把同一个目录放大到真实值的两倍以上。QUOT-327
当时记录的「`C:/ProgramData` 80 GB」就是这个产物 —— junction-safe 口径下它只有约 6 GB。
反过来 `du` 在 ACL 拒绝的路径上会静默少报。

被这几个 junction 坑过：`AppData/Local/Application Data`、`AppData/Local/History`、
`AppData/Local/Temporary Internet Files`、`AppData/Roaming/Qoder`。

脚本末尾的 `residual_report` 因此不用 `du`，改用一段跳过 reparse point 的 PowerShell 遍历，
并把读不动的子路径数量报出来而不是藏起来。写任何「谁是磁盘大户」的结论前先确认统计口径
是否 junction-safe，否则会朝一个不存在的大户要授权。

## 回滚

护栏删的都是可再生产物（依赖目录、构建产物、缓存），没有单文件级回滚。恢复方式：
`npm install` / `yarn install` / `pip install -r` 重建依赖，前端 `dist/` 重新构建。
源码、文档、截图、git 仓库本身永不进入回收集合，所以不存在丢工作的问题。
