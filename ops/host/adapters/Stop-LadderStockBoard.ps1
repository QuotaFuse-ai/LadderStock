<#
.SYNOPSIS
    LadderStock 看板（5950 + schedule）停止适配器——收编自桌面「停止_LadderStock.cmd」。

.DESCRIPTION
    原 cmd 按窗口标题 taskkill（LadderStock-Web / LadderStock-Schedule）+ 端口 PID 兜底。
    现在窗口标题已不存在（任务托管），改为：
      1. Stop-ScheduledTask 'LadderStock API' / 'LadderStock Schedule'
      2. 仅当 PID 仍持有 5950 / 5952 时再杀对应 PID
    作用域：-All + -Modules 过滤，手动双击可停"外部已运行"实例。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-LadderStockBoard.ps1
#>
[CmdletBinding()]
param(
    [switch]$Json
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

& (Join-Path $PSScriptRoot '..\Stop-LdstModules.ps1') -All -Modules 'LadderStock API', 'LadderStock Schedule' -Json:$Json
exit $LASTEXITCODE
