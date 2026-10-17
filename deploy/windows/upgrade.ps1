#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Upgrade the POPPYS AI service to a new release, with a backup first and an automatic rollback.

.DESCRIPTION
  Steps, in order:
    1. Checks and copies the new release into releases\<version> (install-service.ps1 -StageOnly: SHA256SUMS
       when present, the release Python can load the app). The running service is not touched yet. From a git
       checkout every commit is its own version (VERSION plus the short commit, e.g. 0.9.0-abc1234).
    2. Backup: the new release's scripts\backup.py writes a verified backup (database, audit key and anchor) to
       data\backups\pre-upgrade\, with --no-prune: pre-upgrade backups are never deleted automatically. It has
       -BackupMinutes to finish. If it fails, the upgrade stops and nothing has changed.
    3. Stop -> switch the "current" junction to the new release -> start -> wait up to 60 s for
       http://127.0.0.1:8008/readyz (or the home page when the app has no /readyz) to answer 200.
    4. If the new release does not answer in time: automatic rollback to the release that was running
       (rollback.ps1). When the new release had already changed the database (a newer schema the old release
       cannot open), the rollback also restores the backup from step 2; otherwise the database is kept as it is.
       The error log of the failed start is kept in logs\.
    5. After a successful upgrade, the nightly backup task is registered again (same time and options), so it
       uses the new release.

  Exit codes: 0 upgraded, 1 upgrade failed (rolled back, or stopped before any change), 2 upgrade AND rollback
  failed: follow docs\RUNBOOK.md, "If the rollback does not start either".
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".

