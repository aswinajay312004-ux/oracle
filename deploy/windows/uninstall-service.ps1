#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Remove the PoppysAI Windows service registration. Data, backups, logs, releases and settings are kept.

.DESCRIPTION
  Stops the service (WinSW waits up to 30 s for open requests to finish), unregisters it with PoppysAI.exe
  (or sc.exe when PoppysAI.exe is missing) and waits until Windows has removed it. With -RemoveBackupTask the
  nightly backup task is removed too; without it the task keeps backing up data\ every night.
  Nothing under C:\ProgramData\PoppysAI is deleted. Safe to run again.
  Exit codes: 0 OK, 1 failed.
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".
#>
[CmdletBinding()]
param(
  [string]$InstallRoot = (Join-Path $env:ProgramData 'PoppysAI'),
  [switch]$RemoveBackupTask
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'PoppysAI'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$LogDir = Join-Path $InstallRoot 'logs'
$WinSWExe = Join-Path $InstallRoot 'PoppysAI.exe'

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK: $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Stop-Uninstall([string]$Text) { Write-Host ''; Write-Host "FAILED: $Text" -ForegroundColor Red; exit 1 }
function Write-DeployHistory([string]$Text) {
  try {
    if (Test-Path -LiteralPath $LogDir) {
      Add-Content -LiteralPath (Join-Path $LogDir 'deploy-history.log') -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Text) -Encoding ASCII
    }
  } catch { Write-Verbose 'deploy-history.log could not be written' }
}

function Invoke-Native([string]$FilePath, [string[]]$ArgumentList) {
  # Runs a program, shows its output indented and returns its exit code ('Continue': see install-service.ps1).
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $FilePath @ArgumentList 2>&1 | ForEach-Object { Write-Host ('   ' + $_) }
    return $LASTEXITCODE
  } finally { $ErrorActionPreference = $saved }
}

function Wait-ServiceGone([int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

# ---------------------------------------------------------------------------------------------------------------
Write-Host 'POPPYS AI - remove the Windows service' -ForegroundColor Green
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $service) {
  Write-Note 'The PoppysAI service is not installed; nothing to remove.'
} else {
  Write-Step '1. Stopping the service'
  if ($service.Status -ne 'Stopped') { Stop-Service -Name $ServiceName -Force }
  Write-Ok 'Stopped.'
  Write-Step '2. Removing the service registration'
  if (Test-Path -LiteralPath $WinSWExe) { $code = Invoke-Native $WinSWExe @('uninstall') } else { $code = Invoke-Native 'sc.exe' @('delete', $ServiceName) }
  if ($code -ne 0) { Stop-Uninstall "Removing the service failed (exit code $code)." }
  if (-not (Wait-ServiceGone 30)) { Stop-Uninstall 'Windows has marked the service for deletion but not removed it yet. Close the Services window and Task Manager; it then disappears (or after a restart).' }
  Write-DeployHistory 'UNINSTALL service removed'
  Write-Ok 'The PoppysAI service is removed.'
}

$task = Get-ScheduledTask -TaskPath '\POPPYS AI\' -TaskName 'Nightly backup' -ErrorAction SilentlyContinue
if ($task -and $RemoveBackupTask) {
  Unregister-ScheduledTask -TaskPath '\POPPYS AI\' -TaskName 'Nightly backup' -Confirm:$false
  Write-Ok 'Removed the nightly backup task.'
} elseif ($task) {
  Write-Note 'The nightly backup task is still registered and keeps backing up the data folder. To remove it: backup-task.ps1 -Remove'
}

Write-Host ''
Write-Host 'Kept (nothing was deleted):' -ForegroundColor Green
foreach ($part in @('data', 'data\backups', 'logs', 'config', 'releases')) {
  $path = Join-Path $InstallRoot $part
  if (Test-Path -LiteralPath $path) { Write-Note $path }
}
Write-Note 'To use POPPYS AI without the service again, see docs\RUNBOOK.md, "Going back to start.ps1".'
exit 0
