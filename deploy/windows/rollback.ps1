#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Switch the POPPYS AI service to another installed release: by default back to the one that was active
  before the last upgrade. Optionally put a database backup back at the same time.

.DESCRIPTION
  Steps: pause restart-after-crash -> stop the service -> check that the release can open the database ->
  (only with -RestoreDatabase, or when the database is too new for it) restore that backup into data\ ->
  point the "current" junction at the release -> restart-after-crash on again -> start -> wait up to 60 s for
  http://127.0.0.1:8008 to answer (/readyz when the app has it, otherwise the home page and /api/session).

  A newer release may change the database (a newer schema, PRAGMA user_version). An older release refuses to
  start on such a database, so this script compares the two before switching: when the database is newer than
  the release, it stops (nothing changed) and prints the command with -RestoreDatabase and the backup made
  before that upgrade. upgrade.ps1 passes that backup as -PreUpgradeBackup, so its automatic rollback restores
  it by itself when (and only when) the failed release had already changed the database.

  Nothing is deleted: every release stays in releases\, and a restored database moves the previous files aside
  (data\poppys.sqlite3.before-restore-<time>; backup.py puts them back if the restore fails half-way).

  Exit codes: 0 OK, 1 failed (read the message; the service may be stopped), 3 nothing to do.
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".

.PARAMETER To
  Release folder name in releases\ (see -List). Default: the release named in previous.txt.
.PARAMETER RestoreDatabase
  A backup .zip (for example data\backups\pre-upgrade\poppys-backup-...zip) to restore while the service is stopped.
.PARAMETER PreUpgradeBackup
  Used by upgrade.ps1: the backup made before the upgrade. It is restored only when the database turns out to be
  newer than release -To can open.
.PARAMETER List
  Show the installed releases and exit.
.PARAMETER Action
  'rollback' (default) or 'upgrade' (used by upgrade.ps1; changes only the wording and the history line).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File C:\ProgramData\PoppysAI\current\deploy\windows\rollback.ps1 -List
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File C:\ProgramData\PoppysAI\current\deploy\windows\rollback.ps1
#>
[CmdletBinding()]
param(
  [string]$To = '',
  [string]$InstallRoot = (Join-Path $env:ProgramData 'PoppysAI'),
  [string]$RestoreDatabase = '',
  [string]$PreUpgradeBackup = '',
  [int]$ReadySeconds = 60,
  [string]$Reason = 'manual',
  [ValidateSet('rollback', 'upgrade')][string]$Action = 'rollback',
  [switch]$List
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'PoppysAI'
$Port = 8008
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$ReleasesDir = Join-Path $InstallRoot 'releases'
$DataDir = Join-Path $InstallRoot 'data'
$LogDir = Join-Path $InstallRoot 'logs'
$ConfigDir = Join-Path $InstallRoot 'config'
$LiveDb = Join-Path $DataDir 'poppys.sqlite3'
$CurrentLink = Join-Path $InstallRoot 'current'
$PreviousFile = Join-Path $InstallRoot 'previous.txt'
$WinSWExe = Join-Path $InstallRoot 'PoppysAI.exe'
# Well-known SIDs, so the permissions work on any Windows language.
$Admins = '*S-1-5-32-544'; $System = '*S-1-5-18'; $LocalService = '*S-1-5-19'
$Word = 'Rollback'
if ($Action -eq 'upgrade') { $Word = 'Switch' }
$script:RecoveryPaused = $false

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK: $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Write-Warn([string]$Text) { Write-Host "   WARNING: $Text" -ForegroundColor Yellow }
function Write-DeployHistory([string]$Text) {
  try {
    if (Test-Path -LiteralPath $LogDir) {
      Add-Content -LiteralPath (Join-Path $LogDir 'deploy-history.log') -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Text) -Encoding ASCII
    }
  } catch { Write-Verbose 'deploy-history.log could not be written' }
}
function Stop-Switch([string]$Text, [int]$Code = 1) {
  Resume-ServiceRecovery
  Write-Host ''; Write-Host "FAILED: $Text" -ForegroundColor Red
  Write-DeployHistory "$($Action.ToUpper()) FAILED to $To ($Reason): $Text"
  exit $Code
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

function Get-JunctionTarget([string]$Path) {
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if (-not $item -or $item.LinkType -ne 'Junction') { return '' }
  return [string](@($item.Target)[0])
}

function Set-Junction([string]$Path, [string]$Target) {
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if ($item) {
    if ($item.LinkType -ne 'Junction') { throw "$Path exists and is not a junction. It was not changed." }
    [System.IO.Directory]::Delete($Path)    # removes only the link, never the folder it points to
  }
  New-Item -ItemType Junction -Path $Path -Target $Target | Out-Null
}

function Resolve-RealPath([string]$Path) {
  # A path through the "current" junction, as the release it points to now (this script switches the junction).
  $full = [System.IO.Path]::GetFullPath($Path)
  $prefix = $CurrentLink + '\'
  if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    $real = Get-JunctionTarget $CurrentLink
    if ($real) { return (Join-Path $real $full.Substring($prefix.Length)) }
  }
  return $full
}

