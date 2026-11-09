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

# ---------------- 未捕获异常兜底：写日志、不让 .NET 异常框打断面板 ----------------
$script:panelErrorLog = Join-Path (Split-Path -Parent $script:HostRoot) 'launcher\panel-error.log'
function Write-PanelError([string]$Where, $Exception) {
    try {
        $line = ('[{0}] {1} :: {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Where, ($Exception | Out-String))
        $dir = Split-Path -Parent $script:panelErrorLog
        if (-not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
        [IO.File]::AppendAllText($script:panelErrorLog, $line + "`r`n", (New-Object Text.UTF8Encoding($false)))
    }
    catch { }
}
[System.Windows.Forms.Application]::SetUnhandledExceptionMode([System.Windows.Forms.UnhandledExceptionMode]::CatchException)
$script:uiExceptionHandler = [System.Threading.ThreadExceptionEventHandler]{
    param($sender, $e)
    Write-PanelError 'UI-thread' $e.Exception
    try { Update-Status ('操作出错（已记录日志）：' + $e.Exception.Message) } catch { }
}
[System.Windows.Forms.Application]::add_ThreadException($script:uiExceptionHandler)

$script:cfg = Import-HostConfig
$script:stopScript = Join-Path $script:HostRoot 'Stop-LdstModules.ps1'
$script:startScript = Join-Path $script:HostRoot 'Start-LdstModules.ps1'
$script:precheckScript = Join-Path $script:HostRoot 'Invoke-HostPrecheck.ps1'
$script:launcherDir = Join-Path (Split-Path -Parent $script:HostRoot) 'launcher\scripts'
$script:tickHubPeer = 'C:\Users\Administrator\.tickhub\start_peer.cmd'
$script:tickHubRepo = 'D:\workplace\TickHub-CN'
$script:ldstExe = 'D:\workplace\LadderStock\.venv\Scripts\ldst.exe'
$script:ldstRepo = 'D:\workplace\LadderStock'
$script:ldstLogDir = 'C:\Users\Administrator\AppData\Local\LadderStock\logs'
$script:closingConfirmed = $false

# ---------------- 单实例保护（按窗口判定，不用 Mutex） ----------------
# 旧版用 Mutex 记名互斥：一旦上次进程没窗口（异常残留/被隐藏），新双击会「闪一下就退出」。
# 另外 FindWindow 在本机对中文标题不生效，改用 EnumWindows 遍历标题匹配。
Add-Type -Namespace Ldst -Name Win -MemberDefinition @'
public delegate bool EnumWinProc(System.IntPtr h, System.IntPtr l);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool EnumWindows(EnumWinProc cb, System.IntPtr l);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(System.IntPtr h, System.Text.StringBuilder s, int n);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr hWnd);
'@
$script:existingHwnd = [System.IntPtr]::Zero
try {
    $cb = [Ldst.Win+EnumWinProc]{
        param($h, $l)
        $sb = New-Object System.Text.StringBuilder 512
        [void][Ldst.Win]::GetWindowText($h, $sb, 512)
        if ($sb.ToString() -like '*LadderStock 主机端*') { $script:existingHwnd = $h; return $false }
        return $true
    }
    [void][Ldst.Win]::EnumWindows($cb, [System.IntPtr]::Zero)
}
catch { }
if ($script:existingHwnd -ne [System.IntPtr]::Zero) {
    try {
        [void][Ldst.Win]::ShowWindow($script:existingHwnd, 9)   # SW_RESTORE
        [void][Ldst.Win]::SetForegroundWindow($script:existingHwnd)
    }
    catch { }
    exit
}

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
$panel.Height = 78
$panel.Padding = New-Object System.Windows.Forms.Padding(8, 4, 8, 4)

function New-PanelButton([string]$Text, [int]$Left, [int]$Top, [int]$Width) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Text
    $b.Width = $Width
    $b.Height = 30
    $b.Left = $Left
    $b.Top = $Top
    return $b
}

