#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Switch the POPPYS AI service to another installed release: by default back to the one that was active
  before the last upgrade. Optionally put a database backup back at the same time.

.DESCRIPTION
  Steps: stop the service -> (only with -RestoreDatabase) restore that backup into data\ -> point the
  "current" junction at the release -> start -> wait up to 60 s for http://127.0.0.1:8008 to answer
  (/readyz when the app has it, otherwise the home page and /api/session).

  Nothing is deleted: every release stays in releases\, and a restored database moves the previous file aside
  (data\poppys.sqlite3.before-restore-<time>). upgrade.ps1 runs this script to switch to the new release and,
  when the new release does not answer, runs it again to come back automatically.

  Exit codes: 0 OK, 1 failed (read the message; the service may be stopped), 3 nothing to do.
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".

.PARAMETER To
  Release folder name in releases\ (see -List). Default: the release named in previous.txt.
.PARAMETER RestoreDatabase
  A backup .zip (for example data\backups\pre-upgrade\poppys-backup-...zip) to restore while the service is stopped.
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
$CurrentLink = Join-Path $InstallRoot 'current'
$PreviousFile = Join-Path $InstallRoot 'previous.txt'
$WinSWExe = Join-Path $InstallRoot 'PoppysAI.exe'
$Word = 'Rollback'
if ($Action -eq 'upgrade') { $Word = 'Switch' }

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK: $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Write-DeployHistory([string]$Text) {
  try {
    if (Test-Path -LiteralPath $LogDir) {
      Add-Content -LiteralPath (Join-Path $LogDir 'deploy-history.log') -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Text) -Encoding ASCII
    }
  } catch { Write-Verbose 'deploy-history.log could not be written' }
}
function Stop-Switch([string]$Text, [int]$Code = 1) {
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

function Sync-OracleSetting([string]$Release) {
  # An older release may hold an older copy of the Oracle settings (for example from before a password change):
  # always copy the master in config\ before starting it. The file is copied, never printed.
  $master = Join-Path (Join-Path $InstallRoot 'config') '.env.poppys'
  if (-not (Test-Path -LiteralPath $master -PathType Leaf)) { Write-Note "No $master; the release keeps its own settings copy."; return }
  $copy = Join-Path $Release '.env.poppys'
  Copy-Item -LiteralPath $master -Destination $copy -Force
  $code = Invoke-Native 'icacls.exe' @($copy, '/inheritance:r', '/grant:r', '*S-1-5-32-544:F', '*S-1-5-18:F', '*S-1-5-19:R', '/Q')
  if ($code -ne 0) { throw "icacls could not protect $copy (exit code $code)." }
  Write-Ok 'Oracle settings copied from config\ into the release.'
}

function Start-AppService {
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
if ($To -ieq $activeVersion -and -not $RestoreDatabase) {
  Write-Host "Release $To is already active; nothing to do." -ForegroundColor Yellow
  exit 3
}

Write-Host "POPPYS AI - $($Word.ToLower()) from $activeVersion to $To" -ForegroundColor Green
try {
  Write-Step '1. Stopping the service'
  Stop-Service -Name $ServiceName -Force
  if (-not (Wait-PortFree 35)) { Stop-Switch "Port $Port is still answering after the service stopped: another program uses it. Close it, then run this script again." }
  Write-Ok 'Stopped.'

  if ($RestoreDatabase) {
    Write-Step "2. Restoring the database from $RestoreDatabase"
    $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', (Join-Path $target 'scripts\backup.py'), '--restore', $RestoreDatabase,
      '--to', $DataDir, '--force', '--port', "$Port", '--log', (Join-Path $DataDir 'backups\backup.log'))
    if ($code -ne 0) {
      Write-Note 'The database was not changed. Starting the service again on the release that was active ...'
      [void](Start-AppService)
      Stop-Switch "The restore did not run (backup.py exit code $code; read its message above)."
    }
    Write-Ok 'Database restored (the previous file was moved aside, not deleted).'
  }

  Write-Step "3. Switching to $To"
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

  Write-Step "4. Starting POPPYS AI $To and waiting up to $ReadySeconds s"
  $how = Start-AppService
  if (-not $how) {
    Show-LogTail
    Stop-Switch "POPPYS AI $To did not answer on http://127.0.0.1:$Port within $ReadySeconds s."
  }
} catch {
  Stop-Switch "$($_.Exception.Message)"
}
Write-DeployHistory "$($Action.ToUpper()) OK $activeVersion -> $To ($Reason)"
Write-Ok "POPPYS AI $To answers ($how)."
Write-Host ''
Write-Host "$Word finished: POPPYS AI $To is running at http://127.0.0.1:$Port" -ForegroundColor Green
exit 0
