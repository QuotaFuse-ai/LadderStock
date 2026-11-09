# 六模块启停矩阵（QUOT-482-B）

> 范围：`supervisor/module_adapters.py` 里六个模块的真实启动 / 停止 / 探活规则。
> 内核契约（账本、引擎、归属判定）见 [ledger-and-lifecycle.md](./ledger-and-lifecycle.md)。
> 本文所有命令均为「可直接复制执行」的 PowerShell，目录按本机部署写死；
> 适配器本身不写死目录，解析顺序见 §7。

## 0. 总表

| 序 | 模块 | 端口（全部 127.0.0.1） | 启动入口 | 停止形态 | 识别关键字 |
|---|------|----------------------|---------|---------|-----------|
| 1 | tickhub | 8000（mcp）, 8787（admin） | `TickHub-CN/.venv/Scripts/th-cn.exe` | 进程树 | `th-cn` + `start-all` / `th-cn admin serve` |
| 2 | tickhub_us | 8002（mcp） | `TickHub-US/.venv/Scripts/th-us.exe` | 进程树 | `th-us` + `mcp http` + `8002` |
| 3 | wire | 8790（web）, 8791（mcp） | `Wire/quotafuse-wire/.venv/Scripts/qfw.exe` | 进程树 | `qfw serve` / `qfw mcp http` |
| 4 | senate | 8765（mcp）, 8786（webui） | `StockSenate/.venv/Scripts/ssenate.exe` | 进程树 | `ssenate mcp http` / `ssenate webui` |
| 5 | financecoffer | 8100（wslhost 转发） | WSL `docker compose up -d` + `wsl.exe` 锚进程 | WSL docker 容器 | compose project `financecoffer` + service `api` |
| 6 | proxyfleet | 8788, 8789 | 计划任务 `ProxyFleet`（SYSTEM） | `schtasks /end` | `pfctl.py run` |

探活口径（适配器 `probe()`）：**端口在监听，且监听进程的 cmdline 命中该端口对应面的关键字**；
金融柜没有 Windows 侧业务进程，改为「8100 在听 **且** WSL 内 financecoffer/api 容器在运行」。
读不到证据一律判 False（失败关闭）。

## 1. tickhub（8000 / 8787）

两个面、同一个 exe，必须分别拉起、一起停止。

启动：

```powershell
& 'D:\workplace\TickHub-CN\.venv\Scripts\th-cn.exe' start-all --host 127.0.0.1 --port 8000 --crawler on
& 'D:\workplace\TickHub-CN\.venv\Scripts\th-cn.exe' admin serve --host 127.0.0.1 --port 8787 --force
```

停止（按端口找持有者，整树不停则升级 `/F`）：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000,8787 |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { taskkill /PID $_ /T }
# 3 秒后端口仍在：换成 taskkill /PID $_ /T /F
```

识别关键字：8000 → `th-cn` + `start-all`；8787 → `th-cn` + `admin` + `serve`。

已知坑：

- 控制台脚本是两层进程：`th-cn.exe` 只是 launcher，真正监听端口的是它的
  `python.exe` 子进程；`OwningProcess` 看到的 cmdline 形如
  `"python.exe" "D:\workplace\TickHub-CN\.venv\Scripts\th-cn.exe" start-all ...`，
  所以识别要匹配**完整命令行**，不能只比进程名。
- 两个面独立存活：只停 8000 的树，8787 仍在听。适配器 `stop()` 一次把所有面
  都停干净，避免引擎强杀只认主 PID 留孤儿。
- `--crawler on` 是线上既有形态；`start-all` 起爬虫后会持续写数据，启停窗口尽量
  避开盘中。
- 环境变量来自 `~/.tickhub/proxy.env` 与 `D:\QuotaFuseModules\TickHub-CN\ops\peer.env`
  （存在才加载，可用 `LDST_TICKHUB_ENV_FILES` 覆盖）。**不要把文件内容写进日志或提交**。

## 2. tickhub_us（8002）

启动：

```powershell
& 'D:\workplace\TickHub-US\.venv\Scripts\th-us.exe' mcp http --host 127.0.0.1 --port 8002
```

停止：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8002 |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { taskkill /PID $_ /T }
```

识别关键字：`th-us` + `mcp` + `http` + `8002`（带上端口，避免与 CN 侧命令混淆）。

已知坑：

