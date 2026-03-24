"""
Azure Operations MCP Server

A Model Context Protocol (MCP) server that provides tools for monitoring,
managing, querying, and analyzing Azure resources. Integrates with:
- Azure Resource Graph for resource discovery
- Azure Monitor for metrics, health, and activity logs
- Azure Cost Management for cost analysis
- Report generation for rich HTML visualizations

Runs on streamable-http transport. Accepts bearer tokens from the UI/API
layer for authenticating Azure API calls.

Usage:
    python azure_ops_mcp_server.py
    # Or with custom port:
    python azure_ops_mcp_server.py --port 3001
"""

import sys
import asyncio
import argparse
import logging

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastmcp import FastMCP

from azure_auth import AzureTokenMiddleware

# Import tool modules
from resource_graph_tools import (
    list_resources,
    query_resource_graph,
    get_resource_summary,
    find_orphaned_resources,
)
from monitoring_tools import (
    get_resource_metrics,
    check_resource_health,
    get_activity_log,
    list_metric_alerts,
    check_idle_resources,
)
from resource_tools import (
    get_resource_details,
    list_resource_groups,
    get_subscription_info,
    vm_power_operation,
    update_resource_tags,
    list_subscriptions,
)
from cost_tools import (
    get_cost_summary,
    get_cost_by_resource_group,
    get_cost_by_service,
    get_cost_by_resource,
    list_budgets,
    get_advisor_recommendations,
)
from report_tools import (
    generate_resource_report,
    generate_cost_report,
    generate_dashboard_report,
)
from report_store import get_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Suppress verbose Azure SDK HTTP logging
for _name in ("azure.core.pipeline.policies.http_logging_policy", "azure.identity", "azure.mgmt"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Create MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "Azure Operations MCP Server",
    instructions="""You are an Azure Operations assistant. Use these tools to help users
monitor, manage, query, and analyze their Azure resources. The user's Azure
bearer token is automatically injected from HTTP headers — you do not need to
provide a token parameter. Start with list_subscriptions or get_subscription_info
to establish context, then use resource graph tools for discovery, monitoring tools
for metrics and health, cost tools for financial analysis, and report tools for
visualizations.""",
)

# ---------------------------------------------------------------------------
# Resource Graph tools
# ---------------------------------------------------------------------------
mcp.tool(list_resources)
mcp.tool(query_resource_graph)
mcp.tool(get_resource_summary)
mcp.tool(find_orphaned_resources)

# ---------------------------------------------------------------------------
# Monitoring tools
# ---------------------------------------------------------------------------
mcp.tool(get_resource_metrics)
mcp.tool(check_resource_health)
mcp.tool(get_activity_log)
mcp.tool(list_metric_alerts)
mcp.tool(check_idle_resources)

# ---------------------------------------------------------------------------
# Resource Management tools
# ---------------------------------------------------------------------------
mcp.tool(get_resource_details)
mcp.tool(list_resource_groups)
mcp.tool(get_subscription_info)
mcp.tool(vm_power_operation)
mcp.tool(update_resource_tags)
mcp.tool(list_subscriptions)

# ---------------------------------------------------------------------------
# Cost Management tools
# ---------------------------------------------------------------------------
mcp.tool(get_cost_summary)
mcp.tool(get_cost_by_resource_group)
mcp.tool(get_cost_by_service)
mcp.tool(get_cost_by_resource)
mcp.tool(list_budgets)
mcp.tool(get_advisor_recommendations)

# ---------------------------------------------------------------------------
# Report Generation tools
# ---------------------------------------------------------------------------
mcp.tool(generate_resource_report)
mcp.tool(generate_cost_report)
mcp.tool(generate_dashboard_report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Azure Operations MCP Server")
    parser.add_argument("--port", type=int, default=3001, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--transport", default="streamable-http",
                        choices=["streamable-http", "sse", "stdio"],
                        help="MCP transport type")
    args = parser.parse_args()

    logger.info("Starting Azure Operations MCP Server on %s:%d (transport=%s)",
                args.host, args.port, args.transport)

    # Wrap the FastMCP ASGI app with auth middleware so that the
    # Authorization header is extracted into a ContextVar before
    # any tool function runs.
    raw_app = mcp.http_app()
    auth_app = AzureTokenMiddleware(raw_app)

    # Compose with a report-serving endpoint so clients can fetch
    # generated HTML reports via GET /reports/{report_id}.
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import HTMLResponse, Response
    from starlette.middleware.cors import CORSMiddleware as StarletteCORS

    async def serve_report(request):
        report_id = request.path_params["report_id"]
        html = get_report(report_id)
        if html is None:
            return Response("Report not found", status_code=404)
        return HTMLResponse(html)

    app = Starlette(
        routes=[
            Route("/reports/{report_id}", serve_report, methods=["GET"]),
            Mount("/", app=auth_app),
        ],
        lifespan=raw_app.lifespan,
    )
    app.add_middleware(
        StarletteCORS,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
