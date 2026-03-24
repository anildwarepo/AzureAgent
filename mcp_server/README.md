# Azure Operations MCP Server

MCP (Model Context Protocol) server that provides tools for monitoring, managing, querying, and analyzing Azure resources. This server is the integration layer between the Agent and Azure APIs.

## Architecture

```
UI (SPA) → API Layer → Agent → MCP Server → Azure APIs
                                    ↓
                              ┌─────────────┐
                              │ Tool Groups  │
                              ├─────────────┤
                              │ Resource     │  Azure Resource Graph
                              │ Graph Tools  │  - List resources
                              │              │  - Custom KQL queries
                              │              │  - Resource summary
                              │              │  - Find orphans
                              ├─────────────┤
                              │ Monitoring   │  Azure Monitor
                              │ Tools        │  - Resource metrics
                              │              │  - Resource health
                              │              │  - Activity logs
                              │              │  - Metric alerts
                              │              │  - Idle detection
                              ├─────────────┤
                              │ Resource     │  Azure Resource Manager
                              │ Mgmt Tools   │  - Resource details
                              │              │  - Resource groups
                              │              │  - Subscription info
                              │              │  - VM operations
                              │              │  - Tag management
                              ├─────────────┤
                              │ Cost         │  Cost Management API
                              │ Tools        │  - Cost summary
                              │              │  - Cost by RG/service
                              │              │  - Top resources
                              │              │  - Budgets
                              │              │  - Advisor recs
                              ├─────────────┤
                              │ Report       │  HTML Generation
                              │ Tools        │  - Resource report
                              │              │  - Cost report
                              │              │  - Dashboard report
                              └─────────────┘
```

## Tools

### Resource Graph Tools (`resource_graph_tools.py`)
| Tool | Description |
|------|-------------|
| `list_resources` | List Azure resources with filtering by type and resource group |
| `query_resource_graph` | Execute custom KQL queries against Resource Graph |
| `get_resource_summary` | Get resource counts by type, location, and resource group |
| `find_orphaned_resources` | Find orphaned/deallocated resources (disks, IPs, NICs, etc.) |

### Monitoring Tools (`monitoring_tools.py`)
| Tool | Description |
|------|-------------|
| `get_resource_metrics` | Query Azure Monitor metrics for a resource |
| `check_resource_health` | Check resource availability status |
| `get_activity_log` | Query management operations activity log |
| `list_metric_alerts` | List configured metric alert rules |
| `check_idle_resources` | Check if resources are idle based on metrics |

### Resource Management Tools (`resource_tools.py`)
| Tool | Description |
|------|-------------|
| `get_resource_details` | Get full ARM resource details |
| `list_resource_groups` | List all resource groups |
| `get_subscription_info` | Get subscription details |
| `vm_power_operation` | Start/stop/restart/deallocate a VM |
| `update_resource_tags` | Add, update, or replace resource tags |
| `list_subscriptions` | List accessible subscriptions |

### Cost Management Tools (`cost_tools.py`)
| Tool | Description |
|------|-------------|
| `get_cost_summary` | Get total cost over a time period |
| `get_cost_by_resource_group` | Cost breakdown by resource group |
| `get_cost_by_service` | Cost breakdown by Azure service |
| `get_cost_by_resource` | Top most expensive resources |
| `list_budgets` | List configured budgets with spend tracking |
| `get_advisor_recommendations` | Azure Advisor recommendations |

### Report Generation Tools (`report_tools.py`)
| Tool | Description |
|------|-------------|
| `generate_resource_report` | Interactive HTML report for resource findings |
| `generate_cost_report` | Interactive HTML cost visualization |
| `generate_dashboard_report` | Comprehensive dashboard combining all data |

## Authentication

The MCP server uses a token-passthrough pattern:
1. User authenticates via Entra ID in the SPA
2. SPA acquires tokens with Azure management scope (`https://management.azure.com/.default`)
3. Token is passed through the API layer to MCP tool calls
4. Each tool creates a `BearerTokenCredential` from the token for Azure SDK calls

## Running

### Local Development
```bash
# Install dependencies
uv pip install -e .

# Run the server
python azure_ops_mcp_server.py --port 3001

# Or with specific transport
python azure_ops_mcp_server.py --transport sse --port 3001
```

### Docker
```bash
docker build -t azure-ops-mcp-server .
docker run -p 3001:3001 azure-ops-mcp-server
```

## MCP Client Configuration

Connect to this server from an MCP client:

```json
{
  "mcpServers": {
    "azure-ops": {
      "url": "http://localhost:3001/mcp"
    }
  }
}
```
