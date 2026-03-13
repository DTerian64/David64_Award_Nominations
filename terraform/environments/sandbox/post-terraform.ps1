# post-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Run AFTER Pass 2 terraform apply
# Creates sandbox branch and triggers first deployment
#
# Usage:
#   cd terraform\environments\dev
#   .\post-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Award Nomination — Post-Terraform Setup (Dev)"       -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Collect final outputs ─────────────────────────────────────────────────────
Write-Host "Reading terraform outputs..." -ForegroundColor Yellow
$outputs    = terraform output -json | ConvertFrom-Json
$appUrl     = $outputs.app_url.value
$frontendUrl = $outputs.frontend_url.value
$acrServer  = $outputs.acr_login_server.value
Write-Host "  App URL      : $appUrl" -ForegroundColor Green
Write-Host "  Frontend URL : $frontendUrl" -ForegroundColor Green
Write-Host "  ACR Server   : $acrServer" -ForegroundColor Green
Write-Host ""

# ── Create sandbox branch (idempotent) ────────────────────────────────────────────
Write-Host "Setting up sandbox branch..." -ForegroundColor Yellow

# Navigate to repo root (3 levels up from environments/dev)
$repoRoot = Resolve-Path "..\..\..\"
Push-Location $repoRoot

$existingBranch = git branch --list dev
if ($existingBranch) {
    Write-Host "  Branch 'dev' already exists — skipping" -ForegroundColor DarkYellow
} else {
    git checkout -b sandbox
    Write-Host "  Branch 'dev' created" -ForegroundColor Green
}

# Push sandbox branch if not already on remote
$remoteBranch = git ls-remote --heads origin dev
if (-not $remoteBranch) {
    git push -u origin dev
    Write-Host "  Branch 'dev' pushed to origin" -ForegroundColor Green
} else {
    Write-Host "  Branch 'dev' already on remote — skipping push" -ForegroundColor DarkYellow
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
Write-Host "  1. Push a commit to 'dev' branch to trigger first GitHub Actions deployment"
Write-Host "  2. Monitor deployment: GitHub → Actions → Deploy to Dev"
Write-Host "  3. Once deployed, verify app at: $frontendUrl"
Write-Host "  4. When done testing: terraform destroy"
Write-Host ""