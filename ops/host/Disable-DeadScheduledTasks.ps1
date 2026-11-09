<#
.SYNOPSIS
    QUOT-483：禁用计划任务死清单（旧对偶）。【默认干跑，只报告不执行】。

    !! 禁用动作整体推迟 —— 按 QUOT-483 裁定（2026-11-08 评论 01a1bac3）：
    !! 上一轮误禁打死过 8 个 peer 端口（已人工恢复），本轮禁止执行任何
    !! Disable 操作。本脚本只入库备查，等 supervisor 验收通过后再启用。
    !! 日常运维请勿直接 -Apply，需小队 owner 明确批准。

.DESCRIPTION
    清单来源：LdstHostConfig.psd1 的 DeadTasks（与 KeepTasks 互斥校验）。
    现状基线（2026-11-09 只读实测，回炉派单步骤 1）：7 条均已 Disabled——
      QF Wire 8790 / QF Wire MCP 8791 / TickHub CN Admin 8787 / TickHub US 8002
        Action 指向已废弃的 multica_workspaces 旧 run（quot-200）目录，真死任务；
      StockSenate MCP 8765 / StockSenate WebUI 8788
        Action 同指向 quot-200 旧 run 目录（旧入口，端口由 QF-Peer-* 对位任务在供）；
      LadderStock UI 5950
        Action 指向 start_board_main.cmd 5950（与现役 LadderStock API 同源），
        旧对偶命名，裁定可禁（现已是 Disabled）。

    -Apply 时逐条执行，且有三道护栏：
      1. 只碰 DeadTasks 清单内的任务名；
      2. Action 校验：Execute/Arguments 必须含 multica_workspaces 或 multica_workdir
         （即确属旧 agent run 残留）才允许禁用，不匹配默认跳过（-Force 才放行）；
      3. 禁用前先 Export-ScheduledTask 导出 XML 到
         %LOCALAPPDATA%\LadderStock\task-backups\<任务名>.xml，供恢复/重建。
    Disable 只改 State 不动定义，恢复用 Restore-ScheduledTasks.ps1（Enable 即可）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Disable-DeadScheduledTasks.ps1              # 干跑报告
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Disable-DeadScheduledTasks.ps1 -Apply       # 真禁（需批准）
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Disable-DeadScheduledTasks.ps1 -Apply -Force
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Force,
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$cfg = Import-HostConfig

# 护栏 0：DeadTasks 与 KeepTasks 不得相交
$dup = @($cfg.DeadTasks | Where-Object { $cfg.KeepTasks -contains $_ })
if ($dup.Count -gt 0) {
    Write-Host ('[FAIL] DeadTasks 与 KeepTasks 相交：{0}，配置有误，拒绝执行' -f ($dup -join ', '))
    exit 1
}

$backupDir = Join-Path $env:LOCALAPPDATA 'LadderStock\task-backups'
$changed = 0
$skipped = 0
$report = @()

foreach ($name in $cfg.DeadTasks) {
    $task = $null
    try { $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { }

    if (-not $task) {
        $report += [pscustomobject]@{ Task = $name; State = 'absent'; Action = 'skip'; Detail = '任务不存在，无需操作' }
        $skipped++
        continue
    }

    if ([string]$task.State -eq 'Disabled') {
        $report += [pscustomobject]@{ Task = $name; State = 'Disabled'; Action = 'none'; Detail = '已是 Disabled，幂等无需操作' }
        continue
    }

    # 护栏 2：Action 必须指向旧 agent run 残留路径
    $acts = @($task.Actions | ForEach-Object { ('{0} {1}' -f $_.Execute, $_.Arguments) })
    $isLegacy = $true
    foreach ($a in $acts) {
        if ($a -notmatch 'multica_workspaces|multica_workdir') { $isLegacy = $false }
    }
    if (-not $isLegacy -and -not $Force) {
        $report += [pscustomobject]@{
            Task = $name; State = [string]$task.State; Action = 'skip'
            Detail = ('Action 不含 multica_workspaces/multica_workdir（可能是活任务）→ 默认跳过，-Force 才放行。Execute/Args: {0}' -f ($acts -join ' ; '))
        }
        $skipped++
        continue
    }

    if (-not $Apply) {
        $report += [pscustomobject]@{
            Task = $name; State = [string]$task.State; Action = 'would-disable'
            Detail = ('干跑：将禁用并备份 XML（护栏{0}）。Execute/Args: {1}' -f $(if (-not $isLegacy) { ':Force' } else { '' }), ($acts -join ' ; '))
        }
        $changed++
        continue
    }

    # 护栏 3：禁用前导出 XML 备份
    try {
        if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }
        $safe = $name -replace '[\\/:\*\?"<>\|]', '_'
        $xmlPath = Join-Path $backupDir ($safe + '.xml')
        Export-ScheduledTask -TaskName $name | Out-File -LiteralPath $xmlPath -Encoding Unicode -Force
        Disable-ScheduledTask -TaskName $name | Out-Null
        $now = (Get-ScheduledTask -TaskName $name).State
        $ok = ([string]$now -eq 'Disabled')
        $report += [pscustomobject]@{
            Task = $name; State = [string]$now; Action = $(if ($ok) { 'disabled' } else { 'verify-failed' })
            Detail = ('XML 备份 {0}' -f $xmlPath)
        }
        if ($ok) { $changed++ } else { $skipped++ }
        Write-HostLog ('dead-task disable: {0} -> {1} (backup {2})' -f $name, $now, $xmlPath)
    }
    catch {
        $report += [pscustomobject]@{ Task = $name; State = [string]$task.State; Action = 'error'; Detail = $_.Exception.Message }
        $skipped++
        Write-HostLog ('dead-task disable failed: {0} - {1}' -f $name, $_.Exception.Message) -Level Error
    }
}

if ($Json) {
    $report | ConvertTo-Json -Depth 3
}
else {
    Write-Host '==== 计划任务死清单（QUOT-483）===='
    Write-Host ('模式：{0}' -f $(if ($Apply) { '-Apply 真实执行' } else { '干跑（只报告；禁用动作推迟，待 supervisor 验收通过后再启用）' }))
    Write-Host ''
    $report | Format-Table Task, State, Action, Detail -AutoSize -Wrap | Out-String -Width 200 | Write-Host
    Write-Host ('需变更 {0} 条，跳过 {1} 条。' -f $changed, $skipped)
}

if ($Apply -and $changed -gt 0) {
    Send-HostToast -Title '计划任务死清单已更新' -Body ('禁用 {0} 条（XML 备份在 {1}）' -f $changed, $backupDir)
}

if ($Apply -and $skipped -gt 0) { exit 1 }
exit 0
