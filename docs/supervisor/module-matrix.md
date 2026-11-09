# 六模块启停矩阵（QUOT-482-B）+ 按需代理池（QUOT-506）

> 范围：`supervisor/module_adapters.py` 里六个顺序模块与按需模块 `proxypool` 的真实
> 启动 / 停止 / 探活规则。
> 内核契约（账本、引擎、归属判定）见 [ledger-and-lifecycle.md](./ledger-and-lifecycle.md)。
> 本文所有命令均为「可直接复制执行」的 PowerShell，目录按本机部署写死；
> 适配器本身不写死目录，解析顺序见 §8。

## 0. 总表

| 序 | 模块 | 端口（全部 127.0.0.1） | 启动入口 | 停止形态 | 识别关键字 |
|---|------|----------------------|---------|---------|-----------|
| 1 | tickhub | 8000（mcp）, 8787（admin） | `TickHub-CN/.venv/Scripts/th-cn.exe` | 进程树 | `th-cn` + `start-all` / `th-cn admin serve` |
| 2 | tickhub_us | 8002（mcp） | `TickHub-US/.venv/Scripts/th-us.exe` | 进程树 | `th-us` + `mcp http` + `8002` |
| 3 | wire | 8790（web）, 8791（mcp） | `Wire/quotafuse-wire/.venv/Scripts/qfw.exe` | 进程树 | `qfw serve` / `qfw mcp http` |
| 4 | senate | 8765（mcp）, 8786（webui） | `StockSenate/.venv/Scripts/ssenate.exe` | 进程树 | `ssenate mcp http` / `ssenate webui` |
| 5 | financecoffer | 8100（wslhost 转发） | WSL `docker compose up -d` + `wsl.exe` 锚进程 | WSL docker 容器 | compose project `financecoffer` + service `api` |
| 6 | proxyfleet | 8788, 8789 | 计划任务 `ProxyFleet`（SYSTEM） | `schtasks /end` | `pfctl.py run` |
| 7 | proxypool（按需） | 配置 `Mappings[].Listen`（默认 18080），**不在静态白名单** | `python proxypool_forwarder.py --id <映射>`（每条映射一个进程） | 进程树 | `proxypool_forwarder` + `--id` + 映射 Id |

探活口径（适配器 `probe()`）：**端口在监听，且监听进程的 cmdline 命中该端口对应面的关键字**；
金融柜没有 Windows 侧业务进程，改为「8100 在听 **且** WSL 内 financecoffer/api 容器在运行」；
代理池是**双向判据**：「端口在听 **且** 对该端口真实 TCP 建连成功 **且** 持有者 cmdline 命中本模块转发标记」。
读不到证据一律判 False（失败关闭）。

代理池是**按需模块**：登记在 `ON_DEMAND_MODULES`，不在 `MODULE_ORDER` 里——
`start_all` / `stop_all` 不碰它，只能经单模块入口显式拉起；配置 `Enabled` / `AutoStart`
均默认 `$false`，实现里没有任何自启分支。

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
  ForEach-Object { taskkill /PID $_ /T /F }
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
- 这几个 Python 服务对温和 `taskkill /T` 不响应（报「只能强制终止」），停止命令
  一律带 `/F`；适配器先发 CTRL_BREAK、3 秒内不退出再升级 `/F`。
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
  ForEach-Object { taskkill /PID $_ /T /F }
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
  ForEach-Object { taskkill /PID $_ /T /F }
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
  ForEach-Object { taskkill /PID $_ /T /F }
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
# 先取默认发行版（PS 5.1 直接读 wsl 输出会因 UTF-16 乱码，这里临时切 Unicode）
$d = (& { $o=[Console]::OutputEncoding; [Console]::OutputEncoding=[Text.Encoding]::Unicode
  try { (wsl.exe -l -v | Select-String '^\s*\*' | ForEach-Object { ($_.ToString() -replace '^\s*\*\s*','').Split()[0] })
  } finally { [Console]::OutputEncoding=$o } })

