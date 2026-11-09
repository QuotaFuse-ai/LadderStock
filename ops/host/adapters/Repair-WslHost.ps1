<#
.SYNOPSIS
    QUOT-483 / D1：WSL 侧 /etc/wsl.conf 与 /usr/local/bin/wsl-boot.sh 幂等修复。

.DESCRIPTION
    历史上这两个文件出现过 0 字节事故（FC 不自启）。本脚本不是"从零写"，
    而是"校验 + 幂等安装"：读取 WSL 内当前内容与内置模板逐字节比对——
      * 一致    → 只报告 ok，不动
      * 不一致  → 先备份为同名 .bak-YYYYMMDD，再写入模板，boot 脚本补 chmod 755
    默认发行版动态探测（不写死 Ubuntu-24.04），写入以 -u root 执行。

    模板内容即当前线上已被验证过的版本（2026-11-08 人工确认可拉起 FC 全栈）：
      /etc/wsl.conf      ：systemd=false；boot command=/usr/local/bin/wsl-boot.sh
      wsl-boot.sh        ：等 dockerd → FinanceCoffer/deploy compose up/docker start → 探 8100

    安装后需要一次 wsl --shutdown 才生效（/etc/wsl.conf 只在发行版启动时读）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Repair-WslHost.ps1 -Action Check
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Repair-WslHost.ps1 -Action Install
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Install')]
    [string]$Action = 'Check'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\HostCommon.ps1"

$WslConfExpected = @'
# /etc/wsl.conf — QUOT-483 修复（2026-11-08）
# 部署前会先备份现文件为 /etc/wsl.conf.bak-YYYYMMDD（见 Repair-WslHost.ps1 -Action Install）。
# systemd 保持 false：启用会改变整个发行版的服务模型，并与 Windows 侧 MulticaDockerd
# supervisor 形成双重 dockerd 管理者，风险大于收益；FC 的自启由 boot command 承载。
[boot]
systemd = false
command = /usr/local/bin/wsl-boot.sh

[interop]
enabled = true
appendWindowsPath = true
'@

$WslBootExpected = @'
#!/bin/sh
# wsl-boot.sh — WSL 启动时拉起 FinanceCoffer 全栈（QUOT-483 / D1）
# 部署位置：/usr/local/bin/wsl-boot.sh，由 /etc/wsl.conf [boot] command 触发（root）。
# dockerd 的主责是 Windows 侧 MulticaDockerd 计划任务（supervisor loop）；
# 本脚本只做兜底等待，不抢着再起一个 dockerd。
LOG=/var/log/wsl-boot.log
{
  echo "=== wsl-boot $(date -Is) pid=$$ ==="
  (
    DEPLOY_DIR=/mnt/d/workplace/FinanceCoffer/deploy
    i=0
    until docker info >/dev/null 2>&1; do
      i=$((i+1))
      if [ "$i" -ge 150 ]; then
        echo "wsl-boot: dockerd not ready after 150s, giving up"
        exit 1
      fi
      sleep 1
    done
    echo "wsl-boot: dockerd ready after ${i}s"
    cd "$DEPLOY_DIR" || { echo "wsl-boot: no $DEPLOY_DIR"; exit 1; }
    if [ -f "$DEPLOY_DIR/.env" ]; then
      docker compose --env-file .env -f compose.yaml up -d
      rc=$?
    else
      ALL=$(docker ps -aq --filter "label=com.docker.compose.project=financecoffer")
      if [ -z "$ALL" ]; then
        echo "wsl-boot: no financecoffer container and no .env; nothing to start"
        exit 1
      fi
      docker start $ALL
      rc=$?
    fi
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8100/health 2>/dev/null)
    echo "wsl-boot: compose rc=$rc, 8100 health=$code, done $(date -Is)"
  ) >>"$LOG" 2>&1 &
} >>"$LOG" 2>&1
'@

$distro = Get-WslDefaultDistro
if (-not $distro) {
    Write-Host '[FAIL] 探测不到 WSL 默认发行版（wsl 未安装/未初始化？）'
    Write-HostLog 'wsl repair: no default distro' -Level Error
    exit 1
}
Write-Host ("WSL 默认发行版: {0}" -f $distro)

$stamp = Get-Date -Format 'yyyyMMdd'
$failed = 0

