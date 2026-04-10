<#
.SYNOPSIS
    Post-provision hook to build containers and deploy apps in sequence.
#>

param(
    [string]$McpServerPath = "../../mcp_server",
    [string]$FastApiPath = "../../af_fastapi",
    [string]$WebappPath = "../../azure-agent-spa"
)

$ErrorActionPreference = "Stop"

# Guard against recursive execution: when Invoke-ContainerAppsDeploy calls
# 'azd provision --no-prompt', azd re-triggers hooks.  Skip the nested run.
if ($env:AZD_POSTPROVISION_PHASE -eq "1") {
    Write-Host "Skipping nested postprovision hook (provision phase in progress)."
    exit 0
}

Write-Host "=========================================="
Write-Host "Post-provision hook starting..."
Write-Host "=========================================="

function Get-AzdEnvValue {
    param([string]$Name)
    # Use cmd /c to avoid PS5.1 NativeCommandError on azd stderr warnings
    $result = (cmd /c "azd env get-value $Name 2>nul")
    if ($result) { $result.Trim() } else { "" }
}

function Set-AzdEnvValue {
    param(
        [string]$Name,
        [string]$Value
    )
    # Temporarily relax ErrorActionPreference so azd stderr warnings
    # (e.g., version-out-of-date) don't cause PS5.1 to throw.
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & azd env set $Name "$Value" 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $savedEAP
    }
}

# Helper: Run a native command without PS5.1 NativeCommandError on stderr.
# Temporarily lowers ErrorActionPreference so warnings/info on stderr don't
# become terminating errors.
function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command
    )
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
    } finally {
        $ErrorActionPreference = $savedEAP
    }
}

function Get-FolderHash {
    param([string]$FolderPath)

    $files = Get-ChildItem -Path $FolderPath -Recurse -File |
        Where-Object { $_.FullName -notmatch '(__pycache__|node_modules|\.venv|\.git|\.(pyc|pyo|egg-info))' } |
        Sort-Object FullName

    $hashInput = ""
    foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($FolderPath.Length)
        $fileHash = (Get-FileHash -Path $file.FullName -Algorithm MD5).Hash
        $hashInput += "$relativePath`:$fileHash`n"
    }

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($hashInput)
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $hashBytes = $md5.ComputeHash($bytes)
    [BitConverter]::ToString($hashBytes) -replace '-', ''
}

function Test-BuildNeeded {
    param(
        [string]$FolderPath,
        [string]$HashEnvVarName
    )

    $currentHash = Get-FolderHash -FolderPath $FolderPath
    $storedHash = Get-AzdEnvValue $HashEnvVarName

    if ($currentHash -eq $storedHash) {
        return @{ Needed = $false; Hash = $currentHash }
    }

    return @{ Needed = $true; Hash = $currentHash }
}

function Save-FolderHash {
    param(
        [string]$HashEnvVarName,
        [string]$Hash
    )
    Set-AzdEnvValue -Name $HashEnvVarName -Value $Hash
}

function Test-DockerAvailable {
    <#
    .SYNOPSIS
        Returns $true if Docker Desktop (or any local Docker daemon) is reachable.
    #>
    try {
        $verOutput = & docker version --format '{{.Server.Version}}' 2>&1
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrEmpty($verOutput)) {
            Write-Host "Docker Desktop detected (server version: $verOutput)"
            return $true
        }
    } catch { }
    return $false
}

