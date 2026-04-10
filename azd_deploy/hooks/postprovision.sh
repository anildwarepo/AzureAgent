#!/bin/bash
#
# Post-provision hook to build containers and deploy apps in sequence.
#

set -euo pipefail

# Guard against recursive execution: when deploy_all_container_apps calls
# 'azd provision --no-prompt', azd re-triggers hooks.  Skip the nested run.
if [ "${AZD_POSTPROVISION_PHASE:-}" = "1" ]; then
  echo "Skipping nested postprovision hook (provision phase in progress)."
  exit 0
fi

MCP_SERVER_PATH="${1:-../../mcp_server}"
FASTAPI_PATH="${2:-../../af_fastapi}"
WEBAPP_PATH="${3:-../../azure-agent-spa}"

get_azd_env() {
  azd env get-value "$1" 2>/dev/null | tr -d '\r'
}

set_azd_env() {
  azd env set "$1" "$2" >/dev/null
}

get_folder_hash() {
  local folder_path="$1"
  find "$folder_path" -type f \
    ! -path "*/__pycache__/*" \
    ! -path "*/node_modules/*" \
    ! -path "*/.git/*" \
    ! -name "*.pyc" \
    ! -name "*.pyo" \
    ! -path "*.egg-info/*" \
    -exec md5sum {} \; 2>/dev/null | sort | md5sum | cut -d' ' -f1
}

build_needed() {
  local folder_path="$1"
  local hash_env_var="$2"

  local current_hash
  current_hash=$(get_folder_hash "$folder_path")
  local stored_hash
  stored_hash=$(get_azd_env "$hash_env_var")

  if [ "$current_hash" = "$stored_hash" ]; then
    echo "false;$current_hash"
  else
    echo "true;$current_hash"
  fi
}

# Deploy all container apps using az deployment group create directly.
deploy_all_container_apps() {
  local resource_group="$1"

  echo ""
  echo "=========================================="
  echo "DEPLOY PHASE: Deploying all container apps"
  echo "=========================================="

  local infra_dir
  infra_dir="$(cd "$(dirname "$0")/../infra" && pwd)"
  local template_file="$infra_dir/main.bicep"
  local parameters_file="$infra_dir/main.parameters.json"

  if [ ! -f "$template_file" ]; then
    echo "ERROR: Bicep template not found: $template_file" >&2
    return 1
  fi
  if [ ! -f "$parameters_file" ]; then
    echo "ERROR: Parameters file not found: $parameters_file" >&2
    return 1
  fi

  echo "  Resource Group:   $resource_group"
  echo "  Template:         $template_file"
  echo "  Deploy flags:     MCP=true, FastAPI=true, Webapp=true"
  echo ""

  # Create a resolved parameters file replacing azd ${...} tokens
  local resolved_params="/tmp/azd-deploy-params-resolved.json"
  python3 -c "
import json
with open('$parameters_file') as f:
    params = json.load(f)
params['parameters']['deployContainerApp']['value'] = False
params['parameters']['deployContainerAppsEnv']['value'] = True
params['parameters']['deployMcpServerContainerApp']['value'] = True
params['parameters']['deployFastApiContainerApp']['value'] = True
params['parameters']['deployWebappContainerApp']['value'] = True
json.dump(params, open('$resolved_params', 'w'), indent=2)
"

  echo "  Resolved parameters written to: $resolved_params"
  echo "  Starting ARM deployment (this may take several minutes)..."
  echo ""

  local deploy_name="postprovision-$(date +%Y%m%d-%H%M%S)"

  az deployment group create \
    --resource-group "$resource_group" \
    --template-file "$template_file" \
    --parameters "@$resolved_params" \
    --name "$deploy_name" \
    --no-prompt

  local deploy_exit=$?
  rm -f "$resolved_params"

  if [ $deploy_exit -ne 0 ]; then
    echo ""
    echo "ERROR: Container app deployment failed (exit code $deploy_exit)." >&2
    return $deploy_exit
  fi

  echo ""
  echo "  Container app deployment completed successfully."
}

# ============================================================
# MAIN EXECUTION
# ============================================================

acr_name="$(get_azd_env acrName)"
acr_login_server="$(get_azd_env acrLoginServer)"
mcp_image_name="$(get_azd_env mcpServerImageName)"
mcp_image_tag="$(get_azd_env mcpServerImageTag)"
build_mcp="$(get_azd_env buildMcpServerContainer)"
fastapi_image_name="$(get_azd_env fastApiImageName)"
fastapi_image_tag="$(get_azd_env fastApiImageTag)"
build_fastapi="$(get_azd_env buildFastApiContainer)"
webapp_image_name="$(get_azd_env webappImageName)"
webapp_image_tag="$(get_azd_env webappImageTag)"
build_webapp="$(get_azd_env buildWebappContainer)"
resource_group="$(get_azd_env AZURE_RESOURCE_GROUP)"

if [ -z "$acr_name" ]; then
  echo "ACR not deployed, skipping container builds"
  exit 0
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"

# ---- BUILD PHASE: Build all container images ----
echo "=========================================="
echo "Building container images..."
echo "=========================================="

