# Deploys the dashboard (dashboard/) to Vercel and stores the keys it needs as
# Vercel environment variables. Values are read from local files and never printed.
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_dashboard.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Dashboard = Join-Path $ProjectRoot "dashboard"
Set-Location $Dashboard

$client = (Get-Content (Join-Path $ProjectRoot "Backend\client_secret.json") -Raw | ConvertFrom-Json).installed
$token = Get-Content (Join-Path $ProjectRoot "Backend\youtube-oauth2.json") -Raw | ConvertFrom-Json
$groq = (Get-Content (Join-Path $ProjectRoot ".env") | Where-Object { $_ -match '^GROQ_API_KEY=' }) -replace '^GROQ_API_KEY="?(.*?)"?$', '$1'

$values = [ordered]@{
    YT_CLIENT_ID     = $client.client_id
    YT_CLIENT_SECRET = $client.client_secret
    YT_REFRESH_TOKEN = $token.refresh_token
    GROQ_API_KEY     = $groq
    GITHUB_REPO      = "elite-mind-studio/elite-mind-autopilot"
}

Write-Host "Linking Vercel project 'elite-mind-dashboard'..."
npx --yes vercel@latest link --yes --project elite-mind-dashboard | Out-Null

foreach ($name in $values.Keys) {
    # Replace any old value, then add the new one for production
    npx --yes vercel@latest env rm $name production --yes 2>$null | Out-Null
    $values[$name] | npx --yes vercel@latest env add $name production | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "set $name" } else { Write-Host "FAILED $name" }
}

Write-Host ""
Write-Host "Deploying..."
npx --yes vercel@latest deploy --prod --yes