function Invoke-DockerBuild {
    <#
    .SYNOPSIS
        Build a container image locally with Docker Desktop and push it to ACR.
    #>
    param(
        [string]$RegistryName,
        [string]$LoginServer,
        [string]$SourcePath,
        [string]$ImageName,
        [string]$ImageTag,
        [string]$Label,
        [string[]]$BuildArgs = @()
    )

    Push-Location $SourcePath
    try {
        Write-Host "  Building $Label container locally with Docker Desktop..."

        # Log in to ACR so we can push
        Write-Host "  Logging into ACR $RegistryName..."
        $savedEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $loginOutput = & az acr login --name $RegistryName 2>&1
            $loginExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $savedEAP
        }
        if ($loginExitCode -ne 0) {
            Write-Host "  ACR login output: $loginOutput" -ForegroundColor Yellow
            throw "ACR login failed for $RegistryName (exit code $loginExitCode). Ensure you have AcrPush or Contributor role on the ACR."
        }
        Write-Host "  ACR login successful."

        $fullTagged = "${LoginServer}/${ImageName}:${ImageTag}"
        $fullLatest = "${LoginServer}/${ImageName}:latest"

        $dockerArgs = @("build", "-t", $fullTagged, "-t", $fullLatest, "-f", "Dockerfile",
                        "--provenance=false", "--sbom=false")
        foreach ($arg in $BuildArgs) {
            $dockerArgs += "--build-arg"
            $dockerArgs += $arg
        }
        $dockerArgs += "."

        & docker @dockerArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed for $Label (exit code $LASTEXITCODE)"
        }

        Write-Host "  Pushing $Label image to $LoginServer..."
        # Retry push up to 3 times — ACR private endpoints can cause transient EOF errors
        foreach ($pushImage in @($fullTagged, $fullLatest)) {
            $pushSuccess = $false
            for ($attempt = 1; $attempt -le 3; $attempt++) {
                & docker push $pushImage
                if ($LASTEXITCODE -eq 0) {
                    $pushSuccess = $true
                    break
                }
                if ($attempt -lt 3) {
                    Write-Host "  Push failed (attempt $attempt/3), retrying in 5 seconds..."
                    Start-Sleep -Seconds 5
                    Invoke-NativeCommand { az acr login --name $RegistryName 2>&1 | Out-Null }
                }
            }
            if (-not $pushSuccess) {
                throw "Docker push failed for ${pushImage} after 3 attempts (exit code $LASTEXITCODE)"
            }
        }

        Write-Host "  $Label image pushed successfully."
    } finally {
        Pop-Location
    }
}

