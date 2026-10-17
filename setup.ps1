# One-time setup on the Windows laptop. Safe to run again.
#   .\setup.ps1        the app's runtime packages only (requirements.txt): what the laptop needs to run POPPYS AI
#   .\setup.ps1 -Dev   also the test and CI tools (requirements-dev.txt: pytest, ruff, pip-audit), for developers
param([switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv')) {
  # The product targets Python 3.13 (the release ships embeddable CPython 3.13.x; docs/RESEARCH_DECISIONS.md).
  $ErrorActionPreference = 'Continue'   # Windows PowerShell 5.1 turns redirected native stderr into errors under 'Stop'
  & py -3.13 -c "import sys" 2>$null
  $found = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = 'Stop'
  if (-not $found) { throw 'Python 3.13 (64-bit) is required. Install it from python.org (tick "py launcher"), then run .\setup.ps1 again.' }
  py -3.13 -m venv .venv
}
$version = & .\.venv\Scripts\python.exe -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($version -ne '3.13') {
  Write-Warning ".venv uses Python $version; POPPYS AI targets 3.13. To switch: delete the .venv folder and run .\setup.ps1 again."
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
$requirements = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
Write-Host "Installing the pinned packages from $requirements..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pip install -r $requirements
if (-not (Test-Path '.env.poppys')) {
  Write-Host 'Creating the restricted POPPYS_APP Oracle account (existing GARMENT_DEMO data is not touched)...' -ForegroundColor Cyan
  .\.venv\Scripts\python.exe scripts\bootstrap_local.py
}
Write-Host 'Granting read access for the explorer and insert access for verified BOM tables...' -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\grant_bom_tables.py
.\.venv\Scripts\python.exe scripts\check_oracle.py
Write-Host 'Setup finished. Next: python scripts\discover_schema.py (asks for the GARMENT_DEMO password), then .\start.ps1' -ForegroundColor Green
