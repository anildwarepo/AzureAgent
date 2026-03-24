# run.ps1 — Launch the Azure Operations Agent stack locally
#
# Components:
#   1. MCP Server       (port 3001)  — Azure Operations MCP tools
#   2. FastAPI Backend   (port 8080)  — Agent + streaming API
#   3. React SPA         (port 3000)  — Chat + Dashboard UI

$ErrorActionPreference = "Stop"

# Always run from this script's directory
Set-Location $PSScriptRoot

function Stop-ProcessOnPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique

    if (-not $connections) {
        return
    }

    Write-Host "Stopping existing $ServiceName process(es) on :$Port ..."
    foreach ($procId in $connections) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "  Stopped PID $procId"
        }
        catch {
            Write-Warning "  Could not stop PID $procId on port $Port. $_"
        }
    }

    Start-Sleep -Seconds 1
}

# ── 1. Azure Operations MCP Server (port 3001) ──────────────────────────────
Stop-ProcessOnPort -Port 3001 -ServiceName "MCP Server"
Write-Host "`n[1/3] Starting Azure Operations MCP Server on :3001 ..."
Start-Process powershell -WorkingDirectory (Join-Path $PSScriptRoot 'mcp_server') -ArgumentList @(
    '-NoExit',
    '-Command',
    'uv run python azure_ops_mcp_server.py --port 3001'
)

# ── 2. FastAPI Backend (port 8080) ───────────────────────────────────────────
Stop-ProcessOnPort -Port 8080 -ServiceName "FastAPI"
Write-Host "[2/3] Starting FastAPI Backend on :8080 ..."

# Copy the Azure Ops env file so dotenv picks it up
$envSrc = Join-Path (Join-Path $PSScriptRoot 'af_fastapi') '.env.azure_ops'
$envDst = Join-Path (Join-Path $PSScriptRoot 'af_fastapi') '.env'
if (Test-Path $envSrc) { Copy-Item $envSrc $envDst -Force }

Start-Process powershell -WorkingDirectory (Join-Path $PSScriptRoot 'af_fastapi') -ArgumentList @(
    '-NoExit',
    '-Command',
    'uv run uvicorn azure_ops_api:app --port 8080'
)

# ── 3. React SPA (port 3000) ────────────────────────────────────────────────
Stop-ProcessOnPort -Port 3000 -ServiceName "React SPA"
Write-Host "[3/3] Starting React SPA on :3000 ..."
Start-Process powershell -WorkingDirectory (Join-Path $PSScriptRoot 'azure-agent-spa') -ArgumentList @(
    '-NoExit',
    '-Command',
    'npm install; npm start'
)

Write-Host ""
Write-Host "All services launched in separate windows:"
Write-Host "  MCP Server:  http://localhost:3001/mcp"
Write-Host "  Backend API: http://localhost:8080/health"
Write-Host "  SPA:         http://localhost:3000"
Write-Host ""