# deploy/.env 存在时：
wsl.exe -d $d -u root -e bash -lc "cd /mnt/d/workplace/FinanceCoffer/deploy && docker compose --env-file .env -f compose.yaml up -d"
# deploy/.env 不存在时（本机现状）：
wsl.exe -d $d -u root -e bash -lc 'ids=$(docker ps -aq --filter label=com.docker.compose.project=financecoffer); docker start $ids'
```

停止（compose stop → docker stop → docker kill，判据是回读容器为空）：

```powershell
$d = (& { $o=[Console]::OutputEncoding; [Console]::OutputEncoding=[Text.Encoding]::Unicode
  try { (wsl.exe -l -v | Select-String '^\s*\*' | ForEach-Object { ($_.ToString() -replace '^\s*\*\s*','').Split()[0] })
  } finally { [Console]::OutputEncoding=$o } })
# 注意：PS 5.1 传原生参数时会把内嵌双引号改写成 \"，这里脚本内一律不用双引号。
$script = @'
set -u
pkill -f '^fc-anchor' 2>/dev/null || true
D=label=com.docker.compose.project=financecoffer
if [ -f /mnt/d/workplace/FinanceCoffer/deploy/.env ] && [ -f /mnt/d/workplace/FinanceCoffer/deploy/compose.yaml ]; then
  cd /mnt/d/workplace/FinanceCoffer/deploy && docker compose --env-file .env -f compose.yaml stop -t 20
fi
docker ps -q --filter $D | xargs -r docker stop -t 20
docker ps -q --filter $D | xargs -r docker kill
count=$(docker ps -q --filter $D | wc -l)
if [ $count -eq 0 ]; then echo FC_STOPPED=yes; else echo FC_STOPPED=no; exit 1; fi
'@
wsl.exe -d $d -u root -e bash -lc $script
```

识别：8100 在监听 **且** 下面这条命令有输出：

```powershell
$d = (& { $o=[Console]::OutputEncoding; [Console]::OutputEncoding=[Text.Encoding]::Unicode
  try { (wsl.exe -l -v | Select-String '^\s*\*' | ForEach-Object { ($_.ToString() -replace '^\s*\*\s*','').Split()[0] })
  } finally { [Console]::OutputEncoding=$o } })
wsl.exe -d $d -u root -e bash -lc "docker ps --filter label=com.docker.compose.project=financecoffer --filter label=com.docker.compose.service=api --format '{{.Names}}'"
```

已知坑：

- 发行版名不写死：适配器取 `wsl -l -v` 里带 `*` 的默认发行版，`LDST_WSL_DISTRO` 可覆盖。
- `wsl.exe` 必须带 `-e` 再给 `bash -lc <脚本>`：缺省 `--` 形态会先把命令交给默认
  shell 二次展开，脚本里 `$(docker ps -q ...)` 被提前求值，多行容器 ID 变成独立
  命令行（实测 `bash: line 2: <id>: command not found`，停止脚本静默失效）。
- PS 5.1 给原生程序传参时会把脚本里的双引号改写成 `\"`，bash 收到后丢引号、按空格
  拆词（`[: too many arguments`）。手工执行的脚本里一律不写双引号；适配器走
  `subprocess` argv 直传，不受此影响。
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

## 7. proxypool（按需，端口来自配置）

不是常驻服务，而是**可被按需拉起、随主程序关闭一起停**的 TCP 直通转发层。
配置在 `ops/host/LdstHostConfig.psd1` 的 `Modules` 表里（`Name='proxypool'`），
`Enabled` / `AutoStart` 默认都是 `$false`，且**不在 `StartOrder` 里**——开机、
launcher 与 supervisor 全启都不会碰它，只能显式启动。

```powershell
# 一个模块管 N 条映射：整池一次 start 拉起全部 Enabled 的映射（每条一个转发进程）
& python 'src\ladderstock\supervisor\proxypool_forwarder.py' --id multica-api `
    --listen-host 127.0.0.1 --listen-port 18080 `
    --upstream-host 127.0.0.1 --upstream-port 8080
```

规则：

