# Azure Deploy Agent Instructions

Guidelines for the `azd_deploy/` infrastructure that publishes the SPA webapp,
FastAPI, and MCP server as Azure Container Apps with managed identities.

---

## Architecture — What Gets Deployed

Only the resources **actually used by code** are provisioned:

| Resource | Purpose | Required |
|----------|---------|----------|
| AI Services account (kind: AIServices) | Azure OpenAI endpoint | Yes |
| GPT model deployment (on AI Services) | Chat completions for the agent | Yes |
| VNet + subnets | Network isolation for ACR + Container Apps | Yes |
| Azure Container Registry (Premium, private endpoint) | Container image store | Yes |
| Container Apps Environment (VNet-integrated) | Runtime for all 3 apps | Yes |
| MCP Server Container App (port 3001) | MCP tools server | Yes |
| FastAPI Container App (port 8080) | Agent API backend | Yes |
| Webapp Container App (port 80, nginx) | SPA frontend + reverse proxy | Yes |
| Log Analytics Workspace | Container Apps logs | Yes (auto) |

### Resources intentionally **excluded** (not used by code)

- **PostgreSQL Flexible Server / Apache AGE** — no code connects to it
- **AI Foundry Project** (`CognitiveServices/accounts/projects`) — not referenced
- **Azure Cache for Redis** — not needed
- **Azure Search** — no code uses it
- **Application Insights** — no telemetry configured

Do **not** re-add these unless the application code actually imports and uses them.

---

## Key Configuration Rules

### Ports
- MCP Server Dockerfile exposes **3001** → `targetPort: 3001` in Bicep
- FastAPI Dockerfile exposes **8080** → `targetPort: 8080`
- Webapp nginx listens on **80** → `targetPort: 80`

### Domain / FQDN construction
The Container Apps Environment `defaultDomain` output already includes or
excludes `.internal.` based on the `internalOnly` setting. Never hardcode
`.internal.` — always use:
```
${appName}.${containerAppsEnv.outputs.defaultDomain}
```

### Managed Identity & RBAC
Each container app gets a **user-assigned managed identity** (created by the
`container-app.bicep` module). The `AZURE_CLIENT_ID` env var is set to that
identity's client ID so `DefaultAzureCredential` in Python picks it up.

Role assignments granted:
- **AcrPull** on the Container Registry (all 3 apps)
- **Cognitive Services OpenAI User** on the AI Services account (MCP Server + FastAPI)

If additional Azure permissions are needed (e.g., Reader on subscriptions for
Resource Graph queries), add scoped role assignments in `main.bicep`.

### Dockerfiles

- **FastAPI** (`af_fastapi/Dockerfile`): CMD must be
  `uvicorn azure_ops_api:app` — the app object lives in `azure_ops_api.py`,
  **not** `af_fastapi.py` (which doesn't exist in this repo).
- **MCP Server** (`mcp_server/Dockerfile`): Uses `uv pip install --system
  --prerelease=allow -r pyproject.toml` to install deps, then copies `*.py`.
- **Webapp** (`webapp/Dockerfile`): Multi-stage CRA build → nginx. The
  postprovision hook copies SPA source from `azure-agent-spa/` into `webapp/`
  before building.

### Entra ID / Authentication
The SPA uses MSAL to acquire a **user-delegated** Azure Management token
(`https://management.azure.com/user_impersonation`). This token is forwarded
through FastAPI → MCP server → Azure SDK calls (Resource Graph, Monitor, etc.).

- The Entra app registration `clientId` and `tenantId` are in
  `azure-agent-spa/src/authConfig.js`.
- The postprovision hook **automatically adds** the webapp's ACA FQDN as a
  SPA redirect URI on the app registration (via `az ad app update`).
- The deploying user must have permission to update the app registration
  (Owner or Application Administrator role on the app).
- Managed identity (`AZURE_CLIENT_ID`) is only used for Azure OpenAI calls;
  all other Azure API calls use the user's delegated token.

### `azure.yaml` has no `services:` section
The postprovision hook handles container builds and deployment via
`az deployment group create`. `azd deploy` alone will not work —
use `azd up` for the full workflow.

---

## Summary Checklist (all applied)

| # | Fix | Status |
|---|-----|--------|
| 1 | MCP server targetPort → 3001 | Done |
| 2 | Created `webapp/` with Dockerfile + nginx | Done |
| 3 | Removed hardcoded `.internal.` from FQDNs | Done |
| 4 | Added Cognitive Services OpenAI User role for MCP Server | Done |
| 5 | Removed Redis (not needed) | Done |
| 6 | Removed PostgreSQL (not used by code) | Done |
| 7 | Removed AI Project (not used by code) | Done |
| 8 | Removed Azure Search env vars (not used) | Done |
| 9 | Removed App Insights env vars (not used) | Done |
| 10 | Fixed FastAPI CMD `azure_ops_api:app` | Done |
| 11 | Fixed MCP Server Dockerfile install | Done |
| 12 | Deleted stale `main.json` ARM template | Done |
