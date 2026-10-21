# Bring the newest POPPYS AI from GitHub into the running app, keeping the share link (docs/DOCKER.md, "Update").
#   .\update-live.ps1      git pull (this branch, fast-forward only) -> rebuild the image -> restart only POPPYS AI
# The share-online.cmd window can stay open: the Cloudflare link points at this laptop's port 8008, so the same link
# shows the new version after a short restart (about 10-30 seconds). Oracle and its data are never touched.
param([string]$OracleContainer = 'oracle26ai')
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$local = 'http://127.0.0.1:8008'

function Fail($message) { Write-Host "`n$message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }

Step 'Newest version from GitHub'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git is not installed. Install Git for Windows, then run update-live.cmd again.' }
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if (git status --porcelain --untracked-files=no) {
  Fail "Files of POPPYS AI were changed on this laptop, so the update stops (nothing was changed).`nKeep them aside with:  git stash   then run update-live.cmd again."
}
$before = (git rev-parse HEAD).Trim()
git pull --ff-only origin $branch
if ($LASTEXITCODE -ne 0) { Fail 'git pull did not work (internet, or the branch moved in another way). Nothing was changed.' }
$after = (git rev-parse HEAD).Trim()
if ($before -eq $after) { Write-Host 'Already the newest version.' -ForegroundColor Green }
else { git log --oneline "$before..$after" }

Step 'Docker'
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker is not running. Open Docker Desktop, then run update-live.cmd again.' }
# Shared through the Cloudflare link now? Then the new version is started shared too, so the link keeps working.
$env:POPPYS_REMOTE = if ((docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' poppys-ai 2>$null) -match '^POPPYS_REMOTE=1$') { '1' } else { '0' }
Write-Host ($(if ($env:POPPYS_REMOTE -eq '1') { 'POPPYS AI is shared through the link: it stays shared.' } else { 'POPPYS AI runs on this laptop only.' }))

Step 'Build the new version'
docker compose build
if ($LASTEXITCODE -ne 0) { Fail 'The new version could not be built (see above). The running version was not changed.' }

Step 'Restart POPPYS AI (Oracle keeps running)'
$oracle = docker inspect -f '{{.State.Running}}' $OracleContainer 2>$null
if ($LASTEXITCODE -eq 0 -and $oracle -ne 'true') { docker start $OracleContainer *> $null }
docker compose up -d app
if ($LASTEXITCODE -ne 0) { Fail 'POPPYS AI could not start. Run: docker compose logs app' }
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
  try { $ok = (Invoke-WebRequest "$local/healthz" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { Fail 'POPPYS AI did not answer. Run: docker compose logs app' }
Write-Host "`nUpdated. $local$(if ($env:POPPYS_REMOTE -eq '1') { ' and the same share link' }) now show the new version." -ForegroundColor Green
Write-Host 'People who have the page open: press F5 (refresh) once.'