function Invoke-Wsl {
    param([string]$Command)
    @(& wsl.exe -d $distro -u root -e bash -c $Command 2>$null) -replace '\x00', ''
}

# wsl.exe 输出经 PS 按行拆分且行尾残留 `r，中文注释经控制台解码会失真——
# 故以 sha256（hex，纯 ASCII）比较模板与线上文件，绕开编码与换行差异。
function Get-TemplateHash {
    param([string]$Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes((Get-NormalizedTemplate $Text))))).Replace('-', '').ToLower()
    } finally { $sha.Dispose() }
}

# 统一为 LF + 单个尾随换行（here-string 不含末尾换行，Linux 文件通常以 \n 结尾）。
function Get-NormalizedTemplate {
    param([string]$Text)
    $Text.Replace("`r`n", "`n").TrimEnd("`n") + "`n"
}

function Get-WslFileHash {
    param([string]$WslPath)
    # @(...)：Invoke-Wsl 单行输出会被 -replace 降维为标量 String，$out[0] 会变成字符索引。
    $out = @(Invoke-Wsl ('sha256sum {0} 2>/dev/null' -f $WslPath))
    if ($out.Count -gt 0 -and $out[0]) { $out[0].Split(' ')[0].ToLower() }
}

foreach ($item in @(
    @{ Path = '/etc/wsl.conf'; Expected = $WslConfExpected; Mode = '' }
    @{ Path = '/usr/local/bin/wsl-boot.sh'; Expected = $WslBootExpected; Mode = '755' }
)) {
    $expHash = Get-TemplateHash $item.Expected
    $curHash = Get-WslFileHash $item.Path
    if ($curHash -eq $expHash) {
        Write-Host ("[ OK ] {0} 与模板一致，未改动" -f $item.Path)
        continue
    }

    if ($Action -eq 'Check') {
        if ($curHash) {
            Write-Host ("[WARN] {0} 与模板不一致（-Action Install 才会修复）" -f $item.Path)
        } else {
            Write-Host ("[FAIL] {0} 读取为空或不存在" -f $item.Path)
        }
        $failed++
        continue
    }

    # Install：先备份现文件（含 0 字节事故现场），再经 /mnt 中转写入模板。
    # 必须无 BOM 写入：BOM 混进 wsl-boot.sh 会毁掉 shebang。
    $tmp = Join-Path $env:TEMP ('wsl-fix-{0}' -f [Guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($tmp, (Get-NormalizedTemplate $item.Expected), (New-Object Text.UTF8Encoding $false))
    $targetBak = ('{0}.bak-{1}' -f $item.Path, $stamp)
    $null = Invoke-Wsl ("cp -a {0} {1} 2>/dev/null" -f $item.Path, $targetBak)
    $wslTmp = ('/mnt/' + $tmp.Substring(0, 1).ToLower() + $tmp.Substring(2).Replace('\', '/'))
    $null = Invoke-Wsl ("mkdir -p {0}" -f (Split-Path $item.Path))
    $copy = Invoke-Wsl ("cp {0} {1}" -f $wslTmp, $item.Path)
    if ($copy) { Write-Host $copy }
    if ($item.Mode) { $null = Invoke-Wsl ("chmod {0} {1}" -f $item.Mode, $item.Path) }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue

    if ((Get-WslFileHash $item.Path) -eq $expHash) {
        Write-Host ("[ OK ] {0} 已写入模板（备份 {1}）" -f $item.Path, $targetBak)
        Write-HostLog ("wsl repair: installed {0} (bak {1})" -f $item.Path, $targetBak)
    }
    else {
        Write-Host ("[FAIL] {0} 写入后校验不一致" -f $item.Path)
        Write-HostLog ("wsl repair: verify failed {0}" -f $item.Path) -Level Error
        $failed++
    }
}

if ($Action -eq 'Install') {
    Write-Host ''
    Write-Host '注意：/etc/wsl.conf 只在发行版启动时读取，改动需要 wsl --shutdown 后重启 WSL 才生效。'
    Write-Host '（wsl --shutdown 会中断 WSL 内全部服务十几秒——避开盘中时段执行）'
    Send-HostToast -Title 'WSL 宿主修复' -Body ('/etc/wsl.conf 与 wsl-boot.sh 已就位（{0}），重启 WSL 后生效' -f $stamp)
}

if ($failed -gt 0) { exit 1 }
exit 0
