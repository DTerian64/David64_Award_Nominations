#PS C:\Users\David\source\repos\David64_Award_Nominations\Award_Nomination_App\backend> ./Deployment/backend_deploy_docker.ps1

# Build
docker build -t award-nomination-api:latest .

# Test locally first
#docker run -p 8000:8000 --env-file .env award-nomination-api:latest

# Tag for Azure Container Registry
docker tag award-nomination-api:latest acrawardnomination.azurecr.io/award-nomination-api:latest

#authenticate with Azure Container Registry (ACR)
#az login
#az account set --subscription 4ddf12bb-5397-445f-bcaa-df4c7d3dfdca

$acrName="acrawardnomination"
$loginServer="$acrName.azurecr.io"

$token = (az acr login -n $acrName --expose-token --query accessToken -o tsv)
docker login $loginServer -u 00000000-0000-0000-0000-000000000000 -p $token


#tag
docker tag award-nomination-api:latest acrawardnomination.azurecr.io/award-nomination-api:v1
docker tag award-nomination-api:latest $loginServer/award-nomination-api:latest

# Push
docker push acrawardnomination.azurecr.io/award-nomination-api:v1
docker push acrawardnomination.azurecr.io/award-nomination-api:latest

# Update container app
az containerapp update `
  --name award-api-eastus `
  --resource-group rg_award_nomination `
  --image acrawardnomination.azurecr.io/award-nomination-api:latest

  az containerapp update `
  --name award-api-westus `
  --resource-group rg_award_nomination `
  --image acrawardnomination.azurecr.io/award-nomination-api:latest

#unmurk this for the first time run #add rule to allow traffic from app to SQL
#az sql server firewall-rule create `
#  -g rg_award_nomination `
#  -s david64-sql `
#  -n aca-eastus-outbound `
#  --start-ip-address 40.121.18.117 `
#  --end-ip-address 40.121.18.117

#  az sql server firewall-rule create `
#  -g rg_award_nomination `
#  -s david64-sql `
#  -n aca-westus-outbound `
#  --start-ip-address 20.253.254.64 `
#  --end-ip-address 20.253.254.64
  