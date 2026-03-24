"""
Azure Resource Management Tools

Provides MCP tools for managing Azure resources: get details, list by group,
start/stop/restart VMs, and manage resource tags.
"""

import logging
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)

API_VERSION_MAP = {
    "microsoft.compute/virtualmachines": "2024-03-01",
    "microsoft.web/sites": "2023-12-01",
    "microsoft.storage/storageaccounts": "2023-05-01",
    "microsoft.sql/servers/databases": "2023-08-01-preview",
    "microsoft.dbforpostgresql/flexibleservers": "2023-12-01-preview",
    "microsoft.containerregistry/registries": "2023-07-01",
    "microsoft.keyvault/vaults": "2023-07-01",
    "microsoft.network/virtualnetworks": "2023-11-01",
    "microsoft.network/networksecuritygroups": "2023-11-01",
    "microsoft.network/publicipaddresses": "2023-11-01",
    "microsoft.network/loadbalancers": "2023-11-01",
    "microsoft.app/containerapps": "2024-03-01",
    "microsoft.cognitiveservices/accounts": "2024-04-01-preview",
}

DEFAULT_API_VERSION = "2023-07-01"


def _get_api_version(resource_type: str) -> str:
    return API_VERSION_MAP.get(resource_type.lower(), DEFAULT_API_VERSION)


async def get_resource_details(
    resource_id: Annotated[str, "Full Azure resource ID"],
    api_version: Annotated[str | None, "ARM API version (auto-detected if omitted)"] = None,
) -> dict:
    """
    Get detailed information about a specific Azure resource by its resource ID.
    Returns the full ARM resource representation including properties, SKU, tags, and status.
    """
    token = get_current_token()

    if not api_version:
        parts = resource_id.lower().split("/providers/")
        if len(parts) >= 2:
            provider_path = parts[-1]
            segments = provider_path.split("/")
            if len(segments) >= 2:
                resource_type = f"{segments[0]}/{segments[1]}"
                api_version = _get_api_version(resource_type)
        if not api_version:
            api_version = DEFAULT_API_VERSION

    url = f"https://management.azure.com{resource_id}?api-version={api_version}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def list_resource_groups(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    List all resource groups in the subscription with their locations and tags.
    """
    token = get_current_token()

    url = f"https://management.azure.com/subscriptions/{subscription_id}/resourcegroups?api-version=2024-03-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    groups = []
    for rg in data.get("value", []):
        groups.append({
            "name": rg.get("name", ""),
            "location": rg.get("location", ""),
            "provisioning_state": rg.get("properties", {}).get("provisioningState", ""),
            "tags": rg.get("tags", {}),
        })

    return {"count": len(groups), "resource_groups": groups}


async def get_subscription_info(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    Get information about the Azure subscription including display name, state, and policies.
    """
    token = get_current_token()

    url = f"https://management.azure.com/subscriptions/{subscription_id}?api-version=2022-12-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return {
        "subscription_id": data.get("subscriptionId", ""),
        "display_name": data.get("displayName", ""),
        "state": data.get("state", ""),
        "tenant_id": data.get("tenantId", ""),
        "subscription_policies": data.get("subscriptionPolicies", {}),
    }


async def vm_power_operation(
    resource_id: Annotated[str, "Full Azure resource ID of the VM"],
    operation: Annotated[str, "Power operation: start, deallocate, restart, powerOff"],
) -> dict:
    """
    Perform a power operation on a Virtual Machine.
    Supported operations: start, deallocate (stop + release compute), restart, powerOff.
    """
    valid_ops = {"start", "deallocate", "restart", "powerOff"}
    if operation not in valid_ops:
        return {"error": f"Invalid operation '{operation}'. Must be one of: {', '.join(sorted(valid_ops))}"}

    token = get_current_token()

    url = f"https://management.azure.com{resource_id}/{operation}?api-version=2024-03-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers)
        if resp.status_code == 202:
            return {
                "status": "accepted",
                "operation": operation,
                "resource_id": resource_id,
                "message": f"VM {operation} operation initiated. This is an async operation.",
                "async_operation_uri": resp.headers.get("Azure-AsyncOperation", ""),
            }
        resp.raise_for_status()
        return {"status": "completed", "operation": operation, "resource_id": resource_id}


async def update_resource_tags(
    resource_id: Annotated[str, "Full Azure resource ID"],
    tags: Annotated[str, "JSON string of tags to set, e.g. '{\"env\":\"dev\",\"owner\":\"team-a\"}'"],
    operation: Annotated[str, "Tag operation: merge (add/update tags) or replace (replace all tags)"] = "merge",
) -> dict:
    """
    Update tags on an Azure resource. Use 'merge' to add/update specific tags
    without removing existing ones, or 'replace' to set tags to exactly the provided set.
    """
    import json

    try:
        tag_dict = json.loads(tags)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON for tags parameter"}

    token = get_current_token()

    url = f"https://management.azure.com{resource_id}/providers/Microsoft.Resources/tags/default?api-version=2024-03-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    op = "Merge" if operation == "merge" else "Replace"
    body = {
        "operation": op,
        "properties": {"tags": tag_dict},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    return {
        "status": "success",
        "operation": op,
        "resource_id": resource_id,
        "tags": data.get("properties", {}).get("tags", {}),
    }


async def list_subscriptions() -> dict:
    """
    List all Azure subscriptions accessible with the current token.
    Returns subscription IDs, display names, and states.
    """
    token = get_current_token()

    url = "https://management.azure.com/subscriptions?api-version=2022-12-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    subs = []
    for sub in data.get("value", []):
        subs.append({
            "subscription_id": sub.get("subscriptionId", ""),
            "display_name": sub.get("displayName", ""),
            "state": sub.get("state", ""),
            "tenant_id": sub.get("tenantId", ""),
        })

    return {"count": len(subs), "subscriptions": subs}
