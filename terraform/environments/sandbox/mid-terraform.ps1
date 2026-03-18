# mid-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Run AFTER Pass 1 terraform apply, BEFORE Pass 2
#
# - Reads SWA URL and VITE_* values from terraform outputs
# - Patches swa_redirect_urls + cors_allowed_origins in terraform.tfvars
# - Sets AZURE_STATIC_WEB_APPS_API_TOKEN secret in the GitHub 'sandbox' environment
# - Sets VITE_* as GitHub Environment variables in 'sandbox'
#   (the GitHub Actions runner has no access to Azure SWA app_settings at build
#    time — values must be passed explicitly via the workflow env: block)
#
# NOTE: ACA principal IDs no longer need patching — KV access policies are
#       wired directly to user-assigned managed identities in main.tf.
#
# Usage:
#   cd terraform\environments\sandbox
#   .\mid-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Award Nomination — Mid-Terraform Setup (Sandbox)"        -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Collect terraform outputs ─────────────────────────────────────────────────
Write-Host "Reading terraform outputs..." -ForegroundColor Yellow

$outputs            = terraform output -json | ConvertFrom-Json
$frontendUrl        = $outputs.frontend_url.value
$swaDeploymentToken = terraform output -raw swa_deployment_token

Write-Host "  Frontend URL : $frontendUrl" -ForegroundColor Green

# ── Read optional custom domain from tfvars ───────────────────────────────────
$tfvarsPath = "terraform.tfvars"
$tfvars = Get-Content $tfvarsPath -Raw
$customDomain = ""
if ($tfvars -match 'swa_custom_domain\s*=\s*"([^"]+)"') {
    $customDomain = $Matches[1]
    Write-Host "  Custom domain: $customDomain" -ForegroundColor Green
}
Write-Host ""

# ── Patch Pass 2 values in terraform.tfvars ──────────────────────────────────
Write-Host "Patching terraform.tfvars..." -ForegroundColor Yellow

# api_base_url — AFD endpoint hostname, only known after Pass 1 creates the AFD
$apiBaseUrl = $outputs.app_url.value
if ($tfvars -match 'api_base_url\s*=\s*"[^"]*"') {
    $tfvars = $tfvars -replace 'api_base_url\s*=\s*"[^"]*"', "api_base_url = `"$apiBaseUrl`""
} else {
    $tfvars += "`napi_base_url = `"$apiBaseUrl`""
}
Write-Host "  api_base_url        : $apiBaseUrl" -ForegroundColor Green

# swa_redirect_urls — app registration allowed redirect URIs
# Include both the default SWA URL and the custom domain (if set)
$redirectUrls = "`"$frontendUrl/`""
if ($customDomain -ne "") {
    $redirectUrls += ", `"https://$customDomain/`""
}
if ($tfvars -match 'swa_redirect_urls\s*=\s*\[') {
    $tfvars = $tfvars -replace 'swa_redirect_urls\s*=\s*\[[^\]]*\]', "swa_redirect_urls = [$redirectUrls]"
} else {
    $tfvars += "`nswa_redirect_urls = [$redirectUrls]"
}
Write-Host "  swa_redirect_urls   : [$redirectUrls]" -ForegroundColor Green

# cors_allowed_origins — injected into Container App env vars
$corsOrigins = "$frontendUrl,http://localhost:5173,http://localhost:3000"
if ($customDomain -ne "") {
    $corsOrigins += ",https://$customDomain"
}
if ($tfvars -match 'cors_allowed_origins\s*=\s*"') {
    $tfvars = $tfvars -replace 'cors_allowed_origins\s*=\s*"[^"]*"', "cors_allowed_origins = `"$corsOrigins`""
} else {
    $tfvars += "`ncors_allowed_origins = `"$corsOrigins`""
}
Write-Host "  cors_allowed_origins: $corsOrigins" -ForegroundColor Green

Set-Content $tfvarsPath $tfvars -NoNewline
Write-Host ""

# ── Update GitHub Environment secret + variables (sandbox) ───────────────
Write-Host "Updating GitHub 'sandbox' environment secret and variables..." -ForegroundColor Yellow
$ghInstalled = Get-Command gh -ErrorAction SilentlyContinue
if ($ghInstalled) {
    # Secret — SWA deployment token (needed by the GitHub Actions workflow)
    $swaDeploymentToken | gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN --env sandbox
    Write-Host "  Secret  AZURE_STATIC_WEB_APPS_API_TOKEN updated" -ForegroundColor Green

    # Variables — VITE_* build-time values (passed via workflow env: block to Vite)
    # Note: VITE_TENANT_ID is no longer needed — frontend uses /organizations authority.
    gh variable set VITE_CLIENT_ID     --env sandbox --body $outputs.vite_client_id.value
    gh variable set VITE_API_SCOPE     --env sandbox --body $outputs.vite_api_scope.value
    gh variable set VITE_API_URL       --env sandbox --body $outputs.app_url.value
    gh variable set VITE_API_CLIENT_ID --env sandbox --body $outputs.vite_api_client_id.value
    Write-Host "  Variables VITE_* updated" -ForegroundColor Green
} else {
    Write-Host "  gh CLI not found — update manually:" -ForegroundColor DarkYellow
    Write-Host "  GitHub → repo Settings → Environments → sandbox → Secrets/Variables" -ForegroundColor DarkYellow
    Write-Host "  Secret   : AZURE_STATIC_WEB_APPS_API_TOKEN = <run: terraform output -raw swa_deployment_token>" -ForegroundColor DarkYellow
    Write-Host "  Variable : VITE_CLIENT_ID     = $($outputs.vite_client_id.value)" -ForegroundColor DarkYellow
    Write-Host "  Variable : VITE_API_SCOPE     = $($outputs.vite_api_scope.value)" -ForegroundColor DarkYellow
    Write-Host "  Variable : VITE_API_URL       = $($outputs.app_url.value)" -ForegroundColor DarkYellow
    Write-Host "  Variable : VITE_API_CLIENT_ID = $($outputs.vite_api_client_id.value)" -ForegroundColor DarkYellow
}
Write-Host ""

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Mid-terraform setup complete!" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps (Pass 2):" -ForegroundColor Yellow
Write-Host "  1. Run: terraform plan -out terraform.tfplan"
Write-Host "  2. Run: terraform apply `"terraform.tfplan`""
Write-Host "  3. Run: .\post-terraform.ps1"
Write-Host ""