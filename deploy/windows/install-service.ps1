#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install or repair POPPYS AI as a Windows service (WinSW 2.12.0). Safe to run again.

.DESCRIPTION
  Steps, in order (each prints a "==" line):
    1. Checks SHA256SUMS in the release folder when there is one, and the SHA-256 of the WinSW download.
    2. Creates C:\ProgramData\PoppysAI\{releases,data,logs,config} with locked-down permissions:
       administrators and SYSTEM full control; the service account (LOCAL SERVICE) read-only on the program,
       modify on data\ and logs\, read on config\; other users read-only on the program and logs and no access
       to data\ or config\.
    3. Copies the app into releases\<version> (never .env files, local data or git metadata), links its data
       folder to data\, and checks that the release's Python can import the app.
    4. First install only: copies the existing database from <Source>\data with scripts\backup.py (a verified
       backup, then a restore; the original stays where it is) and copies .env.poppys into config\.
    5. Points the "current" junction at the release, registers the PoppysAI service from PoppysAI.xml
       (LocalService, delayed automatic start, restart on failure) and starts it, then waits up to 60 s for
       http://127.0.0.1:8008 to answer (/readyz when the app has it, otherwise the home page).

  WinSW is not in the repository. Download WinSW-x64.exe 2.12.0 (MIT licence) from
  https://github.com/winsw/winsw/releases/tag/v2.12.0, rename it PoppysAI.exe and put it next to this script
  (or pass -WinSW). Before the first install, close the start.ps1 window: port 8008 must be free.

  Exit codes: 0 OK, 1 failed (read the message), 3 (-StageOnly) that version is already the active one.
  Not yet tested on real Windows: see docs\RUNBOOK.md, "Not yet tested on real Windows".

.PARAMETER Source
  The unpacked release folder or the git checkout. Default: the folder two levels above this script.
.PARAMETER InstallRoot
  Where the service lives (%BASE% in PoppysAI.xml). Default: C:\ProgramData\PoppysAI.
.PARAMETER Version
  Release name. Default: the VERSION file, else "git describe", else a hash of the app files.
.PARAMETER WinSW
  Path to WinSW-x64.exe 2.12.0. Default: PoppysAI.exe or WinSW-x64.exe next to this script, then the copy
  already installed in InstallRoot.
.PARAMETER AllowUnknownWinSW
  Accept a WinSW file whose SHA-256 is not the 2.12.0 x64 one (only if you know why).
.PARAMETER Force
  Install over a different active version without the backup and automatic rollback of upgrade.ps1.
.PARAMETER StageOnly
  Used by upgrade.ps1: copy and check the release, print its version as the last line, change nothing else.
.PARAMETER NoStart
  Register the service but do not start it.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-service.ps1
#>
[CmdletBinding()]
param(
  [string]$Source = '',
  [string]$InstallRoot = (Join-Path $env:ProgramData 'PoppysAI'),
  [string]$Version = '',
  [string]$WinSW = '',
  [switch]$AllowUnknownWinSW,
  [switch]$Force,
  [switch]$StageOnly,
  [switch]$NoStart,
  [int]$ReadySeconds = 60
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'PoppysAI'
$Port = 8008
# SHA-256 of WinSW-x64.exe from https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe
# (computed from that download on 2026-10-17; compare with the digest GitHub shows on the release page).
$WinSWSha256 = '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da'
$WinSWUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe'
# Well-known SIDs, so the permissions work on any Windows language.
$Admins = '*S-1-5-32-544'; $System = '*S-1-5-18'; $LocalService = '*S-1-5-19'; $Users = '*S-1-5-32-545'

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$ReleasesDir = Join-Path $InstallRoot 'releases'
$DataDir = Join-Path $InstallRoot 'data'
$LogDir = Join-Path $InstallRoot 'logs'
$ConfigDir = Join-Path $InstallRoot 'config'
$CurrentLink = Join-Path $InstallRoot 'current'

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK: $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" }
function Write-Warn([string]$Text) { Write-Host "   WARNING: $Text" -ForegroundColor Yellow }
function Stop-Install([string]$Text, [int]$Code = 1) {
  Write-Host ''; Write-Host "FAILED: $Text" -ForegroundColor Red
  Write-DeployHistory "INSTALL FAILED $Text"
  exit $Code
}
function Write-DeployHistory([string]$Text) {
  try {
    if (Test-Path -LiteralPath $LogDir) {
      Add-Content -LiteralPath (Join-Path $LogDir 'deploy-history.log') -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Text) -Encoding ASCII
    }
  } catch { Write-Verbose 'deploy-history.log could not be written' }
}

