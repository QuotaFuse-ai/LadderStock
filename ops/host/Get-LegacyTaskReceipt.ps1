<#
.SYNOPSIS
    QUOT-523：散落计划任务只读回执（收编清单 + Action 现状）。默认只读，不做任何变更。

.DESCRIPTION
    单入口 supervisor（`ldst supervisor start|stop`，或看板 `startup`/`stop_all`）接管
    board + watchdog + 七个模块之后，旧启停链路的计划任务就成了「散落计划任务依赖」。
    本脚本只回答两件事，供验收与切换窗口评审：

      1. 清单里每条任务的现状：存在与否 / State / Action 指向哪个脚本；
      2. 它属于哪一类：
         legacy-autostart  旧启停链路（start_all.cmd / start_watchdog.cmd / start_board_main.cmd
                           / 旧 quot-200 run-logs / 已 Disabled 的死任务）——收编后应停用；
         stat-monitor      看 Scan（跑统计，不启停进程）——与 5952 看门狗唯一性无关，**不是**启停依赖；
         keep-board-loop   看板 5950 自循环（LadderStock API 等）——单入口接管 5950 之前必须保留；
         peer-peer         QF-Peer-* 单模块对位任务——与单入口同一批入口，切换后由 supervisor 掌舵；
         external          清单外的任务（只列出 LadderStock / TickHub 相关，便于评审）。

    红线：`-Apply` 也**只导出 XML 回执**，不 Disable / 不 Unregister / 不动任何 Action
    （QUOT-483 先例：误禁打死过 8 个 peer 端口）。真正停用旧任务属于生产切换，
    必须用户批准的切换窗口内人工执行；回滚见 Restore-ScheduledTasks.ps1。

.PARAMETER TaskName
    只报告这些任务（可重复）。缺省 = 内置清单 + 名称含 LadderStock/TickHub 的相关任务。

.PARAMETER Apply
    把每条任务的 Export-ScheduledTask XML 落到回执目录（可复现证据），仍不改任务状态。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-LegacyTaskReceipt.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-LegacyTaskReceipt.ps1 -Json
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-LegacyTaskReceipt.ps1 -Apply
#>
[CmdletBinding()]
param(
    [string[]]$TaskName = @(),
    [switch]$Apply,
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# 收编清单：名称 → 分类。分类只影响报告措辞，不影响执行——脚本从不改任务。
$catalog = @{
    'Peer Startup Check'        = 'legacy-autostart'
    'LadderStock Modules'       = 'legacy-autostart'
    'LadderStock Watchdog'      = 'legacy-autostart'
    'LadderStock UI 5950'       = 'legacy-autostart'
    'QF Wire 8790'              = 'legacy-autostart'
    'QF Wire MCP 8791'          = 'legacy-autostart'
    'StockSenate MCP 8765'      = 'legacy-autostart'
    'StockSenate WebUI 8788'    = 'legacy-autostart'
    'TickHub CN Admin 8787'     = 'legacy-autostart'
    'TickHub US 8002'           = 'legacy-autostart'
    'LadderStock Watch Scan'    = 'stat-monitor'
    'LadderStock API'           = 'keep-board-loop'
    'LadderStock Schedule'      = 'keep-board-loop'
    'TickHub CN 8000'           = 'peer-peer'
    'TickHub CN 8000 Watchdog'  = 'peer-peer'
    'QF-Peer-StockSenate-MCP'   = 'peer-peer'
    'QF-Peer-StockSenate-WebUI' = 'peer-peer'
    'QF-Peer-TickHub-Admin'     = 'peer-peer'
    'QF-Peer-TickHub-US'        = 'peer-peer'
    'QF-Peer-Wire-MCP'          = 'peer-peer'
    'QF-Peer-Wire-Web'          = 'peer-peer'
}
# 报告顺序：清单原序在前，名称相关但清单外的任务按发现顺序追加
$order = @($catalog.Keys)

$notes = @{
    'legacy-autostart' = '旧启停链路：收编后应在用户批准的切换窗口内停用（本脚本不动）'
    'stat-monitor'     = '统计任务，不启停进程：不是 5952/8000 的唯一性依赖，可保留'
    'keep-board-loop'  = '看板 5950 自循环：单入口真正接管 5950 之前必须保留'
    'peer-peer'        = '与单入口同一批入口：切换后由 supervisor 掌舵，停用同样需切换窗口'
    'external'         = '清单外但名称相关：只报告，由评审判定'
}

$receiptDir = Join-Path $env:LOCALAPPDATA 'LadderStock\task-receipts'
$all = @(Get-ScheduledTask -ErrorAction SilentlyContinue)

# 名字清单：显式参数优先，否则内置清单 + 名称相关的其它任务
if ($TaskName.Count -gt 0) {
    $names = @($TaskName)
}
else {
    $related = @($all | Where-Object { $_.TaskName -match 'LadderStock|TickHub|QF-Peer|StockSenate|Wire' } |
            ForEach-Object { $_.TaskName })
    $extra = @($related | Where-Object { -not $catalog.ContainsKey($_) } | Select-Object -Unique)
    $names = @($order + $extra)
}

$report = @()
foreach ($name in $names) {
    $task = @($all | Where-Object { $_.TaskName -eq $name }) | Select-Object -First 1
    $kind = if ($catalog.ContainsKey($name)) { $catalog[$name] } else { 'external' }
    if (-not $task) {
        $report += [pscustomobject]@{
            Task = $name; Kind = $kind; State = 'absent'; Action = '(无)'
            Note = '任务不存在（已删或从未创建）'
        }
        continue
    }
    $acts = @($task.Actions | ForEach-Object { ('{0} {1}' -f $_.Execute, $_.Arguments).Trim() }) -join ' ; '
    $detail = $notes[$kind]
    if ($acts -match 'quot-200') { $detail += '；Action 指向 quot-200 旧 run 目录' }
    if ($Apply) {
        try {
            if (-not (Test-Path $receiptDir)) { New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null }
            $safe = $name -replace '[\\/:\*\?"<>\|]', '_'
            $xmlPath = Join-Path $receiptDir ($safe + '.xml')
            Export-ScheduledTask -TaskName $name | Out-File -LiteralPath $xmlPath -Encoding Unicode -Force
            $detail += '；XML 回执 ' + $xmlPath
        }
        catch {
            $detail += '；XML 回执失败：' + $_.Exception.Message
        }
    }
    $report += [pscustomobject]@{
        Task = $name; Kind = $kind; State = [string]$task.State; Action = $acts; Note = $detail
    }
}

if ($Json) {
    $report | ConvertTo-Json -Depth 3
    exit 0
}

Write-Host '==== 散落计划任务回执（QUOT-523，只读）===='
Write-Host ('模式：{0}' -f $(if ($Apply) { '-Apply 导出 XML 回执（仍不改任务状态）' } else { '只读（不导出、不变更）' }))
Write-Host ''
$report | Format-Table Task, Kind, State, Action, Note -AutoSize -Wrap | Out-String -Width 220 | Write-Host
$legacy = @($report | Where-Object { $_.Kind -eq 'legacy-autostart' -and $_.State -ne 'Disabled' -and $_.State -ne 'absent' })
Write-Host ('旧启停链路仍启用 {0} 条；停用需用户批准的切换窗口（回滚：Restore-ScheduledTasks.ps1）。' -f $legacy.Count)
exit 0
