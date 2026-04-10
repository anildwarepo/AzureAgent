<#
.SYNOPSIS
    Pre-provision hook to delete existing container apps and reset flags.
#>

Write-Host "=========================================="
Write-Host "Pre-provision hook starting..."
Write-Host "=========================================="

# Delete existing container apps so they get cleanly recreated with latest
# images and env vars during the postprovision deployment.
$savedEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$rg = (cmd /c "azd env get-value AZURE_RESOURCE_GROUP 2>nul")
if ($rg) { $rg = $rg.Trim() }
if (-not [string]::IsNullOrEmpty($rg)) {
    Write-Host "Deleting existing container apps in $rg (if any)..."
    $apps = az containerapp list --resource-group $rg --query "[].name" -o tsv 2>$null
    if ($apps) {
        foreach ($app in ($apps -split "`r?`n" | Where-Object { $_ })) {
            Write-Host "  Deleting container app: $app"
            az containerapp delete --name $app --resource-group $rg --yes 2>&1 | Out-Null
        }
        Write-Host "  Container apps deleted."
    } else {
        Write-Host "  No container apps found."
    }
}
$ErrorActionPreference = $savedEAP

# Reset container app deployment flags to false so the initial azd provision
# (run by 'azd up') only creates infrastructure.  The postprovision hook will
# deploy container apps directly via az deployment group create.
Write-Host "Resetting container app deployment flags for clean provision..."
cmd /c "azd env set deployMcpServerContainerApp false 2>&1"
cmd /c "azd env set deployFastApiContainerApp false 2>&1"
cmd /c "azd env set deployWebappContainerApp false 2>&1"

Write-Host "=========================================="
