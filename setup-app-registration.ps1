<#
.SYNOPSIS
    Creates and configures an Entra ID app registration for the Azure Ops Agent SPA.

.DESCRIPTION
    This script automates the full setup:
      1. Creates a multi-tenant app registration
      2. Configures SPA platform with redirect URIs
      3. Sets Application ID URI (api://{appId})
      4. Exposes a delegated "access_as_user" scope
      5. Adds Azure Service Management user_impersonation permission
      6. Pre-authorises the SPA as a known client (no double consent)
      7. Creates the service principal (enterprise app)
      8. Creates a client secret for the backend OBO flow
      9. Grants admin consent for API permissions
     10. Assigns Cognitive Services OpenAI User role on the OpenAI resource
     11. Updates authConfig.js and .env with the new values

.PARAMETER AppDisplayName
    Display name for the app registration. Default: "azure-ops-agent-spa"

.PARAMETER RedirectUris
    SPA redirect URIs. Default: @("http://localhost:3000")

.PARAMETER SecretYears
    Lifetime of the client secret in years. Default: 1

.PARAMETER AzureOpenAIResourceName
    Name of the Azure OpenAI / Cognitive Services resource to grant the SP access to.
    If not provided, the script reads AZURE_OPENAI_ENDPOINT from af_fastapi/.env* and
    extracts the resource name automatically.

.EXAMPLE
    .\setup-app-registration.ps1
    .\setup-app-registration.ps1 -AppDisplayName "my-agent-spa" -RedirectUris @("http://localhost:3000","https://myapp.azurecontainerapps.io")
    .\setup-app-registration.ps1 -AzureOpenAIResourceName "my-openai-resource"
#>

[CmdletBinding()]
param(
    [string]$AppDisplayName = "azure-ops-agent-spa",
    [string[]]$RedirectUris = @("http://localhost:3000"),
    [int]$SecretYears = 1,
    [string]$AzureOpenAIResourceName = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ── Prerequisites ────────────────────────────────────────────────────────────
Write-Host "`n=== Entra ID App Registration Setup ===" -ForegroundColor Cyan

# Verify Azure CLI is logged in
try {
    $account = az account show --query "{tenantId:tenantId, subscription:name}" -o json 2>$null | ConvertFrom-Json
    Write-Host "  Tenant:       $($account.tenantId)" -ForegroundColor Gray
    Write-Host "  Subscription: $($account.subscription)" -ForegroundColor Gray
} catch {
    Write-Error "Not logged in to Azure CLI. Run 'az login' first."
    exit 1
}

$tenantId = $account.tenantId

# ── Step 1: Create the app registration ──────────────────────────────────────
Write-Host "`n[1/11] Creating app registration '$AppDisplayName' (multi-tenant)..." -ForegroundColor Yellow

$appJson = az ad app create `
    --display-name $AppDisplayName `
    --sign-in-audience "AzureADMultipleOrgs" `
    --enable-access-token-issuance true `
    --query "{appId:appId, objectId:id}" `
    -o json | ConvertFrom-Json

$appId    = $appJson.appId
$objectId = $appJson.objectId

Write-Host "  App (client) ID: $appId" -ForegroundColor Green
Write-Host "  Object ID:       $objectId" -ForegroundColor Green

# ── Step 2: Configure SPA platform redirect URIs ─────────────────────────────
Write-Host "`n[2/11] Configuring SPA platform redirect URIs..." -ForegroundColor Yellow

$redirectArray = $RedirectUris | ForEach-Object { "`"$_`"" }
$redirectJson  = "[" + ($redirectArray -join ",") + "]"

$spaBody = @{
    web = @{ redirectUris = @() }
    spa = @{ redirectUris = $RedirectUris }
} | ConvertTo-Json -Depth 5 -Compress

$spaFile = Join-Path $env:TEMP "app_spa_config.json"
$spaBody | Out-File -FilePath $spaFile -Encoding utf8 -Force
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$spaFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Redirect URIs: $($RedirectUris -join ', ')" -ForegroundColor Green

# ── Step 3: Set Application ID URI and token version ─────────────────────────
Write-Host "`n[3/11] Setting Application ID URI (api://$appId) and token version v2..." -ForegroundColor Yellow

az ad app update --id $appId --identifier-uris "api://$appId" 2>$null

# Set accessTokenAcceptedVersion=2 so the SPA's v2 tokens work with OBO
$tokenVerBody = '{"api":{"requestedAccessTokenVersion":2}}'
$tokenVerFile = Join-Path $env:TEMP "app_token_ver.json"
[System.IO.File]::WriteAllText($tokenVerFile, $tokenVerBody)
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$tokenVerFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Done." -ForegroundColor Green

# ── Step 4: Expose delegated 'access_as_user' scope ──────────────────────────
Write-Host "`n[4/11] Exposing delegated scope 'access_as_user'..." -ForegroundColor Yellow

$scopeId = [guid]::NewGuid().ToString()

$scopeBody = @{
    api = @{
        oauth2PermissionScopes = @(
            @{
                id                      = $scopeId
                adminConsentDescription = "Access Azure Ops Agent API on behalf of the signed-in user"
                adminConsentDisplayName = "Access as user"
                isEnabled               = $true
                type                    = "User"
                userConsentDescription  = "Access Azure Ops Agent API on your behalf"
                userConsentDisplayName  = "Access as user"
                value                   = "access_as_user"
            }
        )
    }
} | ConvertTo-Json -Depth 5 -Compress

$scopeFile = Join-Path $env:TEMP "app_scope_config.json"
$scopeBody | Out-File -FilePath $scopeFile -Encoding utf8 -Force
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$scopeFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Scope ID: $scopeId" -ForegroundColor Green

# ── Step 5: Add Azure Service Management delegated permission ────────────────
Write-Host "`n[5/11] Adding Azure Service Management 'user_impersonation' permission..." -ForegroundColor Yellow

# Well-known IDs for Azure Service Management API
$mgmtApiId            = "797f4846-ba00-4fd7-ba43-dac1f8f63013"
$userImpersonationId  = "41094075-9dad-400e-a0bd-54e686782033"

$permBody = @{
    requiredResourceAccess = @(
        @{
            resourceAppId  = $mgmtApiId
            resourceAccess = @(
                @{ id = $userImpersonationId; type = "Scope" }
            )
        }
    )
} | ConvertTo-Json -Depth 5 -Compress

$permFile = Join-Path $env:TEMP "app_perm_config.json"
$permBody | Out-File -FilePath $permFile -Encoding utf8 -Force
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$permFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Done." -ForegroundColor Green

# ── Step 6: Pre-authorise the SPA as a known client ──────────────────────────
Write-Host "`n[6/11] Pre-authorising SPA as known client application..." -ForegroundColor Yellow

$preAuthBody = @{
    api = @{
        preAuthorizedApplications = @(
            @{
                appId                    = $appId
                delegatedPermissionIds   = @($scopeId)
            }
        )
    }
} | ConvertTo-Json -Depth 5 -Compress

$preAuthFile = Join-Path $env:TEMP "app_preauth_config.json"
$preAuthBody | Out-File -FilePath $preAuthFile -Encoding utf8 -Force
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$preAuthFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Done." -ForegroundColor Green

# ── Step 7: Create service principal ─────────────────────────────────────────
Write-Host "`n[7/11] Creating service principal (enterprise app)..." -ForegroundColor Yellow

$spJson = az ad sp create --id $appId --query "{spId:id, displayName:displayName}" -o json 2>$null | ConvertFrom-Json
$spObjectId = $spJson.spId
Write-Host "  Service Principal ID: $spObjectId" -ForegroundColor Green

# ── Step 8: Create client secret for backend OBO flow ────────────────────────
Write-Host "`n[8/11] Creating client secret (valid $SecretYears year(s))..." -ForegroundColor Yellow

$credJson = az ad app credential reset `
    --id $appId `
    --display-name "backend-obo-secret" `
    --years $SecretYears `
    --query "{appId:appId, secret:password, tenant:tenant}" `
    -o json | ConvertFrom-Json

$clientSecret = $credJson.secret
Write-Host "  Secret created (shown below — save it now, it won't be shown again)." -ForegroundColor Green

# ── Step 9: Grant admin consent ──────────────────────────────────────────────
Write-Host "`n[9/11] Granting admin consent for API permissions..." -ForegroundColor Yellow

az ad app permission admin-consent --id $appId 2>$null
Write-Host "  Done." -ForegroundColor Green

# ── Step 10: Assign Cognitive Services OpenAI User role ──────────────────────
Write-Host "`n[10/11] Assigning 'Cognitive Services OpenAI User' role to the SP..." -ForegroundColor Yellow

# Auto-detect OpenAI resource name from .env files if not provided
if (-not $AzureOpenAIResourceName) {
    $envFiles = @(
        (Join-Path $PSScriptRoot "af_fastapi" ".env.azure_ops"),
        (Join-Path $PSScriptRoot "af_fastapi" ".env")
    )
    foreach ($ef in $envFiles) {
        if (Test-Path $ef) {
            $match = Select-String -Path $ef -Pattern 'AZURE_OPENAI_ENDPOINT\s*=\s*https://([^.]+)\.cognitiveservices\.azure\.com' | Select-Object -First 1
            if ($match) {
                $AzureOpenAIResourceName = $match.Matches[0].Groups[1].Value
                Write-Host "  Auto-detected OpenAI resource: $AzureOpenAIResourceName (from $ef)" -ForegroundColor Gray
                break
            }
        }
    }
}

if ($AzureOpenAIResourceName) {
    $openaiResourceId = az cognitiveservices account list --query "[?name=='$AzureOpenAIResourceName'].id | [0]" -o tsv 2>$null
    if ($openaiResourceId) {
        az role assignment create `
            --assignee $spObjectId `
            --role "Cognitive Services OpenAI User" `
            --scope $openaiResourceId 2>$null | Out-Null
        Write-Host "  Assigned on: $AzureOpenAIResourceName" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Could not find Cognitive Services resource '$AzureOpenAIResourceName'" -ForegroundColor DarkYellow
        Write-Host "  Manually run: az role assignment create --assignee $spObjectId --role 'Cognitive Services OpenAI User' --scope <resource-id>" -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  Skipped: No AZURE_OPENAI_ENDPOINT found in .env files. Set -AzureOpenAIResourceName or assign manually." -ForegroundColor DarkYellow
}

# ── Step 11: Update authConfig.js and .env ───────────────────────────────────
Write-Host "`n[11/11] Updating config files..." -ForegroundColor Yellow

# Update authConfig.js
Write-Host "  Updating azure-agent-spa/src/authConfig.js..." -ForegroundColor Gray

$authConfigPath = Join-Path $PSScriptRoot "azure-agent-spa" "src" "authConfig.js"
if (Test-Path $authConfigPath) {
    $authConfigContent = @"
// Entra ID configuration for Azure Operations Agent SPA
//
// Auto-generated by setup-app-registration.ps1
// App Registration: $AppDisplayName

export const msalConfig = {
  auth: {
    clientId: "$appId",
    authority: "https://login.microsoftonline.com/common",
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
};

// Delegated scope — token is forwarded to backend which exchanges it via OBO
// for an Azure Management token
export const azureManagementLoginRequest = {
  scopes: ["api://$appId/access_as_user"],
};
"@
    Set-Content -Path $authConfigPath -Value $authConfigContent -Encoding utf8
    Write-Host "  Updated: $authConfigPath" -ForegroundColor Green
} else {
    Write-Host "  Skipped: $authConfigPath not found" -ForegroundColor DarkYellow
}

# Update .env files with new client ID and secret
$envFiles = @(
    (Join-Path $PSScriptRoot "af_fastapi" ".env"),
    (Join-Path $PSScriptRoot "af_fastapi" ".env.azure_ops")
)
foreach ($ef in $envFiles) {
    if (Test-Path $ef) {
        $content = Get-Content $ef -Raw
        $content = $content -replace 'AZURE_CLIENT_ID=.*', "AZURE_CLIENT_ID=$appId"
        $content = $content -replace 'AZURE_CLIENT_SECRET=.*', "AZURE_CLIENT_SECRET=$clientSecret"
        Set-Content -Path $ef -Value $content.TrimEnd() -Encoding utf8
        Write-Host "  Updated: $ef" -ForegroundColor Green
    }
}

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "App Registration Details:" -ForegroundColor White
Write-Host "  Display Name:     $AppDisplayName"
Write-Host "  App (client) ID:  $appId"
Write-Host "  Object ID:        $objectId"
Write-Host "  Tenant ID:        $tenantId"
Write-Host "  Authority:        https://login.microsoftonline.com/common"
Write-Host "  API Scope:        api://$appId/access_as_user"
Write-Host ""
Write-Host "Backend Environment Variables (set these for the FastAPI backend):" -ForegroundColor White
Write-Host "  AZURE_TENANT_ID=$tenantId"
Write-Host "  AZURE_CLIENT_ID=$appId"
Write-Host "  AZURE_CLIENT_SECRET=$clientSecret" -ForegroundColor Red
Write-Host ""
Write-Host "IMPORTANT: Save the client secret above — it will not be shown again." -ForegroundColor Red
Write-Host ""
Write-Host "Cross-Tenant Notes:" -ForegroundColor White
Write-Host "  - The app is multi-tenant. Users from any Azure AD org can sign in."
Write-Host "  - The backend extracts 'tid' from the user's token and performs OBO"
Write-Host "    against the user's own tenant for Azure Management API access."
Write-Host "  - External tenant admins must grant consent once via:"
Write-Host "    https://login.microsoftonline.com/{tenant-id}/adminconsent?client_id=$appId" -ForegroundColor Cyan
Write-Host "  - Azure OpenAI is always accessed with the backend SP identity (not user delegation)."
Write-Host ""

# Clean up temp files
Remove-Item -Path $spaFile, $scopeFile, $permFile, $preAuthFile -Force -ErrorAction SilentlyContinue
