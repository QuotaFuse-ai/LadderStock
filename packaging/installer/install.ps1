# LadderStock Windows 安装器（QUOT-525 的「装」这一步）
#
# 目的：把 onedir 产物装成「双击图标就用」，不再要求普通人复制代码、配
# PYTHONPATH / LDST_DATA_DIR 或系统 Python。产物自带解释器与依赖，这里只做四件事：
#
#   1. 把程序目录装进安装根（默认 %LOCALAPPDATA%\LadderStock）；
#   2. **绝不碰用户的 data/ 与 config/**（冻结态数据落在安装根内，升级时程序
#      目录整体替换、数据目录原地保留，所以升级不会丢任务与偏好）；
#   3. 建开始菜单 / 桌面快捷方式，写卸载注册项（控制面板可见）；
#   4. 告诉用户还差一步：AI Key 在页面里点「接入 AI」填一次。
#
# 布局（安装根）：
#
#   %LOCALAPPDATA%\LadderStock\
#   ├── LadderStock\           程序（升级时整体替换）
#   ├── data\                  用户数据（永不动）
#   ├── config\                用户配置（含 ai.yaml，永不动）
#   ├── install.json           安装记录
#   └── TickHub-CN\ ...        peer 数据服务（用户按需放，installer 不代管）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Root D:\Apps\LadderStock -NoDesktop
param(
    [string]$Bundle = "",
    [string]$Root = "",
    [switch]$NoDesktop,
    [switch]$Start,
    [switch]$KeepOld
)

$ErrorActionPreference = "Stop"

function Say([string]$text) { Write-Host $text }

# ---- 定位产物 -------------------------------------------------------------
if (-not $Bundle) { $Bundle = Join-Path $PSScriptRoot "LadderStock" }
if (-not (Test-Path -LiteralPath $Bundle)) {
    throw "找不到程序目录：$Bundle（把 Install-LadderStock.cmd 与 LadderStock\ 放在同一层再运行）"
}
$Exe = Join-Path $Bundle "LadderStock.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "程序目录里没有 LadderStock.exe：$Bundle"
}

if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA "LadderStock" }
$Target = Join-Path $Root "LadderStock"

# ---- 正在运行则先退出 -----------------------------------------------------
$running = Get-Process -Name "LadderStock" -ErrorAction SilentlyContinue
if ($running) {
    throw "LadderStock 正在运行（PID $($running.Id -join ',')）。请先从托盘/任务栏退出，再运行安装。"
}

# ---- 版本 ----------------------------------------------------------------
$Version = ""
# 产物里带 version.txt 时以它为准：`__runtime-info` 不报版本，exe 也没写版本
# 资源，不读它这里就只能显示 unknown。
$VersionFile = Join-Path $Bundle "version.txt"
if (Test-Path -LiteralPath $VersionFile) {
    $Version = (Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8).Trim()
}
if (-not $Version) {
    try {
        $raw = & $Exe "__runtime-info" "--output" "json" 2>$null
        $info = $raw | ConvertFrom-Json
        if ($info -and $info.version) { $Version = [string]$info.version }
    } catch {
        $Version = ""
    }
}
if (-not $Version) {
    try { $Version = (Get-Item -LiteralPath $Exe).VersionInfo.FileVersion } catch { $Version = "" }
}
if (-not $Version) { $Version = "unknown" }

Say "LadderStock 安装器"
Say "  程序目录 : $Bundle"
Say "  安装根    : $Root"
Say "  版本      : $Version"

# ---- 保留用户数据 ---------------------------------------------------------
# 冻结态 data/config 与程序目录同级（portable 布局）：升级只替换程序目录，
# 绝不清空安装根，否则用户的 research_plans.json / preferences.json 会被抹掉。
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$DataDir = Join-Path $Root "data"
$ConfigDir = Join-Path $Root "config"
foreach ($dir in @($DataDir, $ConfigDir)) {
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

# ---- 换装 ----------------------------------------------------------------
$OldDir = $null
if (Test-Path -LiteralPath $Target) {
    $stamp = Get-Date -Format "yyyyMMddHHmmss"
    $OldDir = "$Target.old-$stamp"
    Say "  备份旧程序 -> $OldDir"
    Move-Item -LiteralPath $Target -Destination $OldDir -Force
}
Say "  复制程序 ..."
Copy-Item -LiteralPath $Bundle -Destination $Target -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $Target "LadderStock.exe"))) {
    throw "复制后仍找不到 LadderStock.exe：$Target"
}

