<#
.SYNOPSIS
    Start the dev-instance watchdog for 5951 (QUOT-589).

.DESCRIPTION
    QUOT-589：每实例一个 scoped 看门狗。prod 5950 继续用 5952 + restart-api.ps1
    （install-service.ps1，语义不变）；dev 实例（5951）用它自己的看门狗：

      * 端口 5953（DEC-063 的 5952 属于 prod，dev 不复用，避免互相探错）；
      * --api http://127.0.0.1:5951：只探活、只重启 dev 实例；
      * --restart-script scripts\restart-dev.ps1：只结束/拉起 5951；
      * LDST_WATCHDOG_PORT=5953：serving backend（5951）的同源 /platform 转发
        由此环境变量注入——dev 实例请以同一环境启动 `ldst run --port 5951`。

    不触碰 prod（5950/5952）、8000 peer、9100、9000。
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev-watchdog.ps1
#>
[CmdletBinding()]
param(
    [int]$WatchdogPort = 5953,
    [int]$ApiPort = 5951
)
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$env:LDST_WATCHDOG_PORT = "$WatchdogPort"

$ldst = Get-Command ldst -ErrorAction SilentlyContinue
if ($ldst) {
    $exe = $ldst.Source
    $base = @()
} else {
    $exe = 'python'
    $base = @('-m', 'ladderstock')
}

& $exe @base watchdog `
    --port $WatchdogPort `
    --api "http://127.0.0.1:$ApiPort" `
    --restart-task '' `
    --restart-script (Join-Path $RepoRoot 'scripts\restart-dev.ps1')
