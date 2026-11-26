<#
.SYNOPSIS
    Remove the 'LadderStock Single Entry' scheduled task (switch to desktop-launch only).

.DESCRIPTION
    Idempotent: exits without changes if the task does not exist.
    Running LadderStock processes are NOT killed here; stop the service first
    via the tray icon or Invoke-SingleEntry.ps1 -Action stop -Force if needed.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall-scheduled-task.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$task = 'LadderStock Single Entry'

$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    Write-Host "Scheduled task '$task' not found (already removed). Nothing to do."
    exit 0
}

Write-Host "Found scheduled task '$task' (state: $($existing.State)). Removing..."
schtasks.exe /End /TN "$task" 2>$null | Out-Null
schtasks.exe /Delete /TN "$task" /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "Removed. Launch LadderStock from the desktop shortcut (LadderStock.lnk)."
} else {
    Write-Host "Remove failed (exit $LASTEXITCODE). Use Task Scheduler GUI to delete it manually."
    exit $LASTEXITCODE
}