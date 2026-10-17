#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Register (or remove) the nightly POPPYS AI backup in Task Scheduler: every day at 01:30, scripts\backup.py.

.DESCRIPTION
  The task "\POPPYS AI\Nightly backup" runs
      <python> -E -s -X utf8 <app>\scripts\backup.py --data-dir <data> --app-root <app> --dest <data>\backups
  which makes an online SQLite backup, checks it (integrity_check, foreign_key_check, SHA-256), zips it with the
  mapping config and prunes old backups (14 daily, 8 weekly, 12 monthly). One line per run is written to
  <data>\backups\backup.log. If the laptop is off at 01:30 the task runs as soon as it is back on.

  Service install (C:\ProgramData\PoppysAI\current exists): the task runs as LOCAL SERVICE, the same low-rights
  account as the service, and uses the "current" junction, so it follows upgrades by itself.
  Without the service (start.ps1 from a checkout, pass -AppRoot): it runs as you, without storing a password
  (S4U logon), on <AppRoot>\data.

  Safe to run again: the task is replaced. Exit codes: 0 OK, 1 failed.
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".

.PARAMETER At
  Time of day (24 h, local). Default 01:30.
.PARAMETER SecondCopy
  A second folder for a copy of every backup (a second internal drive or an always-connected USB disk).
.PARAMETER IncludeUploads
  Also back up the uploaded buyer files (data\*.upload).
.PARAMETER AppRoot
  Use a checkout run with start.ps1 instead of the installed service.
.PARAMETER RunNow
  Start the task once now and show the result.
.PARAMETER Remove
  Unregister the task (the backups themselves are kept).
#>
[CmdletBinding()]
param(
  [string]$InstallRoot = (Join-Path $env:ProgramData 'PoppysAI'),
  [string]$At = '01:30',
  [string]$SecondCopy = '',
  [switch]$IncludeUploads,
  [string]$AppRoot = '',
  [switch]$RunNow,
  [switch]$Remove
)
$ErrorActionPreference = 'Stop'

$TaskPath = '\POPPYS AI\'
$TaskName = 'Nightly backup'
$LocalServiceSid = 'S-1-5-19'

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK: $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Write-Warn([string]$Text) { Write-Host "   WARNING: $Text" -ForegroundColor Yellow }
function Stop-Task([string]$Text) { Write-Host ''; Write-Host "FAILED: $Text" -ForegroundColor Red; exit 1 }

function Get-ReleasePython([string]$Release) {
  foreach ($candidate in @('python\python.exe', '.venv\Scripts\python.exe')) {
    $path = Join-Path $Release $candidate
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
  }
  return ''
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

function Get-PoppysTask { return (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue) }

# ---------------------------------------------------------------------------------------------------------------
if ($Remove) {
  if (Get-PoppysTask) {
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
    Write-Ok "Removed the scheduled task $TaskPath$TaskName. Existing backups are kept."
  } else { Write-Note 'The nightly backup task is not registered; nothing to remove.' }
  exit 0
}

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$serviceMode = (-not $AppRoot) -and (Test-Path -LiteralPath (Join-Path $InstallRoot 'current\backend\main.py'))
if ($serviceMode) {
  $AppRoot = Join-Path $InstallRoot 'current'          # the junction: the task follows upgrades
  $DataDir = Join-Path $InstallRoot 'data'
} else {
  if (-not $AppRoot) { $AppRoot = Join-Path $PSScriptRoot '..\..' }
  $AppRoot = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $AppRoot).ProviderPath).TrimEnd('\')
  $DataDir = Join-Path $AppRoot 'data'
}
$python = Get-ReleasePython $AppRoot
$backupPy = Join-Path $AppRoot 'scripts\backup.py'
$Dest = Join-Path $DataDir 'backups'
Write-Host 'POPPYS AI - nightly backup task' -ForegroundColor Green
if (-not $python) { Stop-Task "No Python in $AppRoot (python\python.exe or .venv\Scripts\python.exe). Run setup.ps1 or install-service.ps1 first." }
if (-not (Test-Path -LiteralPath $backupPy)) { Stop-Task "$backupPy is missing: this release is older than the backup script. Upgrade first." }
if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'poppys.sqlite3'))) { Write-Warn "There is no database in $DataDir yet; the backup will report that until POPPYS AI has been used once." }
try { $time = [datetime]::ParseExact($At, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture) } catch { Stop-Task "-At must look like 01:30 (24-hour clock), not '$At'." }

