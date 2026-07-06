# Deploy LeadPilot AI to Scalingo + Supabase
# Usage (PowerShell):
#   $env:SCALINGO_API_TOKEN = "your-token"
#   $env:DATABASE_URL = "postgresql://postgres.REF:PASSWORD@...pooler.supabase.com:6543/postgres"
#   $env:ADMIN_PASSWORD = "strong-password"
#   $env:WEBHOOK_SECRET = "..."
#   $env:EMAIL_INBOUND_SECRET = "..."
#   $env:MISTRAL_API_KEY = "..."
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

& $ScalingoExe --app $AppName env 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating Scalingo app: $AppName ($Region)..."
    & $ScalingoExe create $AppName --region $Region
}

if (-not $env:DATABASE_URL) {
    throw @"
DATABASE_URL missing.
Supabase (EU) → Settings → Database → Connection pooling (Transaction mode, port 6543).
Set: `$env:DATABASE_URL = 'postgresql://postgres.REF:PASSWORD@aws-1-eu-central-1.pooler.supabase.com:6543/postgres'
"@
}

function New-Secret {
    return ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
}

function Set-EnvVar($name, $value) {
    if (-not $value) { return }
    Write-Host "  env-set $name"
    & $ScalingoExe --app $AppName env-set "${name}=${value}"
}

if (-not $env:SECRET_KEY) { $env:SECRET_KEY = New-Secret 48 }
if (-not $env:JWT_SECRET_KEY) { $env:JWT_SECRET_KEY = New-Secret 48 }
if (-not $env:WEBHOOK_SECRET) { $env:WEBHOOK_SECRET = New-Secret 32 }
if (-not $env:EMAIL_INBOUND_SECRET) { $env:EMAIL_INBOUND_SECRET = New-Secret 32 }

if (-not $env:ADMIN_PASSWORD) {
    throw "Set ADMIN_PASSWORD before deploying to production."
}

$publicBaseUrl = if ($env:PUBLIC_BASE_URL) {
    $env:PUBLIC_BASE_URL
} else {
    "https://www.pilotcore.fr"
}

Write-Host "Configuring environment..."
Set-EnvVar "FLASK_ENV" "production"
Set-EnvVar "PREFERRED_URL_SCHEME" "https"
Set-EnvVar "PUBLIC_BASE_URL" $publicBaseUrl
Set-EnvVar "SECRET_KEY" $env:SECRET_KEY
Set-EnvVar "JWT_SECRET_KEY" $env:JWT_SECRET_KEY
Set-EnvVar "DATABASE_URL" $env:DATABASE_URL
Set-EnvVar "ADMIN_USERNAME" $(if ($env:ADMIN_USERNAME) { $env:ADMIN_USERNAME } else { "LeadPilot_Admin" })
Set-EnvVar "ADMIN_PASSWORD" $env:ADMIN_PASSWORD
Set-EnvVar "WEBHOOK_SECRET" $env:WEBHOOK_SECRET
Set-EnvVar "EMAIL_INBOUND_SECRET" $env:EMAIL_INBOUND_SECRET
Set-EnvVar "TWILIO_AUTO_PROVISION_NUMBERS" "1"
Set-EnvVar "TWILIO_VALIDATE_SIGNATURE" "1"
Set-EnvVar "TWILIO_AI_PHONE_NUMBER" $(if ($env:TWILIO_AI_PHONE_NUMBER) { $env:TWILIO_AI_PHONE_NUMBER } else { "+33159169691" })
Set-EnvVar "TWILIO_AI_PHONE_DISPLAY" $(if ($env:TWILIO_AI_PHONE_DISPLAY) { $env:TWILIO_AI_PHONE_DISPLAY } else { "+33 1 59 16 96 91" })

$requiredFromEnv = @(
    "MISTRAL_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
    "TWILIO_DEFAULT_TENANT_ID"
)
$optionalFromEnv = @(
    "MISTRAL_MODEL", "OPENAI_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_STARTER", "STRIPE_PRICE_PRO", "STRIPE_PRICE_PREMIUM",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_USE_SSL", "SMTP_USE_TLS",
    "EMAIL_FROM", "IMAP_HOST", "IMAP_PORT", "IMAP_USER", "IMAP_PASSWORD", "IMAP_USE_SSL", "IMAP_FOLDER",
    "TWILIO_SMS_FROM", "CALL_OVERAGE_PRICE_CENTS"
)

$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+)\s*$') {
            $k, $v = $matches[1], $matches[2].Trim().Trim('"').Trim("'")
            if (-not (Get-ChildItem "env:$k" -ErrorAction SilentlyContinue).Value) {
                Set-Item -Path "env:$k" -Value $v
            }
        }
    }
}

foreach ($k in $requiredFromEnv) {
    $val = (Get-Item "env:$k" -ErrorAction SilentlyContinue).Value
    if (-not $val) {
        throw "Missing required env var: $k (set in shell or .env)"
    }
    Set-EnvVar $k $val
}

foreach ($k in $optionalFromEnv) {
    $val = (Get-Item "env:$k" -ErrorAction SilentlyContinue).Value
    Set-EnvVar $k $val
}

Write-Host "Adding git remote scalingo..."
& $ScalingoExe --app $AppName git-setup 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    git remote remove scalingo 2>$null
    & $ScalingoExe --app $AppName git-setup
}

Write-Host "Deploying to Scalingo (no force push)..."
git push scalingo main

Write-Host ""
Write-Host "Done! App URL: https://${serverName}"
Write-Host "Health (liveness):  https://${serverName}/health"
Write-Host "Health (readiness): https://${serverName}/health/ready"
Write-Host "Twilio voice webhook: https://${serverName}/voice/inbound"
Write-Host "Stripe webhook:       https://${serverName}/billing/webhook"
Write-Host "Inbound email:        https://${serverName}/admin/email/inbound?secret=YOUR_EMAIL_INBOUND_SECRET"
