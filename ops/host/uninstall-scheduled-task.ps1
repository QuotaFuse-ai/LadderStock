<#
.SYNOPSIS
    移除「LadderStock Single Entry」计划任务：切换到纯桌面双击启动形态。

.DESCRIPTION
    DEC-092 桌面形态下，服务生命周期由应用自己管理（双击 __desktop 即全起），
    不再需要登录触发的计划任务。运行本脚本会：
      1. 结束该任务当前正在运行的实例（如有）；
      2. 从任务计划程序删除该任务；
    幂等：任务不存在时直接退出，不做任何事。

    注意：运行中的 LadderStock 服务进程不会被强杀——如需立即停止，
    请使用托盘退出或 `Invoke-SingleEntry.ps1 -Action stop -Force`。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall-scheduled-task.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$task = 'LadderStock Single Entry'

$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    Write-Host "计划任务 '$task' 不存在（可能已移除），无需处理。"
    exit 0
}

Write-Host "找到计划任务 '$task'（状态：$($existing.State)），开始移除…"
schtasks.exe /End /TN "$task" 2>$null | Out-Null
schtasks.exe /Delete /TN "$task" /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "已移除计划任务 '$task'。之后启动 LadderStock 请使用桌面快捷方式（LadderStock.lnk）。"
} else {
    Write-Host "移除失败（exit $LASTEXITCODE）。可改用「任务计划程序」GUI 手动删除该任务。"
    exit $LASTEXITCODE
}