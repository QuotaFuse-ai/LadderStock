<#
.SYNOPSIS
    QUOT-523：生产单入口启停包装器——`ldst supervisor`（同一本账、同一份归属判定）。

.DESCRIPTION
    QUOT-482 之后，board + watchdog + 七个模块由 supervisor 一棵树托管；这个脚本是
    计划任务 / 桌面上唯一该被点或调用的入口（显式 start / stop / restart / status），
    不要在别的脚本里再各拉一份 `start_all.cmd` / `start_watchdog.cmd`。

    -Status   只读打印 `supervisor status`（owner / evidence / 监听）。
    -DryRun   只打印将执行的命令并回显预演，不拉起也不停止任何进程。
    -Restart  需要 -Modules。

    安全边界（与 DEC-014 一致）：
      * 只操作账本能证明由本控制面拉起的进程；外部占用端口一律不碰；
      * `stop` 默认是预演，真正停止要求 -Force（等价 `ldst supervisor stop --force`）；
      * 面板 5950 自身不在七个模块里：停掉模块组不会关掉看板，看板由自己的
        任务 / 快捷方式管理，直到用户批准的切换窗口接管。

    路径：启动器按 `LDST_LAUNCHER` → `LDST_INSTALL_ROOT\LadderStock\.venv\Scripts\ldst.exe`
    → `python -m ladderstock.cli.main` 解析，全部 bundle 相对，不写死盘符。

.PARAMETER Action
    start | stop | restart | status（缺省 start）。

.PARAMETER Modules
    restart 必填；start / stop 可选，只处理列出的模块。

.PARAMETER Force
    stop 的确认开关（等价 `ldst supervisor stop --force`）。

.PARAMETER DryRun
    打印意图不执行。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SingleEntry.ps1 -Action status
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SingleEntry.ps1 -Action start
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SingleEntry.ps1 -Action stop -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'start',
    [string[]]$Modules = @(),
    [switch]$Force,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Resolve-SupervisorPrefix {
    $launcher = [Environment]::GetEnvironmentVariable('LDST_LAUNCHER')
    # `LDST_LAUNCHER` 也接受解释器：是 python*.exe 时按 `-m` 源码树形态补全
    if ($launcher -and $launcher -match '\\python[0-9.]*w?\.exe$') {
        return @($launcher, '-m', 'ladderstock.cli.main')
    }
    if ($launcher) { return @($launcher) }
    $root = [Environment]::GetEnvironmentVariable('LDST_INSTALL_ROOT')
    if ($root) {
        $exe = Join-Path $root 'LadderStock\.venv\Scripts\ldst.exe'
        if (Test-Path -LiteralPath $exe) { return @($exe) }
    }
    $python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $python) { throw '找不到启动器：设 LDST_LAUNCHER / LDST_INSTALL_ROOT，或把 python 放进 PATH' }
    return @($python, '-m', 'ladderstock.cli.main')
}

$prefix = Resolve-SupervisorPrefix
$argv = @($prefix) + @('supervisor', $Action)
if ($Modules.Count -gt 0) {
    if ($Action -eq 'restart' -or $Action -eq 'start' -or $Action -eq 'stop') {
        $argv += '--only'
        $argv += $Modules | Sort-Object -Unique
    }
}
if ($Action -eq 'stop' -and $Force) { $argv += '--force' }
if ($DryRun) {
    if ($Action -eq 'start' -or $Action -eq 'restart') {
        Write-Host ('[dry-run] 将执行：{0}' -f ($argv -join ' '))
        exit 0
    }
    if ($Action -eq 'stop') {
        # 预演必须走真实的 supervisor stop（不带 --force 本身就是预演），只打印不动
        Write-Host ('[dry-run] 将执行：{0}' -f ($argv -join ' '))
    }
}

# python 输出 UTF-8；控制台编码不跟着改，Windows PowerShell 5.1 会把它当 ANSI 解成乱码
$prevEnc = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'
try {
    & $argv[0] $argv[1..($argv.Count - 1)]
    $code = $LASTEXITCODE
}
finally {
    if ($null -eq $prevEnc) { Remove-Item -Path Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $prevEnc }
}
exit $code
