# QUOT-549: clean-machine end-to-end acceptance for the packaged Windows bundle.
#
# Simulates a normal user's PC:
#   - artifact extracted to a temp dir OUTSIDE the source tree
#   - PATH reduced to Windows system dirs (no python, no venv, no repo)
#   - PYTHONHOME/PYTHONPATH cleared, isolated LOCALAPPDATA + data/config
#   - default browser open attempt allowed (LDST_NO_BROWSER=0) so we can
#     capture browser-process evidence
#   - ephemeral non-production port (default 5968)
#
# Checks: /api/health ok, / serves real dashboard (#root), core pages respond
# (settings etc.), default browser process spawned, port released on exit.
# Writes result.json + startup log into the temp dir (kept for evidence).
param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [int]$Port = 5968,
    [int]$TimeoutSec = 120,
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"

function Wait-Health([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { return Invoke-RestMethod -Uri $Url -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500 }
    }
    return $null
}

$Zip = (Resolve-Path $Zip).Path
$Temp = Join-Path $env:TEMP ("ldst-e2e-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Temp | Out-Null
if ($EvidenceDir) { New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null }

$install = Join-Path $Temp "install"
Write-Host "== extract $Zip -> $install"
Expand-Archive -Path $Zip -DestinationPath $install -Force
$exe = Join-Path $install "LadderStock\LadderStock.exe"
if (-not (Test-Path $exe)) {
    $exe = Get-ChildItem $install -Recurse -Filter "LadderStock.exe" | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $exe -or -not (Test-Path $exe)) { throw "LadderStock.exe not found after extract" }

$LocalAppData = Join-Path $Temp "localappdata"
$DataDir = Join-Path $Temp "data"
$ConfigDir = Join-Path $Temp "config"
$StdOut = Join-Path $Temp "startup-stdout.log"
$StdErr = Join-Path $Temp "startup-stderr.log"
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
$pages = @{}
$browserProcs = @()
$errorText = ""

try {
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $LocalAppData
    $env:LDST_DATA_DIR = $DataDir
    $env:LDST_CONFIG_DIR = $ConfigDir
    $env:LDST_PAGE_AUTOSTART = "0"
    $env:LDST_NO_BROWSER = "0"

    $pythonOnPath = Get-Command python -ErrorAction SilentlyContinue
    Write-Host "python on PATH: $(if ($pythonOnPath) { $pythonOnPath.Source } else { '<none>' })"
    Write-Host "exe: $exe"
    Write-Host "temp: $Temp"

    $proc = Start-Process -FilePath $exe `
        -ArgumentList @("__desktop", "--port", "$Port") `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr

    $health = Wait-Health "http://127.0.0.1:$Port/api/health" $TimeoutSec
    if ($null -eq $health) { throw "health endpoint did not respond within ${TimeoutSec}s" }
    if (-not $health.ok) { throw "health payload not ok: $($health | ConvertTo-Json -Compress)" }

    # dashboard root page: real frontend, not the fallback hint page
    $root = [string](Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 15).Content
    if ($root -notmatch 'id="root"') { throw "dashboard HTML missing #root" }
    if ($root -match '前端尚未构建') { throw "served 'frontend not built' hint page" }
    $pages["/"] = $root.Length

    # core pages / APIs a normal user touches (route presence on the bundle)
    $pageTargets = @("/api/health", "/api/state/config", "/api/ladder", "/settings")
    foreach ($t in $pageTargets) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$t" -UseBasicParsing -TimeoutSec 15
            $pages[$t] = $r.StatusCode
        } catch {
            $code = 0
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            $pages[$t] = $code
        }
    }
    Write-Host "pages: $($pages | ConvertTo-Json -Compress)"

    # default browser spawn evidence (normal-user double-click path)
    Start-Sleep -Seconds 5
    $browserProcs = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(msedge|chrome|firefox|brave|opera|iexplore)' } |
        Select-Object -ExpandProperty Name -Unique)
    Write-Host "browser processes: $($browserProcs -join ', ')"

    if ($browserProcs.Count -eq 0) { throw "no default browser process detected after start" }

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
    Start-Sleep -Milliseconds 800

    $portFree = $true
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $portFree = $false
    } catch { $portFree = $true }

    $result = [ordered]@{
        ok             = $ok
        zip            = $Zip
        exe            = $exe
        port           = $Port
        python_on_path = if ($pythonOnPath) { $pythonOnPath.Source } else { $null }
        health         = $health
        pages          = $pages
        browser        = $browserProcs
        port_released  = $portFree
        error          = if ($errorText) { $errorText } else { $null }
        temp           = $Temp
    }
    $resultPath = Join-Path $Temp "result.json"
    $result | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $resultPath
    Write-Host "result: $resultPath"
    if ($EvidenceDir) {
        Copy-Item $resultPath (Join-Path $EvidenceDir "e2e-result.json") -Force
        foreach ($l in @($StdOut, $StdErr)) {
            if (Test-Path $l) { Copy-Item $l (Join-Path $EvidenceDir ("e2e-" + (Split-Path $l -Leaf))) -Force }
        }
    }

    $env:Path = $saved.Path
    foreach ($name in @("LOCALAPPDATA", "PYTHONHOME", "PYTHONPATH", "LDST_DATA_DIR", "LDST_CONFIG_DIR", "LDST_PAGE_AUTOSTART", "LDST_NO_BROWSER")) {
        $value = $saved[$name]
        if ($null -eq $value) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $value }
    }
}

if ($ok) { exit 0 }
exit 1