# 第一行：状态 / 看板 / TickHub / LadderStock
$btnRefresh = New-PanelButton '刷新' 8 6 88
$btnBoard = New-PanelButton '打开看板 5950' 104 6 120
$btnStartTick = New-PanelButton '启动 TickHub' 232 6 110
$btnStopTick = New-PanelButton '停止 TickHub' 350 6 110
$btnStartLdst = New-PanelButton '启动 LadderStock' 468 6 120
$btnStopLdst = New-PanelButton '停止 LadderStock' 596 6 120

# 第二行：环境 / 日志 / 全停
$btnRestore = New-PanelButton '一键恢复环境' 8 40 110
$btnOpenLog = New-PanelButton '打开恢复日志' 126 40 110
$btnStop = New-PanelButton '停止全部…' 244 40 110

$null = $panel.Controls.AddRange(@($btnRefresh, $btnBoard, $btnStartTick, $btnStopTick, $btnStartLdst, $btnStopLdst, $btnRestore, $btnOpenLog, $btnStop))

$form.Controls.Add($grid)
$form.Controls.Add($statusBar)
$form.Controls.Add($panel)

function Update-Status([string]$Text) { $statusLabel.Text = $Text }

function Render-Grid($rows) {
    $grid.Rows.Clear()
    foreach ($r in $rows) {
        $hasHealthUrl = [bool]($r.PSObject.Properties['HealthUrl'] -and $r.HealthUrl)
        $i = $grid.Rows.Add($r.Name, $r.TaskState, $(if ($r.Port -gt 0) { [string]$r.Port } else { '-' }),
            $(if ($r.Port -gt 0) { if ($r.Listening) { 'LISTEN' } else { 'down' } } else { '-' }),
            $(if ($r.PortPid -gt 0) { [string]$r.PortPid } else { '-' }),
            $r.Health, $(if ($hasHealthUrl -and $r.DataDirActual) { if ($r.DataDirOk) { 'ok' } else { 'MISMATCH' } } else { '-' }))
        if (-not $r.Healthy) { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::MistyRose }
        elseif ($r.Port -gt 0 -and -not $r.Listening) { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::LightYellow }
        else { $grid.Rows[$i].DefaultCellStyle.BackColor = [System.Drawing.Color]::Honeydew }
    }
    $bad = @($rows | Where-Object { -not $_.Healthy }).Count
    if ($bad -eq 0) { Update-Status ("就绪：{0} 个模块在线 {1}" -f $rows.Count, (Get-Date -Format 'HH:mm:ss')) }
    else { Update-Status ("{0} 个模块异常，最后刷新 {1}" -f $bad, (Get-Date -Format 'HH:mm:ss')) }
}

# ---- 异步刷新：Get-ModuleStatus 要几秒，同步跑会整窗卡住 ----
$script:refreshPS = $null
$script:refreshHandle = $null
$script:refreshTimer = $null

function Complete-Refresh {
    if (-not $script:refreshHandle -or -not $script:refreshHandle.IsCompleted) { return }
    try {
        $raw = $script:refreshPS.EndInvoke($script:refreshHandle)
        $text = ($raw | Out-String).Trim()
        if ($text) {
            # PS5.1 的 ConvertFrom-Json 对数组返回单个 Object[]，再 @() 包一层会只渲染一行
            $parsed = $text | ConvertFrom-Json
            $rows = if ($parsed -is [array]) { $parsed } else { @($parsed) }
            Render-Grid $rows
        }
    }
    catch {
        Update-Status ("刷新失败：{0}" -f $_.Exception.Message)
    }
    finally {
        try { $script:refreshPS.Dispose() } catch { }
        $script:refreshPS = $null
        $script:refreshHandle = $null
    }
}

function Start-Refresh {
    if ($script:refreshHandle -and -not $script:refreshHandle.IsCompleted) { return }   # 上一轮没跑完就跳过
    if ($script:refreshPS) { try { $script:refreshPS.Dispose() } catch { } }
    $script:refreshPS = [powershell]::Create()
    $null = $script:refreshPS.AddScript({
        param($hostCommon)
        . $hostCommon
        Set-StrictMode -Off
        @(Get-ModuleStatus) | ConvertTo-Json -Depth 5 -Compress
    }).AddArgument((Join-Path $script:HostRoot 'HostCommon.ps1'))
    $script:refreshHandle = $script:refreshPS.BeginInvoke()
    if (-not $script:refreshTimer) {
        $script:refreshTimer = New-Object System.Windows.Forms.Timer
        $script:refreshTimer.Interval = 800
        $script:refreshTimer.Add_Tick({ Complete-Refresh })
        $script:refreshTimer.Start()
    }
}

