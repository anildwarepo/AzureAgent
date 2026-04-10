"""
Azure Quota Management Tools

Provides MCP tools for viewing current quota limits, submitting quota increase
requests, and checking quota request status using the Azure Quota REST API
(Microsoft.Quota resource provider).

Scope format:
  subscriptions/{subId}/providers/{provider}/locations/{location}
Examples:
  subscriptions/00000000-.../providers/Microsoft.Compute/locations/eastus
  subscriptions/00000000-.../providers/Microsoft.Network/locations/eastus
"""

import logging
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)

QUOTA_API_VERSION = "2025-09-01"
ARM_BASE = "https://management.azure.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_scope(subscription_id: str, provider: str, location: str) -> str:
    """Build the Azure Resource Manager scope path for Quota APIs."""
    return f"subscriptions/{subscription_id}/providers/{provider}/locations/{location}"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# List quota limits
# ---------------------------------------------------------------------------

async def list_quota_limits(
    subscription_id: Annotated[str, "Azure subscription ID"],
    provider: Annotated[str, "Resource provider, e.g. Microsoft.Compute, Microsoft.Network, Microsoft.MachineLearningServices"],
    location: Annotated[str, "Azure region, e.g. eastus, westus2"],
) -> dict:
    """
    List all current quota limits for a resource provider in a given location.
    Returns each resource's name, current limit, whether quota increase is applicable, and unit.
    """
    token = get_current_token()
    scope = _build_scope(subscription_id, provider, location)
    url = f"{ARM_BASE}/{scope}/providers/Microsoft.Quota/quotas?api-version={QUOTA_API_VERSION}"

    all_items: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        while url:
            resp = await client.get(url, headers=_headers(token))
            resp.raise_for_status()
            data = resp.json()
            all_items.extend(data.get("value", []))
            url = data.get("nextLink")

    # Flatten for readability
    quotas = []
    for item in all_items:
        props = item.get("properties", {})
        name_obj = props.get("name", {})
        limit_obj = props.get("limit", {})
        quotas.append({
            "resource_name": name_obj.get("value", item.get("name", "")),
            "display_name": name_obj.get("localizedValue", ""),
            "current_limit": limit_obj.get("value"),
            "limit_type": limit_obj.get("limitType"),
            "is_quota_applicable": props.get("isQuotaApplicable"),
            "resource_type": props.get("resourceType"),
            "unit": props.get("unit", "Count"),
        })

    return {
        "provider": provider,
        "location": location,
        "subscription_id": subscription_id,
        "count": len(quotas),
        "quotas": quotas,
    }


# ---------------------------------------------------------------------------
# Get a single quota limit
# ---------------------------------------------------------------------------

async def get_quota_limit(
    subscription_id: Annotated[str, "Azure subscription ID"],
    provider: Annotated[str, "Resource provider, e.g. Microsoft.Compute"],
    location: Annotated[str, "Azure region, e.g. eastus"],
    resource_name: Annotated[str, "Quota resource name, e.g. standardFSv2Family, StandardSkuPublicIpAddresses"],
) -> dict:
    """
    Get the current quota limit for a specific resource.
    Use this to check remaining quota before submitting an increase request.
    """
    token = get_current_token()
    scope = _build_scope(subscription_id, provider, location)
    url = f"{ARM_BASE}/{scope}/providers/Microsoft.Quota/quotas/{resource_name}?api-version={QUOTA_API_VERSION}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Create or update (request increase) quota
# ---------------------------------------------------------------------------

