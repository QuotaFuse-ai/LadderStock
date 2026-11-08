<#
.SYNOPSIS
    TickHub CN（端口 8000）启动适配器——收编自桌面「启动_TickHub.cmd」。

.DESCRIPTION
    行为对齐原 cmd：8000 已监听则跳过（幂等）；否则拉起 MCP peer。
    代理 A/B 开关仍是一行 C:\Users\Administrator\.tickhub\proxy.env 的 PROXY_PROVIDER
    （kuaidaili = B 轮换私 IP；tunnel = A 隧道网关），本脚本不改它。
    启动通道：计划任务「TickHub CN 8000」优先（watchdog 托管），不可用才走隐藏 WMI 兜底。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-TickHubPeer.ps1
#>
[CmdletBinding()]
param(
    [int]$WaitSeconds = 60,
    [switch]$Json
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

& (Join-Path $PSScriptRoot '..\Start-LdstModules.ps1') -Only 'TickHub CN' -WaitSeconds $WaitSeconds -Json:$Json
exit $LASTEXITCODE
