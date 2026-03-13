Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$base_folder = 'C:\Users\David\source\repos\David64_Award_Nominations\Award_Nomination_App'

Set-Location "$base_folder\frontend"

$env:VITE_CLIENT_ID     = "32cbf47f-b93f-4af1-826c-defb79dd0c52"
$env:VITE_TENANT_ID     = "4d5f34d3-d97b-40c7-8704-edff856d3654"
$env:VITE_API_SCOPE     = "api://3a093543-6ac8-45b7-ba63-a59593e95152/access_as_user"
$env:VITE_API_URL       = "https://award-nomination-api-dev-fmh2b4ezdfcufxb6.z02.azurefd.net"
$env:VITE_API_CLIENT_ID = "3a093543-6ac8-45b7-ba63-a59593e95152"

npm install
npm run build

Set-Location "$base_folder\terraform\environments\dev"
$token = terraform output -raw swa_deployment_token

Set-Location "$base_folder"

swa deploy ./frontend/dist `
  --deployment-token $token `
  --env production `
  --app-name "award-nomination-frontend-dev" `
  --resource-group "rg_award_nomination_dev"