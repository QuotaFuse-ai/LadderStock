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
    @{ Name = 'LadderStock Schedule'; Task = 'LadderStock Schedule';    Port = 5952;  HealthUrl = ''; Wmi = ''; DataDir = '' }
    @{ Name = 'Vibe AStock API';   Task = 'Vibe AStock API';            Port = 8770;  HealthUrl = ''; Wmi = 'D:\vibe-astock\scripts\svc_api.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'Vibe AStock UI';    Task = 'Vibe AStock UI';             Port = 5940;  HealthUrl = ''; Wmi = 'D:\vibe-astock\scripts\svc_ui.cmd'; WmiArg = ''; WmiType = 'script'; DataDir = '' }
    @{ Name = 'ProxyFleet';        Task = 'ProxyFleet';                 Port = 0;     HealthUrl = ''; Wmi = 'D:\workplace\ruanjian\python312\python.exe'; WmiArg = 'D:\workplace\ProxyFleet\pfctl.py run'; WmiType = 'raw'; DataDir = '' }
)

# 依赖顺序（Start 的排序依据；不在表中的名字忽略）
StartOrder = @(
    'ProxyFleet', 'TickHub CN', 'TickHub Admin', 'TickHub US',
    'QF Wire Web', 'QF Wire MCP', 'StockSenate MCP', 'StockSenate WebUI',
    'LadderStock API', 'LadderStock Schedule', 'Vibe AStock API', 'Vibe AStock UI'
)

# QUOT-483 查清结论：以下任务为幂等健康检查 / 保活 / 开机自启通道，无停服副作用，保留不禁。
KeepTasks = @('Peer Startup Check', 'TickHub CN 8000 Watchdog', 'LadderStock Modules', 'LadderStock Watchdog', 'LadderStock Watch Scan')

# 死任务清单（旧对偶，Action 指向已废弃的 multica_workdir powershell）。
# 对应 Disable-DeadScheduledTasks.ps1；【默认只报告不执行，等 supervisor 验收通过后再启用】。
DeadTasks = @('QF Wire 8790', 'QF Wire MCP 8791', 'TickHub CN Admin 8787', 'TickHub US 8002', 'LadderStock UI 5950')

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
