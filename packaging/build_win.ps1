# QUOT-525: build the self-contained Windows bundle (PyInstaller onedir).
#
# Layout produced:
#   dist/LadderStock/LadderStock.exe   <- install_root = dist/
#   dist/LadderStock/_internal/...     <- bundled CPython + deps
#
# Double-click LadderStock.exe: starts the board on 127.0.0.1:5950 and opens
# the default browser. No system Python / venv is required on the target PC.
param(
    [string]$PythonExe = "",
    [switch]$NoInstall,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildDir = Join-Path $Root "build\pack-win"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

if (-not $PythonExe) {
    $venvDir = Join-Path $BuildDir "venv"
    $PythonExe = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $PythonExe)) {
        Write-Host "== create venv: $venvDir"
        python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
    }
}

if (-not $NoInstall) {
    Write-Host "== install build dependencies"
    & $PythonExe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $PythonExe -m pip install -e "${Root}[all]" "pyinstaller>=6.11,<7"
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

if (-not $SkipFrontend) {
    Write-Host "== build frontend (frontend/dist)"
    Push-Location (Join-Path $Root "frontend")
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
    throw "frontend/dist/index.html missing; run without -SkipFrontend first"
}

Write-Host "== run PyInstaller"
& $PythonExe -m PyInstaller `
    --noconfirm --clean `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $BuildDir "work") `
    (Join-Path $PSScriptRoot "LadderStock.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $Root "dist\LadderStock\LadderStock.exe"
if (-not (Test-Path $exe)) { throw "expected artifact missing: $exe" }

$sizeMb = [math]::Round(((Get-ChildItem (Join-Path $Root "dist\LadderStock") -Recurse |
    Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "OK: $exe"
Write-Host "bundle size: $sizeMb MB"
Write-Host "smoke test:  powershell -ExecutionPolicy Bypass -File packaging/smoke_win.ps1 -Exe '$exe'"
