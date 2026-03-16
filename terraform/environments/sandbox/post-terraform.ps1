# post-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Run AFTER Pass 2 terraform apply
# Creates sandbox branch and triggers first deployment
#
# Usage:
#   cd terraform\environments\sandbox
#   .\post-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Award Nomination — Post-Terraform Setup (Sandbox)"   -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Collect final outputs ─────────────────────────────────────────────────────
Write-Host "Reading terraform outputs..." -ForegroundColor Yellow
$outputs    = terraform output -json | ConvertFrom-Json
$appUrl     = $outputs.app_url.value
$frontendUrl = $outputs.frontend_url.value
$acrServer  = $outputs.acr_login_server.value
$acrName    = $outputs.acr_name.value
Write-Host "  App URL      : $appUrl" -ForegroundColor Green
Write-Host "  Frontend URL : $frontendUrl" -ForegroundColor Green
Write-Host "  ACR Server   : $acrServer" -ForegroundColor Green
Write-Host ""

# ── Set GitHub 'sandbox' environment variables for CI/CD workflows ────────────
Write-Host "Updating GitHub 'sandbox' environment variables for backend workflow..." -ForegroundColor Yellow
$ghInstalled = Get-Command gh -ErrorAction SilentlyContinue
if ($ghInstalled) {
    # ACR
    gh variable set ACR_NAME         --env sandbox --body $acrName
    gh variable set ACR_LOGIN_SERVER --env sandbox --body $acrServer
    gh variable set ACR_SECRET_NAME  --env sandbox --body "$acrName-password"

    # Container Apps
    gh variable set CONTAINER_APP_EASTUS --env sandbox --body $outputs.container_app_primary.value
    gh variable set CONTAINER_APP_WESTUS --env sandbox --body $outputs.container_app_secondary.value

    # Infra
    gh variable set RESOURCE_GROUP     --env sandbox --body $outputs.resource_group_name.value
    gh variable set FRONTDOOR_PROFILE  --env sandbox --body $outputs.frontdoor_profile.value
    gh variable set FRONTDOOR_ENDPOINT --env sandbox --body $outputs.frontdoor_endpoint.value

    # Static values
    gh variable set IMAGE_TAG    --env sandbox --body "sandbox"
    gh variable set ENVIRONMENT  --env sandbox --body "sandbox"

    Write-Host "  GitHub sandbox environment variables updated" -ForegroundColor Green
} else {
    Write-Host "  gh CLI not found — set these manually in GitHub → repo Settings → Environments → sandbox → Variables:" -ForegroundColor DarkYellow
    Write-Host "  ACR_NAME             = $acrName" -ForegroundColor DarkYellow
    Write-Host "  ACR_LOGIN_SERVER     = $acrServer" -ForegroundColor DarkYellow
    Write-Host "  ACR_SECRET_NAME      = $acrName-password" -ForegroundColor DarkYellow
    Write-Host "  CONTAINER_APP_EASTUS = $($outputs.container_app_primary.value)" -ForegroundColor DarkYellow
    Write-Host "  CONTAINER_APP_WESTUS = $($outputs.container_app_secondary.value)" -ForegroundColor DarkYellow
    Write-Host "  RESOURCE_GROUP       = $($outputs.resource_group_name.value)" -ForegroundColor DarkYellow
    Write-Host "  FRONTDOOR_PROFILE    = $($outputs.frontdoor_profile.value)" -ForegroundColor DarkYellow
    Write-Host "  FRONTDOOR_ENDPOINT   = $($outputs.frontdoor_endpoint.value)" -ForegroundColor DarkYellow
    Write-Host "  IMAGE_TAG            = sandbox" -ForegroundColor DarkYellow
    Write-Host "  ENVIRONMENT          = sandbox" -ForegroundColor DarkYellow
}
Write-Host ""

# ── Create sandbox branch (idempotent) ────────────────────────────────────────────
Write-Host "Setting up sandbox branch..." -ForegroundColor Yellow

# Navigate to repo root (3 levels up from environments/dev)
$repoRoot = Resolve-Path "..\..\..\"
Push-Location $repoRoot

$existingBranch = git branch --list sandbox
if ($existingBranch) {
    Write-Host "  Branch 'sandbox' already exists — skipping" -ForegroundColor DarkYellow
} else {
    git checkout -b sandbox
    Write-Host "  Branch 'sandbox' created" -ForegroundColor Green
}

# Push sandbox branch if not already on remote
$remoteBranch = git ls-remote --heads origin sandbox
if (-not $remoteBranch) {
    git push -u origin sandbox
    Write-Host "  Branch 'sandbox' pushed to origin" -ForegroundColor Green
} else {
    Write-Host "  Branch 'sandbox' already on remote — skipping push" -ForegroundColor DarkYellow
}

Pop-Location
Write-Host ""

# ── Verify ACR is accessible ──────────────────────────────────────────────────
Write-Host "Verifying ACR login..." -ForegroundColor Yellow
az acr login --name ($acrServer -split "\.")[0] 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ACR login successful" -ForegroundColor Green
} else {
    Write-Host "  ACR login failed — check firewall IP rules" -ForegroundColor DarkYellow
}
Write-Host ""

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Sandbox environment fully deployed!" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  App URL      : $appUrl"     -ForegroundColor White
Write-Host "  Frontend URL : $frontendUrl" -ForegroundColor White
Write-Host ""
Write-Host "Remaining manual steps:" -ForegroundColor Yellow
Write-Host "  1. Push a commit to 'sandbox' branch to trigger first GitHub Actions deployment"
Write-Host "  2. Monitor deployment: GitHub → Actions → Deploy to Sandbox"
Write-Host "  3. Once deployed, verify app at: $frontendUrl"
Write-Host "  4. When done testing: terraform destroy"
Write-Host ""