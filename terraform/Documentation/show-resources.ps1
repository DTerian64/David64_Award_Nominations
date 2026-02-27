az config set extension.dynamic_install_allow_preview=true --only-show-errors

#SQL Server + Database:
az sql server show --name david64-sql --resource-group rg_award_nomination --query "{sku:sku, version:version, location:location}" -o json
az sql db show --server david64-sql --resource-group rg_award_nomination --name AwardNominations --query "{sku:sku, maxSizeBytes:maxSizeBytes, zoneRedundant:zoneRedundant}" -o json

#Container Registry:
az acr show --name acrawardnomination --resource-group rg_award_nomination --query "{sku:sku, location:location, adminUserEnabled:adminUserEnabled}" -o json

#Key Vault:
az keyvault show --name kv-awardnominations --resource-group rg_award_nomination --query "{sku:properties.sku, location:location, softDeleteRetentionInDays:properties.softDeleteRetentionInDays}" -o json

#Azure OpenAI:
az cognitiveservices account show --name award-nomination-open-AI --resource-group rg_award_nomination --query "{sku:sku, location:location, kind:kind}" -o json
az cognitiveservices account deployment list --name award-nomination-open-AI --resource-group rg_award_nomination --query "[].{name:name, model:properties.model, sku:sku}" -o json

#Storage Account:
az storage account show --name awardnominationmodels --resource-group rg_award_nomination --query "{sku:sku, kind:kind, location:location, accessTier:properties.accessTier}" -o json

#Container Apps:
az containerapp show --name award-api-eastus --resource-group rg_award_nomination --query "{location:location, cpu:properties.template.containers[0].resources.cpu, memory:properties.template.containers[0].resources.memory, minReplicas:properties.scale.minReplicas, maxReplicas:properties.scale.maxReplicas, targetPort:properties.configuration.ingress.targetPort}" -o json
az containerapp show --name award-api-westus --resource-group rg_award_nomination --query "{location:location, cpu:properties.template.containers[0].resources.cpu, memory:properties.template.containers[0].resources.memory, minReplicas:properties.scale.minReplicas, maxReplicas:properties.scale.maxReplicas, targetPort:properties.configuration.ingress.targetPort}" -o json

#Front Door:
az afd profile show --profile-name Award-Nomination-ADF --resource-group rg_award_nomination --query "{sku:sku, location:location}" -o json
az afd origin-group list --profile-name Award-Nomination-ADF --resource-group rg_award_nomination --query "[].{name:name, healthProbe:healthProbeSettings, loadBalancing:loadBalancingSettings}" -o json

#Static Web App:
az staticwebapp show --name award-nomination-frontend --resource-group rg_award_nomination --query "{sku:sku, location:location, repositoryUrl:repositoryUrl, branch:branch, buildProperties:buildProperties}" -o json

#Log Analytics Workspaces
az monitor log-analytics workspace show --workspace-name workspace-rgawardnomination6aem --resource-group rg_award_nomination --query "{sku:sku, location:location, retentionInDays:retentionInDays}" -o json
az monitor log-analytics workspace show --workspace-name workspace-rgawardnomination57mY --resource-group rg_award_nomination --query "{sku:sku, location:location, retentionInDays:retentionInDays}" -o json

#Grafana
az grafana show --name awardnomination-grafana --resource-group rg_award_nomination --query "{sku:sku, location:location}" -o json