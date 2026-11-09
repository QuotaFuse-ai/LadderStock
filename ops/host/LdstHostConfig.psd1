# QUOT-483 宿主侧 supervisor 配置（主机端开发，2026-11-08）。
# 单一事实来源：启动顺序 / 任务名 / 端口 / 健康探针 / 死任务清单都在这里。
# 注意：psd1 只能放字面量，任何 env 组合在脚本侧完成。

# 模块表 = 单图标 launcher 的拉起与停止依据，顺序即依赖顺序。
# Task      计划任务名（首选通道：Start-ScheduledTask，由 supervisor/watchdog 托管）
# Port      本机监听端口（空 = 不探测，仅看任务状态）
# HealthUrl HTTP 健康探针（可选）
# Wmi/WmiArg/WmiType 计划任务不可用（不存在/Disabled）时的隐藏兜底拉起命令。
#           WmiType=script 时经 powershell -WindowStyle Hidden 包装（不弹控制台窗口）；
#           WmiType=raw 时按原样执行（空 = 无兜底）
# DataDir   data_dir 校验（仅 LadderStock API：非部署树实例一律判错并拒绝接管）
# 注意：Schedule 的 5952 不做端口探活（Port=0）——实测持有者是 watchdog python 进程而非 schedule
# 服务本身，探通≠服务活着；健康判定以任务状态 Running 为准（QUOT-483 2026-11-09）。
@{
Modules = @(
    @{ Name = 'TickHub CN';        Task = 'TickHub CN 8000';            Port = 8000;  HealthUrl = ''; Wmi = 'C:\Users\Administrator\.tickhub\start_peer.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'TickHub Admin';     Task = 'QF-Peer-TickHub-Admin';      Port = 8787;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_tickhub_admin.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'TickHub US';        Task = 'QF-Peer-TickHub-US';         Port = 8002;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_tickhub_us.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'QF Wire Web';       Task = 'QF-Peer-Wire-Web';           Port = 8790;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_wire_web.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'QF Wire MCP';       Task = 'QF-Peer-Wire-MCP';           Port = 8791;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_wire.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'StockSenate MCP';   Task = 'QF-Peer-StockSenate-MCP';    Port = 8765;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_stocksenate.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'StockSenate WebUI'; Task = 'QF-Peer-StockSenate-WebUI';  Port = 8786;  HealthUrl = ''; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\peers\start_stocksenate_webui.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'LadderStock API';   Task = 'LadderStock API';            Port = 5950;  HealthUrl = 'http://127.0.0.1:5950/api/health'; Wmi = 'D:\QuotaFuseModules\LadderStock\ops\start_board_main.cmd'; WmiArg = '5950'; WmiType = 'script'; DataDir = 'D:\QuotaFuseModules\LadderStock\data' }
    @{ Name = 'LadderStock Schedule'; Task = 'LadderStock Schedule';    Port = 0;     HealthUrl = ''; Wmi = ''; DataDir = '' }
    @{ Name = 'Vibe AStock API';   Task = 'Vibe AStock API';            Port = 8770;  HealthUrl = ''; Wmi = 'D:\vibe-astock\scripts\svc_api.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'Vibe AStock UI';    Task = 'Vibe AStock UI';             Port = 5940;  HealthUrl = ''; Wmi = 'D:\vibe-astock\scripts\svc_ui.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'ProxyFleet';        Task = 'ProxyFleet';                 Port = 0;     HealthUrl = ''; Wmi = 'D:\workplace\ruanjian\python312\python.exe'; WmiArg = 'D:\workplace\ProxyFleet\pfctl.py run'; WmiType = 'raw'; DataDir = '' }
    # proxypool（QUOT-506）：按需拉起的 TCP 直通转发池。默认不启动——Enabled/AutoStart 均 false，
    # 且【不在 StartOrder 里】：launcher 与 supervisor 全启都不碰它，只能经单模块入口显式拉起。
    # ListenHost 只允许回环；Mappings[].Listen 与其它模块端口表冲突时 start() 直接报错，不换端口。
    # Task/Port 为空是刻意的：本模块无计划任务、无固定端口（端口在 Mappings 里），
    # 留全字段只为兼容 HostCommon.ps1 的 StrictMode 属性访问。
    @{ Name = 'proxypool';         Enabled = $false; AutoStart = $false; ListenHost = '127.0.0.1'
       Mappings = @(
           @{ Id = 'multica-api'; Listen = 18080; UpstreamHost = '127.0.0.1'; UpstreamPort = 8080; Enabled = $true; Owner = 'ai' }
       )
       SelfHeal = @{ MaxRestarts = 2; WindowMinutes = 10 }
       NeedsHumanOnLimit = $true
       Task = ''; Port = 0; HealthUrl = ''; Wmi = ''; WmiArg = ''; WmiType = ''; DataDir = '' }
)

# 依赖顺序（Start 的排序依据；不在表中的名字忽略）
StartOrder = @(
    'ProxyFleet', 'TickHub CN', 'TickHub Admin', 'TickHub US',
    'QF Wire Web', 'QF Wire MCP', 'StockSenate MCP', 'StockSenate WebUI',
    'LadderStock API', 'LadderStock Schedule', 'Vibe AStock API', 'Vibe AStock UI'
)

# 保留清单（QUOT-483 第 1 步逐条判定的 A 类，禁止禁用）：
#   Peer Startup Check        保留（收编候选：待 482 验收后把默认 $Tasks 换成 QF-Peer-*+8000）
#   TickHub CN 8000 Watchdog  保留（ensure 自愈专用、不抢端口，QUOT-456 修复版）
#   LadderStock Modules       保留（幂等批量兜底，与 QF-Peer-* 不冲突）
#   LadderStock Watchdog      暂保留；可禁但延后——豆包沙箱 python 脆点，须等 482 自愈就位
#                             且确认 5952 守护唯一性后由 482 执行（2026-11-09 实测 5952 树唯一）
#   LadderStock Watch Scan    暂保留待裁定——Action 指向已删 quot-200 脚本（实测 MISSING），
#                             周期触发空失败；收编或退役由 supervisor 裁定
KeepTasks = @('Peer Startup Check', 'TickHub CN 8000 Watchdog', 'LadderStock Modules', 'LadderStock Watchdog', 'LadderStock Watch Scan')

# 死任务清单（QUOT-483 回炉版，2026-11-09 实测三类裁定）：
#   * 前 6 条 = 旧入口同名任务，Action 指向已删 quot-200 run-logs 脚本（实测全部 MISSING），
#     端口现由 QF-Peer-* 六条在供（StockSenate WebUI 实际在 8786，8788 属 ProxyFleet/DEC-047）；
#   * LadderStock UI 5950 = 与 LadderStock API 任务同体（同 Action 同端口），双实例风险，维持 Disabled；
#   * 对应 Disable / Restore 两脚本；【默认只报告不执行，等 supervisor 验收通过后再启用】。
DeadTasks = @(
    'QF Wire 8790', 'QF Wire MCP 8791',
    'StockSenate MCP 8765', 'StockSenate WebUI 8788',
    'TickHub CN Admin 8787', 'TickHub US 8002',
    'LadderStock UI 5950'
)

# 日志上限（D5）
LogMaxBytes = 10485760
LogKeep     = 3

# 前置检查 portproxy 映射（监听端口 -> WSL 目标端口）
PortProxy = @(
    @{ Listen = 18080; Connect = 8080 }
    @{ Listen = 13000; Connect = 3000 }
    @{ Listen = 3000;  Connect = 3000 }
    @{ Listen = 8080;  Connect = 8080 }
)
}