# 停止清单不能在关闭时现算：Stop-LdstModules -DryRun -All -Json 实测要 30 秒，
# 放在 FormClosing 里同步跑，就是「点关闭后窗口僵住几十秒」的根因。
# 改成后台 runspace 周期预算，关闭时只读缓存。
$script:stopPlanCache = $null
$script:stopPlanPS = $null
$script:stopPlanHandle = $null

function Start-StopPlanRefresh {
    if (-not (Test-Path -LiteralPath $script:stopScript)) { return }
    if ($script:stopPlanHandle -and -not $script:stopPlanHandle.IsCompleted) { return }
    if ($script:stopPlanPS) { try { $script:stopPlanPS.Dispose() } catch { } }
    $script:stopPlanPS = [powershell]::Create()
    $null = $script:stopPlanPS.AddScript({
        param($scriptPath)
        Set-StrictMode -Off
        try { (& $scriptPath -DryRun -All -Json 2>$null | Out-String) } catch { '' }
    }).AddArgument($script:stopScript)
    $script:stopPlanHandle = $script:stopPlanPS.BeginInvoke()
}

function Update-StopPlanCache {
    if (-not $script:stopPlanHandle -or -not $script:stopPlanHandle.IsCompleted) { return }
    try {
        $raw = ($script:stopPlanPS.EndInvoke($script:stopPlanHandle) | Out-String).Trim()
        if ($raw) {
            $obj = $raw | ConvertFrom-Json
            $lines = @()
            foreach ($p in $obj.plan) {
                $g = ''
                if ($p.Guard) { $g = ('（' + $p.Guard + '）') }
                $lines += ("  - {0,-22} 任务[{1}] 端口 {2} pid {3} {4}" -f $p.Module, $p.Task, $p.Port, $p.ProcId, $g)
            }
            if ($lines.Count -gt 0) {
                $script:stopPlanCache = $lines
                Write-HostLog ("stop plan cached: {0} 项" -f $lines.Count)
            }
        }
    }
    catch { }
    finally {
        try { $script:stopPlanPS.Dispose() } catch { }
        $script:stopPlanPS = $null
        $script:stopPlanHandle = $null
    }
}

function Get-StopPlanText {
    param([string[]]$Arguments)
    if ($Arguments -contains '-All' -and $script:stopPlanCache) { return , @($script:stopPlanCache) }
    return @()
}

$script:startJob = $null
$script:startTimer = $null

function Start-AllModulesAsync {
    Update-Status '前置检查 → 启动模块（后台进行）…'
    $script:startJob = Start-Job -ScriptBlock {
        param($pre, $start, $skipPre)
        # 面板关闭时会把「停止全部」甩到独立进程跑；这时立刻重开面板再启动模块，
        # 会和还没跑完的停止过程互相打架（日志里那些 port still listening 就是这么来的）。
        # 所以先等停止进程收工，最多等 90 秒。
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline) {
            $stopping = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
                          Where-Object { $_.CommandLine -like '*Stop-LdstModules*' })
            if ($stopping.Count -eq 0) { break }
            Start-Sleep -Seconds 5
        }
        if (-not $skipPre) {
            & $pre 2>&1 | Out-Null
        }
        & $start -Json 2>&1 | Out-Null
    } -ArgumentList $script:precheckScript, $script:startScript, [bool]$SkipPrecheck
    # 轮询 job 完成 → 刷新面板（计时器必须放 script 作用域，否则 Tick 回调里取不到）
    if ($script:startTimer) { try { $script:startTimer.Stop(); $script:startTimer.Dispose() } catch { } }
    $script:startTimer = New-Object System.Windows.Forms.Timer
    $script:startTimer.Interval = 2000
    $script:startTimer.Add_Tick({
        if ($script:startJob -and $script:startJob.State -in 'Completed', 'Failed', 'Stopped') {
            $script:startTimer.Stop()
            $script:startTimer.Dispose()
            $script:startTimer = $null
            if ($script:startJob.State -eq 'Failed') {
                Update-Status '启动失败，详见 host-start.log'
            }
            else {
                Update-Status '启动流程完成'
            }
            Remove-Job -Job $script:startJob -Force -ErrorAction SilentlyContinue
            $script:startJob = $null
            Start-Refresh
        }
    })
    $script:startTimer.Start()
}

