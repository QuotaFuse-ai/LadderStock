<#
.SYNOPSIS
    QUOT-483：计划任务恢复（应急回滚通道）——把 Disabled 的任务恢复启用。

.DESCRIPTION
    与 Disable-DeadScheduledTasks.ps1 成对。Disable 只改 State 不动定义，
    因此恢复只需 Enable-ScheduledTask + 回读确认（Ready/Running）。

    合规用场景：
      * supervisor 验收 DeadTasks 禁用方案时，需要临时恢复对照；
      * 误禁用后回滚（如上一轮 8 个 peer 端口事件的应急恢复）。
    死任务恢复启用后会按触发器自行运行，Action 指向废弃路径 ×
    与现役模块同端的（LadderStock UI 5950）可能拉起重复实例，
    请只在对照/应急期间启用，验证后再次禁用。

    干跑模式列出全部 DeadTasks 的当前 State 与可恢复性；-Apply 才真恢复。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Restore-ScheduledTasks.ps1 -DryRun
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Restore-ScheduledTasks.ps1 -Tasks 'LadderStock UI 5950' -Apply
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Restore-ScheduledTasks.ps1 -Apply   # 恢复 DeadTasks 全部
#>
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(Position = 0)]
    [string[]]$Tasks = @(),
    [Parameter(ParameterSetName = 'Apply')]
    [switch]$Apply,
    [Parameter(ParameterSetName = 'List')]
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$cfg = Import-HostConfig
$names = @()
if ($Tasks.Count -gt 0) {
    $names = $Tasks | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
else {
    $names = $cfg.DeadTasks
}

$restored = 0
$skipped = 0
$report = @()

foreach ($name in $names) {
    $task = $null
    try { $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { }

    if (-not $task) {
        $report += [pscustomobject]@{ Task = $name; State = 'absent'; Result = 'skip'; Detail = '任务不存在（Enable 恢复的定义尚在；整个删了的才需要 task-backups XML 手动重建）' }
        $skipped++
        continue
    }

    $state = [string]$task.State
    if ($state -ne 'Disabled') {
        $report += [pscustomobject]@{ Task = $name; State = $state; Result = 'none'; Detail = '非 Disabled，无需恢复' }
        continue
    }

    if (-not $Apply) {
        $report += [pscustomobject]@{ Task = $name; State = $state; Result = 'would-restore'; Detail = '干跑：将 Enable-ScheduledTask 并回读确认' }
        $restored++
        continue
    }

    try {
        Enable-ScheduledTask -TaskName $name | Out-Null
        $now = [string](Get-ScheduledTask -TaskName $name).State
        $ok = ($now -eq 'Ready' -or $now -eq 'Running')
        $report += [pscustomobject]@{ Task = $name; State = $now; Result = $(if ($ok) { 'restored' } else { 'verify-failed' }); Detail = '' }
        if ($ok) {
            $restored++
            Write-HostLog ('task restore: {0} -> {1}' -f $name, $now)
        }
        else {
            $skipped++
            Write-HostLog ('task restore verify failed: {0} -> {1}' -f $name, $now) -Level Error
        }
    }
    catch {
        $report += [pscustomobject]@{ Task = $name; State = $state; Result = 'error'; Detail = $_.Exception.Message }
        $skipped++
        Write-HostLog ('task restore failed: {0} - {1}' -f $name, $_.Exception.Message) -Level Error
    }
}

Write-Host '==== 计划任务恢复（QUOT-483）===='
Write-Host ('模式：{0}' -f $(if ($Apply) { '-Apply 真实恢复' } else { '干跑' }))
Write-Host ''
$report | Format-Table Task, State, Result, Detail -AutoSize -Wrap | Out-String -Width 200 | Write-Host
Write-Host ('可恢复 {0} 条，跳过 {1} 条。' -f $restored, $skipped)

if ($Apply -and $restored -gt 0) {
    Send-HostToast -Title '计划任务已恢复' -Body ('恢复 {0} 条任务启用——请尽快验证后再次禁用' -f $restored)
}
if ($Apply -and $skipped -gt 0) { exit 1 }
exit 0
