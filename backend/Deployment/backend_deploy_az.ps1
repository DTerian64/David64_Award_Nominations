#acrawardnomination is already created
cd C:\Users\David\source\repos\David64_Award_Nominations>

az login
az account set --subscription 4ddf12bb-5397-445f-bcaa-df4c7d3dfdca
az acr login -n acrawardnomination --expose-token

#Build in the cloud (ACR Tasks)
az acr build `
  --registry acrawardnomination `
  --image award-nomination-api:v1 `
  --file .\backend\Dockerfile `
  .\backend

  #Confirm the image exists:
az acr repository list -n acrawardnomination -o table
az acr repository show-tags -n acrawardnomination --repository award-nomination-api -o table

#Create a Container Apps Environment per region
$rg="rg_award_nomination"

# East US
az containerapp env create `
  -n cae-award-eastus `
  -g $rg `
  -l eastus

# West US
az containerapp env create `
  -n cae-award-westus `
  -g $rg `
  -l westus

  #Deploy the API container in each region
$acr="acrawardnomination.azurecr.io"
$image="$acr/award-nomination-api:v1"

# East
az containerapp create `
  -n award-api-eastus `
  -g $rg `
  --environment cae-award-eastus `
  --image $image `
  --ingress external `
  --target-port 8000 `
  --registry-server $acr `
  --min-replicas 1 `
  --max-replicas 3

# West
az containerapp create `
  -n award-api-westus `
  -g $rg `
  --environment cae-award-westus `
  --image $image `
  --ingress external `
  --target-port 8000 `
  --registry-server $acr `
  --min-replicas 1 `
  --max-replicas 3

#Configure secrets / env vars (SQL, Entra)


az containerapp secret set `
  -n award-api-eastus `
  -g $rg `
  --secrets `
    sql-server='david64-sql.database.windows.net' `
    sql-database='AwardNominations' `
    sql-user='dterian' `
    sql-password='Hripsime1961@2026#'

az containerapp update `
  -n award-api-eastus -g $rg `
  --set-env-vars `
    SQL_SERVER=secretref:sql-server `
    SQL_DATABASE=secretref:sql-database `
    SQL_USER=secretref:sql-user `
    SQL_PASSWORD=secretref:sql-password

az containerapp logs show -n award-api-eastus -g $rg --tail 200

az containerapp secret set `
  -n award-api-westus `
  -g $rg `
  --secrets `
    sql-server='david64-sql.database.windows.net' `
    sql-database='AwardNominations' `
    sql-user='dterian' `
    sql-password='Hripsime1961@2026#'

az containerapp update `
  -n award-api-westus -g $rg `
  --set-env-vars `
    SQL_SERVER=secretref:sql-server `
    SQL_DATABASE=secretref:sql-database `
    SQL_USER=secretref:sql-user `
    SQL_PASSWORD=secretref:sql-password

az containerapp logs show -n award-api-eastus -g $rg --tail 200

az containerapp show -n award-api-eastus -g $rg --query properties.configuration.ingress.fqdn -o tsv
#award-api-eastus.lemonpond-a2daba01.eastus.azurecontainerapps.io
az containerapp show -n award-api-westus -g $rg --query properties.configuration.ingress.fqdn -o tsv
#award-api-westus.ambitiousglacier-50e13e03.westus.azurecontainerapps.io

# Enable system-assigned managed identity
az containerapp identity assign `
  --name award-api-eastus `
  --resource-group rg_award_nomination 
  --system-assigned

# Get the identity principal ID
$principalId = az containerapp identity show `
  --name award-api-eastus `
  --resource-group rg_award_nomination `
  --query principalId -o tsv

# Grant the identity access to blob storage
az role assignment create `
  --assignee $principalId `
  --role "Storage Blob Data Reader" `
  --scope "/subscriptions/4ddf12bb-5397-445f-bcaa-df4c7d3dfdca/resourceGroups/rg_award_nomination/providers/Microsoft.Storage/storageAccounts/awardnominationmodels"

# Enable system-assigned managed identity for award-api-westus
  az containerapp identity assign `
  --name award-api-westus `
  --resource-group rg_award_nomination `
  --system-assigned

# Get the identity principal ID
$principalId = az containerapp identity show `
  --name award-api-westus `
  --resource-group rg_award_nomination `
  --query principalId -o tsv

# Grant the identity access to blob storage
az role assignment create `
  --assignee $principalId `
  --role "Storage Blob Data Reader" `
  --scope "/subscriptions/4ddf12bb-5397-445f-bcaa-df4c7d3dfdca/resourceGroups/rg_award_nomination/providers/Microsoft.Storage/storageAccounts/awardnominationmodels"

