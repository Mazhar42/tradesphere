# Starts the local showcase. It uses its own database (Docker project "tradesphere-demo"),
# separate from your development data.
#
#   pwsh -ExecutionPolicy Bypass -File docs\demo\demo.ps1           start with the data as it is
#   pwsh -ExecutionPolicy Bypass -File docs\demo\demo.ps1 -Reset    wipe the DEMO data and rebuild it
#
# Reset on the day of the demonstration: "today's sales" and the overdue ages follow the date.
param([switch]$Reset)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$demo = @('compose', '-p', 'tradesphere-demo')

function Invoke-Docker {
    docker @args
    if ($LASTEXITCODE -ne 0) { throw "docker $($args -join ' ') failed" }
}

# The development stack uses the same ports. Stopping it keeps its data.
Invoke-Docker compose stop

docker image inspect tradesphere-api *> $null
if ($LASTEXITCODE -ne 0) { Invoke-Docker build --target dev -t tradesphere-api ./backend }

if ($Reset) { Invoke-Docker @demo down -v }
Invoke-Docker @demo up -d --no-build api worker

Write-Host 'Waiting for the application...'
$deadline = (Get-Date).AddMinutes(3)
while ($true) {
    try { Invoke-RestMethod 'http://localhost:8000/api/health' | Out-Null; break }
    catch {
        if ((Get-Date) -gt $deadline) { throw 'The API did not start. See: docker compose -p tradesphere-demo logs' }
        Start-Sleep -Seconds 2
    }
}

if ($Reset) {
    Invoke-Docker @demo exec -T api python -m app.cli create-owner --username demo.owner --full-name 'Rahim Uddin' --password 'Meghna-Demo-2026'
    Invoke-Docker @demo exec -T api python scripts/demo_data.py
}

Set-Location frontend
if (-not (Test-Path node_modules)) { pnpm install }
Write-Host ''
Write-Host 'Open http://localhost:5173  (password for every demo login: Meghna-Demo-2026)'
Write-Host 'Keep this window open. Ctrl+C stops the screens; afterwards: docker compose -p tradesphere-demo stop'
pnpm dev
