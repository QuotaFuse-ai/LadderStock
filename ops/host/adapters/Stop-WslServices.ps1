<#
.SYNOPSIS
    QUOT-483 / D1：停止 WSL 内 FinanceCoffer（financecoffer）容器栈。

.DESCRIPTION
    停止顺序（优雅优先，逐步兜底，每步后回读确认）：
      1. docker compose stop      —— compose.yaml 就位时，SIGTERM 优雅退出（-t 20）
      2. docker stop -t 20        —— 对优雅停后仍 running 的容器逐个温和停止
      3. docker kill              —— 最后兜底强杀
    回读判据："回读不到" —— docker ps 按 compose 项目 label
    （com.docker.compose.project=financecoffer）过滤后为空，即视为停净。

    刻意不做 service docker stop 兜底：那会波及 WSL 内全部容器，超出本脚本
    职责；kill 后仍有残留时报告退出码 1，交人工处理。

    -IncludeWslShutdown 是显式开关（默认绝不带 shutdown）：
      wsl --shutdown 会关掉整个 WSL VM——WSL 内全部服务（不止 FC）中断十几秒，
      请避开盘中时段执行。WSL 下次启动时 /etc/wsl.conf [boot] command 会自动运行
      wsl-boot.sh 把 FC 拉回（前提：Repair-WslHost.ps1 -Action Install 已就位）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-WslServices.ps1            # Status
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-WslServices.ps1 -Action Stop
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Stop-WslServices.ps1 -Action Stop -IncludeWslShutdown

.OUTPUTS 退出码
    0 = Status 成功，或 Stop 后回读不到任何 financecoffer 容器（含 shutdown 已确认）
    1 = Stop 后仍有残留容器在运行
    2 = 探测不到 WSL 发行版 / wsl 调用失败或超时 / 结果无法解析
#>
[CmdletBinding()]
param(
    [ValidateSet('Status', 'Stop')]
    [string]$Action = 'Status',
    [switch]$IncludeWslShutdown,
    [int]$TimeoutSeconds = 120
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

# bash 侧脚本：只输出 ASCII 标记行，PS 侧按标记解析（规避 wsl.exe 输出编码差异）
$WslFcScript = @'
#!/usr/bin/env bash
set -u
DEPLOY_DIR=/mnt/d/workplace/FinanceCoffer/deploy
ACTION="${1:-status}"

health() {
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8100/health 2>/dev/null)
  [ "$code" = "200" ] && echo UP || echo DOWN
}

fc_running() {
  docker ps -q --filter "label=com.docker.compose.project=financecoffer" 2>/dev/null
}

case "$ACTION" in
  status)
    echo "HEALTH=$(health)"
    echo "FC_RUNNING_COUNT=$(fc_running | wc -l)"
    docker ps --format '{{.Names}}\t{{.Status}}' --filter "label=com.docker.compose.project=financecoffer" 2>/dev/null
    ;;
  stop)
    echo "HEALTH_BEFORE=$(health)"
    ids=$(fc_running)
    if [ -z "$ids" ]; then
      echo "FC_ALREADY_STOPPED"
    else
      if [ -f "$DEPLOY_DIR/.env" ] && [ -f "$DEPLOY_DIR/compose.yaml" ]; then
        echo "GRACEFUL: docker compose stop -t 20"
        (cd "$DEPLOY_DIR" && docker compose --env-file .env -f compose.yaml stop -t 20) 2>&1 || true
      else
        echo "GRACEFUL: docker stop -t 20 (compose 文件缺失，直接停)"
        echo "$ids" | xargs -r docker stop -t 20 2>&1 || true
      fi
      remain=$(fc_running)
      if [ -n "$remain" ]; then
        echo "FALLBACK: docker stop -t 20 (对剩余容器)"
        echo "$remain" | xargs -r docker stop -t 20 2>&1 || true
      fi
      remain2=$(fc_running)
      if [ -n "$remain2" ]; then
        echo "FALLBACK: docker kill"
        echo "$remain2" | xargs -r docker kill 2>&1 || true
      fi
    fi
    after=$(fc_running)
    if [ -z "$after" ]; then
      echo "RESULT=STOPPED"
    else
      echo "RESULT=STILL-RUNNING $(echo "$after" | tr '\n' ' ')"
    fi
    echo "HEALTH_AFTER=$(health)"
    ;;
  *)
    echo "unknown action: $ACTION"
    exit 2
    ;;
esac
'@

$distro = Get-WslDefaultDistro
if (-not $distro) {
    Write-Host '[FAIL] 探测不到 WSL 默认发行版（wsl 未安装/未初始化？）'
    Write-HostLog 'wsl-fc: no default distro' -LogName 'wsl' -Level Error
    exit 2
}
Write-Host ("WSL 默认发行版: {0}" -f $distro)

