# Start POPPYS AI in Docker (docs/DOCKER.md). Safe to run again; it only starts what is not running.
#   .\docker-start.ps1                 start Oracle (oracle26ai) and POPPYS AI, then open the browser
#   .\docker-start.ps1 -Rebuild        also rebuild the POPPYS AI image (after git pull)
#   .\docker-start.ps1 -OracleContainer my-oracle   when the Oracle container has another name
# Oracle keeps its own container and data; this script only starts it, never creates, replaces or removes it.
param([switch]$Rebuild, [string]$OracleContainer = 'oracle26ai', [switch]$NoBrowser)
# Windows PowerShell 5.1 turns a native program's stderr (docker prints progress there) into errors under 'Stop',
# so every docker call is checked through its exit code instead.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$url = 'http://127.0.0.1:8008'

function Fail($message) { Write-Host "`n$message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }
function DockerReady { docker info *> $null; return ($LASTEXITCODE -eq 0) }
function PortOpen($port) {
  $client = New-Object Net.Sockets.TcpClient
  try { $client.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $client.Close() }
}

Step 'Docker'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker is not installed. Install Docker Desktop, then run .\docker-start.ps1 again.' }
if (-not (DockerReady)) {
  $desktop = Join-Path $Env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (Test-Path $desktop) { Write-Host 'Starting Docker Desktop...'; Start-Process $desktop }
  for ($i = 0; $i -lt 60 -and -not (DockerReady); $i++) { Start-Sleep -Seconds 3 }
  if (-not (DockerReady)) { Fail 'Docker is not running. Open Docker Desktop, wait until it says "Engine running", then run .\docker-start.ps1 again.' }
}
Write-Host 'Docker is running.' -ForegroundColor Green

if (-not (Test-Path '.env.poppys' -PathType Leaf)) {
  Fail '.env.poppys is missing (it holds the Oracle accounts). Run .\setup.ps1 once, then run .\docker-start.ps1 again.'
}

Step "Oracle ($OracleContainer)"
$state = docker inspect -f '{{.State.Running}}' $OracleContainer 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Warning "No container named $OracleContainer. POPPYS AI will start, but saving waits until Oracle is reachable on port 1521."
} elseif ($state -ne 'true') {
  docker start $OracleContainer *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Could not start $OracleContainer. Open Docker Desktop -> Containers and start it." }
  Write-Host 'Waiting for Oracle to accept connections (up to 3 minutes)...'
  for ($i = 0; $i -lt 90 -and -not (PortOpen 1521); $i++) { Start-Sleep -Seconds 2 }
}
if (PortOpen 1521) { Write-Host 'Oracle is reachable on port 1521.' -ForegroundColor Green }
else { Write-Warning 'Oracle is not reachable on port 1521 yet. POPPYS AI starts anyway; saving waits for Oracle.' }

Step 'POPPYS AI'
$running = docker ps -q -f 'name=^poppys-ai$' 2>$null
if (-not $running -and (PortOpen 8008)) {
  Fail 'Port 8008 is already used, probably by POPPYS AI started with .\start.ps1. Press Ctrl+C in that window (or close it), then run .\docker-start.ps1 again.'
}
$image = docker images -q poppys-ai:local 2>$null
if ($Rebuild -or -not $image) {
  Write-Host 'Building the POPPYS AI image (the first time takes a few minutes and needs internet)...'
  docker compose build
  if ($LASTEXITCODE -ne 0) { Fail 'The image could not be built. See the messages above.' }
}

# The first time, the users, drafts and uploads of the .\start.ps1 installation (.\data) are copied into the Docker
# data volume, so nothing is lost when moving to Docker. Later starts never copy again.
docker compose run --rm --no-deps app python -c "import pathlib,sys; sys.exit(0 if pathlib.Path('/app/data/poppys.sqlite3').exists() else 1)" *> $null
if ($LASTEXITCODE -ne 0 -and (Test-Path 'data\poppys.sqlite3')) {
  Write-Host 'Copying the existing POPPYS data (users, drafts, uploaded files) into Docker, once...'
  $from = (Resolve-Path 'data').Path
  docker compose run --rm --no-deps --user root -v "${from}:/import:ro" app sh -c "cp -a /import/. /app/data/ && chown -R poppys:poppys /app/data"
  if ($LASTEXITCODE -ne 0) { Fail 'The existing data could not be copied. Nothing was changed in .\data.' }
}

docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail 'POPPYS AI could not start. See the messages above, or run: docker compose logs app' }
Write-Host 'Waiting for POPPYS AI...'
$ok = $false
for ($i = 0; $i -lt 45 -and -not $ok; $i++) {
  try { $ok = (Invoke-WebRequest "$url/healthz" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { Fail 'POPPYS AI did not answer. Run: docker compose logs app' }

try {
  $ready = Invoke-RestMethod "$url/readyz" -TimeoutSec 10
  foreach ($c in $ready.checks) {
    $color = @{ pass = 'Green'; warn = 'Yellow'; fail = 'Red' }[$c.status]
    Write-Host ("  {0,-12} {1} {2}" -f $c.name, $c.status, $c.detail) -ForegroundColor $color
  }
} catch { }
Write-Host "`nPOPPYS AI is running at $url" -ForegroundColor Green
Write-Host 'Stop it with .\docker-stop.ps1. It starts again by itself when Docker Desktop starts.'
if (-not $NoBrowser) { Start-Process $url }