function Invoke-AcrBuild {
    param(
        [string]$RegistryName,
        [string]$SourcePath,
        [string]$ImageName,
        [string]$ImageTag,
        [string]$Label,
        [string[]]$BuildArgs = @()
    )

    Push-Location $SourcePath
    try {
        Write-Host "  Building $Label container in ACR $RegistryName (this may take several minutes)..."

        # Start ACR build without streaming logs (streaming can crash in PS5/cp1252).
        # We'll poll run status and then print full build logs from log artifact URL.
        $azArgs = @("acr", "build", "--registry", $RegistryName,
                    "--image", "${ImageName}:${ImageTag}",
                    "--image", "${ImageName}:latest",
                    "--file", "Dockerfile", ".",
                    "--no-logs", "--only-show-errors", "--output", "json")
        foreach ($arg in $BuildArgs) {
            $azArgs += "--build-arg"
            $azArgs += $arg
        }

        $savedEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $buildResponse = (& az @azArgs 2>&1 | Out-String).Trim()
            $buildExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $savedEAP
        }

        if ($buildExitCode -ne 0) {
            throw "ACR build failed for $Label (exit code $buildExitCode)"
        }

        $buildObj = $null
        try {
            $buildObj = $buildResponse | ConvertFrom-Json
        } catch {
            throw "Unable to parse ACR build response for $Label. Raw response: $buildResponse"
        }

        $runId = $null
        if ($buildObj.PSObject.Properties.Name -contains "runId") {
            $runId = $buildObj.runId
        }
        if ([string]::IsNullOrEmpty($runId) -and ($buildObj.PSObject.Properties.Name -contains "id")) {
            if ($buildObj.id -match '/runs/([^/\s]+)$') {
                $runId = $Matches[1]
            }
        }
        if ([string]::IsNullOrEmpty($runId)) {
            throw "Could not determine ACR run ID for $Label build. Response: $buildResponse"
        }

        Write-Host "  ACR build queued. Run ID: $runId"

        $finalRun = $null
        $lastStatus = ""
        $pollCount = 0
        $maxPollCount = 180
        while ($true) {
            $pollCount++
            if ($pollCount -gt $maxPollCount) {
                throw "Timed out waiting for ACR build run status for $Label (runId: $runId)."
            }
            $savedEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $runJson = (& az acr task show-run --registry $RegistryName --run-id $runId --only-show-errors --output json 2>&1 | Out-String).Trim()
                $showRunExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $savedEAP
            }

            if ($showRunExitCode -ne 0) {
                if (($pollCount % 3) -eq 0) {
                    Write-Host "  Waiting for run status... (runId: $runId)"
                }
                Start-Sleep -Seconds 5
                continue
            }

            try {
                $runObj = $runJson | ConvertFrom-Json
            } catch {
                if (($pollCount % 3) -eq 0) {
                    Write-Host "  Waiting for run status... (runId: $runId)"
                }
                Start-Sleep -Seconds 5
                continue
            }

            $status = [string]$runObj.status
            if ($status -ne $lastStatus) {
                $timeStamp = Get-Date -Format "HH:mm:ss"
                Write-Host "  [$timeStamp] ACR build status: $status"
                $lastStatus = $status
            }

            if ($status -in @("Succeeded", "Failed", "Canceled", "Error")) {
                $finalRun = $runObj
                break
            }

            Start-Sleep -Seconds 8
        }

        if ($null -ne $finalRun -and -not [string]::IsNullOrEmpty($finalRun.logArtifactLink)) {
            Write-Host ""
            Write-Host "  Fetching ACR build logs for $Label..."
            try {
                $logResponse = Invoke-WebRequest -Uri $finalRun.logArtifactLink -UseBasicParsing -TimeoutSec 180
                $logContent = [string]$logResponse.Content
                if (-not [string]::IsNullOrEmpty($logContent)) {
                    Write-Host "  ----- BEGIN ACR BUILD LOG ($Label) -----"
                    foreach ($line in ($logContent -split "`r?`n")) {
                        Write-Host $line
                    }
                    Write-Host "  ----- END ACR BUILD LOG ($Label) -----"
                }
            } catch {
                Write-Host "  WARNING: Could not fetch ACR log artifact for ${Label}: $($_.Exception.Message)"
            }
            Write-Host ""
        }

        if ($null -eq $finalRun -or $finalRun.status -ne "Succeeded") {
            $finalStatus = if ($null -ne $finalRun) { [string]$finalRun.status } else { "Unknown" }
            throw "ACR build failed for $Label (status: $finalStatus)"
        }
    } finally {
        Pop-Location
    }
}