# ---- 快捷方式 ------------------------------------------------------------
function NewShortcut([string]$linkPath, [string]$target, [string]$workDir, [string]$desc) {
    $parent = Split-Path -Parent $linkPath
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $target
    $link.Arguments = "__desktop"
    $link.WorkingDirectory = $workDir
    $link.IconLocation = "$target,0"
    $link.Description = $desc
    $link.Save()
}

$ExeTarget = Join-Path $Target "LadderStock.exe"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\LadderStock 投研助手"
$lnk = Join-Path $StartMenu "LadderStock 投研助手.lnk"
NewShortcut $lnk $ExeTarget $Target "LadderStock 投研助手"
Say "  开始菜单 : $lnk"
if (-not $NoDesktop) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $deskLnk = Join-Path $desktop "LadderStock 投研助手.lnk"
    NewShortcut $deskLnk $ExeTarget $Target "LadderStock 投研助手"
    Say "  桌面     : $deskLnk"
}

# ---- 卸载注册项（控制面板「应用和功能」可见） -----------------------------
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LadderStock"
New-Item -Path $UninstallKey -Force | Out-Null
$UninstallCmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\uninstall.ps1`""
Set-ItemProperty -Path $UninstallKey -Name "DisplayName" -Value "LadderStock 投研助手"
Set-ItemProperty -Path $UninstallKey -Name "DisplayVersion" -Value $Version
Set-ItemProperty -Path $UninstallKey -Name "Publisher" -Value "QuotaFuse"
Set-ItemProperty -Path $UninstallKey -Name "InstallLocation" -Value $Root
Set-ItemProperty -Path $UninstallKey -Name "DisplayIcon" -Value "$ExeTarget,0"
Set-ItemProperty -Path $UninstallKey -Name "UninstallString" -Value $UninstallCmd
Set-ItemProperty -Path $UninstallKey -Name "NoModify" -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "NoRepair" -Value 1 -Type DWord

# 卸载脚本与安装记录都放进安装根：控制面板点「卸载」时得找得到它
$UninstallPs1 = Join-Path $PSScriptRoot "uninstall.ps1"
if (Test-Path -LiteralPath $UninstallPs1) {
    Copy-Item -LiteralPath $UninstallPs1 -Destination (Join-Path $Root "uninstall.ps1") -Force
}
$record = [ordered]@{
    installed_at = (Get-Date).ToString("s")
    version      = $Version
    root         = $Root
    bundle       = $Target
    source       = (Split-Path -Parent $Bundle)
}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root "install.json") -Encoding UTF8

# ---- 还差一步：AI Key -----------------------------------------------------
$aiYaml = Join-Path $ConfigDir "ai.yaml"
$hasKey = $false
if (Test-Path -LiteralPath $aiYaml) {
    $text = Get-Content -LiteralPath $aiYaml -Raw -Encoding UTF8
    if ($text -match "api_key\s*:\s*\S" -or $text -match "base_url\s*:\s*http") { $hasKey = $true }
}
if (-not $hasKey) {
    Say ""
    Say "还差一步（约 1 分钟）：启动后在看板页面点「接入 AI」，填一次 base_url 与 api_key。"
    Say "这一步只在这里做，不需要改环境变量、不需要装 Python。"
}

Say ""
Say "装好了：双击「LadderStock 投研助手」即可（默认 http://127.0.0.1:5950）。"
Say "数据与配置在 $Root —— 备份或迁移整个目录即可，重装不会覆盖它们。"

if ($OldDir -and -not $KeepOld) {
    # 旧程序目录默认留一份：万一新版起不来，用户还能回滚
    Say "旧版本保留在 $OldDir（确认新版可用后可手动删除）。"
}

if ($Start) {
    Say "启动 ..."
    Start-Process -FilePath $ExeTarget -ArgumentList "__desktop" -WorkingDirectory $Target
}
