# QUOT-589：dev 实例（5951）重启助手——只拉起 5951，绝不碰 5950。
# 与 restart-api.ps1 同契约：杀 PID 由看门狗自己做（只杀此刻占用 --api 端口的
# 监听者），本脚本只负责在端口让出后把 dev 实例拉回来。
$ErrorActionPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Port     = 5951

Set-Location -LiteralPath $RepoRoot
$ldst = Get-Command ldst -ErrorAction SilentlyContinue
if ($ldst) {
    Start-Process -FilePath $ldst.Source -ArgumentList 'run', '--port', "$Port" -WindowStyle Hidden
} else {
    Start-Process -FilePath 'python' -ArgumentList '-m', 'ladderstock', 'run', '--port', "$Port" -WindowStyle Hidden
}
