<#
.SYNOPSIS
    QUOT-483 宿主侧启动器：按依赖顺序拉起全部模块（单图标 launcher 的核心）。

.DESCRIPTION
    规则：
      * 幂等——端口已在 LISTEN 的模块跳过（外部已运行的绝不被重启）
      * 计划任务优先——任务存在且非 Disabled 时用 Start-ScheduledTask（由既有
        watchdog 托管，无窗口、有日志）；任务不可用才走 WMI 隐藏兜底
      * WMI 兜底经 powershell -WindowStyle Hidden 包装，【不弹出任何控制台窗口】
        （Win32_Process.Create 的子进程不在调用方 job object，不会随本次调用被回收）
      * data_dir 校验（QUOT-483 验收项）：LadderStock API 已在跑但 data_dir 不是
        D:\QuotaFuseModules\LadderStock\data 时，判错实例、拒绝接管、不杀进程
      * 只把"本次真正拉起的模块"写进 ledger（Stop-LdstModules 只停 ledger 内的）

    退出码：0 = 全部就绪；1 = 至少一个模块没起来

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-LdstModules.ps1 -Json
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-LdstModules.ps1 -Only LadderStock API -WaitSeconds 90
#>
[CmdletBinding()]
param(
    # 只拉起指定模块（可多个，模块名见 LdstHostConfig.psd1 的 Name）
    [string[]]$Only = @(),

    # 跳过指定模块
    [string[]]$Skip = @(),

    # 每个模块最长等待就绪秒数（默认 60）
    [int]$WaitSeconds = 60,

    # JSON 输出（供 QUOT-482 supervisor 解析）
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$cfg = Import-HostConfig
$results = New-Object System.Collections.ArrayList
$bad = 0

function Wait-ModuleUp {
    param([int]$Port, [int]$Seconds)
    if ($Port -le 0) { Start-Sleep -Seconds 2; return $true }
    $w = 0
    while ($w -lt $Seconds) {
        if (Test-PortListening -Port $Port) { return $true }
        Start-Sleep -Seconds 2
        $w += 2
    }
    return (Test-PortListening -Port $Port)
}

foreach ($name in $cfg.StartOrder) {
    if ($Only.Count -gt 0 -and ($Only -notcontains $name)) { continue }
    if ($Skip.Count -gt 0 -and ($Skip -contains $name)) { continue }
    $m = $cfg.Modules | Where-Object { $_.Name -eq $name }
    if (-not $m) { continue }

    # data_dir 校验（验收项）：已在跑的 LadderStock API 必须是部署树实例
    if ($m.DataDir -and $m.HealthUrl -and (Test-PortListening -Port $m.Port)) {
        $actual = ''
        try {
            $r = Invoke-RestMethod -Uri $m.HealthUrl -TimeoutSec 5 -ErrorAction Stop
            if ($r -and $r.PSObject.Properties['data_dir']) {
                $actual = [string]$r.data_dir
            }
            elseif ($r -and $r.PSObject.Properties['data'] -and $r.data -and $r.data.PSObject.Properties['data_dir']) {
                $actual = [string]$r.data.data_dir
            }
        }
        catch { }
        $wrongInstance = ($actual -and $actual -ne $m.DataDir) -or (-not $actual)
        if ($wrongInstance) {
            if ($actual) {
                $msg = ("data_dir 不符：实际 {0}，期望 {1}；判错实例，拒绝接管" -f $actual, $m.DataDir)
            }
            else {
                $msg = ("健康响应不回报 data_dir，无法证明是部署树实例（期望 {0}）；拒绝接管" -f $m.DataDir)
            }
            $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'wrong-instance'; Detail = $msg })
            $bad++
            Write-Host ("[FAIL] {0} - {1}" -f $name, $msg)
            Write-HostLog ("start {0}: {1}" -f $name, $msg) -Level Error
            continue
        }
    }

    if (Test-PortListening -Port $m.Port) {
        $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'already-running'; Detail = ("port {0}" -f $m.Port) })
        Write-Host ("[SKIP] {0} - port {1} 已在监听" -f $name, $m.Port)
        Write-HostLog ("start {0}: already running (port {1})" -f $name, $m.Port)
        continue
    }

    $task = $null
    try { $task = Get-ScheduledTask -TaskName $m.Task -ErrorAction Stop } catch { $task = $null }
    $taskUsable = ($task -and [string]$task.State -ne 'Disabled')

    if ($taskUsable) {
        Start-ScheduledTask -TaskName $m.Task -ErrorAction SilentlyContinue
        if (Wait-ModuleUp -Port $m.Port -Seconds $WaitSeconds) {
            Write-LedgerEntry -Module $name -Method task -Note ('port {0}' -f $m.Port)
            $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'started'; Detail = ("task '{0}' -> port {1}" -f $m.Task, $m.Port) })
            Write-Host ("[ OK ] {0} - 计划任务 {1} 已拉起" -f $name, $m.Task)
            Write-HostLog ("start {0}: via scheduled task {1}" -f $name, $m.Task)
        }
        else {
            $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'timeout'; Detail = ("task '{0}' {1}s 未就绪" -f $m.Task, $WaitSeconds) })
            $bad++
            Write-Host ("[FAIL] {0} - 任务 {1} {2}s 未就绪" -f $name, $m.Task, $WaitSeconds)
            Write-HostLog ("start {0}: task {1} timeout after {2}s" -f $name, $m.Task, $WaitSeconds) -Level Error
        }
        continue
    }

    if (-not $m.Wmi) {
        $reason = '无计划任务且无兜底命令'
        if ($task) { $reason = ("任务 '{0}' 状态 {1}，且无兜底命令" -f $m.Task, $task.State) }
        $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'no-launcher'; Detail = $reason })
        $bad++
        Write-Host ("[FAIL] {0} - {1}" -f $name, $reason)
        Write-HostLog ("start {0}: {1}" -f $name, $reason) -Level Error
        continue
    }

    # WMI 隐藏兜底：powershell -WindowStyle Hidden 包装脚本调用，不弹控制台
    $cwd = ''
    if ($m.WmiType -eq 'script') {
        $cwd = Split-Path -Path $m.Wmi -Parent
        $cmdline = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& ''{0}'' {1}"' -f $m.Wmi, $m.WmiArg
    }
    else {
        $cmdline = '{0} {1}' -f $m.Wmi, $m.WmiArg
    }
    try {
        $wmiArgs = @{ CommandLine = $cmdline; CurrentDirectory = $cwd }
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments $wmiArgs -ErrorAction Stop
        if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create 返回 {0}" -f $r.ReturnValue) }
        $procId = [int]$r.ProcessId
    }
    catch {
        $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'wmi-failed'; Detail = $_.Exception.Message })
        $bad++
        Write-Host ("[FAIL] {0} - WMI 兜底失败: {1}" -f $name, $_.Exception.Message)
        Write-HostLog ("start {0}: wmi failed {1}" -f $name, $_.Exception.Message) -Level Error
        continue
    }

    if (Wait-ModuleUp -Port $m.Port -Seconds $WaitSeconds) {
        Write-LedgerEntry -Module $name -Method wmi -ProcId $procId -Note ('port {0}' -f $m.Port)
        $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'started'; Detail = ("wmi pid {0} -> port {1}" -f $procId, $m.Port) })
        Write-Host ("[ OK ] {0} - WMI 隐藏拉起 (pid {1})" -f $name, $procId)
        Write-HostLog ("start {0}: via wmi pid {1}" -f $name, $procId)
    }
    else {
        $null = $results.Add([pscustomobject][ordered]@{ Name = $name; Result = 'timeout'; Detail = ("wmi pid {0} {1}s 未就绪" -f $procId, $WaitSeconds) })
        $bad++
        Write-Host ("[FAIL] {0} - WMI 拉起 {1}s 未就绪 (pid {2})" -f $name, $WaitSeconds, $procId)
        Write-HostLog ("start {0}: wmi timeout pid {1} after {2}s" -f $name, $procId, $WaitSeconds) -Level Error
    }
}

Invoke-HostLogRollover -LogDir $cfg.LogDir -MaxBytes $cfg.LogMaxBytes -Keep $cfg.LogKeep

if ($Json) {
    [pscustomobject]@{ ok = ($bad -eq 0); failed = $bad; results = $results.ToArray() } | ConvertTo-Json -Depth 4
}
elseif ($bad -gt 0) {
    Write-Host ("START 完成，{0} 个模块未就绪" -f $bad)
}
else {
    Write-Host 'START 完成，全部模块就绪'
}

if ($bad -gt 0) {
    Send-HostToast -Title 'LadderStock 启动' -Body ("{0} 个模块未就绪，看 host-start.log" -f $bad)
    exit 1
}
exit 0