function Invoke-Native([string]$FilePath, [string[]]$ArgumentList) {
  # Runs a program, shows its output indented and returns its exit code. Windows PowerShell 5.1 turns every
  # stderr line of a native program into an error under 'Stop', so the call itself runs under 'Continue'.
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
    if ($item.LinkType -ne 'Junction') { throw "$Path exists and is not a junction. It was not changed; move it away and run again." }
    [System.IO.Directory]::Delete($Path)    # removes only the link, never the folder it points to
  }
  New-Item -ItemType Junction -Path $Path -Target $Target | Out-Null
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

function Test-ReleaseChecksum([string]$Dir) {
  # SHA256SUMS lines look like "<64 hex>  relative/path" (sha256sum format). A git checkout has no such file.
  $sums = Join-Path $Dir 'SHA256SUMS'
  if (-not (Test-Path -LiteralPath $sums -PathType Leaf)) {
    Write-Note 'No SHA256SUMS file in the release folder: checksum check skipped (normal for a git checkout).'
    return $true
  }
  $bad = 0
  $count = 0
  $listed = @{}
  foreach ($line in Get-Content -LiteralPath $sums) {
    if ($line -match '^\s*$' -or $line -match '^\s*#') { continue }
    if ($line -match '^([0-9A-Fa-f]{64}) [ *](.+)$') {
      $expected = $Matches[1].ToLowerInvariant()
      $rel = $Matches[2].Trim()
    } else {
      Write-Note "Unreadable line in SHA256SUMS: $line"; $bad++; continue
    }
    if ($rel -match '(^|[\\/])\.\.([\\/]|$)' -or [System.IO.Path]::IsPathRooted($rel)) { Write-Note "Unsafe path in SHA256SUMS: $rel"; $bad++; continue }
    $count++
    $listed[($rel -replace '\\', '/').ToLowerInvariant()] = $true
    $file = Join-Path $Dir $rel
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Write-Note "Missing: $rel"; $bad++; continue }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Write-Note "Changed since the release was built: $rel"; $bad++ }
  }
  if ($bad -gt 0 -or $count -eq 0) {
    Write-Note "$bad problem(s) in SHA256SUMS ($count files listed)."
    return $false
  }
  $root = (Resolve-Path -LiteralPath $Dir).ProviderPath.TrimEnd([char[]]@('\', '/'))
  $extra = @(Get-ChildItem -LiteralPath $Dir -Recurse -File -Force | Where-Object {
      $rel = $_.FullName.Substring($root.Length + 1) -replace '\\', '/'
      -not $listed.ContainsKey($rel.ToLowerInvariant()) -and $rel -ne 'SHA256SUMS' -and $_.Name -notin @('Thumbs.db', 'desktop.ini') -and
        $rel -notmatch '(^|/)__pycache__/' -and $rel -notmatch '^(data|\.git)/' -and $_.Name -notin @('PoppysAI.exe', 'WinSW-x64.exe')
    })
  if ($extra.Count -gt 0) {
    Write-Warn "$($extra.Count) file(s) are in the release folder but not in SHA256SUMS (first: $($extra[0].FullName)). Check that nothing was added."
  }
  Write-Ok "SHA256SUMS: all $count listed files match."
  return $true
}

function Get-ContentHash([string]$Dir) {
  $root = (Resolve-Path -LiteralPath $Dir).ProviderPath.TrimEnd([char[]]@('\', '/'))
  $lines = foreach ($part in @('backend', 'frontend', 'config', 'scripts', 'deploy')) {
    $sub = Join-Path $root $part
    if (Test-Path -LiteralPath $sub) {
      Get-ChildItem -LiteralPath $sub -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } | ForEach-Object {
        ($_.FullName.Substring($root.Length + 1) -replace '\\', '/') + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
      }
    }
  }
  $text = (@($lines) | Sort-Object) -join "`n"
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $digest = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text))
  return -join ($digest | ForEach-Object { $_.ToString('x2') })
}

