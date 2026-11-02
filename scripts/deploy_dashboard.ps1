# Deploys the dashboard (dashboard/) to Vercel and stores the keys it needs as
# Vercel environment variables. Values are read from local files and never printed.
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_dashboard.ps1

# Vercel CLI prints status lines on stderr; do not treat them as errors
$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Dashboard = Join-Path $ProjectRoot "dashboard"
Set-Location $Dashboard

# Only ever deploy to the dashboard's own Vercel account (secrets go with it)
$ExpectedAccount = "testyemma123"
$account = (cmd /c "npx --yes vercel@latest whoami 2>nul" | Select-Object -Last 1).Trim()
if ($account -ne $ExpectedAccount) {
    Write-Host "STOPPED: Vercel is logged in as '$account', not '$ExpectedAccount'."
    Write-Host "Run: npx vercel logout ; npx vercel login   (sign in as $ExpectedAccount), then try again."
    exit 1
}

$client = (Get-Content (Join-Path $ProjectRoot "Backend\client_secret.json") -Raw | ConvertFrom-Json).installed
$token = Get-Content (Join-Path $ProjectRoot "Backend\youtube-oauth2.json") -Raw | ConvertFrom-Json
$headlineFile = Join-Path $ProjectRoot "Backend\youtube-oauth2-headline_tales.json"
$headline = if (Test-Path $headlineFile) { (Get-Content $headlineFile -Raw | ConvertFrom-Json).refresh_token } else { "" }
$groq = (Get-Content (Join-Path $ProjectRoot ".env") | Where-Object { $_ -match '^GROQ_API_KEY=' }) -replace '^GROQ_API_KEY="?(.*?)"?$', '$1'

$values = [ordered]@{
    YT_CLIENT_ID     = $client.client_id
    YT_CLIENT_SECRET = $client.client_secret
    YT_REFRESH_TOKEN = $token.refresh_token
    YT_REFRESH_TOKEN_HEADLINE = $headline
    GROQ_API_KEY     = $groq
    GITHUB_REPO      = "elite-mind-studio/elite-mind-autopilot"
}

Write-Host "Linking Vercel project 'elite-mind-dashboard'..."
cmd /c "npx --yes vercel@latest link --yes --project elite-mind-dashboard 2>&1" | Out-Null

foreach ($name in $values.Keys) {
    # Replace any old value, then add the new one for production
    cmd /c "npx --yes vercel@latest env rm $name production --yes 2>&1" | Out-Null
    $values[$name] | cmd /c "npx --yes vercel@latest env add $name production 2>&1" | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "set $name" } else { Write-Host "FAILED $name" }
}

Write-Host ""
Write-Host "Deploying..."
cmd /c "npx --yes vercel@latest deploy --prod --yes 2>&1"