# Deploy all container apps using az deployment group create directly.
# This bypasses azd entirely, avoiding env file locking and TUI output issues.
# All images must be built BEFORE this runs.
function Invoke-ContainerAppsDeploy {
    param(
        [string]$ResourceGroup
    )

    Write-Host ""
    Write-Host "=========================================="
    Write-Host "DEPLOY PHASE: Deploying all container apps"
    Write-Host "=========================================="

    $infraDir = Resolve-Path (Join-Path $PSScriptRoot "..\infra")
    $templateFile = Join-Path $infraDir "main.bicep"
    $parametersFile = Join-Path $infraDir "main.parameters.json"

    if (-not (Test-Path $templateFile)) {
        throw "Bicep template not found: $templateFile"
    }
    if (-not (Test-Path $parametersFile)) {
        throw "Parameters file not found: $parametersFile"
    }

    Write-Host "  Resource Group:   $ResourceGroup"
    Write-Host "  Template:         $templateFile"
    Write-Host "  Deploy flags:     MCP=true, FastAPI=true, Webapp=true"
    Write-Host ""

    # Create a resolved parameters file that replaces ${...} azd-interpolation
    # tokens with actual values and sets all container deploy flags to true.
    $paramsJson = Get-Content $parametersFile -Raw | ConvertFrom-Json
    $paramsJson.parameters.deployContainerApp.value = $false
    $paramsJson.parameters.deployContainerAppsEnv.value = $true
    $paramsJson.parameters.deployMcpServerContainerApp.value = $true
    $paramsJson.parameters.deployFastApiContainerApp.value = $true
    $paramsJson.parameters.deployWebappContainerApp.value = $true

    $resolvedParamsFile = Join-Path $env:TEMP "azd-deploy-params-resolved.json"
    $paramsJson | ConvertTo-Json -Depth 10 | Set-Content -Path $resolvedParamsFile -Encoding UTF8
    Write-Host "  Resolved parameters written to: $resolvedParamsFile"

    # Run az deployment group create with the Bicep template and resolved parameters.
    # We use --only-show-errors to suppress verbose/info chatter that can buffer
    # indefinitely in PS5.1 under azd hook redirection, making the script look hung.
    # A background job prints a heartbeat so azd's output never goes silent.
    Write-Host "  Starting ARM deployment (this may take several minutes)..."
    Write-Host ""

    # Use a timestamp-based deployment name to avoid conflicts with active deployments
    $deployName = "postprovision-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

    $azArgs = @("deployment", "group", "create",
                "--resource-group", $ResourceGroup,
                "--template-file", $templateFile,
                "--parameters", "@$resolvedParamsFile",
                "--name", $deployName,
                "--no-prompt", "--only-show-errors", "--output", "none")

    # Run az as a background job so the foreground can print heartbeat dots
    $deployJob = Start-Job -ScriptBlock {
        param($azArgs)
        & az @azArgs 2>&1
        $LASTEXITCODE
    } -ArgumentList (,$azArgs)

    # Print a dot every 10s while the deployment runs
    while ($deployJob.State -eq 'Running') {
        Start-Sleep -Seconds 10
        Write-Host -NoNewline "."
    }
    Write-Host ""  # newline after dots

    # Collect output and exit code from the job
    $jobOutput = Receive-Job -Job $deployJob -ErrorAction SilentlyContinue
    Remove-Job -Job $deployJob -Force -ErrorAction SilentlyContinue

    # The last line of output is $LASTEXITCODE from inside the job
    $deployExitCode = 0
    if ($null -ne $jobOutput -and $jobOutput.Count -gt 0) {
        $lastLine = $jobOutput[-1]
        if ($lastLine -match '^\d+$') {
            $deployExitCode = [int]$lastLine
            $jobOutput = $jobOutput[0..($jobOutput.Count - 2)]
        }
    }
    # Print any az output (errors, warnings)
    foreach ($line in $jobOutput) {
        if (-not [string]::IsNullOrEmpty("$line")) {
            Write-Host "  $line"
        }
    }

    # Clean up temp file
    if (Test-Path $resolvedParamsFile) {
        Remove-Item $resolvedParamsFile -Force -ErrorAction SilentlyContinue
    }

    if ($deployExitCode -ne 0) {
        Write-Host ""
        Write-Host "ERROR: Container app deployment failed (exit code $deployExitCode)." -ForegroundColor Red
        throw "Container app deployment failed (exit code $deployExitCode)."
    }

    Write-Host ""
    Write-Host "  Container app deployment completed successfully."
}

# ============================================================
# MAIN EXECUTION
# ============================================================

$acrName = Get-AzdEnvValue "acrName"
$acrLoginServer = Get-AzdEnvValue "acrLoginServer"
$mcpServerImageName = Get-AzdEnvValue "mcpServerImageName"
$mcpServerImageTag = Get-AzdEnvValue "mcpServerImageTag"
$buildMcpServerContainer = Get-AzdEnvValue "buildMcpServerContainer"
$fastApiImageName = Get-AzdEnvValue "fastApiImageName"
$fastApiImageTag = Get-AzdEnvValue "fastApiImageTag"
$buildFastApiContainer = Get-AzdEnvValue "buildFastApiContainer"
$webappImageName = Get-AzdEnvValue "webappImageName"
$webappImageTag = Get-AzdEnvValue "webappImageTag"
$buildWebappContainer = Get-AzdEnvValue "buildWebappContainer"
$resourceGroup = Get-AzdEnvValue "AZURE_RESOURCE_GROUP"

