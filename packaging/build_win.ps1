# QUOT-525: build the self-contained Windows bundle (PyInstaller onedir).
#
# Layout produced:
#   dist/LadderStock/LadderStock.exe   <- install_root = dist/
#   dist/LadderStock/_internal/...     <- bundled CPython + deps (主程序)
#   dist/LadderStock/python/           <- bundled embeddable Python (peer venv 底座)
#
# Double-click LadderStock.exe: starts the board on 127.0.0.1:5950 and opens
# the default browser. No system Python / venv is required on the target PC.
#
# 仓库根 `python/` 是随包资产，原样 collect 进产物；构建环境用临时 venv，
# 不把依赖装进 embeddable 的 `._pth` 隔离目录。
param(
    [string]$PythonExe = "",
    [switch]$NoInstall,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildDir = Join-Path $Root "build\pack-win"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

# 解释器优先级：显式参数 > 构建机系统 python 建的临时 venv。
# `python/` 里的 embeddable CPython 是**随包资产**（打进产物给 peer venv 用），
# 不是构建环境——PyInstaller 需要完整的 site-packages，embeddable 的 `._pth`
# 隔离模式装不全依赖，所以构建一律走临时 venv，随包 python 原样 collect。
if (-not $PythonExe) {
    $venvDir = Join-Path $BuildDir "venv"
    $PythonExe = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $PythonExe)) {
        Write-Host "== create build venv: $venvDir"
        python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
    }
}

if (-not $NoInstall) {
    Write-Host "== install build dependencies"
    & $PythonExe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    # torch 先按 CPU 轮子装：PyPI 默认的 Windows 轮子带 CUDA，会让产物凭空多出
    # 2GB+，而看板只用 CPU 推理。已经是 CPU 版时这一步是 no-op。
    & $PythonExe -m pip install "torch>=2.10,<3" --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw "torch (cpu) install failed" }
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

# 随包 embeddable Python 必须真在产物里：peer venv 靠它，缺了就是「装不了子模块」。
$bundledPy = Join-Path $Root "dist\LadderStock\python\python.exe"
if (-not (Test-Path $bundledPy)) {
    throw "bundled python missing: $bundledPy（仓库根 python/ 是否齐全？）"
}

$sizeMb = [math]::Round(((Get-ChildItem (Join-Path $Root "dist\LadderStock") -Recurse |
    Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "OK: $exe"
Write-Host "bundle size: $sizeMb MB"
Write-Host "smoke test:  powershell -ExecutionPolicy Bypass -File packaging/smoke_win.ps1 -Exe '$exe'"
