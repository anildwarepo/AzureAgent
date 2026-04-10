1. GUARD: Recursion check
   └─ If AZD_POSTPROVISION_PHASE=1 → exit 0 (skip nested hook)

2. FUNCTION DEFINITIONS (lines 27-370)
   ├─ Get-AzdEnvValue          - Read azd env var (via cmd /c)
   ├─ Set-AzdEnvValue          - Write azd env var (ErrorActionPref guard)
   ├─ Invoke-NativeCommand     - Run native cmd without PS5.1 stderr crash
   ├─ Get-FolderHash           - MD5 hash of folder contents
   ├─ Test-BuildNeeded         - Compare current vs stored hash
   ├─ Save-FolderHash          - Persist hash to azd env
   ├─ Invoke-AcrBuild          - Build container in ACR, fetch logs
   ├─ Wait-PostgresqlReady     - Poll server state until "Ready"
   ├─ Ensure-PostgresqlAllowAllIps - Open firewall (unused currently)
   ├─ Invoke-WithRetry         - Generic retry wrapper
   ├─ Initialize-PostgresqlAgeAndData
   │   ├─ Set AGE extension params
   │   ├─ Restart PostgreSQL
   │   ├─ Reset admin password (az postgres flexible-server update)
   │   ├─ Run test_pg_connection.py (creates AGE extension)
   │   ├─ Load customer_graph (load_customer_graph.py)
   │   ├─ Load meetings_graph (load_meetings_graph.py)
   │   ├─ Build customer_graph indexes
   │   └─ Build meetings_graph indexes
   └─ Invoke-ProvisionPhase    - Set deploy flags → azd provision → stream output

3. MAIN EXECUTION (lines 372-460)
   │
   ├─ Read all azd env values (ACR, images, PG, flags)
   │
   ├─ Early exit if no ACR
   │
   ├─ Set encoding (UTF-8, chcp 65001)
   │
   ├─ PHASE 0: PostgreSQL AGE Init (flag-gated)
   │   ├─ if initializePostgresqlAge ≠ "false":
   │   │   ├─ Wait-PostgresqlReady
   │   │   ├─ Initialize-PostgresqlAgeAndData
   │   │   └─ Set initializePostgresqlAge = "false"
   │   └─ else: skip with message
   │
   ├─ PHASE 1: MCP Server
   │   ├─ Check hash → Invoke-AcrBuild if changed
   │   └─ Invoke-ProvisionPhase (MCP=true, FastAPI=false, Webapp=false)
   │
   ├─ PHASE 2: FastAPI Backend
   │   ├─ Check hash → Invoke-AcrBuild if changed
   │   └─ Invoke-ProvisionPhase (MCP=true, FastAPI=true, Webapp=false)
   │
   └─ PHASE 3: Webapp
       ├─ Check hash → Invoke-AcrBuild if changed
       │   └─ Passes VITE_API_BASE_URL build arg with FastAPI FQDN
       └─ Invoke-ProvisionPhase (MCP=true, FastAPI=true, Webapp=true)