.PARAMETER Source
  The unpacked new release folder (or the updated git checkout).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File C:\Temp\poppys-ai-1.3.0\deploy\windows\upgrade.ps1 -Source C:\Temp\poppys-ai-1.3.0
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Source,
  [string]$InstallRoot = (Join-Path $env:ProgramData 'PoppysAI'),
  [int]$ReadySeconds = 60,
  [int]$BackupMinutes = 30
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'PoppysAI'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$ReleasesDir = Join-Path $InstallRoot 'releases'
$DataDir = Join-Path $InstallRoot 'data'
$LogDir = Join-Path $InstallRoot 'logs'
$CurrentLink = Join-Path $InstallRoot 'current'
$PreviousFile = Join-Path $InstallRoot 'previous.txt'

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
function Stop-Upgrade([string]$Text, [int]$Code = 1) {
  Write-Host ''; Write-Host "FAILED: $Text" -ForegroundColor Red
  Write-DeployHistory "UPGRADE FAILED: $Text"
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

function ConvertTo-CommandLineArgument([string]$Value) {
  # One argument for a Windows command line, split the way python.exe splits it (CommandLineToArgvW): quoted when
  # it has a space or a special character, with the backslashes before a quote doubled (so "E:\" stays E:\).
  if ($Value -and $Value -notmatch '[\s"&|<>^(),;=%!]') { return $Value }
  $escaped = [regex]::Replace($Value, '(\\*)"', { param($m) ($m.Groups[1].Value * 2) + '\"' })
  $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
  return '"' + $escaped + '"'
}

function Invoke-NativeTimeout([string]$FilePath, [string[]]$ArgumentList, [int]$Seconds) {
  # Like Invoke-Native, with a time limit: the program writes straight to this window. After $Seconds it is
  # stopped together with its child processes (the .venv python.exe starts the real Python as a child) and -1 is
  # returned.
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $FilePath
  $psi.Arguments = (@($ArgumentList | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join ' ')
  $psi.UseShellExecute = $false
  $proc = [System.Diagnostics.Process]::Start($psi)
  try {
    if ($proc.WaitForExit($Seconds * 1000)) {
      $proc.WaitForExit()
      return $proc.ExitCode
    }
    [void](Invoke-Native 'taskkill.exe' @('/PID', [string]$proc.Id, '/T', '/F'))
    return -1
  } finally { $proc.Dispose() }
}

function Invoke-Helper([string]$Name, [hashtable]$Arguments) {
  # Runs install-service.ps1, rollback.ps1 or backup-task.ps1 from this folder; returns @(exit code, last output line).
  $script = Join-Path $PSScriptRoot $Name
  if (-not (Test-Path -LiteralPath $script)) { throw "$script is missing." }
  $global:LASTEXITCODE = 0
  try {
    $output = @(& $script @Arguments)
    return @($LASTEXITCODE, [string]($output | Select-Object -Last 1))
  } catch {
    Write-Note "$Name stopped with an error: $($_.Exception.Message)"
    return @(1, '')
  }
}

function Get-JunctionTarget([string]$Path) {
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if (-not $item -or $item.LinkType -ne 'Junction') { return '' }
  return [string](@($item.Target)[0])
}

function Get-ReleasePython([string]$Release) {
  foreach ($candidate in @('python\python.exe', '.venv\Scripts\python.exe')) {
    $path = Join-Path $Release $candidate
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
  }
  return ''
}

# ---------------------------------------------------------------------------------------------------------------
Write-Host 'POPPYS AI - upgrade' -ForegroundColor Green
if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { Stop-Upgrade 'The PoppysAI service is not installed. Use install-service.ps1 for a first install.' }
$oldRelease = Get-JunctionTarget $CurrentLink
if (-not $oldRelease) { Stop-Upgrade "$CurrentLink is missing. Run install-service.ps1 to repair the installation first." }
$oldVersion = Split-Path -Leaf $oldRelease
Write-Note "Running now: $oldVersion"
Write-Note "New release: $Source"

Write-Step '1. Checking and copying the new release (the running service is not touched)'
$result = Invoke-Helper 'install-service.ps1' @{ Source = $Source; InstallRoot = $InstallRoot; StageOnly = $true }
if ($result[0] -eq 3) { Write-Host "Version $oldVersion is already installed and active; nothing to upgrade." -ForegroundColor Yellow; exit 0 }
if ($result[0] -ne 0) { Stop-Upgrade 'The new release could not be prepared (see the messages above). Nothing was changed; POPPYS AI keeps running.' }
$newVersion = $result[1].Trim()
$newRelease = Join-Path $ReleasesDir $newVersion
if (-not $newVersion -or -not (Test-Path -LiteralPath (Join-Path $newRelease 'backend\main.py'))) {
  Stop-Upgrade "install-service.ps1 did not report a usable release ('$newVersion'). Nothing was changed."
}
Write-Ok "Release $newVersion is ready in $newRelease"
$rollbackScript = Join-Path $newRelease 'deploy\windows\rollback.ps1'
if (-not (Test-Path -LiteralPath $rollbackScript)) { $rollbackScript = Join-Path $PSScriptRoot 'rollback.ps1' }

Write-Step "2. Backup before the upgrade (the app keeps running; at most $BackupMinutes minutes)"
$python = Get-ReleasePython $oldRelease
if (-not $python) { Stop-Upgrade "The running release $oldRelease has no Python; run install-service.ps1 to repair it first. Nothing was changed." }
# The new release's backup.py (standard library only, so the running release's Python runs it): it also copies the
# audit key and anchor, which older versions of backup.py left out.
$backupPy = Join-Path $newRelease 'scripts\backup.py'
if (-not (Test-Path -LiteralPath $backupPy)) { $backupPy = Join-Path $oldRelease 'scripts\backup.py' }
$preDir = Join-Path $DataDir 'backups\pre-upgrade'
$label = ('pre-upgrade-' + $oldVersion)
if ($label.Length -gt 60) { $label = $label.Substring(0, 60) }
$started = Get-Date
# --no-prune: a second attempt on the same day must not delete the first pre-upgrade backup (daily retention keeps
# only the newest backup of each day). Pre-upgrade backups are deleted by hand only (docs\RUNBOOK.md, section 6).
$code = Invoke-NativeTimeout $python @('-E', '-s', '-X', 'utf8', $backupPy, '--data-dir', $DataDir, '--app-root', $oldRelease, '--dest', $preDir,
  '--label', $label, '--no-prune') ($BackupMinutes * 60)
if ($code -eq -1) { Stop-Upgrade "The backup did not finish within $BackupMinutes minutes and was stopped. The upgrade stopped before changing anything. Try again when POPPYS AI is less busy, or with -BackupMinutes 60." }
if ($code -ne 0) { Stop-Upgrade "The backup failed (backup.py exit code $code). The upgrade stopped before changing anything." }
$backupZip = Get-ChildItem -LiteralPath $preDir -Filter 'poppys-backup-*.zip' | Where-Object { $_.LastWriteTime -ge $started.AddMinutes(-1) } |
  Sort-Object Name -Descending | Select-Object -First 1
if (-not $backupZip) { Stop-Upgrade "backup.py reported success but no new backup is in $preDir. The upgrade stopped before changing anything." }
Write-Ok "Backup: $($backupZip.FullName)"
Set-Content -LiteralPath $PreviousFile -Value $oldVersion -Encoding ASCII

Write-Step "3. Stop, switch to $newVersion, start, wait up to $ReadySeconds s"
Write-DeployHistory "UPGRADE START $oldVersion -> $newVersion (backup $($backupZip.Name))"
$result = Invoke-Helper 'rollback.ps1' @{ To = $newVersion; InstallRoot = $InstallRoot; ReadySeconds = $ReadySeconds; Action = 'upgrade'; Reason = "upgrade from $oldVersion" }
if ($result[0] -eq 0) {
  Write-DeployHistory "UPGRADE OK $oldVersion -> $newVersion"
  Write-Step 'Nightly backup task'
  if (Get-ScheduledTask -TaskPath '\POPPYS AI\' -TaskName 'Nightly backup' -ErrorAction SilentlyContinue) {
    $task = Invoke-Helper 'backup-task.ps1' @{ InstallRoot = $InstallRoot; Refresh = $true }
    if ($task[0] -ne 0) {
      Write-Warn ("The nightly backup task could not be registered again. Run: & '$newRelease\deploy\windows\backup-task.ps1' -RunNow " +
        '(add -SecondCopy or -IncludeUploads if you used them).')
    }
  } else {
    Write-Warn "No nightly backup task is registered. Register it: & '$newRelease\deploy\windows\backup-task.ps1' -RunNow"
  }
  Write-Host ''
  Write-Host "Upgrade finished: POPPYS AI $newVersion is running at http://127.0.0.1:8008" -ForegroundColor Green
  Write-Host "Backup taken before the upgrade: $($backupZip.FullName)"
  Write-Host "To go back to ${oldVersion}: & '$CurrentLink\deploy\windows\rollback.ps1'"
  exit 0
}

Write-Step "4. $newVersion did not start correctly: rolling back to $oldVersion automatically"
$errLog = Join-Path $LogDir 'PoppysAI.err.log'
if (Test-Path -LiteralPath $errLog) {
  $kept = Join-Path $LogDir ('failed-upgrade-' + $newVersion + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.err.log')
  Copy-Item -LiteralPath $errLog -Destination $kept
  Write-Note "The error log of the failed start is kept as $kept"
}
# PreUpgradeBackup: restored only when the failed release had already changed the database to a schema the old
# release cannot open (rollback.ps1 checks); otherwise the database stays as it is.
$result = Invoke-Helper 'rollback.ps1' @{ To = $oldVersion; InstallRoot = $InstallRoot; ReadySeconds = $ReadySeconds; PreUpgradeBackup = $backupZip.FullName;
  Reason = "automatic after failed upgrade to $newVersion" }
if ($result[0] -eq 0) {
  Stop-Upgrade "The upgrade to $newVersion failed and was rolled back: POPPYS AI $oldVersion is running again. Send $LogDir to support." 1
}
$restoreCmd = "& '$rollbackScript' -To $oldVersion -RestoreDatabase '$($backupZip.FullName)'"
Stop-Upgrade ("The upgrade to $newVersion failed AND $oldVersion did not start again. POPPYS AI is down. Follow docs\RUNBOOK.md, " +
  "'If the rollback does not start either'. To put the pre-upgrade data back as well, run (one line):`n   $restoreCmd") 2