if ([string]::IsNullOrEmpty($acrName)) {
    Write-Host "ACR not deployed, skipping container builds"
    exit 0
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:AZURE_CORE_NO_COLOR = "1"
chcp 65001 | Out-Null

# ---- BUILD PHASE: Build all container images ----
Write-Host "=========================================="
Write-Host "Building container images..."
Write-Host "=========================================="

# Determine build strategy: Docker Desktop (local) vs ACR build (remote)
# Even if Docker is available, ACR with private endpoints may not be reachable
# from the local machine. Test ACR login to verify connectivity.
$useDockerDesktop = $false
if (Test-DockerAvailable) {
    Write-Host "Testing ACR connectivity from local machine..."
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $loginOutput = & az acr login --name $acrName 2>&1
        $loginExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedEAP
    }
    if ($loginExitCode -eq 0) {
        $useDockerDesktop = $true
        Write-Host "Build strategy: Docker Desktop (local build + push to ACR)"
    } else {
        Write-Host "WARNING: Docker Desktop available but cannot reach ACR (private endpoint)." -ForegroundColor Yellow
        Write-Host "  $loginOutput"
        Write-Host "Falling back to ACR remote build." -ForegroundColor Yellow
    }
}
if (-not $useDockerDesktop) {
    Write-Host "Build strategy: ACR remote build (az acr build)"
}

if ($buildMcpServerContainer -ne "false") {
    $mcpServerFullPath = Resolve-Path (Join-Path $scriptDir $McpServerPath)
    Write-Host "Checking if MCP Server container needs building..."
    $mcpBuildCheck = Test-BuildNeeded -FolderPath $mcpServerFullPath -HashEnvVarName "mcpServerFolderHash"
    if ($mcpBuildCheck.Needed) {
        if ($useDockerDesktop) {
            Invoke-DockerBuild -RegistryName $acrName -LoginServer $acrLoginServer -SourcePath $mcpServerFullPath -ImageName $mcpServerImageName -ImageTag $mcpServerImageTag -Label "mcp-server"
        } else {
            Invoke-AcrBuild -RegistryName $acrName -SourcePath $mcpServerFullPath -ImageName $mcpServerImageName -ImageTag $mcpServerImageTag -Label "mcp-server"
        }
        Save-FolderHash -HashEnvVarName "mcpServerFolderHash" -Hash $mcpBuildCheck.Hash
        Write-Host "MCP Server container built: $acrLoginServer/${mcpServerImageName}:${mcpServerImageTag}"
    } else {
        Write-Host "MCP Server container is up-to-date, skipping build."
    }
}

if ($buildFastApiContainer -ne "false") {
    $fastApiFullPath = Resolve-Path (Join-Path $scriptDir $FastApiPath)
    Write-Host "Checking if FastAPI container needs building..."
    $fastApiBuildCheck = Test-BuildNeeded -FolderPath $fastApiFullPath -HashEnvVarName "fastApiFolderHash"
    if ($fastApiBuildCheck.Needed) {
        if ($useDockerDesktop) {
            Invoke-DockerBuild -RegistryName $acrName -LoginServer $acrLoginServer -SourcePath $fastApiFullPath -ImageName $fastApiImageName -ImageTag $fastApiImageTag -Label "fastapi"
        } else {
            Invoke-AcrBuild -RegistryName $acrName -SourcePath $fastApiFullPath -ImageName $fastApiImageName -ImageTag $fastApiImageTag -Label "fastapi"
        }
        Save-FolderHash -HashEnvVarName "fastApiFolderHash" -Hash $fastApiBuildCheck.Hash
        Write-Host "FastAPI container built: $acrLoginServer/${fastApiImageName}:${fastApiImageTag}"
    } else {
        Write-Host "FastAPI container is up-to-date, skipping build."
    }
}

