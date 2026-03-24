"""
Azure Resource Graph Tools

Provides MCP tools for querying Azure Resource Graph to discover and
analyze resources across subscriptions.
"""

import json
import logging
from typing import Annotated

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

from azure_auth import get_current_credential

logger = logging.getLogger(__name__)


async def list_resources(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_type: Annotated[str | None, "Filter by resource type (e.g. microsoft.compute/virtualmachines)"] = None,
    resource_group: Annotated[str | None, "Filter by resource group name"] = None,
    top: Annotated[int, "Maximum number of results to return"] = 100,
) -> dict:
    """
    List Azure resources using Resource Graph. Supports filtering by type and resource group.
    Returns resource name, type, location, resource group, SKU, and tags.
    """
    credential = get_current_credential()
    client = ResourceGraphClient(credential)

    where_clauses = []
    if resource_type:
        where_clauses.append(f"| where type =~ '{resource_type}'")
    if resource_group:
        where_clauses.append(f"| where resourceGroup =~ '{resource_group}'")

    filters = "\n".join(where_clauses)
    query = f"""
resources
{filters}
| project id, name, type, location, resourceGroup, subscriptionId,
          sku=tostring(sku.name), tags, kind,
          provisioningState=tostring(properties.provisioningState)
| order by type asc, name asc
| limit {min(top, 1000)}
"""
    request = QueryRequest(subscriptions=[subscription_id], query=query)
    response = client.resources(request)

    return {
        "count": len(response.data),
        "total_records": response.total_records,
        "resources": response.data,
    }


async def query_resource_graph(
    subscription_id: Annotated[str, "Azure subscription ID"],
    query: Annotated[str, "Kusto Query Language (KQL) query to run against Azure Resource Graph"],
) -> dict:
    """
    Execute a custom KQL query against Azure Resource Graph.
    Use this for advanced resource discovery and cross-subscription analysis.

    Example queries:
    - 'resources | summarize count() by type | order by count_ desc'
    - 'resources | where type == "microsoft.compute/virtualmachines" | project name, properties.hardwareProfile.vmSize'
    """
    credential = get_current_credential()
    client = ResourceGraphClient(credential)

    request = QueryRequest(subscriptions=[subscription_id], query=query)
    response = client.resources(request)

    return {
        "count": len(response.data),
        "total_records": response.total_records,
        "result_truncated": str(response.result_truncated),
        "data": response.data,
    }


async def get_resource_summary(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    Get a summary of all resources in the subscription grouped by type, location, and resource group.
    Provides a high-level overview of the Azure environment.
    """
    credential = get_current_credential()
    client = ResourceGraphClient(credential)

    # By type
    type_query = "resources | summarize count() by type | order by count_ desc"
    type_req = QueryRequest(subscriptions=[subscription_id], query=type_query)
    type_resp = client.resources(type_req)

    # By location
    loc_query = "resources | summarize count() by location | order by count_ desc"
    loc_req = QueryRequest(subscriptions=[subscription_id], query=loc_query)
    loc_resp = client.resources(loc_req)

    # By resource group
    rg_query = "resources | summarize count() by resourceGroup | order by count_ desc"
    rg_req = QueryRequest(subscriptions=[subscription_id], query=rg_query)
    rg_resp = client.resources(rg_req)

    # Total count
    total_query = "resources | summarize total=count()"
    total_req = QueryRequest(subscriptions=[subscription_id], query=total_query)
    total_resp = client.resources(total_req)

    total = total_resp.data[0].get("total", 0) if total_resp.data else 0

    return {
        "total_resources": total,
        "by_type": type_resp.data,
        "by_location": loc_resp.data,
        "by_resource_group": rg_resp.data,
    }


async def find_orphaned_resources(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    Find orphaned resources (unattached disks, public IPs, NICs, NSGs, empty App Service Plans, etc.)
    using Azure Resource Graph property checks.
    """
    credential = get_current_credential()
    client = ResourceGraphClient(credential)

    # Query 1: VMs, Disks, Public IPs, NICs
    query1 = """
resources
| where type == "microsoft.compute/virtualmachines"
    and tostring(properties.extended.instanceView.powerState.code) == "PowerState/deallocated"
| extend signal = "VM deallocated", classification = "UNUSED"
| project id, name, type, resourceGroup, subscriptionId, signal, classification
| union (
    resources
    | where type == "microsoft.compute/disks" and isempty(managedBy)
        and tostring(properties.diskState) == "Unattached"
    | extend signal = "Disk unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
| union (
    resources
    | where type == "microsoft.network/publicipaddresses"
        and isempty(properties.ipConfiguration)
    | extend signal = "Public IP unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
| union (
    resources
    | where type == "microsoft.network/networkinterfaces"
        and isempty(properties.virtualMachine)
    | extend signal = "NIC unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
"""

    # Query 2: NSGs, NAT Gateways, App Service Plans, Load Balancers
    query2 = """
resources
| where type == "microsoft.network/networksecuritygroups"
    and array_length(properties.subnets) == 0
    and array_length(properties.networkInterfaces) == 0
| extend signal = "NSG unassociated", classification = "ORPHANED"
| project id, name, type, resourceGroup, subscriptionId, signal, classification
| union (
    resources
    | where type == "microsoft.network/natgateways"
        and array_length(properties.subnets) == 0
    | extend signal = "NAT Gateway unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
| union (
    resources
    | where type == "microsoft.web/serverfarms"
        and toint(properties.numberOfSites) == 0
    | extend signal = "App Service Plan empty", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
| union (
    resources
    | where type == "microsoft.network/loadbalancers"
        and array_length(properties.backendAddressPools) == 0
    | extend signal = "Load Balancer empty", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification
)
"""

    results = []
    for idx, q in enumerate([query1, query2], 1):
        request = QueryRequest(subscriptions=[subscription_id], query=q)
        response = client.resources(request)
        logger.info("Orphan query %d returned %d results", idx, len(response.data))
        results.extend(response.data)

    return {
        "count": len(results),
        "orphaned_resources": results,
    }
