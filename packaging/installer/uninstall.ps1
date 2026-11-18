# LadderStock 卸载器
#
# 默认只删「程序」：快捷方式、卸载注册项、程序目录。
# 用户的 data/ 与 config/（任务、偏好、对话、行情缓存）**默认保留**——
# 卸载一个软件不该顺手删掉用户跟了半个月的研究任务。
# 确实要一起删，明确加 -PurgeData。
param(
    [string]$Root = "",
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
function Say([string]$text) { Write-Host $text }

if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA "LadderStock" }
$Root = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
$Target = Join-Path $Root "LadderStock"

# 只允许在「看起来就是 LadderStock 安装根」的目录里操作，避免路径写错时误删
$marker = Join-Path $Root "install.json"
if (-not (Test-Path -LiteralPath $marker) -and -not (Test-Path -LiteralPath $Target)) {
    throw "$Root 不像 LadderStock 安装根（没有 install.json，也没有 program 目录）。为安全起见不继续。"
}

$running = Get-Process -Name "LadderStock" -ErrorAction SilentlyContinue
if ($running) {
    Say "正在退出 LadderStock（PID $($running.Id -join ',')）..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# 快捷方式
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\LadderStock 投研助手"
if (Test-Path -LiteralPath $StartMenuDir) { Remove-Item -LiteralPath $StartMenuDir -Recurse -Force; Say "已删开始菜单项" }
$deskLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "LadderStock 投研助手.lnk"
if (Test-Path -LiteralPath $deskLnk) { Remove-Item -LiteralPath $deskLnk -Force; Say "已删桌面快捷方式" }

# 卸载注册项
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LadderStock"
if (Test-Path -LiteralPath $UninstallKey) { Remove-Item -LiteralPath $UninstallKey -Recurse -Force; Say "已删卸载注册项" }

# 程序目录（含安装时保留的 *.old-* 备份）
Get-ChildItem -LiteralPath $Root -Directory -Filter "LadderStock*" | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
    Say "已删程序目录 $($_.Name)"
}

if ($PurgeData) {
    foreach ($name in @("data", "config")) {
        $dir = Join-Path $Root $name
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force; Say "已删 $name\" }
    }
    $markerFile = Join-Path $Root "install.json"
    if (Test-Path -LiteralPath $markerFile) { Remove-Item -LiteralPath $markerFile -Force }
    # 卸载脚本自己也在安装根里：不删它，安装根永远「非空」，-PurgeData 就删不干净
    $selfScript = Join-Path $Root "uninstall.ps1"
    if (Test-Path -LiteralPath $selfScript) { Remove-Item -LiteralPath $selfScript -Force }
    # 安装根空了就一起删；还有 peer 目录或用户文件时留着
    $left = @(Get-ChildItem -LiteralPath $Root -Force)
    if ($left.Count -eq 0) { Remove-Item -LiteralPath $Root -Force; Say "已删安装根 $Root" }
} else {
    Say "用户数据保留在 $Root\data 与 $Root\config（要一起删请加 -PurgeData）。"
}
Say "卸载完成。"