- 与 tickhub 共用同名子命令前缀（`mcp http`），所以关键字里必须含 `th-us` 与 `8002`。
- 美股时段外进程仍在，不要按「有没有请求」判断死活，只认端口 + cmdline。

## 3. wire（8790 / 8791）

两个面：web 控制台与 MCP，都是 `qfw.exe`。

启动：

```powershell
& 'D:\workplace\Wire\quotafuse-wire\.venv\Scripts\qfw.exe' serve --host 127.0.0.1 --port 8790
& 'D:\workplace\Wire\quotafuse-wire\.venv\Scripts\qfw.exe' mcp http --host 127.0.0.1 --port 8791
```

停止：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8790,8791 |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { taskkill /PID $_ /T }
```

识别关键字：8790 → `qfw` + `serve`；8791 → `qfw` + `mcp` + `http`。

已知坑：

- 仓库目录多嵌套一层：`Wire\quotafuse-wire`（`PEER_SIBLING_DIRS` 已记录），
  按 `D:\workplace\Wire\quotafuse-wire` 找，不要少一层。
- 本机 qfw 的 venv 由 `D:\workplace\ruanjian\python312` 创建，和 tickhub 的
  uv Python 不是同一个解释器；换个 Python 重建 venv 会让入口失效（复制命令时
  不要改 `Scripts\qfw.exe` 的相对位置）。
- 两个面共用同一 venv 与数据目录，先停 web 再停 mcp 没有依赖问题；反序亦可，
  适配器一次停两棵树。

## 4. senate（8765 / 8786）

start：

```powershell
& 'D:\workplace\StockSenate\.venv\Scripts\ssenate.exe' mcp http --host 127.0.0.1 --port 8765
& 'D:\workplace\StockSenate\.venv\Scripts\ssenate.exe' webui --host 127.0.0.1 --port 8786
```

停止：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8765,8786 |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { taskkill /PID $_ /T }
```

识别关键字：8765 → `ssenate` + `mcp` + `http`；8786 → `ssenate` + `webui`。

已知坑：

- `ssenate` 与 `th-us` 都有 `mcp http` 子命令，识别必须含 `ssenate`。
- `webui` 面依赖同目录静态资源，`cwd` 必须是仓库根；用适配器启动时已保证，
  手工复制命令前先 `cd D:\workplace\StockSenate`。

## 5. financecoffer（8100，WSL）

服务全在 WSL 容器里，Windows 侧 8100 只是 `wslhost.exe` 的端口转发。
停止/启动都必须进 WSL 操作容器，**不能靠 taskkill wslhost**。

启动（`deploy/.env` 存在时 compose up；缺失时退回 docker start 既有容器）：

```powershell
wsl.exe -d (wsl.exe -l -v | Select-String '^\*' | ForEach-Object { ($_ -replace '^\*\s*','').Split()[0] }) -u root -- bash -lc "cd /mnt/d/workplace/FinanceCoffer/deploy && docker compose --env-file .env -f compose.yaml up -d"
# deploy/.env 不存在时：
wsl.exe -d <发行版> -u root -- bash -lc 'ids=$(docker ps -aq --filter label=com.docker.compose.project=financecoffer); docker start $ids'
```

停止（compose stop → docker stop → docker kill，判据是回读容器为空）：