# ---------------- 事件 ----------------
$btnRefresh.Add_Click({ Start-Refresh })

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
    # 同样不能同步跑：停止要 30 秒，会让窗口僵住。甩到独立进程，面板继续可用。
    Update-Status '已在后台停止，稍后自动刷新…'
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $script:stopScript, '-All') -WindowStyle Hidden | Out-Null
        Send-HostToast -Title 'LadderStock' -Body '已在后台开始停止全部模块'
    }
    catch { }
    Start-Sleep -Seconds 2
    Start-Refresh
})

# ---------------- 单窗口内的启动/停止/恢复（原桌面脚本已收进本面板） ----------------
function Test-PortBusy([int]$Port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$c
}

function Stop-ByPort([int]$Port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    foreach ($x in @($c)) {
        try { Stop-Process -Id $x.OwningProcess -Force -ErrorAction Stop; Write-HostLog ("kill pid " + $x.OwningProcess + " on port " + $Port) } catch { }
    }
}

function Stop-ByWindowTitle([string[]]$Titles) {
    foreach ($t in $Titles) {
        $null = & taskkill.exe /FI ("WINDOWTITLE eq " + $t) /F 2>&1
    }
}

$btnStartTick.Add_Click({
    if (Test-PortBusy 8000) { Update-Status 'TickHub 8000 已在监听，无需重复启动'; return }
    if (-not (Test-Path -LiteralPath $script:tickHubPeer)) {
        [void][System.Windows.Forms.MessageBox]::Show(('找不到 ' + $script:tickHubPeer), 'TickHub', 'OK', 'Warning'); return
    }
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('call "' + $script:tickHubPeer + '"')) -WorkingDirectory $script:tickHubRepo -WindowStyle Hidden
    Write-HostLog 'panel: start tickhub'
    Update-Status 'TickHub 启动中（约 10 秒后自动刷新）…'
    Start-Refresh
})

$btnStopTick.Add_Click({
    if (-not (Test-PortBusy 8000)) { Update-Status 'TickHub 8000 本来就没在跑'; return }
    $r = [System.Windows.Forms.MessageBox]::Show('确认停止 TickHub MCP peer（端口 8000）？', '停止 TickHub', 'YesNo', 'Warning')
    if ($r -ne 'Yes') { return }
    Update-Status '停止 TickHub 中…'
    $form.Refresh()
    Stop-ByWindowTitle @('TickHub-MCP-8000*')
    Stop-ByPort 8000
    Write-HostLog 'panel: stop tickhub'
    Update-Status 'TickHub 已停止'
    Start-Refresh
})

$btnStartLdst.Add_Click({
    if (Test-PortBusy 5950) { Update-Status 'LadderStock 5950 已在监听，无需重复启动'; return }
    if (-not (Test-Path -LiteralPath $script:ldstExe)) {
        [void][System.Windows.Forms.MessageBox]::Show(('找不到 ldst.exe：' + $script:ldstExe), 'LadderStock', 'OK', 'Warning'); return
    }
    if (-not (Test-Path -LiteralPath $script:ldstLogDir)) { $null = New-Item -ItemType Directory -Path $script:ldstLogDir -Force }
    $schedLog = Join-Path $script:ldstLogDir 'schedule.log'
    $webLog = Join-Path $script:ldstLogDir 'web-5950.log'
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('"' + $script:ldstExe + '" schedule run >> "' + $schedLog + '" 2>&1')) -WorkingDirectory $script:ldstRepo -WindowStyle Hidden
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('"' + $script:ldstExe + '" run --port 5950 --host 127.0.0.1 >> "' + $webLog + '" 2>&1')) -WorkingDirectory $script:ldstRepo -WindowStyle Hidden
    Write-HostLog 'panel: start ladderstock'
    Update-Status 'LadderStock 启动中（看板 5950 + 调度器）…'
    Start-Refresh
})