Write-Step '1. What the task runs'
$argList = @('-E', '-s', '-X', 'utf8', "`"$backupPy`"", '--data-dir', "`"$DataDir`"", '--app-root', "`"$AppRoot`"", '--dest', "`"$Dest`"")
if ($SecondCopy) { $argList += @('--copy-to', "`"$SecondCopy`"") }
if ($IncludeUploads) { $argList += '--include-uploads' }
$argString = $argList -join ' '
Write-Note "Program:   $python"
Write-Note "Arguments: $argString"
if ($serviceMode) { Write-Note 'Account:   LOCAL SERVICE (same as the PoppysAI service)' } else { Write-Note "Account:   $env:USERDOMAIN\$env:USERNAME (no password stored)" }

if ($SecondCopy) {
  New-Item -ItemType Directory -Force -Path $SecondCopy | Out-Null
  if ($serviceMode) {
    # LOCAL SERVICE must be able to write there. FAT/exFAT USB disks have no permissions at all, so a failure is only a warning.
    $code = Invoke-Native 'icacls.exe' @($SecondCopy, '/grant', "*${LocalServiceSid}:(OI)(CI)M", '/Q')
    if ($code -ne 0) { Write-Warn "Could not give LOCAL SERVICE write access to $SecondCopy (normal on a FAT/exFAT USB disk)." }
  }
}

Write-Step "2. Registering $TaskPath$TaskName (daily at $At)"
$action = New-ScheduledTaskAction -Execute $python -Argument $argString -WorkingDirectory $AppRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
$description = 'POPPYS AI: verified online backup of data\poppys.sqlite3 (scripts\backup.py). Log: ' + (Join-Path $Dest 'backup.log')
$registered = $false
if ($serviceMode) {
  # The SID works on every Windows language; the account name is the fallback.
  foreach ($user in @($LocalServiceSid, 'NT AUTHORITY\LOCAL SERVICE')) {
    try {
      $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType ServiceAccount -RunLevel Limited
      Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description $description -Force | Out-Null
      $registered = $true
      break
    } catch { Write-Note "Registering as '$user' did not work: $($_.Exception.Message)" }
  }
} else {
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
  Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description $description -Force | Out-Null
  $registered = $true
}
if (-not $registered) { Stop-Task 'Task Scheduler refused the task (see above).' }
Write-Ok "Registered. Next run: $((Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName).NextRunTime)"

if ($RunNow) {
  Write-Step '3. Running the backup once now'
  Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
  $deadline = (Get-Date).AddMinutes(10)
  Start-Sleep -Seconds 2
  while ((Get-Date) -lt $deadline -and (Get-PoppysTask).State -eq 'Running') { Start-Sleep -Seconds 2 }
  $info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
  $log = Join-Path $Dest 'backup.log'
  if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 3 | ForEach-Object { Write-Note $_ } }
  switch ($info.LastTaskResult) {
    0 { Write-Ok 'The backup finished: result 0 (OK).' }
    4 { Write-Warn 'The backup is fine, but the copy to the second location failed (result 4). Is the drive connected?' }
    default { Stop-Task "The backup task ended with result $($info.LastTaskResult) (1 = backup failed, 2 = setup problem). Read $log." }
  }
}
Write-Host ''
Write-Host "Nightly backup registered: every day at $At into $Dest" -ForegroundColor Green
Write-Host "Check it: Get-ScheduledTaskInfo -TaskPath '$TaskPath' -TaskName '$TaskName'  (LastTaskResult 0 = OK), and $Dest\backup.log"
exit 0
