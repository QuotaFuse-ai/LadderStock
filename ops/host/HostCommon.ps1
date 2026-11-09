<#
.SYNOPSIS
    QUOT-483 宿主侧 supervisor 共享工具（dot-source，不独立执行）。

.DESCRIPTION
    被 Invoke-HostPrecheck.ps1 / Start-LdstModules.ps1 / Stop-LdstModules.ps1 /
    Show-LdstHostPanel.ps1 及各 adapters 以 . "$PSScriptRoot\HostCommon.ps1" 引入。

    约定：
      * 全部函数兼容 Windows PowerShell 5.1（不用 ??、?.、三元）
      * 日志统一落 %LOCALAPPDATA%\LadderStock\logs\host-*.log（UTF-8，轮转 10MB x 3）
      * Toast（D5）= 系统托盘气泡通知，msg.exe 优先、NotifyIcon 气泡降级，绝不抛异常
      * ledger（JSONL）记录"本次谁拉起了什么"，Stop 只停 ledger 中自己拉起的
#>
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:HostRoot = $PSScriptRoot
$script:HostConfigPath = Join-Path $script:HostRoot 'LdstHostConfig.psd1'
$script:HostLogDir = Join-Path $env:LOCALAPPDATA 'LadderStock\logs'

function Import-HostConfig {
    <#
    .SYNOPSIS 读 LdstHostConfig.psd1 并补上随环境变化的派生字段。
    #>
    [CmdletBinding()]
    param()
    $cfg = Import-PowerShellDataFile -Path $script:HostConfigPath
    $cfg.LogDir = $script:HostLogDir
    $cfg.LedgerFile = Join-Path $script:HostLogDir 'host-launcher.ledger.jsonl'
    if (-not (Test-Path $cfg.LogDir)) { New-Item -ItemType Directory -Path $cfg.LogDir -Force | Out-Null }
    return $cfg
}

function Write-HostLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('Info', 'Warn', 'Error')][string]$Level = 'Info',
        [string]$LogName = 'host'
    )
    $file = Join-Path $script:HostLogDir ("host-{0}.log" -f $LogName)
    if (-not (Test-Path $script:HostLogDir)) { New-Item -ItemType Directory -Path $script:HostLogDir -Force | Out-Null }
    $line = '[{0}] [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -LiteralPath $file -Value $line -Encoding UTF8
}

function Test-PortListening {
    param([Parameter(Mandatory = $true)][int]$Port)
    if ($Port -le 0) { return $false }
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$conns
}

function Get-PortProcessId {
    param([Parameter(Mandatory = $true)][int]$Port)
    if ($Port -le 0) { return 0 }
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess } else { return 0 }
}

function Get-WslDefaultDistro {
    <#
    .SYNOPSIS 探测 WSL 默认发行版名（不写死 Ubuntu）。不同/无 WSL 返回空串。
    #>
    [CmdletBinding()]
    param()
    $raw = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        $raw = & wsl.exe --list --quiet 2>$null
    }
    if ($($LASTEXITCODE) -ne 0 -or -not $raw) { return '' }
    foreach ($line in ($raw -split "`r?`n")) {
        $name = $line.Trim()
        if (-not $name) { continue }
        if ($name -match '^\(Default\)\s+(.+)$') { return $Matches[1].Trim() }
    }
    # 没有 (Default) 标记时取第一行（wsl -l -q 首行即默认）
    foreach ($line in ($raw -split "`r?`n")) {
        $name = ($line -replace '\x00', '').Trim()
        if ($name) { return $name }
    }
    return ''
}