if ($buildWebappContainer -ne "false") {
    $webappFullPath = Resolve-Path (Join-Path $scriptDir $WebappPath)
    # Copy SPA source into webapp build context so Docker can access it
    # (skip if webapp path already IS the SPA source).
    $spaSourcePath = Resolve-Path (Join-Path $scriptDir "../../azure-agent-spa")
    if ($webappFullPath.Path -ne $spaSourcePath.Path) {
        Write-Host "Copying SPA source from $spaSourcePath into $webappFullPath..."
        $spaFiles = @("package.json", "package-lock.json", "public", "src")
        foreach ($item in $spaFiles) {
            $src = Join-Path $spaSourcePath $item
            if (Test-Path $src) {
                $dest = Join-Path $webappFullPath $item
                if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
                Copy-Item -Path $src -Destination $dest -Recurse -Force
            }
        }
    }
    Write-Host "Checking if Webapp container needs building..."
    $webappBuildCheck = Test-BuildNeeded -FolderPath $webappFullPath -HashEnvVarName "webappFolderHash"
    if ($webappBuildCheck.Needed) {
        $buildArgs = @()

        if ($useDockerDesktop) {
            Invoke-DockerBuild -RegistryName $acrName -LoginServer $acrLoginServer -SourcePath $webappFullPath -ImageName $webappImageName -ImageTag $webappImageTag -Label "webapp" -BuildArgs $buildArgs
        } else {
            Invoke-AcrBuild -RegistryName $acrName -SourcePath $webappFullPath -ImageName $webappImageName -ImageTag $webappImageTag -Label "webapp" -BuildArgs $buildArgs
        }
        Save-FolderHash -HashEnvVarName "webappFolderHash" -Hash $webappBuildCheck.Hash
        Write-Host "Webapp container built: $acrLoginServer/${webappImageName}:${webappImageTag}"
    } else {
        Write-Host "Webapp container is up-to-date, skipping build."
    }
}

# ---- DEPLOY PHASE: Single ARM deployment to deploy all container apps ----
Invoke-ContainerAppsDeploy -ResourceGroup $resourceGroup

Write-Host "=========================================="
Write-Host "Post-provision completed successfully."
Write-Host "=========================================="

# ---- Display webapp URL if available ----
$webappFqdn = Get-AzdEnvValue "webappContainerAppFqdn"
if (-not [string]::IsNullOrEmpty($webappFqdn)) {
    Write-Host ""
    Write-Host "=========================================="
    Write-Host "  Webapp URL: https://$webappFqdn" -ForegroundColor Green
    Write-Host "=========================================="
    Write-Host ""

    # ---- Update Entra ID app registration with ACA redirect URI ----
    # Extract the client ID from the SPA's authConfig.js so it stays in sync.
    $authConfigFile = Resolve-Path (Join-Path $scriptDir "../../azure-agent-spa/src/authConfig.js") -ErrorAction SilentlyContinue
    $entraClientId = ""
    if ($authConfigFile -and (Test-Path $authConfigFile)) {
        $match = Select-String -Path $authConfigFile -Pattern 'clientId:\s*"([^"]+)"' | Select-Object -First 1
        if ($match) {
            $entraClientId = $match.Matches[0].Groups[1].Value.Trim()
        }
    }

    if (-not [string]::IsNullOrEmpty($entraClientId)) {
        $redirectUri = "https://$webappFqdn"
        Write-Host "Updating Entra ID app registration ($entraClientId) with redirect URI: $redirectUri"

        # Read current SPA redirect URIs to avoid duplicates
        $savedEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $currentUris = (az ad app show --id $entraClientId --query "spa.redirectUris" -o tsv 2>&1) -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^WARNING' }
        } finally {
            $ErrorActionPreference = $savedEAP
        }

        if ($currentUris -contains $redirectUri) {
            Write-Host "  Redirect URI already registered, skipping."
        } else {
            # Append the new URI to existing ones
            $allUris = @($currentUris | Where-Object { $_ }) + @($redirectUri)
            $uriArgs = $allUris | ForEach-Object { $_ }

            Invoke-NativeCommand {
                az ad app update --id $entraClientId --spa-redirect-uris @uriArgs 2>&1 | Out-Null
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Redirect URI added successfully." -ForegroundColor Green
            } else {
                Write-Host "  WARNING: Failed to update redirect URI. Add it manually in the Azure portal." -ForegroundColor Yellow
                Write-Host "  App Registration > Authentication > SPA redirect URIs > Add: $redirectUri"
            }
        }
    } else {
        Write-Host "WARNING: Could not extract Entra client ID from authConfig.js." -ForegroundColor Yellow
        Write-Host "  Add the redirect URI manually: https://$webappFqdn"
    }
}