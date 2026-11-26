# QUOT-549 / QUOT-525: zip the onedir bundle into a versioned, distributable artifact
# **并带上安装器**——让普通人拿到的是「解压 → 双击安装 → 用」，不是「先配 Python」。
#
# Output: dist/release/LadderStock-<version>-win-x64.zip
#
#   LadderStock/                  onedir（自带解释器与依赖，目标机不需要 Python）
#   Install-LadderStock.cmd       双击安装
#   Uninstall-LadderStock.cmd     双击卸载
#   install.ps1 / uninstall.ps1   安装器本体（.cmd 调用）
#   安装说明.txt                   面向普通人：装什么、AI Key 在哪填、数据在哪
#   README-dist.txt               面向开发：产物布局与自检命令
#
# Version: $env:LDST_RELEASE_VERSION > git tag v* > src/ladderstock/__init__.py __version__
param(
    [string]$DistDir = "dist",
    [string]$OutDir = "dist/release"
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DistDir = Join-Path $Root $DistDir
$OutDir = Join-Path $Root $OutDir
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Version = ""
if ($env:LDST_RELEASE_VERSION) {
    $Version = $env:LDST_RELEASE_VERSION.TrimStart("v")
}
if (-not $Version) {
    $initPy = Join-Path $Root "src\ladderstock\__init__.py"
    $Version = ([regex]::Match((Get-Content $initPy -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
}
if (-not $Version) { throw "cannot determine version (no LDST_RELEASE_VERSION, no __version__)" }

$Bundle = Join-Path $DistDir "LadderStock"
$Exe = Join-Path $Bundle "LadderStock.exe"
if (-not (Test-Path $Exe)) { throw "bundle missing: $Exe" }

# 安装器靠它显示版本（`__runtime-info` 不报版本，exe 也没有版本资源）
Set-Content -LiteralPath (Join-Path $Bundle "version.txt") -Value $Version -Encoding UTF8 -NoNewline

$InstallerDir = Join-Path $PSScriptRoot "installer"
$Stage = Join-Path $OutDir "staging"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

Write-Host "== stage: $Stage"
Copy-Item -LiteralPath $Bundle -Destination $Stage -Recurse -Force
foreach ($name in @("Install-LadderStock.cmd", "Uninstall-LadderStock.cmd", "install.ps1", "uninstall.ps1")) {
    $src = Join-Path $InstallerDir $name
    if (-not (Test-Path $src)) { throw "installer file missing: $src" }
    Copy-Item -LiteralPath $src -Destination $Stage -Force
}

$Guide = @'
LadderStock 投研助手 —— 安装说明

一、怎么装（3 步，不需要 Python、不需要配环境变量）
  1. 把本压缩包解压到一个空文件夹（不要在压缩包里直接双击）；
  2. 双击「Install-LadderStock.cmd」，等它提示「装好了」；
  3. 从开始菜单或桌面双击「LadderStock 投研助手」。

  安装位置：%LOCALAPPDATA%\LadderStock
    LadderStock\        程序（升级时整体替换）
    data\               你的数据（任务、偏好、对话、行情缓存）
    config\             你的配置（含 ai.yaml）
  想换盘安装：在该文件夹打开终端，执行
    powershell -ExecutionPolicy Bypass -File install.ps1 -Root D:\Apps\LadderStock

二、第一次启动还差一步：填 AI
  看板打开后（默认 http://127.0.0.1:5950），在页面上点「接入 AI」，
  填一次 base_url 与 api_key。填完右下角对话就能用。
  只需要做一次，之后重装、升级都不用再填。

三、数据与备份
  备份或换机：整个复制 %LOCALAPPDATA%\LadderStock 即可。
  升级：再跑一次 Install-LadderStock.cmd——程序目录被替换，
        data\ 与 config\ 原地保留，跟过的任务和偏好都还在。

四、卸载
  双击「Uninstall-LadderStock.cmd」，或 设置 → 应用 → 已安装的应用 里卸载。
  默认只删程序、保留 data 与 config；要连数据一起删就加 -PurgeData。

五、常见问题
  · 提示「Windows 已保护你的电脑」：本程序未签名，点「更多信息 → 仍要运行」。
  · 八格里美股/港股显示「未接入」：需要把 TickHub-US 等数据服务放到安装根
    （与 LadderStock\ 同级），页面会说明缺哪一个。
  · 端口被占：默认 5950；换端口用 LadderStock.exe __desktop --port 5951
'@
Set-Content -LiteralPath (Join-Path $Stage "安装说明.txt") -Value $Guide -Encoding UTF8

$Readme = @"
LadderStock $Version (win-x64) —— 产物说明（开发者看）

onedir 自带固定解释器与依赖，目标机不需要系统 Python / venv。
双击 LadderStock\LadderStock.exe（或安装后的快捷方式）起看板并开浏览器。

自检：
  LadderStock __runtime-info --output json
    期望 frozen=true、frontend_dist_ready / bundle_config_ready 均为 true
  /api/health 期望 ok=true；/ 返回真实前端（含 #root）

子命令（同一 exe）：__desktop / __watch-guard / schedule run /
watchdog --port 5952 / proxypool_forwarder / __runtime-info

peer 数据服务（按需，放到安装根、与 LadderStock\ 同级）：
  TickHub-CN\  TickHub-US\  Wire\quotafuse-wire\  StockSenate\  FinanceCoffer\

已知边界：本仓代码不签名（SmartScreen 会提示）；托盘与开机自启不在本单。
"@
Set-Content -LiteralPath (Join-Path $Stage "README-dist.txt") -Value $Readme -Encoding UTF8

$ZipName = "LadderStock-$Version-win-x64.zip"
$ZipPath = Join-Path $OutDir $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "== zip: $ZipPath"
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item $Stage -Recurse -Force

$sizeMb = [math]::Round(((Get-Item $ZipPath).Length / 1MB), 1)
Write-Host "OK: $ZipPath ($sizeMb MB, version $Version)"
Write-Host "  解压后双击 Install-LadderStock.cmd 即可安装（无需 Python）"
Write-Host "##vso[task.setvariable variable=ldst_version]$Version"