function Send-HostToast {
    <#
    .SYNOPSIS D5 桌面通知：气泡 Toast。msg.exe 优先，NotifyIcon 降级。永不抛异常。
    .PARAMETER Body 正文（可空）。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [string]$Body = '',
        [int]$TimeoutSeconds = 60
    )
    try {
        $msgOk = $false
        try {
            $p = Start-Process -FilePath 'msg.exe' -ArgumentList '*', ("$Title`n$Body") -WindowStyle Hidden -PassThru -ErrorAction Stop
            if (-not $p.WaitForExit(5000)) { try { $p.Kill() } catch { } }
            $msgOk = ($p.ExitCode -eq 0)
        }
        catch { $msgOk = $false }
        if ($msgOk) { return $true }

        # msg.exe 失败（多半是无交互会话）→ 托盘气泡降级，放在独立 Job 里避免阻塞调用方
        $null = Start-Job -ScriptBlock {
            param($t, $b)
            try {
                Add-Type -AssemblyName System.Windows.Forms
                $icon = New-Object System.Windows.Forms.NotifyIcon
                $icon.Icon = [System.Drawing.SystemIcons]::Information
                $icon.Visible = $true
                $icon.ShowBalloonTip(8000, $t, $b, [System.Windows.Forms.ToolTipIcon]::Info)
                Start-Sleep -Seconds 9
                $icon.Dispose()
            }
            catch { }
        } -ArgumentList $Title, $Body
        return $true
    }
    catch {
        return $false
    }
}

function Invoke-HostLogRollover {
    <#
    .SYNOPSIS D5 日志上限：超过 LogMaxBytes 的日志滚动为 .1/.2/.3，保留 LogKeep 份。
    #>
    [CmdletBinding()]
    param(
        [string]$LogDir = $script:HostLogDir,
        [int]$MaxBytes = 10485760,
        [int]$Keep = 3
    )
    if (-not (Test-Path $LogDir)) { return }
    foreach ($f in (Get-ChildItem -LiteralPath $LogDir -Filter '*.log' -File -ErrorAction SilentlyContinue)) {
        try {
            if ($f.Length -lt $MaxBytes) { continue }
            $base = $f.FullName
            for ($i = $Keep - 1; $i -ge 1; $i--) {
                $src = "$base.$i"
                if (Test-Path -LiteralPath $src) {
                    if ($i -eq $Keep - 1) { Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue }
                    else { Move-Item -LiteralPath $src -Destination "$base.$($i + 1)" -Force -ErrorAction SilentlyContinue }
                }
            }
            Move-Item -LiteralPath $base -Destination "$base.1" -Force -ErrorAction SilentlyContinue
            Write-HostLog ("rollover: {0} ({1} bytes)" -f $f.Name, $f.Length)
        }
        catch {
            Write-HostLog ("rollover failed: {0} - {1}" -f $f.Name, $_.Exception.Message) -Level Warn
        }
    }
}

function Write-LedgerEntry {
    <#
    .SYNOPSIS 追加一条启动记账：谁、用什么方式、拉起了哪个模块。
    .PARAMETER Pid WMI 兜底拉起时的进程 ID（任务通道为 0）。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Method,   # task | wmi
        [int]$ProcId = 0,
        [string]$Note = ''
    )
    $cfg = Import-HostConfig
    $entry = [ordered]@{
        ts     = (Get-Date).ToString('s')
        module = $Module
        method = $Method
        procId = $ProcId
        note   = $Note
    } | ConvertTo-Json -Compress
    Add-Content -LiteralPath $cfg.LedgerFile -Value $entry -Encoding UTF8
}

function Read-LedgerEntries {
    [CmdletBinding()]
    param()
    $cfg = Import-HostConfig
    if (-not (Test-Path $cfg.LedgerFile)) { return @() }
    $out = @()
    foreach ($line in (Get-Content -LiteralPath $cfg.LedgerFile)) {
        $t = $line.Trim()
        if (-not $t) { continue }
        try { $out += ($t | ConvertFrom-Json) } catch { }
    }
    return $out
}