function Get-ReleasePython([string]$Release) {
  foreach ($candidate in @('python\python.exe', '.venv\Scripts\python.exe')) {
    $path = Join-Path $Release $candidate
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
  }
  return ''
}

function Test-PortOpen([int]$Number) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $task = $client.ConnectAsync('127.0.0.1', $Number)
    if ($task.Wait(1000)) { return $client.Connected }
    return $false
  } catch { return $false } finally { $client.Close() }
}

function Wait-PortFree([int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (-not (Test-PortOpen $Port)) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Get-HttpStatus([string]$Url) {
  # Status code of a GET, 0 when nothing answers. No proxy: the app listens on loopback only.
  try {
    $request = [System.Net.WebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = 5000
    $request.ReadWriteTimeout = 5000
    $request.AllowAutoRedirect = $false
    $response = $request.GetResponse()
    try { return [int]$response.StatusCode } finally { $response.Close() }
  } catch {
    $e = $_.Exception
    while ($e -and -not ($e -is [System.Net.WebException])) { $e = $e.InnerException }
    if ($e -and $e.Response) {
      $code = [int]$e.Response.StatusCode
      $e.Response.Close()
      return $code
    }
    return 0
  }
}

function Wait-PoppysReady([int]$Seconds) {
  # /readyz when this app version has it (200 = pass or warn); otherwise the home page must answer 200 and
  # /api/session (which reads the users table) must answer 200 too.
  $base = "http://127.0.0.1:$Port"
  $deadline = (Get-Date).AddSeconds($Seconds)
  $useReadyz = $true
  while ((Get-Date) -lt $deadline) {
    if ($useReadyz) {
      $code = Get-HttpStatus "$base/readyz"
      if ($code -eq 200) { return '/readyz' }
      if ($code -eq 404) { $useReadyz = $false; continue }
    } else {
      if ((Get-HttpStatus "$base/") -eq 200) {
        $session = Get-HttpStatus "$base/api/session"
        if ($session -eq 200 -or $session -eq 404) { return 'the home page' }
      }
    }
    Start-Sleep -Seconds 2
  }
  return ''
}

function Show-LogTail([int]$Lines = 25) {
  foreach ($name in @('PoppysAI.err.log', 'PoppysAI.wrapper.log')) {
    $file = Join-Path $LogDir $name
    if (Test-Path -LiteralPath $file) {
      Write-Host "   --- last lines of $file ---"
      Get-Content -LiteralPath $file -Tail $Lines | ForEach-Object { Write-Host "   $_" }
    }
  }
}

function Write-ServiceXml([string]$Release, [string]$Target) {
  # Same rendering as install-service.ps1; returns $true when PoppysAI.xml changed.
  $template = Join-Path $Release 'deploy\windows\PoppysAI.xml'
  if (-not (Test-Path -LiteralPath $template)) { return $false }
  $text = [System.IO.File]::ReadAllText($template)
  if (Test-Path -LiteralPath (Join-Path $Release 'python\python.exe')) {
    $text = $text.Replace('%BASE%\current\.venv\Scripts\python.exe', '%BASE%\current\python\python.exe')
  }
  $changed = $true
  if (Test-Path -LiteralPath $Target) { $changed = ([System.IO.File]::ReadAllText($Target) -ne $text) }
  if ($changed) { [System.IO.File]::WriteAllText($Target, $text, (New-Object System.Text.UTF8Encoding($false))) }
  return $changed
}

function Wait-ServiceGone([int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function ConvertTo-Millisecond([string]$Text) {
  # A WinSW 2.12 time span ("10 sec", "1 hour", "30sec"; no unit = milliseconds) in milliseconds; -1 if unreadable.
  if ($Text -notmatch '^\s*(\d+)\s*([A-Za-z]*)\s*$') { return -1 }
  $number = [int64]$Matches[1]
  $unit = $Matches[2].ToLowerInvariant()
  if ($unit -eq '' -or $unit -eq 'ms') { return $number }
  if ($unit -in @('sec', 'secs', 'second', 'seconds')) { return $number * 1000 }
  if ($unit -in @('min', 'mins', 'minute', 'minutes')) { return $number * 60000 }
  if ($unit -in @('hour', 'hours')) { return $number * 3600000 }
  if ($unit -in @('day', 'days')) { return $number * 86400000 }
  return -1
}

function Get-RecoverySetting([string]$XmlFile) {
  # The sc.exe failure settings that match <onfailure> and <resetfailure> in PoppysAI.xml (what WinSW registers):
  # @{ Reset = seconds; Actions = 'restart/10000/restart/30000/restart/60000' }.
  $setting = @{ Reset = 3600; Actions = 'restart/10000/restart/30000/restart/60000' }
  try {
    [xml]$doc = [System.IO.File]::ReadAllText($XmlFile)
    $parts = @()
    foreach ($f in @($doc.service.onfailure)) {
      $kind = ([string]$f.action).ToLowerInvariant()
      $ms = ConvertTo-Millisecond ([string]$f.delay)
      if ($ms -lt 0) { $ms = 0 }
      if ($kind -eq 'restart' -or $kind -eq 'reboot') { $parts += ($kind + '/' + $ms) }
    }
    if ($parts.Count -gt 0) { $setting.Actions = ($parts -join '/') }
    $resetMs = ConvertTo-Millisecond ([string]$doc.service.resetfailure)
    if ($resetMs -ge 0) { $setting.Reset = [int64]($resetMs / 1000) }
  } catch { Write-Note "Could not read $XmlFile; using the standard restart settings (10 s, 30 s, 60 s; reset after 1 hour)." }
  return $setting
}

function Suspend-ServiceRecovery {
  # While the release is switched or the database restored, Windows must not restart the service after a crash
  # (WinSW's restart-on-failure): a crash loop could otherwise start it in the middle of the switch.
  # PowerShell 7.3+ would quote the empty argument ""; Legacy passes it as Windows PowerShell 5.1 does.
  Set-Variable -Name PSNativeCommandArgumentPassing -Value 'Legacy' -Scope Local
  $code = Invoke-Native 'sc.exe' @('failure', $ServiceName, 'reset=', '0', 'actions=', '""')
  if ($code -eq 0) {
    $script:RecoveryPaused = $true
    Write-Note 'Restart after a crash is paused until the service is started again.'
  } else { Write-Warn "Could not pause the restart-after-crash setting (sc.exe exit code $code); continuing." }
}

function Resume-ServiceRecovery {
  if (-not $script:RecoveryPaused) { return }
  $setting = Get-RecoverySetting (Join-Path $InstallRoot 'PoppysAI.xml')
  $code = Invoke-Native 'sc.exe' @('failure', $ServiceName, 'reset=', [string]$setting.Reset, 'actions=', $setting.Actions)
  if ($code -eq 0) {
    $script:RecoveryPaused = $false
    Write-Note 'Restart after a crash is on again.'
  } else {
    Write-Warn "Could not turn restart-after-crash on again (sc.exe exit code $code). Run install-service.ps1 from $CurrentLink\deploy\windows to repair it."
  }
}

function Protect-SettingsFile([string]$Path) {
  $code = Invoke-Native 'icacls.exe' @($Path, '/inheritance:r', '/grant:r', "${Admins}:F", "${System}:F", "${LocalService}:R", '/Q')
  if ($code -ne 0) { throw "icacls could not protect $Path (exit code $code)." }
}

function Sync-OracleSetting([string]$Release) {
  # An older release may hold an older copy of the Oracle settings (for example from before a password change):
  # always copy the master in config\ before starting it. The file is copied, never printed. The copy is made in
  # config\ (no access for ordinary users), protected there and then moved into the release folder, which
  # ordinary users can read: a move on the same disk keeps its permissions, so it is never readable by them.
  $master = Join-Path $ConfigDir '.env.poppys'
  if (-not (Test-Path -LiteralPath $master -PathType Leaf)) { Write-Note "No $master; the release keeps its own settings copy."; return }
  $copy = Join-Path $Release '.env.poppys'
  $staged = Join-Path $ConfigDir ('.env.poppys.staging-' + [guid]::NewGuid().ToString('N'))
  try {
    Copy-Item -LiteralPath $master -Destination $staged -Force
    Protect-SettingsFile $staged
    if (Test-Path -LiteralPath $copy) { Remove-Item -LiteralPath $copy -Force }
    [System.IO.File]::Move($staged, $copy)
  } catch {
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    throw "The Oracle settings could not be copied safely into $Release ($($_.Exception.Message)). No unprotected copy was left behind."
  }
  Write-Ok 'Oracle settings copied from config\ into the release (readable by administrators and the service only).'
}

function Test-DatabaseFit {
  # 'none' (no database yet), 'ok', 'newer' (changed by a newer release: $To refuses to start on it) or 'unknown'.
  # Read-only: backup.py --db-info opens the database with mode=ro.
  if (-not (Test-Path -LiteralPath $LiveDb -PathType Leaf)) { return 'none' }
  $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', $ToolsBackupPy, '--db-info', '--data-dir', $DataDir, '--app-root', $target, '--log', 'none')
  if ($code -eq 0) { return 'ok' }
  if ($code -eq 3) { return 'newer' }
  Write-Warn ("Could not check whether release $To can open the database (backup.py exit code $code). Continuing; if $To refuses it, " +
    'the start below fails and the message shows what to do.')
  return 'unknown'
}

function Get-NewerDatabaseText {
  $label = 'pre-upgrade-' + $To
  if ($label.Length -gt 60) { $label = $label.Substring(0, 60) }
  $zip = Get-ChildItem -LiteralPath (Join-Path $DataDir 'backups\pre-upgrade') -Filter ('poppys-backup-*-' + $label + '.zip') -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
  $zipText = "<the backup in $DataDir\backups\pre-upgrade made before the upgrade from $To>"
  if ($zip) { $zipText = $zip.FullName }
  return ("The database was changed by a newer POPPYS AI version (a newer database schema), so release $To cannot open it. " +
    "To go back to $To anyway, also put back the backup made before that upgrade. Everything entered in POPPYS AI since then is " +
    "lost (orders already saved to Oracle stay in Oracle). Run:`n   & '$PSCommandPath' -To $To -RestoreDatabase '$zipText'")
}

function Start-AppService {
  Resume-ServiceRecovery
  try { Start-Service -Name $ServiceName } catch { Write-Note "Start-Service: $($_.Exception.Message)"; return '' }
  return (Wait-PoppysReady $ReadySeconds)
}

# ---------------------------------------------------------------------------------------------------------------
$active = Get-JunctionTarget $CurrentLink
$activeVersion = ''
if ($active) { $activeVersion = Split-Path -Leaf $active }
$previous = ''
if (Test-Path -LiteralPath $PreviousFile) { $previous = ([string](Get-Content -LiteralPath $PreviousFile -TotalCount 1)).Trim() }

if ($List) {
  Write-Host "Installed releases in $ReleasesDir (newest first):"
  Get-ChildItem -LiteralPath $ReleasesDir -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | ForEach-Object {
    $mark = ''
    if ($_.Name -ieq $activeVersion) { $mark += '  <- active' }
    if ($_.Name -ieq $previous) { $mark += '  <- previous (default for rollback)' }
    Write-Host ("  {0,-40} {1:yyyy-MM-dd HH:mm}{2}" -f $_.Name, $_.LastWriteTime, $mark)
  }
  exit 0
}

if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -or -not (Test-Path -LiteralPath $WinSWExe)) {
  Stop-Switch 'The PoppysAI service is not installed here. Run deploy\windows\install-service.ps1 first.'
}
if (-not $To) { $To = $previous }
if (-not $To) { Stop-Switch "No earlier release is recorded in $PreviousFile. Run this script with -List, then with -To <release>." }
$target = Join-Path $ReleasesDir $To
if (-not (Test-Path -LiteralPath (Join-Path $target 'backend\main.py'))) { Stop-Switch "$target is not an installed POPPYS AI release (see -List)." }
$python = Get-ReleasePython $target
if (-not $python) { Stop-Switch "$target has no Python (python\python.exe or .venv\Scripts\python.exe)." }
if ($RestoreDatabase -and -not (Test-Path -LiteralPath $RestoreDatabase -PathType Leaf)) { Stop-Switch "Backup file not found: $RestoreDatabase" }
if ($PreUpgradeBackup -and -not (Test-Path -LiteralPath $PreUpgradeBackup -PathType Leaf)) {
  Write-Warn "The pre-upgrade backup $PreUpgradeBackup is missing; it cannot be restored if it is needed."
  $PreUpgradeBackup = ''
}
if ($To -ieq $activeVersion -and -not $RestoreDatabase) {
  if ((Get-Service -Name $ServiceName).Status -eq 'Running') {
    Write-Host "Release $To is already active and running; nothing to do." -ForegroundColor Yellow
    exit 3
  }
  # Active but stopped (for example an upgrade stopped it and then failed before switching): go through the steps,
  # which only start it again.
}
# This release's backup.py (the newest code: database version check, restore that puts files back on failure),
# run with the target release's Python. Resolved now: the "current" junction changes below.
$ToolsBackupPy = Resolve-RealPath (Join-Path $PSScriptRoot '..\..\scripts\backup.py')
if (-not (Test-Path -LiteralPath $ToolsBackupPy -PathType Leaf)) { $ToolsBackupPy = Join-Path $target 'scripts\backup.py' }

Write-Host "POPPYS AI - $($Word.ToLower()) from $activeVersion to $To" -ForegroundColor Green
if ($To -ine $activeVersion -and -not $RestoreDatabase -and -not $PreUpgradeBackup) {
  # Checked while the service still runs, so a refusal costs no downtime. Checked again once it is stopped.
  Write-Note "Checking that release $To can open the database ..."
  if ((Test-DatabaseFit) -eq 'newer') { Stop-Switch ((Get-NewerDatabaseText) + "`n   Nothing was changed; POPPYS AI $activeVersion keeps running.") }
}
$auditWarning = ''
try {
  Write-Step '1. Stopping the service'
  Suspend-ServiceRecovery
  Stop-Service -Name $ServiceName -Force
  if (-not (Wait-PortFree 35)) { Stop-Switch "Port $Port is still answering after the service stopped: another program uses it. Close it, then run this script again." }
  Write-Ok 'Stopped.'

  Write-Step "2. Checking that release $To can open the database"
  $fit = Test-DatabaseFit
  if ($fit -eq 'newer' -and -not $RestoreDatabase) {
    if ($PreUpgradeBackup) {
      Write-Note "The database was already changed by the newer release, so $To needs the backup made before the upgrade."
      $RestoreDatabase = $PreUpgradeBackup
    } else {
      Write-Note "Starting the service again on $activeVersion ..."
      [void](Start-AppService)
      Stop-Switch (Get-NewerDatabaseText)
    }
  } elseif ($fit -eq 'ok') { Write-Ok "Release $To can open the database." }
  elseif ($fit -eq 'none') { Write-Note 'There is no database yet; nothing to check.' }

  if ($RestoreDatabase) {
    Write-Step "3. Restoring the database from $RestoreDatabase"
    if ((Get-Service -Name $ServiceName).Status -ne 'Stopped') {
      Stop-Service -Name $ServiceName -Force
      [void](Wait-PortFree 35)
    }
    $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', $ToolsBackupPy, '--restore', $RestoreDatabase, '--to', $DataDir, '--force',
      '--app-root', $target, '--port', "$Port", '--log', (Join-Path $DataDir 'backups\backup.log'))
    if ($code -eq 5) {
      $auditWarning = 'The database is restored, but its audit history cannot be proven untouched (see the backup.py message above). Keep this window''s output for support.'
      Write-Warn $auditWarning
    } elseif ($code -ne 0) {
      if (-not (Test-Path -LiteralPath $LiveDb -PathType Leaf)) {
        # Never start the service without its database: it would make a new, empty one (the first-run screen).
        [void](Invoke-Native 'sc.exe' @('config', $ServiceName, 'start=', 'demand'))
        Stop-Switch ("The restore failed AND $LiveDb is missing. POPPYS AI was NOT started, and does not start with Windows, so it cannot make " +
          "a new, empty database. In $DataDir rename the newest poppys.sqlite3.before-restore-<time> back to poppys.sqlite3, then run " +
          "install-service.ps1 from $CurrentLink\deploy\windows (or call support).")
      }
      Write-Note "The restore did not finish; $LiveDb is still in place (backup.py puts the previous files back when a restore fails half-way)."
      Write-Note "Starting the service again on $activeVersion ..."
      [void](Start-AppService)
      Stop-Switch "The restore did not run (backup.py exit code $code; read its message above)."
    }
    if (-not $auditWarning) { Write-Ok 'Database restored (the previous files were moved aside, not deleted).' }
  }

  Write-Step "4. Switching to $To"
  Sync-OracleSetting $target
  Set-Junction $CurrentLink $target
  Write-Ok "current -> $target"
  if (Write-ServiceXml $target (Join-Path $InstallRoot 'PoppysAI.xml')) {
    Write-Note 'PoppysAI.xml differs in this release: refreshing the service registration ...'
    $code = Invoke-Native $WinSWExe @('uninstall')
    if ($code -ne 0 -or -not (Wait-ServiceGone 30)) { Stop-Switch 'The old service registration could not be removed (close the Services window and run this script again).' }
    $code = Invoke-Native $WinSWExe @('install')
    if ($code -ne 0) { Stop-Switch "PoppysAI.exe install failed (exit code $code). Run install-service.ps1 from $target\deploy\windows." }
  }

  Write-Step "5. Starting POPPYS AI $To and waiting up to $ReadySeconds s"
  $how = Start-AppService
  if (-not $how) {
    Show-LogTail
    Stop-Switch "POPPYS AI $To did not answer on http://127.0.0.1:$Port within $ReadySeconds s."
  }
} catch {
  Stop-Switch "$($_.Exception.Message)"
}
$restored = ''
if ($RestoreDatabase) { $restored = ", database restored from $(Split-Path -Leaf $RestoreDatabase)" }
Write-DeployHistory "$($Action.ToUpper()) OK $activeVersion -> $To ($Reason$restored)"
if ($auditWarning) { Write-DeployHistory "AUDIT CHECK FAILED after restoring $RestoreDatabase" }
Write-Ok "POPPYS AI $To answers ($how)."
Write-Host ''
if ($auditWarning) { Write-Host "WARNING: $auditWarning" -ForegroundColor Yellow }
Write-Host "$Word finished: POPPYS AI $To is running at http://127.0.0.1:$Port" -ForegroundColor Green
if ($RestoreDatabase) { Write-Host "The database was restored from $RestoreDatabase (the previous one is in $DataDir as poppys.sqlite3.before-restore-<time>)." }
exit 0