```powershell
wsl.exe -d <发行版> -u root -- bash -lc "cd /mnt/d/workplace/FinanceCoffer/deploy && docker compose --env-file .env -f compose.yaml stop -t 20; ids=`$(docker ps -q --filter label=com.docker.compose.project=financecoffer); [ -n \"`$ids\" ] && echo \"`$ids\" | xargs -r docker stop -t 20; remain=`$(docker ps -q --filter label=com.docker.compose.project=financecoffer); [ -n \"`$remain\" ] && echo \"`$remain\" | xargs -r docker kill; after=`$(docker ps -q --filter label=com.docker.compose.project=financecoffer); [ -z \"`$after\" ] && echo FC_STOPPED=yes || echo FC_STOPPED=no"
```

识别：8100 在监听 **且** 下面这条命令有输出：

```powershell
wsl.exe -d <发行版> -u root -- bash -lc "docker ps --filter label=com.docker.compose.project=financecoffer --filter label=com.docker.compose.service=api --format '{{.Names}}'"
```

已知坑：

- 发行版名不写死：适配器取 `wsl -l -v` 里带 `*` 的默认发行版，`LDST_WSL_DISTRO` 可覆盖。
- `wslhost.exe` 只是转发器，**不能作为归属证据**：容器停了端口有时还会残留几十秒，
  反之容器在跑但转发没建好也会短暂不可达。探活必须双证据。
- 停止判据是「回读不到 financecoffer 容器」，不是命令退出码；`docker stop` 超时后
  还要 `docker kill` 兜底。
- 本机 `deploy/.env` 当前不存在，`compose up` 会失败；适配器按 `wsl-boot.sh` 同款
  逻辑退回 `docker start` 既有容器。
- `wsl.exe` 调用带 180s 超时；docker 冷启动可能接近这个量级，不要用更短的超时手测。
- WSL 内 docker 的容器内嵌 DNS 故障（bridge 网络）与本模块启停无关，但 `compose up`
  重建镜像时会踩到，见 `core/boundary.py` 的 config_notes。

## 6. proxyfleet（8788 / 8789）

只通过**已存在的同名计划任务**启停，绝不 taskkill、绝不改运行身份（SYSTEM）。

启动：

```powershell
schtasks.exe /change /tn ProxyFleet /enable
schtasks.exe /run /tn ProxyFleet
```

停止：

```powershell
schtasks.exe /change /tn ProxyFleet /disable
schtasks.exe /end /tn ProxyFleet
```

识别关键字：`pfctl.py` + `run`（监听进程 cmdline 形如
`python.exe "D:\workplace\ProxyFleet\pfctl.py" run`）。

已知坑：

- 任务每约 1 分钟自愈触发：只 `/end` 不 `/disable`，一分钟内会被拉回来。
  所以停止顺序固定为「先 disable、再 end」。
- `/run` 返回 `267011`（任务已在运行）对启动而言就是目标态，不当失败。
- 任务账户是 SYSTEM；用普通用户 `schtasks /run` 可以触发，但 `/change` 需要管理员。
  适配器要求 supervisor 以管理员身份运行。
- 状态回读用 `Get-ScheduledTaskInfo ProxyFleet` 看 `LastTaskResult` / `LastRunTime`，
  再对端口，不要只看任务名存在。

## 7. 目录与覆盖变量

| 模块 | 默认目录（同级优先） | 覆盖变量 |
|------|--------------------|---------|
| tickhub | `D:\workplace\TickHub-CN` | `LDST_PEER_DIR_TICKHUB` |
| tickhub_us | `D:\workplace\TickHub-US` | `LDST_PEER_DIR_TICKHUB_US` |
| wire | `D:\workplace\Wire\quotafuse-wire` | `LDST_PEER_DIR_WIRE` |
| senate | `D:\workplace\StockSenate` | `LDST_PEER_DIR_SENATE` |
| financecoffer | `D:\workplace\FinanceCoffer` | `LDST_PEER_DIR_FINANCECOFFER` |
| proxyfleet | 任务名 `ProxyFleet` | `LDST_PROXYFLEET_TASK` |

其它：

- `LDST_PEER_ROOT`：同级目录不存在时的部署根，默认 `D:\workplace`。
- `LDST_WSL_DISTRO`：WSL 发行版覆盖。
- `LDST_TICKHUB_ENV_FILES`：tickhub env 文件列表（`os.pathsep` 分隔）。
- 日志目录：`%LOCALAPPDATA%\LadderStock\logs`（每个面一个文件：
  `tickhub-mcp.log` / `tickhub-admin.log` / `tickhub-us-mcp.log` / `wire-web.log` /
  `wire-mcp.log` / `senate-mcp.log` / `senate-webui.log` / `financecoffer-anchor.log`）。

## 8. 验收命令

单模块启动后回读端口：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000,8787 | Select-Object LocalAddress,LocalPort,OwningProcess
```

单模块停止后确认端口消失：

```powershell
@(Get-NetTCPConnection -State Listen -LocalPort 8000,8787 -ErrorAction SilentlyContinue).Count  # 期望 0
```

确认没有孤儿（按入口关键字扫整机）：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'th-cn\.exe|th-us\.exe|qfw\.exe|ssenate\.exe' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

金融柜双证据：

```powershell
(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue) -ne $null
wsl.exe -d <发行版> -u root -- bash -lc "docker ps --filter label=com.docker.compose.project=financecoffer --format '{{.Names}}'"
```

代理池任务状态：

```powershell
Get-ScheduledTaskInfo -TaskName ProxyFleet | Select-Object LastRunTime,LastTaskResult,NextRunTime
```
