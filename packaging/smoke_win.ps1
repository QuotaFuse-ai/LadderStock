# QUOT-525: clean-machine smoke test for the packaged Windows bundle.
#
# Simulates a PC without Python:
#   - PATH reduced to Windows system directories (no python.exe on PATH)
#   - PYTHONHOME / PYTHONPATH cleared
#   - isolated LOCALAPPDATA + data/config dirs
#   - page autostart disabled so no real peer/watch processes are touched
#   - non-production port (default 5965)
#
# Exit code 0 = health OK + dashboard HTML served.
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$Port = 5965,
    [int]$TimeoutSec = 90,
    [switch]$KeepTemp
)

$ErrorActionPreference = "Stop"

function Wait-Health([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 2
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $null
}

$Exe = (Resolve-Path $Exe).Path
$Temp = Join-Path $env:TEMP ("ldst-pack-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Temp | Out-Null
$LocalAppData = Join-Path $Temp "localappdata"
$DataDir = Join-Path $Temp "data"
$ConfigDir = Join-Path $Temp "config"
$StdOut = Join-Path $Temp "stdout.log"
$StdErr = Join-Path $Temp "stderr.log"
New-Item -ItemType Directory -Force -Path $LocalAppData, $DataDir, $ConfigDir | Out-Null

$saved = @{
    Path         = $env:Path
    LOCALAPPDATA = $env:LOCALAPPDATA
    PYTHONHOME   = $env:PYTHONHOME
    PYTHONPATH   = $env:PYTHONPATH
    LDST_DATA_DIR       = $env:LDST_DATA_DIR
    LDST_CONFIG_DIR     = $env:LDST_CONFIG_DIR
    LDST_PAGE_AUTOSTART = $env:LDST_PAGE_AUTOSTART
    LDST_NO_BROWSER     = $env:LDST_NO_BROWSER
}

$proc = $null
$ok = $false
$health = $null
$html = ""
$errorText = ""

try {
    # --- "no Python" environment -------------------------------------------
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $LocalAppData
    $env:LDST_DATA_DIR = $DataDir
    $env:LDST_CONFIG_DIR = $ConfigDir
    $env:LDST_PAGE_AUTOSTART = "0"
    $env:LDST_NO_BROWSER = "1"

    $pythonOnPath = Get-Command python -ErrorAction SilentlyContinue
    Write-Host "python on PATH: $(if ($pythonOnPath) { $pythonOnPath.Source } else { '<none>' })"
    Write-Host "exe: $Exe"
    Write-Host "temp: $Temp"

    $proc = Start-Process -FilePath $Exe `
        -ArgumentList @("__desktop", "--port", "$Port") `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr

    $health = Wait-Health "http://127.0.0.1:$Port/api/health" $TimeoutSec
    if ($null -eq $health) {
        throw "health endpoint did not respond within ${TimeoutSec}s (see $StdErr)"
    }

    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10
    $html = [string]$response.Content

    if (-not $health.ok) { throw "health payload not ok: $($health | ConvertTo-Json -Compress)" }
    if ($html -notmatch 'id="root"') { throw "dashboard HTML missing #root (frontend not bundled?)" }
    if ($html -match '前端尚未构建') { throw "dashboard served the 'frontend not built' hint page" }

    $tree = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $proc.Id })
    Write-Host "process: $($tree[0].Name) pid=$($proc.Id)"
    Write-Host "health: ok name=$($health.name) version=$($health.version) data_dir=$($health.data_dir)"
    Write-Host "html: $($html.Length) bytes, #root present"
    $ok = $true
}
catch {
    $errorText = $_.Exception.Message
    Write-Host "FAILED: $errorText"
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $proc.WaitForExit(5000) | Out-Null
    }
    Start-Sleep -Milliseconds 500

    $result = [ordered]@{
        ok         = $ok
        exe        = $Exe
        port       = $Port
        python_on_path = if ($pythonOnPath) { $pythonOnPath.Source } else { $null }
        health     = $health
        html_bytes = $html.Length
        dashboard  = ($html -match 'id="root"')
        error      = if ($errorText) { $errorText } else { $null }
        stdout     = $StdOut
        stderr     = $StdErr
        temp       = $Temp
    }
    $result | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $Temp "result.json")
    Write-Host "result: $(Join-Path $Temp 'result.json')"

    $env:Path = $saved.Path
    if ($null -eq $saved.LOCALAPPDATA) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue }
    else { $env:LOCALAPPDATA = $saved.LOCALAPPDATA }
    if ($null -eq $saved.PYTHONHOME) { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }
    else { $env:PYTHONHOME = $saved.PYTHONHOME }
    if ($null -eq $saved.PYTHONPATH) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    else { $env:PYTHONPATH = $saved.PYTHONPATH }
    foreach ($name in @("LDST_DATA_DIR", "LDST_CONFIG_DIR", "LDST_PAGE_AUTOSTART", "LDST_NO_BROWSER")) {
        $value = $saved[$name]
        if ($null -eq $value) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $value }
    }

    if (-not $KeepTemp -and $ok) {
        Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
    }
}

if ($ok) { exit 0 }
exit 1
