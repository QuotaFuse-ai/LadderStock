<#
.SYNOPSIS
    QUOT-483 宿主侧前置检查（四步，原样搬自「盯盘一键恢复」第 1-4 步）。

.DESCRIPTION
    单图标 launcher 与 QUOT-482 supervisor 的公共第一道工序。任何一步失败都只报告，
    不抛出、不中止后续步骤；最终以 exit code 汇总：
        0 = 全部通过
        1 = 至少一项失败（详见日志与 stdout）
        2 = 用法/环境错误（非管理员等）

    步骤：
      [1] iphlpsvc 服务必须 Running（portproxy 转发依赖，被停过就全断）
      [2] wsl -e bash -lc "hostname -I" 取当前 WSL IP（每次重启都会变）
      [3] netsh portproxy 按当前 IP 重建 18080->8080 / 13000->3000 / 3000->3000 / 8080->8080
      [4] multica daemon 未在跑则拉起（hidden start --foreground）并等到 running，
          starting 是正常中间态，最多等 60 秒

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-HostPrecheck.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-HostPrecheck.ps1 -Json
#>
[CmdletBinding()]
param(
    # 以 JSON 输出结果对象（供 QUOT-482 supervisor 解析；默认人类可读）
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$cfg = Import-HostConfig
$results = New-Object System.Collections.ArrayList
$failed = 0

function Add-Step {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $null = $script:results.Add([pscustomobject][ordered]@{ Step = $Name; Ok = $Ok; Detail = $Detail })
    if (-not $Ok) { $script:failed++ }
    $tag = if ($Ok) { 'PASS' } else { 'FAIL' }
    Write-Host ('[{0}] {1} - {2}' -f $tag, $Name, $Detail)
    Write-HostLog ("precheck {0}: {1} - {2}" -f $tag, $Name, $Detail) -Level $(if ($Ok) { 'Info' } else { 'Error' })
}

# ---------- [1] iphlpsvc ----------
try {
    $svc = Get-Service -Name 'iphlpsvc' -ErrorAction Stop
    if ($svc.Status -ne 'Running') {
        if (-not (Test-IsAdministrator)) {
            Add-Step 'iphlpsvc' $false ("iphlpsvc = {0}，需管理员才能启动（portproxy 的转发依赖）" -f $svc.Status)
        }
        else {
            Start-Service -Name 'iphlpsvc'
            Start-Sleep -Seconds 2
            $svc = Get-Service -Name 'iphlpsvc'
            Add-Step 'iphlpsvc' ($svc.Status -eq 'Running') ("iphlpsvc {0} -> {1}" -f $svc.Status, $svc.Status)
        }
    }
    else {
        Add-Step 'iphlpsvc' $true 'Running'
    }
}
catch {
    Add-Step 'iphlpsvc' $false ("查询失败: {0}" -f $_.Exception.Message)
}

# ---------- [2] WSL IP ----------
$wslIp = ''
try {
    $raw = & wsl.exe -e bash -lc "hostname -I" 2>$null
    if ($raw) {
        $parts = ($raw -join ' ') -split '\s+'
        foreach ($p in $parts) { if ($p.Trim()) { $wslIp = $p.Trim(); break } }
    }
    if ($wslIp) { Add-Step 'wsl-ip' $true $wslIp }
    else { Add-Step 'wsl-ip' $false '取不到 WSL IP（wsl 未运行或未安装发行版）' }
}
catch {
    Add-Step 'wsl-ip' $false ("执行失败: {0}" -f $_.Exception.Message)
}

# ---------- [3] portproxy ----------
if (-not (Test-IsAdministrator)) {
    Add-Step 'portproxy' $false '重建 portproxy 需要管理员权限'
}
elseif (-not $wslIp) {
    Add-Step 'portproxy' $false '跳过：拿不到 WSL IP，保持现有规则不动'
}
else {
    $ppOk = $true
    $detail = @()
    foreach ($m in $cfg.PortProxy) {
        try {
            $null = & netsh interface portproxy delete v4tov4 ("listenaddress=127.0.0.1 listenport={0}" -f $m.Listen) 2>&1
            $null = & netsh interface portproxy add v4tov4 ("listenaddress=127.0.0.1 listenport={0} connectaddress={1} connectport={2}" -f $m.Listen, $wslIp, $m.Connect) 2>&1
            $detail += ("127.0.0.1:{0}->{1}:{2}" -f $m.Listen, $wslIp, $m.Connect)
        }
        catch {
            $ppOk = $false
            $detail += ("{0}:FAILED({1})" -f $m.Listen, $_.Exception.Message)
        }
    }
    Add-Step 'portproxy' $ppOk ($detail -join '; ')
}

# ---------- [4] multica daemon ----------
$mul = Join-Path $env:USERPROFILE '.multica\bin\multica.exe'
if (-not (Test-Path $mul)) {
    Add-Step 'multica-daemon' $false ("未找到 {0}" -f $mul)
}
else {
    $status = (& $mul daemon status 2>&1 | Out-String)
    if ($status -match 'running') {
        Add-Step 'multica-daemon' $true ((($status -split "`n")[0]).Trim())
    }
    else {
        Write-HostLog 'daemon 未运行 → 拉起（hidden daemon start --foreground）' -Level Warn
        $launched = $true
        try { $null = Start-Process -FilePath $mul -ArgumentList 'daemon', 'start', '--foreground' -WindowStyle Hidden -ErrorAction Stop }
        catch {
            $launched = $false
            Add-Step 'multica-daemon' $false ("拉起失败: {0}" -f $_.Exception.Message)
        }
        if ($launched) {
            $waited = 0
            $running = $false
            while ($waited -lt 60) {
                Start-Sleep -Seconds 10
                $waited += 10
                $status = (& $mul daemon status 2>&1 | Out-String)
                if ($status -match 'running') { $running = $true; break }
            }
            Add-Step 'multica-daemon' $running ("等待 {0}s 后状态: {1}" -f $waited, (($status -split "`n")[0]).Trim())
        }
    }
}

# ---------- 汇总 ----------
if ($Json) {
    [pscustomobject]@{
        ok     = ($failed -eq 0)
        failed = $failed
        steps  = $results.ToArray()
    } | ConvertTo-Json -Depth 4
}
else {
    Write-Host ('PRECHECK {0} (failed={1})' -f $(if ($failed -eq 0) { 'PASS' } else { 'FAIL' }), $failed)
}

# 日志上限（D5）：每次检查顺手轮转一次
Invoke-HostLogRollover -LogDir $cfg.LogDir -MaxBytes $cfg.LogMaxBytes -Keep $cfg.LogKeep

if ($failed -gt 0) { exit 1 }
exit 0
