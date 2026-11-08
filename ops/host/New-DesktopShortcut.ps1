<#
.SYNOPSIS
    QUOT-483：桌面唯一入口图标（单图标 launcher）。

    硬要求（482 裁定）：
      * 桌面最终只留一个 LadderStock 入口图标；
      * 双击 → powershell -WindowStyle Hidden 拉起宿主面板（前置检查 + 按序起模块），
        **不允许弹出黑窗口**（进程经 -WindowStyle Hidden + WMI 隐藏兜底，见 Start-LdstModules）；
      * 面板常驻托盘：关闭面板窗口只停 Window，不停服务；"退出面板（不停服务）"同理。

    本脚本负责：
      1. 创建/更新桌面唯一图标（Target=powershell + Arguments -WindowStyle Hidden）；
      2. 扫描桌面（用户桌面 + 公共桌面）现存的其他 LadderStock 家族旧入口并逐条列出，
        **只报告不移除**——移除旧入口是 supervisor 验收通过后的第二步动作，
        届时按清单单独执行并单独报告（拆分的顺序见 482 收编方案）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\New-DesktopShortcut.ps1 -DeployRoot 'D:\QuotaFuseModules\LadderStock'
#>
[CmdletBinding()]
param(
    [string]$DeployRoot = 'D:\QuotaFuseModules\LadderStock',
    [string]$LinkName = 'LadderStock',
    [string]$PanelRelativePath = 'ops\host\Show-LdstHostPanel.ps1',
    [string]$IconPath = '',
    [int]$IconIndex = 0
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\HostCommon.ps1"

$panel = Join-Path $DeployRoot $PanelRelativePath
if (-not (Test-Path $panel)) {
    Write-Host ('[FAIL] 未找到宿主面板脚本：{0}（先部署 ops\host，再建图标）' -f $panel)
    Write-HostLog ('desktop shortcut: panel script missing {0}' -f $panel) -Level Error
    exit 1
}
$panel = (Resolve-Path $panel).Path

if (-not $IconPath) { $IconPath = Join-Path $env:SystemRoot 'System32\taskmgr.exe' }

$userDesktop = [Environment]::GetFolderPath('Desktop')
$publicDesktop = Join-Path $env:Public 'Desktop'
$desktops = @($userDesktop)
if ((Test-Path $publicDesktop) -and $publicDesktop -ne $userDesktop) { $desktops += $publicDesktop }

# 旧入口识别关键字（LadderStock 家族；QuotaFuse Desk/Hub 属 disk-warden，不在本单图标范围）
$keywords = @('LadderStock', 'QuotaFuseModules', 'tickhub', 'TickHub', '盯盘')

function Test-IconFamily {
    param([string]$LinkName, [string]$Target, [string]$Args, [string]$WorkDir)
    foreach ($k in $keywords) {
        if ($LinkName.Contains($k) -or $Target.Contains($k) -or $Args.Contains($k) -or $WorkDir.Contains($k)) {
            return $true
        }
    }
    return $false
}

function Get-ShortcutInfo {
    param([string]$Path)
    $info = [pscustomobject]@{ TargetPath = ''; Arguments = ''; WorkingDirectory = ''; Description = ''; IconLocation = '' }
    try {
        $sh = New-Object -ComObject WScript.Shell
        $sc = $sh.CreateShortcut($Path)
        $info.TargetPath = [string]$sc.TargetPath
        $info.Arguments = [string]$sc.Arguments
        $info.WorkingDirectory = [string]$sc.WorkingDirectory
        $info.Description = [string]$sc.Description
        $info.IconLocation = [string]$sc.IconLocation
    }
    catch { }
    return $info
}

# ---- 1. 扫描桌面现存 LadderStock 家族图标（含即将创建的那个） ----
$shell = New-Object -ComObject WScript.Shell
$linkPath = Join-Path $userDesktop ($LinkName + '.lnk')
$linkArgs = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $panel)

$existingOurs = @()
$legacy = @()
foreach ($d in $desktops) {
    foreach ($f in (Get-ChildItem -LiteralPath $d -Filter '*.lnk' -File -ErrorAction SilentlyContinue)) {
        $i = Get-ShortcutInfo -Path $f.FullName
        $isOurs = ($i.Arguments -like '*Show-LdstHostPanel*')
        $family = Test-IconFamily -LinkName $f.Name -Target $i.TargetPath -Args $i.Arguments -WorkDir $i.WorkingDirectory
        if ($family -and -not $isOurs) {
            $legacy += [pscustomobject]@{ Name = $f.Name; Desktop = $d; Target = $i.TargetPath; Args = $i.Arguments }
        }
        elseif ($isOurs) {
            $existingOurs += $f.FullName
        }
    }
}

# ---- 2. 创建/覆盖唯一入口 ----
try {
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = 'powershell.exe'
    $link.Arguments = $linkArgs
    $link.WorkingDirectory = $DeployRoot
    $link.Description = 'LadderStock 宿主面板：前置检查 + 按序拉起全部模块（托盘常驻，关窗不停服务）'
    $link.IconLocation = ('{0},{1}' -f $IconPath, $IconIndex)
    $link.Save()
}
catch {
    Write-Host ('[FAIL] 创建桌面图标失败：{0}' -f $_.Exception.Message)
    Write-HostLog ('desktop shortcut: create failed - {0}' -f $_.Exception.Message) -Level Error
    exit 1
}

Write-Host ('[ OK ] 单图标已就位：{0}' -f $linkPath)
Write-Host ('      Target : powershell.exe')
Write-Host ('      Args   : {0}' -f $linkArgs)
Write-Host ('      Icon   : {0},{1}' -f $IconPath, $IconIndex)

# ---- 3. 旧入口清单（只报告，不移除） ----
Write-Host ''
Write-Host '==== 桌面现存 LadderStock 家族旧入口（只报告，本轮不移除）===='
if ($legacy.Count -eq 0) {
    Write-Host '（无）'
}
else {
    $legacy | Format-Table Name, Target, Args -AutoSize -Wrap | Out-String -Width 200 | Write-Host
}
Write-Host '移除旧入口需 supervisor 验收单图标方案通过后进行——届时按本清单单独执行、单独报告。'

Write-Host ''
Write-Host '==== 桌面对版检查（目标：只剩一个 LadderStock 入口）===='
$allOurs = @($existingOurs) + @($linkPath)
$panelCount = @($allOurs | Select-Object -Unique).Count
Write-Host ('panel entry count = {0} (expect 1): {1}' -f $panelCount, (($allOurs | Select-Object -Unique) -join ' ; '))
if ($legacy.Count -gt 0) {
    Write-Host ('legacy family icons = {0} (to be removed after acceptance); panel entries = {1} (unique)' -f $legacy.Count, $panelCount)
}

Write-HostLog ('desktop shortcut: {0} created; legacy family icons {1}' -f $linkPath, $legacy.Count)
Send-HostToast -Title 'LadderStock 单图标已创建' -Body ('桌面唯一入口已就位（{0}），双击即拉全部模块' -f $LinkName)
exit 0
