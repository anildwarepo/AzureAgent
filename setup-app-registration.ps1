<#
.SYNOPSIS
    Creates and configures an Entra ID app registration for the Azure Ops Agent SPA.

.DESCRIPTION
    This script is IDEMPOTENT — it can be run repeatedly without side effects.
    Existing resources are reused; only missing pieces are created.

    Steps:
      1. Creates or reuses a multi-tenant app registration
      2. Configures SPA platform with redirect URIs
      3. Sets Application ID URI (api://{appId}) and token version v2
      4. Exposes a delegated "access_as_user" scope (reuses existing)
      5. Adds Azure Service Management user_impersonation permission (for OBO)
      6. Pre-authorises the SPA as a known client (no double consent)
      7. Creates or reuses the service principal (enterprise app)
      8. Creates a client secret (only if none exists, or -RotateSecret is set)
      9. Grants admin consent for API permissions (home tenant)
     10. Assigns Cognitive Services OpenAI User role (skips if already assigned)
     11. Updates authConfig.js and .env with the current values

.PARAMETER AppDisplayName
    Display name for the app registration. Default: "azure-ops-agent-spa"

.PARAMETER RedirectUris
    SPA redirect URIs. Default: @("http://localhost:3000")

.PARAMETER SecretYears
    Lifetime of the client secret in years. Default: 1

.PARAMETER RotateSecret
    Force-rotate the client secret even if one already exists.

.PARAMETER AzureOpenAIResourceName
    Name of the Azure OpenAI / Cognitive Services resource to grant the SP access to.
    If not provided, the script reads AZURE_OPENAI_ENDPOINT from af_fastapi/.env* and
    extracts the resource name automatically.

.EXAMPLE
    .\setup-app-registration.ps1
    .\setup-app-registration.ps1 -AppDisplayName "my-agent-spa" -RedirectUris @("http://localhost:3000","https://myapp.azurecontainerapps.io")
    .\setup-app-registration.ps1 -RotateSecret
    .\setup-app-registration.ps1 -AzureOpenAIResourceName "my-openai-resource"
#>

[CmdletBinding()]
param(
    [string]$AppDisplayName = "azure-ops-agent-spa",
    [string[]]$RedirectUris = @("http://localhost:3000"),
    [int]$SecretYears = 1,
    [switch]$RotateSecret,
    [string]$AzureOpenAIResourceName = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Temp files used across steps — cleaned up at the end
$tempFiles = [System.Collections.Generic.List[string]]::new()

function Write-TempJson([string]$name, [string]$json) {
    $path = Join-Path $env:TEMP $name
    [System.IO.File]::WriteAllText($path, $json)
    $tempFiles.Add($path)
    return $path
}

# ── Prerequisites ────────────────────────────────────────────────────────────
Write-Host "`n=== Entra ID App Registration Setup ===" -ForegroundColor Cyan

try {
    $account = az account show --query "{tenantId:tenantId, subscription:name}" -o json 2>$null | ConvertFrom-Json
    Write-Host "  Tenant:       $($account.tenantId)" -ForegroundColor Gray
    Write-Host "  Subscription: $($account.subscription)" -ForegroundColor Gray
} catch {
    Write-Error "Not logged in to Azure CLI. Run 'az login' first."
    exit 1
}

$tenantId = $account.tenantId

# ── Step 1: Create or reuse app registration ─────────────────────────────────
Write-Host "`n[1/11] App registration '$AppDisplayName'..." -ForegroundColor Yellow

$existingApp = az ad app list --display-name $AppDisplayName `
    --query "[0].{appId:appId, objectId:id}" -o json 2>$null | ConvertFrom-Json

if ($existingApp -and $existingApp.appId) {
    $appId    = $existingApp.appId
    $objectId = $existingApp.objectId
    Write-Host "  Already exists — reusing." -ForegroundColor Green
} else {
    $appJson = az ad app create `
        --display-name $AppDisplayName `
        --sign-in-audience "AzureADMultipleOrgs" `
        --enable-access-token-issuance true `
        --query "{appId:appId, objectId:id}" `
        -o json | ConvertFrom-Json
    $appId    = $appJson.appId
    $objectId = $appJson.objectId
    Write-Host "  Created." -ForegroundColor Green
}

Write-Host "  App (client) ID: $appId" -ForegroundColor Gray
Write-Host "  Object ID:       $objectId" -ForegroundColor Gray

# ── Step 2: Configure SPA platform redirect URIs ─────────────────────────────
Write-Host "`n[2/11] Configuring SPA platform redirect URIs..." -ForegroundColor Yellow

$spaBody = @{
    web = @{ redirectUris = @() }
    spa = @{ redirectUris = $RedirectUris }
} | ConvertTo-Json -Depth 5 -Compress

$spaFile = Write-TempJson "app_spa_config.json" $spaBody
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$spaFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Redirect URIs: $($RedirectUris -join ', ')" -ForegroundColor Green

# ── Step 3: Set Application ID URI and token version ─────────────────────────
Write-Host "`n[3/11] Setting Application ID URI and token version v2..." -ForegroundColor Yellow

az ad app update --id $appId --identifier-uris "api://$appId" 2>$null

$tokenVerFile = Write-TempJson "app_token_ver.json" '{"api":{"requestedAccessTokenVersion":2}}'
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$tokenVerFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Done." -ForegroundColor Green

# ── Step 4: Expose delegated 'access_as_user' scope ──────────────────────────
Write-Host "`n[4/11] Exposing delegated scope 'access_as_user'..." -ForegroundColor Yellow

# Reuse existing scope ID if the scope already exists
$appScopes = az rest --method GET `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --query "api.oauth2PermissionScopes" -o json 2>$null | ConvertFrom-Json

$existingScope = $appScopes | Where-Object { $_.value -eq "access_as_user" } | Select-Object -First 1

if ($existingScope) {
    $scopeId = $existingScope.id
    Write-Host "  Already exists — reusing scope ID: $scopeId" -ForegroundColor Green
} else {
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

    $scopeFile = Write-TempJson "app_scope_config.json" $scopeBody
    az rest --method PATCH `
        --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
        --body "@$scopeFile" `
        --headers "Content-Type=application/json" 2>$null

    Write-Host "  Created scope ID: $scopeId" -ForegroundColor Green
}

# ── Step 5: Add Azure Service Management delegated permission ────────────────
# Required for the backend OBO flow (.default resolves to these permissions).
# The v2.0 login endpoint uses incremental consent — only the scopes requested
# at login (access_as_user) appear in the consent prompt, not this.
Write-Host "`n[5/11] Adding Azure Service Management 'user_impersonation' permission..." -ForegroundColor Yellow

$mgmtApiId           = "797f4846-ba00-4fd7-ba43-dac1f8f63013"
$userImpersonationId = "41094075-9dad-400e-a0bd-54e686782033"

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

$permFile = Write-TempJson "app_perm_config.json" $permBody
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
                appId                  = $appId
                delegatedPermissionIds = @($scopeId)
            }
        )
    }
} | ConvertTo-Json -Depth 5 -Compress

