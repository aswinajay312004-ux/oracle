# Share POPPYS AI with people on other computers for a demo (docs/DOCKER.md, "Share with other computers").
#   .\share-online.ps1      start sharing: prints an https link (also copied) that works in any browser, anywhere
# Free Cloudflare "quick tunnel": no account, no installation on the other computers. The link changes every time.
# Sharing lasts while this window is open; Ctrl+C or closing it stops the link. Ctrl+C (or .\docker-start.ps1) also
# switches POPPYS AI back to this-laptop-only. Oracle (port 1521) is never shared, only the POPPYS AI pages.
param([string]$OracleContainer = 'oracle26ai')
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$local = 'http://127.0.0.1:8008'

function Fail($message) { Write-Host "`n$message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }
function Healthy {
  try { return (Invoke-WebRequest "$local/healthz" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { return $false }
}
function StartApp($remote) {
  $env:POPPYS_REMOTE = $remote
  docker compose up -d *> $null
  $ok = $false
  for ($i = 0; $i -lt 45 -and -not $ok; $i++) { $ok = Healthy; if (-not $ok) { Start-Sleep -Seconds 2 } }
  return $ok
}

Step 'POPPYS AI'
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker is not running. Run docker-start.cmd first, then share-online.cmd again.' }
if (-not (docker images -q poppys-ai:local 2>$null)) { Fail 'POPPYS AI is not built yet. Run docker-start.cmd first, then share-online.cmd again.' }
$oracle = docker inspect -f '{{.State.Running}}' $OracleContainer 2>$null
if ($LASTEXITCODE -eq 0 -and $oracle -ne 'true') { docker start $OracleContainer *> $null }
if (-not (StartApp '1')) { Fail 'POPPYS AI did not start. Run docker-start.cmd and read its messages.' }
Write-Host 'POPPYS AI is running and may be opened through the share link.' -ForegroundColor Green

Step 'Cloudflare tunnel program (free, only on this laptop)'
$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
foreach ($p in @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe", "$PSScriptRoot\tools\cloudflared.exe")) {
  if (-not $cloudflared -and (Test-Path $p)) { $cloudflared = $p }
}
if (-not $cloudflared) {
  New-Item -ItemType Directory -Force -Path "$PSScriptRoot\tools" | Out-Null
  $target = "$PSScriptRoot\tools\cloudflared.exe"
  Write-Host 'Downloading cloudflared from Cloudflare''s GitHub page (one time, about 60 MB)...'
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $target -UseBasicParsing
    $cloudflared = $target
  } catch {
    Remove-Item $target -ErrorAction SilentlyContinue
    Fail 'cloudflared could not be downloaded. Install it once with:  winget install --id Cloudflare.cloudflared  then run share-online.cmd again.'
  }
}
Write-Host "Using $cloudflared" -ForegroundColor Green

Step 'Share link'
$log = Join-Path $env:TEMP 'poppys-share.log'
Remove-Item $log, "$log.out" -ErrorAction SilentlyContinue
$tunnel = Start-Process -FilePath $cloudflared -ArgumentList @('tunnel', '--no-autoupdate', '--url', $local) `
  -RedirectStandardError $log -RedirectStandardOutput "$log.out" -NoNewWindow -PassThru
$link = $null
for ($i = 0; $i -lt 60 -and -not $link -and -not $tunnel.HasExited; $i++) {
  Start-Sleep -Seconds 1
  $hit = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($hit) { $link = $hit.Matches[0].Value }
}
try {
  if (-not $link) {
    Get-Content $log -Tail 15 -ErrorAction SilentlyContinue
    Fail 'Cloudflare did not give a link (see the lines above). Check the internet connection and run share-online.cmd again.'
  }
  try { Set-Clipboard $link } catch { }
  Write-Host ''
  Write-Host '  ============================================================' -ForegroundColor Green
  Write-Host "    $link" -ForegroundColor Green
  Write-Host '  ============================================================' -ForegroundColor Green
  Write-Host ''
  Write-Host '  Send this link to your friends / clients (it is already copied: paste it in WhatsApp).'
  Write-Host '  They open it in Chrome or Edge and sign in with the POPPYS user name and password.'
  Write-Host '  The link starts working within about 30 seconds. Try it on your phone first.'
  Write-Host ''
  Write-Host '  Keep this window open while they use it. The laptop must stay on and must not sleep.'
  Write-Host '  To stop sharing: press Ctrl+C here.' -ForegroundColor Yellow
  while (-not $tunnel.HasExited) { Start-Sleep -Seconds 2 }
  Write-Host "`nThe tunnel program stopped. Run share-online.cmd again for a new link." -ForegroundColor Yellow
} finally {
  if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
  Write-Host "`nStopping the share link; POPPYS AI goes back to this laptop only..."
  if (StartApp '0') { Write-Host "Sharing stopped. POPPYS AI is at $local on this laptop only." -ForegroundColor Green }
  else { Write-Warning 'POPPYS AI did not come back. Run docker-start.cmd.' }
}
