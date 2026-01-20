$rg = "rg_award_nomination"
$profile = "Award-Nomination-ADF"
$endpoint = "award-nomination-api"      # must be globally unique in azurefd.net namespace
$og = "og-award-api"
$route = "rt-award-api"

$originEast = "award-api-eastus.lemonpond-a2daba01.eastus.azurecontainerapps.io"
$originWest = "award-api-westus.ambitiousglacier-50e13e03.westus.azurecontainerapps.io"

az afd profile create `
  -g $rg `
  --profile-name $profile `
  --sku Standard_AzureFrontDoor

  az afd endpoint create `
  -g $rg `
  --profile-name $profile `
  --endpoint-name $endpoint

  az afd origin-group create `
  -g $rg `
  --profile-name $profile `
  --origin-group-name $og `
  --probe-request-type GET `
  --probe-protocol Https `
  --probe-interval-in-seconds 30 `
  --probe-path "/health" `
  --sample-size 4 `
  --successful-samples-required 3

  az afd origin create `
  -g $rg `
  --profile-name $profile `
  --origin-group-name $og `
  --origin-name "eastus" `
  --host-name $originEast `
  --origin-host-header $originEast `
  --http-port 80 `
  --https-port 443 `
  --priority 1 `
  --weight 50 `
  --enabled-state Enabled

az afd origin create `
  -g $rg `
  --profile-name $profile `
  --origin-group-name $og `
  --origin-name "westus" `
  --host-name $originWest `
  --origin-host-header $originWest `
  --http-port 80 `
  --https-port 443 `
  --priority 1 `
  --weight 50 `
  --enabled-state Enabled

  az afd route create `
  -g $rg `
  --profile-name $profile `
  --endpoint-name $endpoint `
  --route-name $route `
  --origin-group $og `
  --supported-protocols Https `
  --https-redirect Enabled `
  --forwarding-protocol MatchRequest `
  --link-to-default-domain Enabled `
  --patterns-to-match "/*"

  az afd endpoint show -g $rg --profile-name $profile --endpoint-name $endpoint --query hostName -o tsv
  # award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net