$preAuthFile = Write-TempJson "app_preauth_config.json" $preAuthBody
az rest --method PATCH `
    --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --body "@$preAuthFile" `
    --headers "Content-Type=application/json" 2>$null

Write-Host "  Done." -ForegroundColor Green

# ── Step 7: Create or reuse service principal ────────────────────────────────
Write-Host "`n[7/11] Service principal (enterprise app)..." -ForegroundColor Yellow

$spObjectId = az ad sp show --id $appId --query id -o tsv 2>$null

if ($spObjectId) {
    Write-Host "  Already exists — reusing." -ForegroundColor Green
} else {
    $spJson = az ad sp create --id $appId --query "{spId:id}" -o json 2>$null | ConvertFrom-Json
    $spObjectId = $spJson.spId
    Write-Host "  Created." -ForegroundColor Green
}

Write-Host "  Service Principal ID: $spObjectId" -ForegroundColor Gray

# ── Step 8: Client secret for backend OBO flow ──────────────────────────────
Write-Host "`n[8/11] Client secret..." -ForegroundColor Yellow

$existingCreds = az ad app credential list --id $appId `
    --query "[?displayName=='backend-obo-secret'].{id:keyId, endDate:endDateTime}" `
    -o json 2>$null | ConvertFrom-Json
$clientSecret = ""