async def create_quota_request(
    subscription_id: Annotated[str, "Azure subscription ID"],
    provider: Annotated[str, "Resource provider, e.g. Microsoft.Compute"],
    location: Annotated[str, "Azure region, e.g. eastus"],
    resource_name: Annotated[str, "Quota resource name, e.g. standardFSv2Family"],
    new_limit: Annotated[int, "Requested new quota limit value"],
    resource_type: Annotated[str | None, "Optional resource type, e.g. 'dedicated', 'lowPriority', 'PublicIpAddresses'"] = None,
) -> dict:
    """
    Submit a quota increase request (PUT) for a specific resource.
    The request may be approved immediately (200) or accepted for processing (202).
    Check the returned provisioningState or use get_quota_request_status to track progress.
    
    Prerequisites:
    - Microsoft.Quota resource provider must be registered on the subscription
    - Caller must have the 'Quota Request Operator' role (ID: 0e5f05e5-9ab9-446b-b98d-1e2157c94125)
    """
    token = get_current_token()
    logger.info("create_quota_request: token length=%d, resource_name=%s", len(token) if token else 0, resource_name)
    scope = _build_scope(subscription_id, provider, location)
    url = f"{ARM_BASE}/{scope}/providers/Microsoft.Quota/quotas/{resource_name}?api-version={QUOTA_API_VERSION}"

    body: dict = {
        "properties": {
            "name": {
                "value": resource_name,
            },
            "limit": {
                "limitObjectType": "LimitValue",
                "value": new_limit,
            },
        }
    }
    if resource_type:
        body["properties"]["resourceType"] = resource_type

    logger.info("create_quota_request: PUT %s", url)
    logger.info("create_quota_request: body=%s", body)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(url, headers=_headers(token), json=body)

        logger.info("create_quota_request: status=%d", resp.status_code)

        if resp.status_code in (401, 403):
            error_body = {}
            try:
                error_body = resp.json()
            except Exception:
                error_body = {"raw": resp.text}
            logger.warning("create_quota_request: auth error body=%s", error_body)
            return {
                "status_code": resp.status_code,
                "error": f"HTTP {resp.status_code} from Azure Quota API. "
                         "Ensure the user has the 'Quota Request Operator' role "
                         "and Microsoft.Quota is registered on the subscription.",
                "details": error_body,
            }

        resp.raise_for_status()

        result: dict = {
            "status_code": resp.status_code,
        }

        if resp.status_code == 202:
            # Accepted – request is being processed
            result["message"] = "Quota request accepted and is being processed."
            result["location"] = resp.headers.get("location", "")
            result["retry_after"] = resp.headers.get("retry-after", "")
        else:
            result["data"] = resp.json()
            result["message"] = "Quota request completed."

        return result


# ---------------------------------------------------------------------------
# Get quota request status
# ---------------------------------------------------------------------------

async def get_quota_request_status(
    subscription_id: Annotated[str, "Azure subscription ID"],
    provider: Annotated[str, "Resource provider, e.g. Microsoft.Compute"],
    location: Annotated[str, "Azure region, e.g. eastus"],
    request_id: Annotated[str, "Quota request ID (returned from create_quota_request)"],
) -> dict:
    """
    Get the status of a specific quota request by its request ID.
    Returns provisioningState (Accepted, InProgress, Succeeded, Failed, Invalid) and details.
    """
    token = get_current_token()
    scope = _build_scope(subscription_id, provider, location)
    url = f"{ARM_BASE}/{scope}/providers/Microsoft.Quota/quotaRequests/{request_id}?api-version={QUOTA_API_VERSION}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# List quota request history
# ---------------------------------------------------------------------------

async def list_quota_requests(
    subscription_id: Annotated[str, "Azure subscription ID"],
    provider: Annotated[str, "Resource provider, e.g. Microsoft.Compute"],
    location: Annotated[str, "Azure region, e.g. eastus"],
    top: Annotated[int | None, "Max number of requests to return"] = 10,
    filter_expr: Annotated[str | None, "OData filter, e.g. 'provisioningState eq Succeeded'"] = None,
) -> dict:
    """
    List quota requests for the past year for a resource provider in a location.
    Supports filtering by provisioningState, resourceName, and requestSubmitTime.
    """
    token = get_current_token()
    scope = _build_scope(subscription_id, provider, location)
    url = f"{ARM_BASE}/{scope}/providers/Microsoft.Quota/quotaRequests?api-version={QUOTA_API_VERSION}"

    params: dict[str, str] = {}
    if top is not None:
        params["$top"] = str(top)
    if filter_expr:
        params["$filter"] = filter_expr

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=_headers(token), params=params)
        resp.raise_for_status()
        return resp.json()
