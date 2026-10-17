<#
The same checks CI runs (.github/workflows/ci.yml), for the Windows laptop. Mirrors scripts/check.sh.

  .\scripts\check.ps1                  secret scan, lint, JavaScript syntax and unit tests
  .\scripts\check.ps1 lint             one step: secrets | lint | js | tests | oracle
  .\scripts\check.ps1 tests -k api     anything after the step name goes to pytest
  .\scripts\check.ps1 oracle           the suite with the real-Oracle tests on (needs .env.poppys)

$env:PYTHON picks the interpreter (default: the .venv one). Dev tools: pip install -r requirements-dev.txt
Keep this file ASCII-only: Windows PowerShell 5.1 reads scripts without a BOM as ANSI.
#>
# No param block on purpose: pytest flags such as -s or -k would be taken for PowerShell parameters.
$Step = if ($args.Count) { [string]$args[0] } else { 'all' }
$PytestArgs = @($args | Select-Object -Skip 1)   # always an array, even for one argument
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $env:PYTHON
if (-not $py) {
  $py = @('.venv\Scripts\python.exe', '.venv/bin/python') | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $py) { $py = 'python' }
}

function Write-Step($text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Write-Fail($text) { Write-Host "FAILED: $text" -ForegroundColor Red; return $false }
function Write-Ok($text) { Write-Host "OK: $text" -ForegroundColor Green; return $true }
function Test-Tool($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
# A missing tool skips the step on the laptop but fails on CI, so CI never passes by skipping.
function Skip-Missing($name) {
  if ($env:CI -eq 'true') { return (Write-Fail "$name is required on CI.") }
  Write-Host "Skipped: $name is not installed." -ForegroundColor Yellow; return $true
}

# Same patterns as scripts/check.sh (POSIX regex, read by git grep -f). Only file:line is printed, never the text.
$SecretPatterns = @(
  '(ORACLE_PASSWORD|APP_USER_PASSWORD|POPPYS_REPLICA_PASSWORD|POPPYS_ORACLE_PASSWORD)[[:space:]]*[=:][[:space:]]*["'']?[A-Za-z0-9]'
  '(^|[^A-Za-z0-9])Pp[0-9a-f]{48}([^0-9a-f]|$)'
  '[A-Za-z][A-Za-z0-9_$#]*/[A-Za-z0-9][^[:space:]/@"'']*@[A-Za-z0-9_.-]+(:[0-9]+)?/[A-Za-z0-9_.]+'
  '-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}'
)
$SecretFiles = '(^|/)\.env($|\.)|^data/|\.(pem|key|p12|pfx|jks|sqlite3?|db)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)($|\.)'

function Test-Secrets {
  Write-Step 'Secret scan: no settings files, local data or passwords in the repository'
  if (-not (Test-Tool git)) { return (Skip-Missing 'git') }
  $bad = $false
  # Tracked files plus new files that are not ignored yet, so a leak is caught before it is committed.
  $files = @(git ls-files) + @(git ls-files --others --exclude-standard)
  foreach ($f in $files) {
    if ($f -match $SecretFiles -and $f -notmatch '(^|/)\.env\.example$') { Write-Host "  must not be committed: $f"; $bad = $true }
  }
  git check-ignore -q .env.poppys
  if ($LASTEXITCODE -ne 0) { Write-Host '  .gitignore no longer ignores .env.poppys'; $bad = $true }
  # Patterns go through a file: Windows PowerShell 5.1 mangles quotes in native command arguments.
  $list = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($list, ($SecretPatterns -join "`n") + "`n")   # LF: one pattern per line
    $hits = @(git grep --untracked -n -I -E -f $list -- .)
    # git grep exits 1 when nothing matches; anything above 1 is a broken scan, which must not pass as clean.
    if ($LASTEXITCODE -gt 1) { Write-Host '  the scan itself failed (git grep)'; $bad = $true }
  } finally { Remove-Item $list -Force }
  foreach ($h in $hits) { $where = ($h -split ':')[0..1] -join ':'; Write-Host "  possible password or key at $where"; $bad = $true }
  if ($bad) { return (Write-Fail 'remove the file or value above (passwords belong in .env.poppys, which git ignores).') }
  return (Write-Ok 'nothing secret is committed.')
}

function Test-Lint {
  Write-Step 'Lint: ruff (rules in pyproject.toml)'
  $ErrorActionPreference = 'Continue'   # PowerShell 5.1 turns redirected native stderr into errors under 'Stop'
  & $py -m ruff --version *> $null
  if ($LASTEXITCODE -ne 0) { return (Skip-Missing 'ruff (pip install -r requirements-dev.txt)') }
  & $py -m ruff check . | Out-Host
  return ($LASTEXITCODE -eq 0)
}

function Test-Js {
  Write-Step 'JavaScript: syntax of frontend/js/*.js and the strict content-security rules'
  if (-not (Test-Tool node)) { return (Skip-Missing 'node') }
  $bad = $false
  $modules = @(Get-ChildItem frontend/js -Filter *.js)
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('poppys-js-' + [guid]::NewGuid())
  New-Item -ItemType Directory $tmp | Out-Null
  try {
    # The files are ES modules; node only parses them as modules under an .mjs name.
    foreach ($m in $modules) {
      $copy = Join-Path $tmp ($m.BaseName + '.mjs')
      Copy-Item $m.FullName $copy
      & node --check $copy | Out-Host
      if ($LASTEXITCODE -ne 0) { Write-Host "  ^ frontend/js/$($m.Name)"; $bad = $true }
    }
  } finally { Remove-Item $tmp -Recurse -Force }
  # The page runs under script-src 'self': no inline scripts or on*= handlers, no scripts or styles from other sites.
  $page = 'frontend/index.html'; $code = $modules | ForEach-Object { $_.FullName }
  $rules = @(
    @{ files = @($page); pattern = '<script(\s[^>]*)?>'; skip = 'src='; why = 'inline <script> in index.html' },
    @{ files = @($page) + $code; pattern = '<[a-zA-Z][^>]*\son[a-z]+\s*='; skip = $null; why = 'inline on*= handler (use addEventListener)' },
    @{ files = @($page) + $code; pattern = '(src|href)\s*=\s*["'']?(https?:)?//'; skip = $null; why = 'resource from another site (the app is offline)' },
    @{ files = $code; pattern = '\beval\s*\(|new Function\s*\(|set(Timeout|Interval)\s*\(\s*["''`]'; skip = $null; why = 'code built from strings' }
  )
  foreach ($r in $rules) {
    $found = @(Select-String -Path $r.files -Pattern $r.pattern -CaseSensitive | Where-Object { -not $r.skip -or $_.Line -notmatch $r.skip })
    foreach ($x in $found) { Write-Host "  $(Resolve-Path -Relative $x.Path):$($x.LineNumber): $($r.why)"; $bad = $true }
  }
  if ($bad) { return (Write-Fail 'fix the JavaScript above.') }
  return (Write-Ok "$($modules.Count) modules parse and follow the content-security rules.")
}

function Test-Unit {
  Write-Step 'Unit tests: pytest (stand-in Oracle and SQLite, no database needed)'
  & $py -m pytest -q @PytestArgs | Out-Host
  return ($LASTEXITCODE -eq 0)
}

function Test-Oracle {
  # The whole suite with the opt-in switch on, so every real-Oracle test (test_oracle, test_oracle_insight,
  # acceptance E/F, large-table checks, and any added later) runs. It writes only TEST-labelled orders.
  Write-Step 'Real-Oracle tests: the whole suite with POPPYS_TEST_ORACLE=1'
  $before = $env:POPPYS_TEST_ORACLE; $env:POPPYS_TEST_ORACLE = '1'
  try { & $py -m pytest -q @PytestArgs | Out-Host }
  finally { $env:POPPYS_TEST_ORACLE = $before }
  return ($LASTEXITCODE -eq 0)
}

$steps = [ordered]@{ secrets = 'Test-Secrets'; lint = 'Test-Lint'; js = 'Test-Js'; tests = 'Test-Unit'; oracle = 'Test-Oracle' }
if ($Step -eq 'all') {
  # Run every step, then report, so one run shows every problem.
  $failed = @(foreach ($s in 'secrets', 'lint', 'js', 'tests') { if (-not (& $steps[$s] | Select-Object -Last 1)) { $s } })
  if ($failed.Count) { Write-Host ''; Write-Fail ($failed -join ', ') | Out-Null; exit 1 }
  Write-Host ''; Write-Host 'All checks passed.' -ForegroundColor Green; exit 0
}
if (-not $steps.Contains($Step)) { Write-Host "Unknown step '$Step'. Use: secrets, lint, js, tests or oracle." -ForegroundColor Red; exit 2 }
if (& $steps[$Step] | Select-Object -Last 1) { exit 0 } else { exit 1 }
