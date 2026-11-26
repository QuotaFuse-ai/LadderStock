<#
.SYNOPSIS
    LadderStock 看板（5950 + schedule）启动适配器——收编自桌面「启动_LadderStock.cmd」。

.DESCRIPTION
    与原 cmd 的差异（QUOT-483 硬要求）：
      * 原脚本 `start "..." cmd /c` 开两个【黑窗口】拉 schedule 与 web —— 已废除。
        现在统一走计划任务「LadderStock API」+「LadderStock Schedule」（Running 托管，
        watchdog 保活），【不弹任何控制台窗口】。
      * REPO 统一为部署树 D:\QuotaFuseModules\LadderStock（正式服 5950）。
      * data_dir 校验（验收项）：5950 已在跑但 data_dir 不是
        D:\QuotaFuseModules\LadderStock\data 时判错实例，拒绝接管，不杀进程。

    启动链路就是「LadderStock API」任务：D:\QuotaFuseModules\LadderStock\ops\start_board_main.cmd 5950。
    正式服代码/解释器/数据都在部署树内，与测试服 D:\workplace\LadderStock（5951）完全隔离；
    原先借用 `LadderStock-app\src` + `LadderStock-main\.venv` 的临时口径已废止。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-LadderStockBoard.ps1
#>
[CmdletBinding()]
param(
    [int]$WaitSeconds = 90,
    [switch]$Json
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

& (Join-Path $PSScriptRoot '..\Start-LdstModules.ps1') -Only 'LadderStock API', 'LadderStock Schedule' -WaitSeconds $WaitSeconds -Json:$Json
exit $LASTEXITCODE
