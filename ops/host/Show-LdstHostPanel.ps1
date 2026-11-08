<#
.SYNOPSIS
    QUOT-483 单图标 launcher 的 UI：模块状态面板 + 关窗清单确认全停。

.DESCRIPTION
    桌面唯一 LadderStock 入口图标指向本脚本（powershell -WindowStyle Hidden -File），
    双击后：
      1. 先跑 Invoke-HostPrecheck（iphlpsvc / WSL IP / portproxy / multica daemon）
      2. 后台拉起全部模块（Start-LdstModules，任务优先 + WMI 隐藏兜底，【不弹黑窗】）
      3. 面板实时显示每个模块的端口与状态；data_dir 错实例标红（验收项）
      4. 【关窗】→ 弹出"将要停止的进程清单"→ 确认后 Stop-LdstModules -All 全停
         （清单来自 Stop-LdstModules.ps1 -DryRun，与真停用的是同一条代码路径）

    托盘图标：双击恢复窗口；右键可"退出面板（不停服务）"。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File .\Show-LdstHostPanel.ps1
#>
[CmdletBinding()]
param(
    # 跳过前置检查（模块已在跑时的快速开面板用）
    [switch]$SkipPrecheck
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:cfg = Import-HostConfig
$script:stopScript = Join-Path $script:HostRoot 'Stop-LdstModules.ps1'
$script:startScript = Join-Path $script:HostRoot 'Start-LdstModules.ps1'
$script:precheckScript = Join-Path $script:HostRoot 'Invoke-HostPrecheck.ps1'
$script:closingConfirmed = $false

# ---------------- 托盘图标先建（启动耗时期间用户也能看到） ----------------
$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = [System.Drawing.SystemIcons]::Shield
$tray.Text = 'LadderStock 主机端'
$tray.Visible = $true

# ---------------- 表单 ----------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'LadderStock 主机端（盯盘控制台）'
$form.Size = New-Object System.Drawing.Size(880, 460)
$form.StartPosition = 'CenterScreen'
$form.MinimumSize = New-Object System.Drawing.Size(640, 320)

$grid = New-Object System.Windows.Forms.DataGridView
$grid.Dock = 'Fill'
$grid.ReadOnly = $true
$grid.AllowUserToAddRows = $false
$grid.AllowUserToDeleteRows = $false
$grid.RowHeadersVisible = $false
$grid.AutoSizeColumnsMode = 'Fill'
$grid.SelectionMode = 'FullRowSelect'
$grid.BackgroundColor = [System.Drawing.Color]::White
$null = $grid.Columns.Add('Name', '模块')
$null = $grid.Columns.Add('TaskState', '计划任务')
$null = $grid.Columns.Add('Port', '端口')
$null = $grid.Columns.Add('Listening', '监听')
$null = $grid.Columns.Add('PortPid', 'PID')
$null = $grid.Columns.Add('Health', '健康')
$null = $grid.Columns.Add('DirFlag', 'data_dir')

$statusBar = New-Object System.Windows.Forms.StatusStrip
$statusLabel = New-Object System.Windows.Forms.ToolStripStatusLabel
$statusLabel.Spring = $true
$statusLabel.Text = '初始化…'
$null = $statusBar.Items.Add($statusLabel)

$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = 'Bottom'
$panel.Height = 40
$panel.Padding = New-Object System.Windows.Forms.Padding(8, 4, 8, 4)

$btnRefresh = New-Object System.Windows.Forms.Button
$btnRefresh.Text = '刷新'
$btnRefresh.Width = 90
$btnRefresh.Left = 8
$btnBoard = New-Object System.Windows.Forms.Button
$btnBoard.Text = '打开看板 5950'
$btnBoard.Width = 120
$btnBoard.Left = $btnRefresh.Right + 10
$btnStop = New-Object System.Windows.Forms.Button
$btnStop.Text = '停止全部…'
$btnStop.Width = 110
$btnStop.Left = $btnBoard.Right + 10
$null = $panel.Controls.AddRange(@($btnRefresh, $btnBoard, $btnStop))

$form.Controls.Add($grid)
$form.Controls.Add($statusBar)
$form.Controls.Add($panel)

function Update-Status([string]$Text) { $statusLabel.Text = $Text }

function Refresh-Grid {
    try {
        $rows = @(Get-ModuleStatus)
        $grid.Rows.Clear()
        foreach ($r in $rows) {
            $i = $grid.Rows.Add($r.Name, $r.TaskState, $(if ($r.Port -gt 0) { [string]$r.Port } else { '-' }),
                $(if ($r.Port -gt 0) { if ($r.Listening) { 'LISTEN' } else { 'down' } } else { '-' }),
                $(if ($r.PortPid -gt 0) { [string]$r.PortPid } else { '-' }),
                $r.Health, $(if ($r.HealthUrl -and $r.DataDirActual) { if ($r.DataDirOk) { 'ok' } else { 'MISMATCH' } } else { '-' }))
            if (-not $r.Healthy) { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::MistyRose }
            elseif ($r.Port -gt 0 -and -not $r.Listening) { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::LightYellow }
            else { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::Honeydew }
        }
        $ok = @($rows | Where-Object { -not $_.Healthy }).Count
        if ($ok -eq 0) { Update-Status ("就绪：{0} 个模块在线 {1}" -f $rows.Count, (Get-Date -Format 'HH:mm:ss')) }
        else { Update-Status ("{0} 个模块异常，最后刷新 {1}" -f $ok, (Get-Date -Format 'HH:mm:ss')) }
    }
    catch {
        Update-Status ("刷新失败：{0}" -f $_.Exception.Message)
    }
}

function Get-StopPlanText {
    param([string[]]$Arguments)
    $json = & $script:stopScript @Arguments 2>$null | Out-String
    try {
        $obj = $json | ConvertFrom-Json
        $lines = @()
        foreach ($p in $obj.plan) {
            $g = ''
            if ($p.Guard) { $g = ('（' + $p.Guard + '）') }
            $lines += ("  - {0,-22} 任务[{1}] 端口 {2} pid {3} {4}" -f $p.Module, $p.Task, $p.Port, $p.ProcId, $g)
        }
        return , $lines
    }
    catch { return @() }
}

$script:startJob = $null

function Start-AllModulesAsync {
    Update-Status '前置检查 → 启动模块（后台进行）…'
    $script:startJob = Start-Job -ScriptBlock {
        param($pre, $start, $skipPre)
        if (-not $skipPre) {
            & $pre 2>&1 | Out-Null
        }
        & $start -Json 2>&1 | Out-Null
    } -ArgumentList $script:precheckScript, $script:startScript, [bool]$SkipPrecheck
    # 轮询 job 完成 → 刷新面板
    $timer2 = New-Object System.Windows.Forms.Timer
    $timer2.Interval = 2000
    $timer2.Add_Tick({
        if ($script:startJob -and $script:startJob.State -in 'Completed', 'Failed', 'Stopped') {
            $timer2.Stop()
            $timer2.Dispose()
            if ($script:startJob.State -eq 'Failed') {
                Update-Status '启动失败，详见 host-start.log'
            }
            else {
                Update-Status '启动流程完成'
            }
            Remove-Job -Job $script:startJob -Force -ErrorAction SilentlyContinue
            $script:startJob = $null
            Refresh-Grid
        }
    })
    $timer2.Start()
}

# ---------------- 事件 ----------------
$btnRefresh.Add_Click({ Refresh-Grid })

$btnBoard.Add_Click({
    try { Start-Process 'http://127.0.0.1:5950' } catch { }
})

$btnStop.Add_Click({
    $lines = @(Get-StopPlanText -Arguments @('-DryRun', '-All', '-Json'))
    if ($lines.Count -eq 0) {
        [void][System.Windows.Forms.MessageBox]::Show('当前没有在运行的模块。', 'LadderStock', 'OK', 'Information')
        return
    }
    $msg = "确认停止以下模块？（本机看板与全部盯盘服务将停止）`n`n" + ($lines -join "`n")
    $r = [System.Windows.Forms.MessageBox]::Show($msg, '停止确认', 'YesNo', 'Warning')
    if ($r -ne 'Yes') { return }
    Update-Status '停止中…'
    $form.Refresh()
    $null = & $script:stopScript -All 2>&1 | Out-String
    Refresh-Grid
})

$form.Add_Shown({
    Refresh-Grid
    Start-AllModulesAsync
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ Refresh-Grid })
$timer.Start()

$tray.Add_DoubleClick({
    $form.Show()
    $form.WindowState = 'Normal'
    $form.Activate()
})

$menuQuit = New-Object System.Windows.Forms.ToolStripMenuItem
$menuQuit.Text = '退出面板（不停服务）'
$menuQuit.Add_Click({
    $script:closingConfirmed = $true
    $tray.Visible = $false
    $form.Close()
})
$trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
$null = $trayMenu.Items.Add($menuQuit)
$tray.ContextMenuStrip = $trayMenu

$form.Add_FormClosing({
    param($s, $e)
    if ($script:closingConfirmed) { return }
    $lines = @(Get-StopPlanText -Arguments @('-DryRun', '-All', '-Json'))
    if ($lines.Count -eq 0) { $script:closingConfirmed = $true; return }
    $msg = "关闭面板将停止以下进程：`n`n" + ($lines -join "`n") + "`n`n确认后全部停止；选择〔否〕只关面板不清服务。"
    $r = [System.Windows.Forms.MessageBox]::Show($msg, 'LadderStock 停止确认', 'YesNo', 'Warning')
    if ($r -eq 'Yes') {
        Update-Status '停止中…'
        $form.Refresh()
        Write-HostLog 'panel close -> stop all'
        $null = & $script:stopScript -All 2>&1 | Out-String
        Send-HostToast -Title 'LadderStock' -Body '全部模块已停止'
        $script:closingConfirmed = $true
    }
    else {
        $script:closingConfirmed = $true   # 用户明确选择只关面板
        $tray.Visible = $false
    }
})

$form.Add_FormClosed({ try { $tray.Visible = $false; $tray.Dispose() } catch { } })

Write-HostLog 'panel opened'
[void]$form.ShowDialog()
Write-HostLog 'panel closed'
