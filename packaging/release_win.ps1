# QUOT-549: zip the onedir bundle into a versioned, distributable artifact.
#
# Output: LadderStock-<version>-win-x64.zip containing LadderStock/ (onedir)
# and README-dist.txt. Version comes from the git tag (v*) or falls back to
# src/ladderstock/__init__.py __version__.
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

$ZipName = "LadderStock-$Version-win-x64.zip"
$ZipPath = Join-Path $OutDir $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "== zip: $ZipPath"
Compress-Archive -Path $Bundle -DestinationPath $ZipPath -CompressionLevel Optimal

$sizeMb = [math]::Round(((Get-Item $ZipPath).Length / 1MB), 1)
Write-Host "OK: $ZipPath ($sizeMb MB, version $Version)"
Write-Host "##vso[task.setvariable variable=ldst_version]$Version"