# 把 bash 脚本落成临时 .sh 再执行（UTF-8 无 BOM + 强制 LF），避免引号转义与 CRLF 问题
$shPath = Join-Path $env:TEMP ('ldst-wsl-fc-{0}.sh' -f [Guid]::NewGuid().ToString('N'))
[IO.File]::WriteAllText($shPath, ($WslFcScript -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
$wslSh = '/mnt/' + $shPath.Substring(0, 1).ToLowerInvariant() + $shPath.Substring(2).Replace('\', '/')

function Invoke-WslScript {
    <# 带超时的 wsl bash 调用；输出清掉 NUL 后按文本返回（wsl.exe 管道输出编码不稳定） #>
    param([string]$Arg, [int]$TimeoutSec)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "$env:SystemRoot\System32\wsl.exe"
    $psi.Arguments = ('-d {0} -u root -e bash "{1}" {2}' -f $script:distro, $script:wslSh, $Arg)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $proc = [System.Diagnostics.Process]::Start($psi)
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
        try { $proc.Kill() } catch { }
        throw ('wsl 调用超时（{0}s）' -f $TimeoutSec)
    }
    $out = ''
    $err = ''
    try { $out = $outTask.Result } catch { }
    try { $err = $errTask.Result } catch { }
    return (($out + $err) -replace '\x00', '')
}

try {
    $output = Invoke-WslScript -Arg $Action.ToLowerInvariant() -TimeoutSec $TimeoutSeconds
}
catch {
    Write-Host ("[FAIL] {0}" -f $_.Exception.Message)
    Write-HostLog ('wsl-fc {0}: {1}' -f $Action.ToLowerInvariant(), $_.Exception.Message) -LogName 'wsl' -Level Error
    Remove-Item -LiteralPath $shPath -Force -ErrorAction SilentlyContinue
    exit 2
}
Write-Host $output
Write-HostLog ('wsl-fc {0}: {1}' -f $Action.ToLowerInvariant(), ($output -replace "`r?`n", ' | ').Trim(' |')) -LogName 'wsl'

if ($Action -eq 'Status') {
    Remove-Item -LiteralPath $shPath -Force -ErrorAction SilentlyContinue
    exit 0
}

# ---- Stop：解析回读结果 ----
$exitCode = 0
if ($output -match 'RESULT=STOPPED' -or $output -match 'FC_ALREADY_STOPPED') {
    Write-Host '[ OK ] 回读确认：financecoffer 容器已全部停止（running 列表回读不到）'
    Send-HostToast -Title 'WSL FinanceCoffer 已停止' -Body ('容器栈已停净（{0}）' -f (Get-Date -Format 'HH:mm:ss'))
    $exitCode = 0
}
elseif ($output -match 'RESULT=STILL-RUNNING') {
    Write-Host '[FAIL] 兜底后回读仍有 financecoffer 容器在运行，需人工 docker ps 检查'
    Write-HostLog 'wsl-fc stop: containers still running after kill' -LogName 'wsl' -Level Error
    Send-HostToast -Title 'WSL 停止失败' -Body 'financecoffer 容器有残留，请人工检查 docker ps'
    $exitCode = 1
}
else {
    Write-Host '[FAIL] 未解析到停止结果（docker/wsl 异常？见上方原始输出）'
    Write-HostLog 'wsl-fc stop: result marker missing' -LogName 'wsl' -Level Error
    $exitCode = 2
}
Remove-Item -LiteralPath $shPath -Force -ErrorAction SilentlyContinue

# ---- 可选：整体关闭 WSL VM（显式开关，默认不执行） ----
if ($IncludeWslShutdown) {
    Write-Host ''
    Write-Host '[WARN] -IncludeWslShutdown：即将执行 wsl --shutdown。'
    Write-Host '[WARN] 这会关闭整个 WSL VM：WSL 内全部服务（不止 FC）中断十几秒，请确认已避开盘中时段。'
    Write-Host '[WARN] WSL 下次启动时 wsl.conf [boot] command 会自动运行 wsl-boot.sh 拉回 FC 全栈。'
    Write-HostLog 'wsl --shutdown requested: ALL WSL services interrupted for ~seconds' -LogName 'wsl' -Level Warn
    Send-HostToast -Title 'WSL 即将整体关闭' -Body 'wsl --shutdown 执行中：WSL 全部服务中断十几秒，FC 将在下次 WSL 启动时自动恢复'

    $shutdownRc = 0
    & wsl.exe --shutdown 2>$null
    $shutdownRc = $LASTEXITCODE
    if ($shutdownRc -ne 0) {
        Write-Host ('[FAIL] wsl --shutdown 退出码 {0}' -f $shutdownRc)
        Write-HostLog ('wsl shutdown rc={0}' -f $shutdownRc) -LogName 'wsl' -Level Error
        exit 2
    }
    Start-Sleep -Seconds 3
    # 用 --list --running 回读（该查询本身不会把发行版重新拉起）
    $running = (& wsl.exe --list --running 2>$null) -join '' -replace '\x00', ''
    if ($running -match [regex]::Escape($distro)) {
        Write-Host '[WARN] shutdown 后发行版仍在运行列表中（可能刚被其他调用唤醒，wsl-boot.sh 会自愈 FC）'
        Write-HostLog ('wsl shutdown: {0} still listed as running' -f $distro) -LogName 'wsl' -Level Warn
    }
    else {
        Write-Host '[ OK ] wsl --shutdown 已完成，回读确认发行版未在运行'
        Write-HostLog 'wsl shutdown: distro confirmed not running' -LogName 'wsl'
    }
}

exit $exitCode