function Get-ReleaseVersion([string]$Dir) {
  $ver = ''
  $file = Join-Path $Dir 'VERSION'
  if (Test-Path -LiteralPath $file -PathType Leaf) { $ver = [string](Get-Content -LiteralPath $file -TotalCount 1) }
  if (-not $ver.Trim() -and (Test-Path -LiteralPath (Join-Path $Dir '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $ver = [string](& git -C $Dir describe --tags --always --dirty 2>$null | Select-Object -First 1) } finally { $ErrorActionPreference = $saved }
    if ($LASTEXITCODE -ne 0) { $ver = '' }
  }
  # Without VERSION or git: a hash of the app files. Uncommitted changes keep the same "<sha>-dirty" name, so the
  # content hash is added to make each changed copy its own release.
  if (-not $ver.Trim()) {
    $ver = 'build-' + (Get-ContentHash $Dir).Substring(0, 12)
  } elseif ($ver.Trim() -match '-dirty$') {
    $ver = $ver.Trim() + '-' + (Get-ContentHash $Dir).Substring(0, 8)
  }
  return ($ver.Trim() -replace '[^A-Za-z0-9._-]', '-').Trim([char[]]@('-', '.'))
}

function Get-ReleasePython([string]$Release) {
  foreach ($candidate in @('python\python.exe', '.venv\Scripts\python.exe')) {
    $path = Join-Path $Release $candidate
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
  }
  return ''
}

function Test-VenvBase([string]$Release) {
  # A .venv only redirects to the Python it was made from. LOCAL SERVICE cannot read a per-user Python
  # (C:\Users\...) or the Microsoft Store one (WindowsApps).
  $cfg = Join-Path $Release '.venv\pyvenv.cfg'
  if ((Test-Path -LiteralPath (Join-Path $Release 'python\python.exe')) -or -not (Test-Path -LiteralPath $cfg)) { return $true }
  $line = Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
  if (-not $line) { return $true }
  $pyHome = ($line -split '=', 2)[1].Trim()
  if ($pyHome -match '\\Users\\|\\WindowsApps\\') {
    Write-Note "The .venv uses the Python in $pyHome, which is installed for one Windows user only."
    Write-Note 'The service account cannot read it. Install Python 3.13 from python.org with "Install for all users"'
    Write-Note '(Customize installation), delete .venv, run setup.ps1 again, then run this script again.'
    return $false
  }
  if (-not (Test-Path -LiteralPath (Join-Path $pyHome 'python.exe'))) {
    Write-Note "The .venv points at $pyHome, but there is no python.exe there. Run setup.ps1 again."
    return $false
  }
  return $true
}

function Set-FolderAccess([string]$Path, [string[]]$Grants) {
  # Replace inherited permissions with exactly these grants (icacls exit code 0 = done).
  $code = Invoke-Native 'icacls.exe' (@($Path, '/inheritance:r', '/grant:r') + $Grants + @('/Q'))
  if ($code -ne 0) { throw "icacls could not set the permissions of $Path (exit code $code)." }
}

function Protect-SettingsFile([string]$Path) {
  Set-FolderAccess $Path @("${Admins}:F", "${System}:F", "${LocalService}:R")
}

function Copy-Release([string]$From, [string]$To) {
  New-Item -ItemType Directory -Force -Path $To | Out-Null
  foreach ($part in @('backend', 'frontend', 'config', 'scripts', 'deploy', 'docs', 'python', '.venv')) {
    $src = Join-Path $From $part
    if (-not (Test-Path -LiteralPath $src -PathType Container)) { continue }
    $rc = @($src, (Join-Path $To $part), '/MIR', '/XJ', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
      '/XF', '.env', '.env.*', '*.sqlite3', '*.sqlite3-journal', '*.upload', '*.log')
    if ($part -ne '.venv' -and $part -ne 'python') { $rc += @('/XD', '__pycache__', '.pytest_cache') }
    $code = Invoke-Native 'robocopy.exe' $rc
    if ($code -ge 8) { throw "robocopy could not copy $part (exit code $code)." }
  }
  foreach ($name in @('requirements.txt', 'requirements-dev.txt', 'pyproject.toml', 'README.md', 'VERSION', 'GIT_SHA', 'CHANGELOG.md',
      'SHA256SUMS', 'THIRD_PARTY_LICENSES.txt', 'LICENSE', 'LICENSE.txt', 'sbom.cdx.json')) {
    $src = Join-Path $From $name
    if (Test-Path -LiteralPath $src -PathType Leaf) { Copy-Item -LiteralPath $src -Destination (Join-Path $To $name) -Force }
  }
}

function Sync-OracleSetting([string]$Release) {
  # The app reads .env.poppys from its own folder; the master copy lives in config\ and is copied, never printed.
  $master = Join-Path $ConfigDir '.env.poppys'
  if (-not (Test-Path -LiteralPath $master -PathType Leaf)) {
    Write-Warn "There is no $master yet. The app starts, but saving to Oracle needs it: copy .env.poppys there and run this script again."
    return
  }
  $copy = Join-Path $Release '.env.poppys'
  Copy-Item -LiteralPath $master -Destination $copy -Force
  Protect-SettingsFile $copy
  Write-Ok "Oracle settings copied into the release (readable by administrators and the service only)."
}

function Initialize-Release([string]$From, [string]$Release, [string]$Ver, [bool]$InPlace) {
  if ($InPlace) {
    Write-Note "The source is the installed release $Ver itself: nothing to copy."
  } else {
    Write-Note "Copying $From to $Release ..."
    Copy-Release $From $Release
    if (-not (Test-Path -LiteralPath (Join-Path $Release 'VERSION'))) { Set-Content -LiteralPath (Join-Path $Release 'VERSION') -Value $Ver -Encoding ASCII }
    if ((Test-Path -LiteralPath (Join-Path $From '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
      $saved = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      try { $sha = [string](& git -C $From rev-parse HEAD 2>$null | Select-Object -First 1) } finally { $ErrorActionPreference = $saved }
      if ($LASTEXITCODE -eq 0 -and $sha -match '^[0-9a-f]{40}$') { Set-Content -LiteralPath (Join-Path $Release 'GIT_SHA') -Value $sha -Encoding ASCII }
    }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Release 'backend\main.py'))) { throw "$Release has no backend\main.py: this is not a POPPYS AI release." }
  if (-not (Test-VenvBase $Release)) { throw 'The release Python cannot be used by the service (see above).' }
  $python = Get-ReleasePython $Release
  if (-not $python) { throw "$Release has neither python\python.exe nor .venv\Scripts\python.exe. Run setup.ps1 in the source folder first." }
  Set-Junction (Join-Path $Release 'data') $DataDir
  Sync-OracleSetting $Release
  $deployDir = Join-Path $Release 'deploy'
  if (Test-Path -LiteralPath $deployDir) { Get-ChildItem -LiteralPath $deployDir -Recurse -Filter '*.ps1' -File | Unblock-File }
  Push-Location -LiteralPath $Release
  try {
    Write-Note 'Pre-compiling the app (the service account cannot write __pycache__) ...'
    [void](Invoke-Native $python @('-E', '-s', '-X', 'utf8', '-m', 'compileall', '-q', 'backend', 'scripts'))
    Write-Note 'Checking that the release Python can load the app with the service flags (-E -s -X utf8) ...'
    # No double quotes inside native arguments: Windows PowerShell 5.1 does not escape them on the command line.
    $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', '-c', 'import backend.main, sys; print(''app loads with Python '' + sys.version.split()[0])')
    if ($code -ne 0) { throw 'The release Python cannot load the app (see the error above). Run setup.ps1 in the source folder, then try again.' }
  } finally { Pop-Location }
}

function Write-ServiceXml([string]$Release, [string]$Target) {
  # Returns $true when PoppysAI.xml changed (the service registration is then refreshed).
  $template = Join-Path $Release 'deploy\windows\PoppysAI.xml'
  if (-not (Test-Path -LiteralPath $template)) { $template = Join-Path $PSScriptRoot 'PoppysAI.xml' }
  $text = [System.IO.File]::ReadAllText($template)
  if (Test-Path -LiteralPath (Join-Path $Release 'python\python.exe')) {
    $text = $text.Replace('%BASE%\current\.venv\Scripts\python.exe', '%BASE%\current\python\python.exe')
  }
  $changed = $true
  if (Test-Path -LiteralPath $Target) { $changed = ([System.IO.File]::ReadAllText($Target) -ne $text) }
  [System.IO.File]::WriteAllText($Target, $text, (New-Object System.Text.UTF8Encoding($false)))
  return $changed
}

function Find-WinSW([string]$Preferred) {
  $candidates = @()
  if ($Preferred) { $candidates += $Preferred }
  $candidates += @((Join-Path $PSScriptRoot 'PoppysAI.exe'), (Join-Path $PSScriptRoot 'WinSW-x64.exe'), (Join-Path $InstallRoot 'PoppysAI.exe'))
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c -PathType Leaf) { return (Resolve-Path -LiteralPath $c).ProviderPath } }
  return ''
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
if (-not $Source) { $Source = Join-Path $PSScriptRoot '..\..' }
if (-not (Test-Path -LiteralPath $Source -PathType Container)) { Stop-Install "Source folder not found: $Source" }
$Source = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Source).ProviderPath).TrimEnd('\')
if ($Source -ieq $CurrentLink) { $Source = (Get-JunctionTarget $CurrentLink).TrimEnd('\') }
$InPlace = $Source.StartsWith($ReleasesDir + '\', [System.StringComparison]::OrdinalIgnoreCase)

Write-Host 'POPPYS AI - Windows service install' -ForegroundColor Green
Write-Note "Source:       $Source"
Write-Note "Install root: $InstallRoot"

Write-Step '1. Checking the release files'
if (-not (Test-ReleaseChecksum $Source)) { Stop-Install 'The release files do not match SHA256SUMS. Copy the release again from the original zip; do not install it.' }
if ($InPlace) { $Version = Split-Path -Leaf $Source }
if (-not $Version) { $Version = Get-ReleaseVersion $Source }
$Release = Join-Path $ReleasesDir $Version
$Active = Get-JunctionTarget $CurrentLink
$ActiveVersion = ''
if ($Active) { $ActiveVersion = Split-Path -Leaf $Active }
Write-Ok "Release version: $Version"

$exe = ''
if (-not $StageOnly) {
  $exe = Find-WinSW $WinSW
  if (-not $exe) {
    Stop-Install ("WinSW is missing. Download WinSW-x64.exe version 2.12.0 (MIT licence) from https://github.com/winsw/winsw/releases/tag/v2.12.0 " +
      "(direct link: $WinSWUrl), rename it to PoppysAI.exe, put it in $PSScriptRoot and run this script again.")
  }
  $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($hash -ne $WinSWSha256) {
    if (-not $AllowUnknownWinSW) {
      Stop-Install "$exe is not WinSW-x64.exe 2.12.0 (its SHA-256 is $hash). Download it again from $WinSWUrl. To use another build on purpose, add -AllowUnknownWinSW."
    }
    Write-Warn "Using a WinSW build with SHA-256 $hash (not the checked 2.12.0 x64 one), because -AllowUnknownWinSW was given."
  } else { Write-Ok "WinSW-x64 2.12.0 found: $exe (SHA-256 matches)" }
}

Write-Step '2. Folders and permissions'
foreach ($dir in @($InstallRoot, $ReleasesDir, $DataDir, $LogDir, $ConfigDir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Set-FolderAccess $InstallRoot @("${Admins}:(OI)(CI)F", "${System}:(OI)(CI)F", "${LocalService}:(OI)(CI)RX", "${Users}:(OI)(CI)RX")
Set-FolderAccess $DataDir @("${Admins}:(OI)(CI)F", "${System}:(OI)(CI)F", "${LocalService}:(OI)(CI)M")
Set-FolderAccess $LogDir @("${Admins}:(OI)(CI)F", "${System}:(OI)(CI)F", "${LocalService}:(OI)(CI)M", "${Users}:(OI)(CI)RX")
Set-FolderAccess $ConfigDir @("${Admins}:(OI)(CI)F", "${System}:(OI)(CI)F", "${LocalService}:(OI)(CI)R")
Write-Ok "$InstallRoot is ready (the service can write only to data\ and logs\)."

if ($StageOnly) {
  Write-Step "3. Preparing release $Version (nothing else is changed)"
  if ($ActiveVersion -and $ActiveVersion -ieq $Version) {
    Write-Host "   Version $Version is already the active release; there is nothing to upgrade." -ForegroundColor Yellow
    exit 3
  }
  try { Initialize-Release -From $Source -Release $Release -Ver $Version -InPlace $InPlace } catch { Stop-Install "$($_.Exception.Message)" }
  Write-DeployHistory "STAGED $Version from $Source"
  Write-Ok "Release $Version is ready in $Release"
  Write-Output $Version
  exit 0
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service -and $ActiveVersion -and $ActiveVersion -ine $Version -and -not $Force) {
  Stop-Install ("Version $ActiveVersion is installed and running from $Active. To change to $Version use upgrade.ps1 " +
    '(it makes a backup first and rolls back automatically if the new version does not start). Add -Force only to reinstall without those safeguards.')
}
if ($service -and $service.Status -ne 'Stopped') {
  Write-Step 'Stopping the running service'
  Stop-Service -Name $ServiceName -Force
}
if (-not (Wait-PortFree 35)) {
  Stop-Install "Port $Port is still in use by another program. Close the start.ps1 window (or end the old python process in Task Manager) and run this script again."
}

Write-Step "3. Preparing release $Version"
try { Initialize-Release -From $Source -Release $Release -Ver $Version -InPlace $InPlace } catch { Stop-Install "$($_.Exception.Message)" }

Write-Step '4. Data and Oracle settings'
$liveDb = Join-Path $DataDir 'poppys.sqlite3'
$oldData = Join-Path $Source 'data'
$python = Get-ReleasePython $Release
if (Test-Path -LiteralPath $liveDb) {
  Write-Ok "Using the existing database $liveDb"
  if (-not $InPlace -and (Test-Path -LiteralPath (Join-Path $oldData 'poppys.sqlite3'))) {
    Write-Note "(The older copy in $oldData is not used and not changed.)"
  }
} elseif (-not $InPlace -and (Test-Path -LiteralPath (Join-Path $oldData 'poppys.sqlite3'))) {
  Write-Note "First install: copying the database from $oldData with a verified backup (the original stays as it is) ..."
  $migration = Join-Path $DataDir 'backups\first-install'
  $backupPy = Join-Path $Release 'scripts\backup.py'
  $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', $backupPy, '--data-dir', $oldData, '--app-root', $Source, '--dest', $migration,
    '--label', 'first-install', '--no-prune', '--log', 'none')
  if ($code -ne 0) { Stop-Install "The backup of $oldData failed (exit code $code); nothing was installed as a service yet." }
  $zip = Get-ChildItem -LiteralPath $migration -Filter 'poppys-backup-*.zip' | Sort-Object Name -Descending | Select-Object -First 1
  $code = Invoke-Native $python @('-E', '-s', '-X', 'utf8', $backupPy, '--restore', $zip.FullName, '--to', $DataDir, '--log', 'none')
  if ($code -ne 0) { Stop-Install "Restoring the database into $DataDir failed (exit code $code)." }
  $rc = @($oldData, $DataDir, '*.upload', 'schema_discovery.json', 'schema_discovery.md', '/XC', '/XN', '/XO', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
  $code = Invoke-Native 'robocopy.exe' $rc
  if ($code -ge 8) { Write-Warn "Some uploaded files could not be copied from $oldData (robocopy exit code $code)." }
  Write-Ok "The database and uploaded files are now in $DataDir (first-install backup: $($zip.FullName))"
} else {
  Write-Note 'No existing database: POPPYS AI starts empty and asks for the first administrator account.'
}
$master = Join-Path $ConfigDir '.env.poppys'
$sourceEnv = Join-Path $Source '.env.poppys'
if (-not (Test-Path -LiteralPath $master) -and -not $InPlace -and (Test-Path -LiteralPath $sourceEnv)) {
  Copy-Item -LiteralPath $sourceEnv -Destination $master
  Protect-SettingsFile $master
  Write-Ok "Copied .env.poppys into $ConfigDir (the original in $Source can be deleted once the service works)."
  Sync-OracleSetting $Release
}

Write-Step '5. Service'
Set-Junction $CurrentLink $Release
Write-Ok "current -> $Release"
$installedExe = Join-Path $InstallRoot 'PoppysAI.exe'
if ($exe -ine $installedExe) { Copy-Item -LiteralPath $exe -Destination $installedExe -Force }
Unblock-File -LiteralPath $installedExe
[void](Write-ServiceXml $Release (Join-Path $InstallRoot 'PoppysAI.xml'))
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
  Write-Note 'Removing the old service registration, so every setting in PoppysAI.xml is applied again ...'
  $code = Invoke-Native $installedExe @('uninstall')
  if ($code -ne 0) { Stop-Install "PoppysAI.exe uninstall failed (exit code $code)." }
  if (-not (Wait-ServiceGone 30)) { Stop-Install 'Windows has not removed the old service yet ("marked for deletion"). Close the Services window and Task Manager, then run this script again.' }
}
$code = Invoke-Native $installedExe @('install')
if ($code -ne 0) { Stop-Install "PoppysAI.exe install failed (exit code $code). See $LogDir\PoppysAI.wrapper.log and Event Viewer > Windows Logs > Application." }
Write-Ok 'Service PoppysAI registered: runs as LOCAL SERVICE, starts automatically (delayed) and restarts after a crash.'

if ($NoStart) {
  Write-DeployHistory "INSTALL $Version OK (not started)"
  Write-Host ''; Write-Host "Installed $Version. Start it with: Start-Service PoppysAI" -ForegroundColor Green
  exit 0
}
Write-Step '6. Starting POPPYS AI'
try { Start-Service -Name $ServiceName } catch { Show-LogTail; Stop-Install "The service did not start: $($_.Exception.Message)" }
$how = Wait-PoppysReady $ReadySeconds
if (-not $how) {
  Show-LogTail
  Stop-Install "POPPYS AI did not answer on http://127.0.0.1:$Port within $ReadySeconds s. Read the log lines above and docs\RUNBOOK.md."
}
Write-DeployHistory "INSTALL $Version OK"
Write-Ok "POPPYS AI answers ($how)."
Write-Host ''
Write-Host "Installed and running: POPPYS AI $Version at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Logs: $LogDir   Data: $DataDir"
Write-Host 'Next: register the nightly backup with deploy\windows\backup-task.ps1 (see docs\RUNBOOK.md).'
exit 0
