#!/bin/bash
#
# Pre-provision hook to delete existing container apps and reset flags.
#

echo "=========================================="
echo "Pre-provision hook starting..."
echo "=========================================="

# Delete existing container apps so they get cleanly recreated with latest
# images and env vars during the postprovision deployment.
rg="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null | tr -d '\r')"
if [ -n "$rg" ]; then
  echo "Deleting existing container apps in $rg (if any)..."
  apps="$(az containerapp list --resource-group "$rg" --query "[].name" -o tsv 2>/dev/null)"
  if [ -n "$apps" ]; then
    echo "$apps" | while IFS= read -r app; do
      [ -z "$app" ] && continue
      echo "  Deleting container app: $app"
      az containerapp delete --name "$app" --resource-group "$rg" --yes >/dev/null 2>&1 || true
    done
    echo "  Container apps deleted."
  else
    echo "  No container apps found."
  fi
fi

# Reset container app deployment flags to false so the initial azd provision
# (run by 'azd up') only creates infrastructure.  The postprovision hook will
# deploy container apps directly via az deployment group create.
echo "Resetting container app deployment flags for clean provision..."
azd env set deployMcpServerContainerApp false
azd env set deployFastApiContainerApp false
azd env set deployWebappContainerApp false

echo "=========================================="