- **默认不启动**：无计划任务、无 Run 键、无启动文件夹项；代码里没有自启分支。
- **纯 TCP 直通**：不解析、不记录载荷、不带凭据；监听地址只允许 `ListenHost`（回环）。
- **probe 双向判据**：端口监听表有持有者 **且** 对该端口真实 TCP 建连成功 **且**
  持有者 cmdline 命中 `proxypool_forwarder` + `--id` + 映射 Id。只查端口在听不算数。
- **端口冲突必须报错**：`Listen` 与其它模块端口表冲突、映射间重复、或被非本模块进程
  占用时，`start()` 抛出含端口号的错误，**不许静默换端口**。
- **归属**：本次拉起的记 `managed`；端口上已有同映射 Id 的转发进程（前任 supervisor
  残留）记 `adopted`，不再拉第二条、不强杀，并在适配器的 `adopted` 结果里报出；
  真正的外部进程一律拒绝启动。
- **停止判据**：回读端口不可连接，且按 cmdline 关键字扫描无残留转发进程。
- 不用 `netsh interface portproxy`（绑 IP 栈、WSL 重启即失效、不可见不可控，见 QUOT-490）。

手工复现与验收：

```powershell
# 1) 启动后：监听端口 + pid + cmdline
Get-NetTCPConnection -State Listen -LocalPort 18080 | Select-Object LocalAddress,LocalPort,OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId=<pid>" | Select-Object ProcessId,ParentProcessId,CommandLine

# 2) 真实建连（双向判据的第二条；只 listen 不 accept 的假服务不算通）
(Test-NetConnection 127.0.0.1 -Port 18080).TcpTestSucceeded

# 3) 停止后：不可连接 + 无残留进程
@(Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction SilentlyContinue).Count   # 期望 0
# 只看 python 进程：用 shell 查同关键字会把查询命令自己也算进去（自匹配）
@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'python*' -and $_.CommandLine -match 'proxypool_forwarder\.py'
}).Count   # 期望 0
```

## 8. 目录与覆盖变量

| 模块 | 默认目录（同级优先） | 覆盖变量 |
|------|--------------------|---------|
| tickhub | `D:\workplace\TickHub-CN` | `LDST_PEER_DIR_TICKHUB` |
| tickhub_us | `D:\workplace\TickHub-US` | `LDST_PEER_DIR_TICKHUB_US` |
| wire | `D:\workplace\Wire\quotafuse-wire` | `LDST_PEER_DIR_WIRE` |
| senate | `D:\workplace\StockSenate` | `LDST_PEER_DIR_SENATE` |
| financecoffer | `D:\workplace\FinanceCoffer` | `LDST_PEER_DIR_FINANCECOFFER` |
| proxyfleet | 任务名 `ProxyFleet` | `LDST_PROXYFLEET_TASK` |
| proxypool | `ops/host/LdstHostConfig.psd1` | `LDST_HOST_CONFIG` |

其它：

- `LDST_PEER_ROOT`：同级目录不存在时的部署根，默认 `D:\workplace`。
- `LDST_WSL_DISTRO`：WSL 发行版覆盖。
- `LDST_TICKHUB_ENV_FILES`：tickhub env 文件列表（`os.pathsep` 分隔）。
- 日志目录：`%LOCALAPPDATA%\LadderStock\logs`（每个面一个文件：
  `tickhub-mcp.log` / `tickhub-admin.log` / `tickhub-us-mcp.log` / `wire-web.log` /
  `wire-mcp.log` / `senate-mcp.log` / `senate-webui.log` / `financecoffer-anchor.log` /
  `proxypool-<映射 Id>.log`）。

## 9. 验收命令

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
$d = (& { $o=[Console]::OutputEncoding; [Console]::OutputEncoding=[Text.Encoding]::Unicode
  try { (wsl.exe -l -v | Select-String '^\s*\*' | ForEach-Object { ($_.ToString() -replace '^\s*\*\s*','').Split()[0] })
  } finally { [Console]::OutputEncoding=$o } })
wsl.exe -d $d -u root -e bash -lc "docker ps --filter label=com.docker.compose.project=financecoffer --format '{{.Names}}'"
```

代理池任务状态：

```powershell
Get-ScheduledTaskInfo -TaskName ProxyFleet | Select-Object LastRunTime,LastTaskResult,NextRunTime
```
