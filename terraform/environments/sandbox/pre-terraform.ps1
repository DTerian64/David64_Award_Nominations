# pre-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Run ONCE before Pass 1 terraform apply
# - Resets terraform.tfvars from template (clean slate)
# - Verifies Azure CLI login
#
# NOTE: App registrations are created by Terraform (module/app-registrations),
#       not by this script.
#
# Usage:
#   cd terraform\environments\sandbox
#   .\pre-terraform.ps1
# ─────────────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Award Nomination — Pre-Terraform Setup (Sandbox)"    -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Reset terraform.tfvars from template ──────────────────────────────────────
Write-Host "Resetting terraform.tfvars from template..." -ForegroundColor Yellow
$templatePath = "terraform.tfvars.template"
$tfvarsPath   = "terraform.tfvars"

if (-not (Test-Path $templatePath)) {
    Write-Error "terraform.tfvars.template not found. Cannot continue."
    exit 1
}

if (Test-Path $tfvarsPath) {
    $overwrite = Read-Host "  terraform.tfvars already exists. Overwrite with template? (y/n)"
    if ($overwrite -ne "y") {
        Write-Host "  Keeping existing terraform.tfvars" -ForegroundColor DarkYellow
    } else {
        Copy-Item $templatePath $tfvarsPath -Force
        Write-Host "  Reset to template" -ForegroundColor Green
    }
} else {
    Copy-Item $templatePath $tfvarsPath
    Write-Host "  Created from template" -ForegroundColor Green
}
Write-Host ""

# ── Verify az CLI is logged in ────────────────────────────────────────────────
Write-Host "Checking Azure CLI login..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "  Not logged in. Running az login..." -ForegroundColor Yellow
    az login
    $account = az account show | ConvertFrom-Json
}
Write-Host "  Logged in as : $($account.user.name)" -ForegroundColor Green
Write-Host "  Tenant       : $($account.tenantId)" -ForegroundColor Green
Write-Host ""

# ── Remind user to fill in manual values ─────────────────────────────────────
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "  ACTION REQUIRED — Fill in terraform.tfvars"          -ForegroundColor Yellow
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Open terraform.tfvars and replace all YOUR_* values:"
Write-Host "    - my_ips (your home/office public IP)"
Write-Host "    - sql_admin_login / sql_admin_password"
Write-Host "    - secrets block (Gmail, email keys etc.)"
Write-Host ""

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Pre-terraform setup complete!" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Fill in YOUR_* values in terraform.tfvars"
Write-Host "  2. Run: terraform init"
Write-Host "  3. Run: terraform plan -out terraform.tfplan"
Write-Host "  4. Run: terraform apply `"terraform.tfplan`""
Write-Host "  5. Run: .\mid-terraform.ps1"
Write-Host ""