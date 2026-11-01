# Copies the autopilot's keys from .env (and the YouTube/Kaggle login files)
# into a GitHub repo's encrypted Actions secrets. Values are never printed.
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\set_github_secrets.ps1 -Repo owner/repo

param(
    [Parameter(Mandatory = $true)][string]$Repo
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $Gh)) { $Gh = "gh" }

function Set-Secret([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-Host "skipped $Name (empty)"
        return
    }
    $Value | & $Gh secret set $Name -R $Repo | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "set $Name" } else { Write-Host "FAILED $Name" }
}

# Keys from .env
$envValues = @{}
Get-Content (Join-Path $ProjectRoot ".env") | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*"?(.*?)"?\s*$') { $envValues[$Matches[1]] = $Matches[2] }
}
foreach ($name in "PIXABAY_API_KEY", "TIKTOK_SESSION_ID", "POLLINATIONS_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "GROQ_API_KEY") {
    Set-Secret $name $envValues[$name]
}

# Kaggle (free GPU image backup)
$kaggleToken = Join-Path $env:USERPROFILE ".kaggle\access_token"
if (Test-Path $kaggleToken) { Set-Secret "KAGGLE_API_TOKEN" (Get-Content $kaggleToken -Raw).Trim() }
$kaggleUser = (& "$ProjectRoot\.venv\Scripts\kaggle.exe" config view 2>$null | Select-String "username:" | ForEach-Object { ($_ -split ":", 2)[1].Trim() })
Set-Secret "KAGGLE_USERNAME" $kaggleUser

# YouTube login files
Set-Secret "YOUTUBE_CLIENT_SECRET_JSON" (Get-Content (Join-Path $ProjectRoot "Backend\client_secret.json") -Raw)
Set-Secret "YOUTUBE_OAUTH_JSON" (Get-Content (Join-Path $ProjectRoot "Backend\youtube-oauth2.json") -Raw)

Write-Host ""
Write-Host "Secrets now in ${Repo}:"
& $Gh secret list -R $Repo
