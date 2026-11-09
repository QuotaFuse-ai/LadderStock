<#
.SYNOPSIS
    TickHub CN（端口 8000）停止适配器——收编自桌面「stop_TickHub.cmd」。

.DESCRIPTION
    原 cmd 双路停（窗口标题 taskkill + 端口 PID）演变为：
      1. Stop-ScheduledTask 'TickHub CN 8000'（任务通道优先）
      2. 仅当 PID 仍持有 8000 时再杀该 PID（不看窗口标题，不看镜像名）
    作用域：-All（配置表全部）叠加 -Modules 过滤到单个模块，
    因此手动双击本适配器也能停"外部已运行"的 8000。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-TickHubPeer.ps1
#>
[CmdletBinding()]
param(
    [switch]$Json
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

& (Join-Path $PSScriptRoot '..\Stop-LdstModules.ps1') -All -Modules 'TickHub CN' -Json:$Json
exit $LASTEXITCODE
