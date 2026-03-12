# mid-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Run AFTER Pass 1 terraform apply, BEFORE Pass 2
#
# - Reads SWA URL from terraform outputs
# - Patches swa_redirect_urls + cors_allowed_origins in terraform.tfvars
# - Sets AZURE_STATIC_WEB_APPS_API_TOKEN secret in the GitHub 'development' environment
#
# VITE_* build variables are set as SWA app_settings by Terraform (main.tf).
# Oryx reads them at build time automatically — no GitHub env vars needed.
#
# NOTE: ACA principal IDs no longer need patching — KV access policies are
#       wired directly to user-assigned managed identities in main.tf.
#
# Usage:
#   cd terraform\environments\dev
#   .\mid-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Award Nomination — Mid-Terraform Setup (Dev)"        -ForegroundColor Cyan
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

# ── Update GitHub Environment secret (development) ───────────────────────────
Write-Host "Updating GitHub 'development' environment secret..." -ForegroundColor Yellow
$ghInstalled = Get-Command gh -ErrorAction SilentlyContinue
if ($ghInstalled) {
    # Secret — SWA deployment token (needed by the GitHub Actions workflow)
    $swaDeploymentToken | gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN --env development
    Write-Host "  Secret  AZURE_STATIC_WEB_APPS_API_TOKEN updated" -ForegroundColor Green
    Write-Host "  VITE_* are set as SWA app_settings by Terraform — no GitHub vars needed" -ForegroundColor DarkGray
} else {
    Write-Host "  gh CLI not found — update manually:" -ForegroundColor DarkYellow
    Write-Host "  GitHub → repo Settings → Environments → development → Secrets" -ForegroundColor DarkYellow
    Write-Host "  Secret : AZURE_STATIC_WEB_APPS_API_TOKEN = <run: terraform output -raw swa_deployment_token>" -ForegroundColor DarkYellow
    Write-Host "  (VITE_* are managed by Terraform as SWA app_settings — nothing else needed)" -ForegroundColor DarkGray
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