$btnStopLdst.Add_Click({
    if (-not (Test-PortBusy 5950)) { Update-Status 'LadderStock 5950 本来就没在跑'; return }
    $r = [System.Windows.Forms.MessageBox]::Show('确认停止 LadderStock 看板（5950）与调度器？', '停止 LadderStock', 'YesNo', 'Warning')
    if ($r -ne 'Yes') { return }
    Update-Status '停止 LadderStock 中…'
    $form.Refresh()
    Stop-ByWindowTitle @('LadderStock-Web*', 'LadderStock-Schedule*')
    Stop-ByPort 5950
    Write-HostLog 'panel: stop ladderstock'
    Update-Status 'LadderStock 已停止'
    Start-Refresh
})

$btnRestore.Add_Click({
    $ps1 = Join-Path $script:launcherDir '盯盘一键恢复.ps1'
    if (-not (Test-Path -LiteralPath $ps1)) {
        [void][System.Windows.Forms.MessageBox]::Show(('找不到恢复脚本：' + $ps1), '一键恢复', 'OK', 'Warning'); return
    }
    $r = [System.Windows.Forms.MessageBox]::Show('一键恢复会重建 portproxy、拉起 Multica daemon / 5950 / 8770+5940，需要管理员权限。继续？', '一键恢复', 'YesNo', 'Question')
    if ($r -ne 'Yes') { return }
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -WorkingDirectory $script:launcherDir -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $ps1 + '"'))
        Write-HostLog 'panel: run restore'
        Update-Status '恢复脚本已以管理员身份启动，日志见 盯盘恢复-日志.txt'
    }
    catch { Update-Status ('恢复脚本启动失败：' + $_.Exception.Message) }
})

$btnOpenLog.Add_Click({
    $log = Join-Path $script:launcherDir '盯盘恢复-日志.txt'
    if (-not (Test-Path -LiteralPath $log)) {
        [void][System.Windows.Forms.MessageBox]::Show(('日志还没生成：' + $log), '恢复日志', 'OK', 'Information'); return
    }
    try { Start-Process 'notepad.exe' -ArgumentList ('"' + $log + '"') } catch { }
})

$form.Add_Shown({
    Start-Refresh
    Start-AllModulesAsync
    Start-StopPlanRefresh
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 15000   # 一轮采集约 6 秒，5 秒一次等于连轴转；15 秒既够实时也不占 CPU
$timer.Add_Tick({ Start-Refresh })
$timer.Start()

# 停止清单的后台轮询：每 2 秒收一次结果，攒好缓存关闭时就不用现算
$planTimer = New-Object System.Windows.Forms.Timer
$planTimer.Interval = 2000
$planTimer.Add_Tick({
    Update-StopPlanCache
    if (-not $script:stopPlanCache) { Start-StopPlanRefresh }
})
$planTimer.Start()

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
    $lines = @(Get-StopPlanText -Arguments @('-All'))
    if ($lines.Count -eq 0) {
        # 缓存还没攒出来（刚开面板就关）：不等 30 秒现算，直接问要不要停
        $msg = "停止清单还在后台统计。要现在停止全部模块吗？`n`n选〔是〕立即在后台停止并关闭面板；选〔否〕只关面板，服务保持运行。"
    }
    else {
        $msg = "关闭面板将停止以下进程：`n`n" + ($lines -join "`n") + "`n`n确认后全部停止；选择〔否〕只关面板不清服务。"
    }
    $r = [System.Windows.Forms.MessageBox]::Show($msg, 'LadderStock 停止确认', 'YesNo', 'Warning')
    if ($r -eq 'Yes') {
        # 停止动作交给独立进程跑，面板立刻可以关掉，不再让窗口僵在停止过程里
        Write-HostLog 'panel close -> stop all (detached)'
        try {
            Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $script:stopScript, '-All') -WindowStyle Hidden | Out-Null
            Send-HostToast -Title 'LadderStock' -Body '已在后台开始停止全部模块'
        }
        catch { }
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
