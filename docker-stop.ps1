# Stop POPPYS AI in Docker (docs/DOCKER.md). Data stays in the poppys-data volume.
#   .\docker-stop.ps1           stop POPPYS AI; Oracle keeps running
#   .\docker-stop.ps1 -Oracle   also stop the Oracle container (oracle26ai)
param([switch]$Oracle, [string]$OracleContainer = 'oracle26ai')
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
docker compose stop
if ($LASTEXITCODE -ne 0) { Write-Host 'POPPYS AI could not be stopped. Is Docker Desktop running?' -ForegroundColor Red; exit 1 }
Write-Host 'POPPYS AI stopped.' -ForegroundColor Green
if ($Oracle) {
  docker stop $OracleContainer *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "$OracleContainer stopped." -ForegroundColor Green }
  else { Write-Host "$OracleContainer was not running or does not exist." -ForegroundColor Yellow }
}
