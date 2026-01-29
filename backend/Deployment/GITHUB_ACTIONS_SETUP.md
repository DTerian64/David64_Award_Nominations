# GitHub Actions Deployment Setup Guide

## Why GitHub Actions?

✅ **No Docker Desktop required** - Runs in the cloud
✅ **Automatic deployments** - Push to main branch = auto-deploy
✅ **Free for public repos** - 2,000 minutes/month for private repos
✅ **Built-in secrets management** - Secure credential storage
✅ **Deployment history** - Track all deployments
✅ **Rollback capability** - Easy to revert to previous versions

---

## Setup Steps

### Step 1: Create Azure Service Principal

Run this in PowerShell (with Azure CLI):

```powershell
# Login to Azure
az login
az account set --subscription 4ddf12bb-5397-445f-bcaa-df4c7d3dfdca

# Create service principal with contributor role
az ad sp create-for-rbac \
  --name "github-actions-award-nomination" \
  --role contributor \
  --scopes /subscriptions/4ddf12bb-5397-445f-bcaa-df4c7d3dfdca/resourceGroups/rg_award_nomination \
  --sdk-auth
```

This will output JSON like this (save this - you'll need it):

```json
{
  "clientId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "clientSecret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "subscriptionId": "4ddf12bb-5397-445f-bcaa-df4c7d3dfdca",
  "tenantId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
  "resourceManagerEndpointUrl": "https://management.azure.com/",
  ...
}
```

### Step 2: Add GitHub Secret

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `AZURE_CREDENTIALS`
5. Value: Paste the **entire JSON output** from Step 1
6. Click **Add secret**

### Step 3: Create Workflow Directory Structure

In your repository, create this folder structure:

```
your-repo/
├── .github/
│   └── workflows/
│       └── deploy-backend.yml
├── backend/
│   ├── main.py
│   ├── auth.py
│   ├── Dockerfile
│   └── ... (other backend files)
└── frontend/
    └── ... (frontend files)
```

### Step 4: Add the Workflow File

Copy the `deploy-backend.yml` file to `.github/workflows/deploy-backend.yml` in your repository.

### Step 5: Adjust Paths (If Needed)

If your backend code is NOT in a folder called `backend/`, update line 9 in the workflow:

```yaml
paths:
  - 'backend/**'  # Change to your actual backend folder path
```

And line 37:
```yaml
run: |
  cd backend  # Change to your actual backend folder path
  docker build -t ${{ env.IMAGE_NAME }}:latest .
```

### Step 6: Commit and Push

```powershell
git add .github/workflows/deploy-backend.yml
git commit -m "Add GitHub Actions deployment workflow"
git push origin main
```

### Step 7: Watch It Deploy! 🚀

1. Go to your GitHub repository
2. Click **Actions** tab
3. You'll see your workflow running
4. Click on it to watch real-time logs

---

## How to Use

### Automatic Deployment
- Push any changes to the `main` branch that affect `backend/**`
- GitHub Actions automatically builds and deploys

### Manual Deployment
1. Go to **Actions** tab in GitHub
2. Click on "Deploy Backend to Azure Container Apps"
3. Click **Run workflow** → **Run workflow**

### View Deployment Status
- Green checkmark ✅ = Successful deployment
- Red X ❌ = Failed deployment (click to see logs)

---

## Troubleshooting

### Error: "Authorization failed"
- Make sure `AZURE_CREDENTIALS` secret is set correctly
- Verify the service principal has contributor access

### Error: "docker: command not found"
- This shouldn't happen in GitHub Actions (Docker is pre-installed)
- If using self-hosted runner, install Docker

### Error: "Image not found in ACR"
- Check ACR name in workflow matches your actual ACR
- Verify service principal can push to ACR

### Want to see what's being deployed?
- Each deployment tags the image with the git commit SHA
- You can track exactly which code version is running

---

## Cost Comparison

| Method | Cost | Pros | Cons |
|--------|------|------|------|
| **Local Docker Desktop** | Free (personal use) | Fast iteration | Manual process, requires Docker running |
| **GitHub Actions (Public Repo)** | Free | Automated, no local requirements | Slower (cloud build) |
| **GitHub Actions (Private Repo)** | Free (2000 min/month) | Automated, tracked | May need paid plan for heavy use |
| **Azure DevOps** | Free (1800 min/month) | More enterprise features | More complex setup |

---

## Advanced: Environment-Specific Deployments

Want separate dev/staging/production environments? You can modify the workflow:

```yaml
on:
  push:
    branches:
      - main  # Production
      - develop  # Staging
```

Then conditionally deploy based on branch:

```yaml
- name: Deploy to Production (main branch only)
  if: github.ref == 'refs/heads/main'
  run: |
    az containerapp update ...
    
- name: Deploy to Staging (develop branch only)
  if: github.ref == 'refs/heads/develop'
  run: |
    az containerapp update --name award-api-staging ...
```

---

## Next Steps

After setting up GitHub Actions:

1. ✅ Delete or archive your local PowerShell deployment script
2. ✅ Document the deployment process for your team
3. ✅ Set up branch protection rules on `main`
4. ✅ Consider adding automated tests before deployment
5. ✅ Set up Slack/email notifications for deployment status

---

## Need Help?

Common issues and solutions are in the Troubleshooting section above. For specific errors, check the GitHub Actions logs - they're very detailed!
