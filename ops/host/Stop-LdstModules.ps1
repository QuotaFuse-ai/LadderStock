<#
.SYNOPSIS
    QUOT-483 宿主侧停止器：按清单确认后停止模块（关窗全停与 482 调用共用）。

.DESCRIPTION
    两种范围：
      * 默认 = 只停【本机 ledger 里记录的、由启动器拉起的】模块
      * -All  = 停配置表里的全部模块（含外部已运行的；面板确认弹窗勾选后才会传）

    安全规则：
      * -DryRun 只打印"将要停止的清单"，不动任何进程（面板关窗确认弹窗先走这步）
      * 计划任务通道：Stop-ScheduledTask（不杀进程树，任务自己的 supervisor 处理）
      * WMI/进程通道：只杀"当前仍持有该端口"的那个 PID
      * 永不碰镜像名/路径含 multica 的进程（对齐 install-service.ps1 既有规则）
      * data_dir 错实例（端口被别的实例占用）跳过并在清单里标出

    退出码：0 = 清单执行完毕（含空清单）；1 = 有未停干净项

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-LdstModules.ps1 -DryRun
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-LdstModules.ps1 -All -Json
#>
[CmdletBinding()]
param(
    # 停止全部模块（默认只停 ledger 里我拉起的）
    [switch]$All,

    # 只打印清单不执行
    [switch]$DryRun,

    [string[]]$Modules = @(),

    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$cfg = Import-HostConfig
$status = Get-ModuleStatus

# 待停集合：All = 配置表全部；默认 = ledger 中我拉起的
$targets = @()
if ($All) {
    $targets = @($status)
}
else {
    $mine = @{}
    foreach ($e in (Read-LedgerEntries)) {
        if ($e.module) { $mine[$e.module] = $e }
    }
    $targets = @($status | Where-Object { $mine.ContainsKey($_.Name) })
}
if ($Modules.Count -gt 0) {
    $targets = @($targets | Where-Object { $Modules -contains $_.Name })
}

# 生成清单（含风险标注）
$plan = @()
foreach ($t in $targets) {
    $method = 'task'
    $procId = 0
    if (-not $All) {
        $e = (Read-LedgerEntries | Where-Object { $_.module -eq $t.Name } | Select-Object -Last 1)
        if ($e) { $method = [string]$e.method; $procId = [int]$e.procId }
    }
    if (-not $t.Listening -and $t.TaskState -ne 'Running') { continue }   # 本来就没跑
    $procName = ''
    if ($procId -gt 0) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p) { $procName = $p.ProcessName }
    }
    elseif ($t.Listening) {
        $p = Get-Process -Id $t.PortPid -ErrorAction SilentlyContinue
        if ($p) { $procName = $p.ProcessName; $procId = $t.PortPid }
    }
    $plan += [pscustomobject][ordered]@{
        Module   = $t.Name
        Task     = $t.Task
        Method   = $method
        Port     = $t.Port
        ProcId   = $procId
        ProcName = $procName
        Guard    = $(if ($t.Port -gt 0 -and -not $t.DataDirOk) { 'data_dir 错实例，跳过' }
                     elseif ($procName -match 'multica') { 'multica 进程，跳过' }
                     else { '' })
    }
}

if ($Json) {
    [pscustomobject]@{ dryRun = [bool]$DryRun; count = $plan.Count; plan = $plan } | ConvertTo-Json -Depth 4
    exit 0
}

if ($plan.Count -eq 0) {
    Write-Host 'STOP 清单为空（没有需要停止的模块）'
    exit 0
}

Write-Host '将停止以下模块：'
foreach ($p in $plan) {
    Write-Host ('  - {0,-22} 任务 [{1}] 端口 {2} pid {3} {4}' -f $p.Module, $p.Task, $p.Port, $p.ProcId, $p.Guard)
}
if ($DryRun) {
    Write-Host '(DryRun：仅打印清单，未执行)'
    exit 0
}

$left = 0
foreach ($p in $plan) {
    if ($p.Guard) {
        Write-Host ("[SKIP] {0} - {1}" -f $p.Module, $p.Guard)
        Write-HostLog ("stop {0}: skipped ({1})" -f $p.Module, $p.Guard) -Level Warn
        continue
    }
    try {
        $task = Get-ScheduledTask -TaskName $p.Task -ErrorAction SilentlyContinue
        if ($task -and [string]$task.State -ne 'Disabled') {
            Stop-ScheduledTask -TaskName $p.Task -ErrorAction SilentlyContinue
        }
        # 进程通道兜底：仅当该 PID 仍持有端口才杀
        if ($p.ProcId -gt 0) {
            Start-Sleep -Seconds 1
            if ($p.Port -gt 0) {
                $still = Get-NetTCPConnection -State Listen -LocalPort $p.Port -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $p.ProcId }
                if ($still) { Stop-Process -Id $p.ProcId -Force -ErrorAction SilentlyContinue }
            }
            else {
                $alive = Get-Process -Id $p.ProcId -ErrorAction SilentlyContinue
                if ($alive -and $alive.ProcessName -notmatch 'multica') { Stop-Process -Id $p.ProcId -Force -ErrorAction SilentlyContinue }
            }
        }
        Write-Host ("[ OK ] {0} 已停止" -f $p.Module)
        Write-HostLog ("stop {0}: stopped" -f $p.Module)
    }
    catch {
        $left++
        Write-Host ("[FAIL] {0} - {1}" -f $p.Module, $_.Exception.Message)
        Write-HostLog ("stop {0}: {1}" -f $p.Module, $_.Exception.Message) -Level Error
    }
}

# 复核：5 秒后逐端口确认
Start-Sleep -Seconds 5
foreach ($p in $plan) {
    if ($p.Guard -or $p.Port -le 0) { continue }
    if (Test-PortListening -Port $p.Port) {
        $left++
        Write-Host ("[WARN] {0} 端口 {1} 仍在监听（可能由其他进程持有，未强杀）" -f $p.Module, $p.Port)
        Write-HostLog ("stop {0}: port {1} still listening after stop" -f $p.Module, $p.Port) -Level Warn
    }
}

if ($left -gt 0) {
    Send-HostToast -Title 'LadderStock 停止' -Body ("{0} 个模块未停干净，看 host-stop.log" -f $left)
    Write-Host ("STOP 完成，{0} 项未停干净" -f $left)
    exit 1
}
Write-Host 'STOP 完成，清单内模块已全部停止'
exit 0
