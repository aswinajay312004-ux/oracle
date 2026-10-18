# Local verification on the Windows laptop, in one command:
#   .\verify_local.ps1
# 1. Oracle connection check (scripts\check_oracle.py)
# 2. Every automated test, including the local Oracle ones (POPPYS_TEST_ORACLE=1)
# 3. Ask AI accuracy check on the real data (scripts\run_benchmark.py)
# Everything is also written to data\local_verification.txt (git-ignored) to paste into the next session.
# The Oracle tests save a few orders whose reference starts with TEST- in GARMENT_DEMO (as Demo A would), plus
# any buyer, colour or size names those test orders need. Nothing is updated or deleted.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$python = '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'No .venv here yet. Run .\setup.ps1 -Dev first.' }
$encoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # Python writes UTF-8 below; read it as UTF-8 ("→", "≈")
$env:PYTHONUTF8 = '1'
$env:POPPYS_TEST_ORACLE = '1'
$report = Join-Path $PSScriptRoot 'data\local_verification.txt'
New-Item -ItemType Directory -Force (Split-Path $report) | Out-Null
"POPPYS AI local verification, $(Get-Date -Format 'yyyy-MM-dd HH:mm'), commit $(git rev-parse --short HEAD 2>$null)" | Set-Content $report -Encoding utf8

$steps = [ordered]@{
  'Oracle connection'             = @('scripts\check_oracle.py')
  'All tests (with local Oracle)' = @('-m', 'pytest', '-q', '-rfEs')
  'Ask AI accuracy'               = @('scripts\run_benchmark.py')
}
$results = [ordered]@{}
try {
  foreach ($name in $steps.Keys) {
    Write-Host "`n== $name ==" -ForegroundColor Cyan
    $started = Get-Date
    # Each line is shown as it comes and kept for the report.
    $lines = & $python @($steps[$name]) 2>&1 | ForEach-Object { $text = "$_"; Write-Host $text; $text }
    $results[$name] = $LASTEXITCODE
    "`n== $name (exit code $LASTEXITCODE, $([int]((Get-Date) - $started).TotalSeconds) s) ==" | Add-Content $report -Encoding utf8
    $lines | Add-Content $report -Encoding utf8
  }
} finally {
  [Console]::OutputEncoding = $encoding
}

Write-Host "`n== Summary ==" -ForegroundColor Cyan
"`n== Summary ==" | Add-Content $report -Encoding utf8
foreach ($name in $results.Keys) {
  $ok = $results[$name] -eq 0
  $line = '{0,-32} {1}' -f $name, $(if ($ok) { 'PASSED' } else { 'FAILED' })
  Write-Host $line -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
  $line | Add-Content $report -Encoding utf8
}
Write-Host "`nFull output saved in $report"