if ($existingCreds -and $existingCreds.Count -gt 0 -and -not $RotateSecret) {
    Write-Host "  Secret already exists (expires $($existingCreds[0].endDate))." -ForegroundColor Green
    Write-Host "  Use -RotateSecret to force rotation." -ForegroundColor DarkGray

    # Read existing secret from .env so we can preserve it in the summary
    foreach ($ef in @(
        (Join-Path $PSScriptRoot "af_fastapi" ".env"),
        (Join-Path $PSScriptRoot "af_fastapi" ".env.azure_ops")
    )) {
        if (Test-Path $ef) {
            $envMatch = Select-String -Path $ef -Pattern 'AZURE_CLIENT_SECRET=(.+)' | Select-Object -First 1
            if ($envMatch) {
                $clientSecret = $envMatch.Matches[0].Groups[1].Value.Trim()
                break
            }
        }
    }
    if (-not $clientSecret) {
        Write-Host "  NOTE: Existing secret not in .env files — config files won't be updated with it." -ForegroundColor DarkYellow
    }
} else {
    if ($RotateSecret -and $existingCreds) {
        Write-Host "  Rotating secret..." -ForegroundColor Gray
    }
    $credJson = az ad app credential reset `
        --id $appId `
        --display-name "backend-obo-secret" `
        --years $SecretYears `
        --query "{appId:appId, secret:password, tenant:tenant}" `
        -o json | ConvertFrom-Json

    $clientSecret = $credJson.secret
    Write-Host "  Secret created (save it now — it won't be shown again)." -ForegroundColor Green
}

# ── Step 9: Grant admin consent (home tenant only) ───────────────────────────
Write-Host "`n[9/11] Granting admin consent for API permissions (home tenant)..." -ForegroundColor Yellow

az ad app permission admin-consent --id $appId 2>$null
Write-Host "  Done." -ForegroundColor Green
Write-Host "  TIP: External tenant admins can pre-approve via:" -ForegroundColor DarkGray
Write-Host "  https://login.microsoftonline.com/{tenant-id}/adminconsent?client_id=$appId" -ForegroundColor DarkGray

# ── Step 10: Assign Cognitive Services OpenAI User role ──────────────────────
Write-Host "`n[10/11] Assigning 'Cognitive Services OpenAI User' role to the SP..." -ForegroundColor Yellow

# Auto-detect OpenAI resource name from .env files if not provided
if (-not $AzureOpenAIResourceName) {
    foreach ($ef in @(
        (Join-Path $PSScriptRoot "af_fastapi" ".env.azure_ops"),
        (Join-Path $PSScriptRoot "af_fastapi" ".env")
    )) {
        if (Test-Path $ef) {
            $match = Select-String -Path $ef -Pattern 'AZURE_OPENAI_ENDPOINT\s*=\s*https://([^.]+)\.(cognitiveservices|openai)\.azure\.com' | Select-Object -First 1
            if ($match) {
                $AzureOpenAIResourceName = $match.Matches[0].Groups[1].Value
                Write-Host "  Auto-detected resource: $AzureOpenAIResourceName (from $ef)" -ForegroundColor Gray
                break
            }
        }
    }
}

if ($AzureOpenAIResourceName) {
    $openaiResourceId = az cognitiveservices account list -o json 2>$null |
        ConvertFrom-Json | Where-Object { $_.name -eq $AzureOpenAIResourceName } |
        Select-Object -ExpandProperty id -First 1

    if ($openaiResourceId) {
        # Check if role is already assigned
        $existingRole = az role assignment list `
            --assignee $spObjectId `
            --role "Cognitive Services OpenAI User" `
            --scope $openaiResourceId `
            --query "[0].id" -o tsv 2>$null

        if ($existingRole) {
            Write-Host "  Already assigned on: $AzureOpenAIResourceName" -ForegroundColor Green
        } else {
            az role assignment create `
                --assignee-object-id $spObjectId `
                --assignee-principal-type ServicePrincipal `
                --role "Cognitive Services OpenAI User" `
                --scope $openaiResourceId 2>$null | Out-Null
            Write-Host "  Assigned on: $AzureOpenAIResourceName" -ForegroundColor Green
        }
    } else {
        Write-Host "  WARNING: Could not find Cognitive Services resource '$AzureOpenAIResourceName'" -ForegroundColor DarkYellow
        Write-Host "  Manually run:" -ForegroundColor DarkYellow
        Write-Host "    az role assignment create --assignee-object-id $spObjectId --assignee-principal-type ServicePrincipal --role 'Cognitive Services OpenAI User' --scope <resource-id>" -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  Skipped: No AZURE_OPENAI_ENDPOINT found in .env files." -ForegroundColor DarkYellow
    Write-Host "  Set -AzureOpenAIResourceName or assign manually." -ForegroundColor DarkYellow
}

# ── Step 11: Update authConfig.js and .env ───────────────────────────────────
Write-Host "`n[11/11] Updating config files..." -ForegroundColor Yellow

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

// Scope for backend API — only asks for access_as_user (user-consentable,
// no admin consent required in external tenants)
export const backendApiLoginRequest = {
  scopes: ["api://$appId/access_as_user"],
};

// Scope for Azure Management — requested separately via incremental consent
// so external-tenant users aren't blocked by an admin consent prompt on login.
// If the user's tenant blocks this, the backend OBO flow is used as fallback.
export const azureManagementLoginRequest = {
  scopes: ["https://management.azure.com/user_impersonation"],
};

// Helper: admin consent URL for external tenant admins who want to
// pre-approve the app for all users in their tenant (optional).
export const buildAdminConsentUrl = (tenantId) =>
  ``https://login.microsoftonline.com/`${tenantId}/adminconsent?client_id=$appId&redirect_uri=`${encodeURIComponent(window.location.origin)}``;
"@
    Set-Content -Path $authConfigPath -Value $authConfigContent -Encoding utf8
    Write-Host "  Updated: $authConfigPath" -ForegroundColor Green
} else {
    Write-Host "  Skipped: $authConfigPath not found" -ForegroundColor DarkYellow
}

# Update .env files with client ID (and secret if available)
if ($clientSecret) {
    foreach ($ef in @(
        (Join-Path $PSScriptRoot "af_fastapi" ".env"),
        (Join-Path $PSScriptRoot "af_fastapi" ".env.azure_ops")
    )) {
        if (Test-Path $ef) {
            $content = Get-Content $ef -Raw
            $content = $content -replace 'AZURE_CLIENT_ID=.*', "AZURE_CLIENT_ID=$appId"
            $content = $content -replace 'AZURE_CLIENT_SECRET=.*', "AZURE_CLIENT_SECRET=$clientSecret"
            Set-Content -Path $ef -Value $content.TrimEnd() -Encoding utf8
            Write-Host "  Updated: $ef" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  Skipped .env secret update (existing secret retained)." -ForegroundColor DarkGray
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
Write-Host "Backend Environment Variables:" -ForegroundColor White
Write-Host "  AZURE_TENANT_ID=$tenantId"
Write-Host "  AZURE_CLIENT_ID=$appId"
if ($clientSecret) {
    Write-Host "  AZURE_CLIENT_SECRET=$clientSecret" -ForegroundColor Red
    Write-Host ""
    Write-Host "IMPORTANT: Save the client secret above — it will not be shown again." -ForegroundColor Red
} else {
    Write-Host "  AZURE_CLIENT_SECRET=(unchanged — existing secret retained)" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Cross-Tenant Notes:" -ForegroundColor White
Write-Host "  - The app is multi-tenant. Users from any Azure AD org can sign in."
Write-Host "  - Login only requests access_as_user (no admin consent required)."
Write-Host "  - The backend uses OBO to exchange for Azure Management tokens."
Write-Host "  - External tenant admins can optionally pre-approve via:"
Write-Host "    https://login.microsoftonline.com/{tenant-id}/adminconsent?client_id=$appId" -ForegroundColor Cyan
Write-Host ""

# Clean up temp files
foreach ($f in $tempFiles) {
    Remove-Item -Path $f -Force -ErrorAction SilentlyContinue
}
