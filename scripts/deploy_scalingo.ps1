# Deploy LeadPilot AI to Scalingo + Supabase
# Usage (PowerShell):
#   $env:SCALINGO_API_TOKEN = "your-token"   # https://dashboard.scalingo.com/account/tokens
#   $env:DATABASE_URL = "postgresql://postgres.REF:PASSWORD@...pooler.supabase.com:6543/postgres"
#   $env:SECRET_KEY = "..."  # optional, auto-generated if empty
#   .\scripts\deploy_scalingo.ps1

param(
    [string]$AppName = "leadpilot-ai",
    [string]$Region = "osc-fr1",
    [string]$ScalingoExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $ScalingoExe) {
    $candidate = Join-Path $Root ".tools\scalingo_1.47.0_windows_amd64\scalingo.exe"
    if (Test-Path $candidate) { $ScalingoExe = $candidate }
    else { throw "scalingo.exe not found. Install CLI: https://cli.scalingo.com" }
}

if (-not $env:SCALINGO_API_TOKEN) {
    throw "Set SCALINGO_API_TOKEN (Scalingo dashboard → Account → API tokens)"
}

& $ScalingoExe login --api-token $env:SCALINGO_API_TOKEN | Out-Null

$apps = & $ScalingoExe apps 2>&1
if ($apps -notmatch $AppName) {
    Write-Host "Creating Scalingo app: $AppName ($Region)..."
    & $ScalingoExe create $AppName --region $Region
}

if (-not $env:DATABASE_URL) {
    throw @"
DATABASE_URL missing.
Create a Supabase project (supabase.com) → Settings → Database → Connection pooling (Transaction, port 6543).
Set: `$env:DATABASE_URL = 'postgresql://...'
"@
}

function Set-EnvIfMissing($name, $value) {
    if (-not $value) { return }
    Write-Host "  env-set $name"
    & $ScalingoExe --app $AppName env-set "${name}=${value}"
}

if (-not $env:SECRET_KEY) {
    $env:SECRET_KEY = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
}
if (-not $env:JWT_SECRET_KEY) {
    $env:JWT_SECRET_KEY = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
}

$serverName = "${AppName}.${Region}.scalingo.io"

Write-Host "Configuring environment..."
Set-EnvIfMissing "FLASK_ENV" "production"
Set-EnvIfMissing "PREFERRED_URL_SCHEME" "https"
Set-EnvIfMissing "SERVER_NAME" $serverName
Set-EnvIfMissing "SECRET_KEY" $env:SECRET_KEY
Set-EnvIfMissing "JWT_SECRET_KEY" $env:JWT_SECRET_KEY
Set-EnvIfMissing "DATABASE_URL" $env:DATABASE_URL

# Optional — set from your local .env if present
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+)\s*$') {
            $k, $v = $matches[1], $matches[2].Trim()
            if ($k -in @(
                "MISTRAL_API_KEY", "OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                "TWILIO_DEFAULT_TENANT_ID", "TWILIO_AI_PHONE_NUMBER", "TWILIO_AI_PHONE_DISPLAY"
            )) {
                Set-EnvIfMissing $k $v
            }
        }
    }
}

Write-Host "Adding git remote scalingo..."
$remoteUrl = & $ScalingoExe --app $AppName git-setup 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    git remote remove scalingo 2>$null
    & $ScalingoExe --app $AppName git-setup
}

Write-Host "Deploying to Scalingo..."
git push scalingo main --force

Write-Host ""
Write-Host "Done! App URL: https://${serverName}"
Write-Host "Twilio webhook: https://${serverName}/voice/inbound"
Write-Host "Health check:   https://${serverName}/health"