if [ "$build_mcp" != "false" ]; then
  mcp_full_path="$(cd "$script_dir/$MCP_SERVER_PATH" && pwd)"
  IFS=';' read -r mcp_needed mcp_hash <<< "$(build_needed "$mcp_full_path" "mcpServerFolderHash")"
  if [ "$mcp_needed" = "true" ]; then
    az acr build --registry "$acr_name" --image "${mcp_image_name}:${mcp_image_tag}" --image "${mcp_image_name}:latest" --file "$mcp_full_path/Dockerfile" "$mcp_full_path"
    set_azd_env "mcpServerFolderHash" "$mcp_hash"
    echo "MCP Server container built: $acr_login_server/${mcp_image_name}:${mcp_image_tag}"
  else
    echo "MCP Server container is up-to-date, skipping build."
  fi
fi

if [ "$build_fastapi" != "false" ]; then
  fastapi_full_path="$(cd "$script_dir/$FASTAPI_PATH" && pwd)"
  IFS=';' read -r fastapi_needed fastapi_hash <<< "$(build_needed "$fastapi_full_path" "fastApiFolderHash")"
  if [ "$fastapi_needed" = "true" ]; then
    az acr build --registry "$acr_name" --image "${fastapi_image_name}:${fastapi_image_tag}" --image "${fastapi_image_name}:latest" --file "$fastapi_full_path/Dockerfile" "$fastapi_full_path"
    set_azd_env "fastApiFolderHash" "$fastapi_hash"
    echo "FastAPI container built: $acr_login_server/${fastapi_image_name}:${fastapi_image_tag}"
  else
    echo "FastAPI container is up-to-date, skipping build."
  fi
fi

if [ "$build_webapp" != "false" ]; then
  webapp_full_path="$(cd "$script_dir/$WEBAPP_PATH" && pwd)"
  # Copy SPA source into webapp build context so Docker can access it.
  spa_source_path="$(cd "$script_dir/../../azure-agent-spa" && pwd)"
  echo "Copying SPA source from $spa_source_path into $webapp_full_path..."
  for item in package.json package-lock.json public src; do
    if [ -e "$spa_source_path/$item" ]; then
      rm -rf "$webapp_full_path/$item"
      cp -r "$spa_source_path/$item" "$webapp_full_path/$item"
    fi
  done
  IFS=';' read -r webapp_needed webapp_hash <<< "$(build_needed "$webapp_full_path" "webappFolderHash")"
  if [ "$webapp_needed" = "true" ]; then
    az acr build --registry "$acr_name" --image "${webapp_image_name}:${webapp_image_tag}" --image "${webapp_image_name}:latest" --file "$webapp_full_path/Dockerfile" "$webapp_full_path"
    set_azd_env "webappFolderHash" "$webapp_hash"
    echo "Webapp container built: $acr_login_server/${webapp_image_name}:${webapp_image_tag}"
  else
    echo "Webapp container is up-to-date, skipping build."
  fi
fi

# ---- DEPLOY PHASE: Single ARM deployment to deploy all container apps ----
deploy_all_container_apps "$resource_group"

echo "=========================================="
echo "Post-provision completed successfully."
echo "=========================================="

# ---- Update Entra ID app registration with ACA redirect URI ----
webapp_fqdn="$(get_azd_env webappContainerAppFqdn)"
if [ -n "$webapp_fqdn" ]; then
  echo ""
  echo "=========================================="
  echo "  Webapp URL: https://$webapp_fqdn"
  echo "=========================================="
  echo ""

  # Extract client ID from authConfig.js
  auth_config_file="$script_dir/../../azure-agent-spa/src/authConfig.js"
  entra_client_id=""
  if [ -f "$auth_config_file" ]; then
    entra_client_id="$(grep -oP 'clientId:\s*"\K[^"]+' "$auth_config_file" | head -1)"
  fi

  if [ -n "$entra_client_id" ]; then
    redirect_uri="https://$webapp_fqdn"
    echo "Updating Entra ID app registration ($entra_client_id) with redirect URI: $redirect_uri"

    # Read current SPA redirect URIs
    current_uris="$(az ad app show --id "$entra_client_id" --query "spa.redirectUris" -o tsv 2>/dev/null || true)"

    if echo "$current_uris" | grep -qF "$redirect_uri"; then
      echo "  Redirect URI already registered, skipping."
    else
      # Build list of all URIs (existing + new)
      all_uris="$(echo "$current_uris" | tr '\n' ' ') $redirect_uri"
      if az ad app update --id "$entra_client_id" --spa-redirect-uris $all_uris 2>/dev/null; then
        echo "  Redirect URI added successfully."
      else
        echo "  WARNING: Failed to update redirect URI. Add it manually in the Azure portal."
        echo "  App Registration > Authentication > SPA redirect URIs > Add: $redirect_uri"
      fi
    fi
  else
    echo "WARNING: Could not extract Entra client ID from authConfig.js."
    echo "  Add the redirect URI manually: https://$webapp_fqdn"
  fi
fi