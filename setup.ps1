# One-time setup on the Windows laptop. Safe to run again.
#   .\setup.ps1        the app's runtime packages only (requirements.txt): what the laptop needs to run POPPYS AI
#   .\setup.ps1 -Dev   also the test and CI tools (requirements-dev.txt: pytest, ruff, pip-audit), for developers
# Every step checks the exit code of the program it runs and stops with "FAILED: ..." and what to do, so a failed
# step is never followed by "Setup finished".
param([switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
# Windows PowerShell 5.1 turns the stderr of a program into an error under 'Stop' when output is redirected;
# the exit codes are checked after every program instead.
$ErrorActionPreference = 'Continue'

function Stop-Setup([string]$Text) {
  Write-Host ''
  Write-Host "FAILED: $Text" -ForegroundColor Red
  exit 1
}

$venvPython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path '.venv')) {
  # The product targets Python 3.13 (the release ships embeddable CPython 3.13.x; docs/RESEARCH_DECISIONS.md).
  # Get-Command first: when py is missing, $LASTEXITCODE would still hold the result of an earlier program.
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Stop-Setup 'The Python launcher (py) was not found. Install Python 3.13 (64-bit) from python.org: tick "py launcher" and, for the Windows service, "Install for all users". Then run .\setup.ps1 again.'
  }
  $global:LASTEXITCODE = 1
  & py -3.13 -c "import sys" 2>$null
  if ($LASTEXITCODE -ne 0) { Stop-Setup 'Python 3.13 (64-bit) is required. Install it from python.org (tick "py launcher"), then run .\setup.ps1 again.' }
  & py -3.13 -m venv .venv
  if ($LASTEXITCODE -ne 0) { Stop-Setup 'Creating .venv failed (see the message above). Delete the .venv folder if there is one, then run .\setup.ps1 again.' }
}
if (-not (Test-Path $venvPython)) { Stop-Setup '.venv is incomplete (no .venv\Scripts\python.exe). Delete the .venv folder, then run .\setup.ps1 again.' }
$version = & $venvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($LASTEXITCODE -ne 0) { Stop-Setup '.venv\Scripts\python.exe does not run. Delete the .venv folder, then run .\setup.ps1 again.' }
if ($version -ne '3.13') {
  Write-Warning ".venv uses Python $version; POPPYS AI targets 3.13. To switch: delete the .venv folder and run .\setup.ps1 again."
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Stop-Setup 'Updating pip failed (see above). Check the internet connection (or the proxy settings), then run .\setup.ps1 again.' }
$requirements = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
Write-Host "Installing the pinned packages from $requirements..." -ForegroundColor Cyan
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { Stop-Setup "Installing the packages from $requirements failed (see above). Check the internet connection (or the proxy settings), then run .\setup.ps1 again." }

# The next steps talk to Oracle in Docker.
$container = 'oracle26ai'
if ($env:POPPYS_ORACLE_CONTAINER) { $container = $env:POPPYS_ORACLE_CONTAINER }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Stop-Setup 'Docker was not found. Install and start Docker Desktop, wait until it says "Engine running", then run .\setup.ps1 again.'
}
$running = & docker inspect -f '{{.State.Running}}' $container 2>$null
if ($LASTEXITCODE -ne 0 -or [string]$running -ne 'true') {
  Stop-Setup "The Oracle container $container is not running. Start Docker Desktop, then run: docker start $container ; wait until docker ps shows (healthy), then run .\setup.ps1 again."
}

if (-not (Test-Path '.env.poppys')) {
  Write-Host 'Creating the restricted POPPYS_APP Oracle account (existing GARMENT_DEMO data is not touched)...' -ForegroundColor Cyan
  & $venvPython scripts\bootstrap_local.py
  if ($LASTEXITCODE -ne 0) { Stop-Setup 'Creating the POPPYS_APP account failed (see the message above). Check that Oracle is healthy (docker ps), then run .\setup.ps1 again.' }
}
Write-Host 'Granting read access for the explorer and insert access for verified BOM tables...' -ForegroundColor Cyan
& $venvPython scripts\grant_bom_tables.py
if ($LASTEXITCODE -ne 0) { Stop-Setup 'Granting the Oracle permissions failed (see the message above). Check that Oracle is healthy (docker ps), then run .\setup.ps1 again.' }
& $venvPython scripts\check_oracle.py
if ($LASTEXITCODE -ne 0) { Stop-Setup 'The Oracle check found a problem (see the message above). Fix it as it says, then run .\setup.ps1 again.' }
Write-Host 'Setup finished. Next: .\.venv\Scripts\python.exe scripts\discover_schema.py (asks for the GARMENT_DEMO password), then .\start.ps1' -ForegroundColor Green