function Get-ModuleStatus {
    <#
    .SYNOPSIS 汇总每个模块：任务状态 / 端口监听 / 健康探针 / data_dir 校验。
    返回 pscustomobject 数组（面板与 status 查询共用）。
    #>
    [CmdletBinding()]
    param()
    $cfg = Import-HostConfig
    $out = @()

    # 批量预取：逐模块调 Get-NetTCPConnection 单次约 2s，12 个模块 ×2 次会到 45s+，面板因此卡死。
    $listenMap = @{}
    try {
        foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction Stop)) {
            $p = [int]$c.LocalPort
            if (-not $listenMap.ContainsKey($p)) { $listenMap[$p] = [int]$c.OwningProcess }
        }
    }
    catch { }

    $taskMap = @{}
    try {
        foreach ($t in @(Get-ScheduledTask -ErrorAction Stop)) { $taskMap[[string]$t.TaskName] = [string]$t.State }
    }
    catch { }

    foreach ($m in $cfg.Modules) {
        $taskState = 'absent'
        if ($taskMap.ContainsKey([string]$m.Task)) { $taskState = $taskMap[[string]$m.Task] }
        elseif (-not $taskMap.Count) {
            try {
                $t = Get-ScheduledTask -TaskName $m.Task -ErrorAction Stop
                $taskState = [string]$t.State
            }
            catch { $taskState = 'absent' }
        }

        $listening = $false
        $portPid = 0
        if ($m.Port -gt 0 -and $listenMap.ContainsKey([int]$m.Port)) {
            $listening = $true
            $portPid = [int]$listenMap[[int]$m.Port]
        }
        elseif ($m.Port -gt 0) {
            $listening = Test-PortListening -Port ([int]$m.Port)
            if ($listening) { $portPid = Get-PortProcessId -Port ([int]$m.Port) }
        }

        $health = 'n/a'
        $healthy = $true
        $dataDirOk = $true
        $dataDirActual = ''
        $healthUrl = ''
        if ($m -is [System.Collections.IDictionary]) {
            if ($m.Contains('HealthUrl') -and $m['HealthUrl']) { $healthUrl = [string]$m['HealthUrl'] }
        }
        elseif ($m.PSObject.Properties['HealthUrl'] -and $m.HealthUrl) { $healthUrl = [string]$m.HealthUrl }
        if ($healthUrl -and $m.Port -gt 0 -and -not $listening) {
            # 端口都没在听就别再打探针了：每个探针最多等 5 秒，12 个模块叠起来能把面板拖到十几秒
            $health = 'fail'
            $healthy = $false
        }
        elseif ($healthUrl) {
            try {
                $r = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
                $health = 'ok'
                $actual = $null
                if ($r -and $r.PSObject.Properties['data_dir']) {
                    $actual = [string]$r.data_dir
                }
                elseif ($r -and $r.PSObject.Properties['data'] -and $r.data -and $r.data.PSObject.Properties['data_dir']) {
                    $actual = [string]$r.data.data_dir
                }
                if ($m.DataDir) {
                    if ($actual) {
                        $dataDirActual = $actual
                        if ($actual -ne $m.DataDir) {
                            $healthy = $false
                            $dataDirOk = $false
                        }
                    }
                    else {
                        # 配置要求校验 data_dir 但健康响应不回报——无法证明是部署树实例，判错
                        $healthy = $false
                        $dataDirOk = $false
                    }
                }
            }
            catch {
                $health = 'fail'
                $healthy = $false
            }
        }
        elseif ($m.Port -gt 0) {
            $healthy = $listening
        }
        else {
            $healthy = ($taskState -eq 'Running')
        }

        $out += [pscustomobject][ordered]@{
            Name         = $m.Name
            Task         = $m.Task
            TaskState    = $taskState
            Port         = $m.Port
            Listening    = $listening
            PortPid      = $portPid
            HealthUrl    = $healthUrl
            Health       = $health
            Healthy      = $healthy
            DataDirOk    = $dataDirOk
            DataDirActual = $dataDirActual
        }
    }
    return $out
}

function Test-IsAdministrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
