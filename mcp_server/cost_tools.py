"""
Azure Cost Management Tools

Provides MCP tools for querying Azure Cost Management APIs to analyze
spending, get cost breakdowns, budget information, and cost recommendations.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)


async def get_cost_summary(
    subscription_id: Annotated[str, "Azure subscription ID"],
    days: Annotated[int, "Number of days to look back (1-90)"] = 30,
    granularity: Annotated[str, "Cost granularity: Daily, Monthly, or None"] = "Daily",
) -> dict:
    """
    Get a cost summary for the subscription over a time period.
    Returns total cost grouped by day or month with currency information.
    """
    token = get_current_token()

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=min(days, 90))

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
        "dataset": {
            "granularity": granularity,
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"},
            },
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    columns = [col["name"] for col in data.get("properties", {}).get("columns", [])]
    rows = data.get("properties", {}).get("rows", [])

    results = []
    total_cost = 0.0
    currency = ""
    for row in rows:
        entry = dict(zip(columns, row))
        cost = entry.get("Cost", 0)
        total_cost += cost
        if not currency and "Currency" in entry:
            currency = entry["Currency"]
        results.append(entry)

    return {
        "total_cost": round(total_cost, 2),
        "currency": currency,
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "granularity": granularity,
        "data_points": len(results),
        "data": results,
    }


async def get_cost_by_resource_group(
    subscription_id: Annotated[str, "Azure subscription ID"],
    days: Annotated[int, "Number of days to look back (1-90)"] = 30,
) -> dict:
    """
    Get cost breakdown by resource group for the subscription.
    Useful for identifying which resource groups are the most expensive.
    """
    token = get_current_token()

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=min(days, 90))

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"},
            },
            "grouping": [
                {"type": "Dimension", "name": "ResourceGroupName"},
            ],
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    columns = [col["name"] for col in data.get("properties", {}).get("columns", [])]
    rows = data.get("properties", {}).get("rows", [])

    results = []
    for row in rows:
        entry = dict(zip(columns, row))
        results.append(entry)

    results.sort(key=lambda x: x.get("Cost", 0), reverse=True)

    return {
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "resource_group_count": len(results),
        "data": results,
    }


async def get_cost_by_service(
    subscription_id: Annotated[str, "Azure subscription ID"],
    days: Annotated[int, "Number of days to look back (1-90)"] = 30,
) -> dict:
    """
    Get cost breakdown by Azure service (meter category) for the subscription.
    Useful for identifying which services are the most expensive.
    """
    token = get_current_token()

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=min(days, 90))

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"},
            },
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    columns = [col["name"] for col in data.get("properties", {}).get("columns", [])]
    rows = data.get("properties", {}).get("rows", [])

    results = []
    for row in rows:
        entry = dict(zip(columns, row))
        results.append(entry)

    results.sort(key=lambda x: x.get("Cost", 0), reverse=True)

    return {
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "service_count": len(results),
        "data": results,
    }


async def get_cost_by_resource(
    subscription_id: Annotated[str, "Azure subscription ID"],
    days: Annotated[int, "Number of days to look back (1-90)"] = 30,
    top: Annotated[int, "Return top N most expensive resources"] = 20,
) -> dict:
    """
    Get cost breakdown by individual resource. Returns the most expensive resources
    in the subscription. Useful for identifying specific high-cost resources.
    """
    token = get_current_token()

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=min(days, 90))

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"},
            },
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
            ],
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    columns = [col["name"] for col in data.get("properties", {}).get("columns", [])]
    rows = data.get("properties", {}).get("rows", [])

    results = []
    for row in rows:
        entry = dict(zip(columns, row))
        results.append(entry)

    results.sort(key=lambda x: x.get("Cost", 0), reverse=True)
    results = results[:top]

    return {
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "top": top,
        "data": results,
    }


async def list_budgets(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    List all budgets configured for the subscription.
    Returns budget names, amounts, time periods, and current spend vs budget.
    """
    token = get_current_token()

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Consumption/budgets?api-version=2023-11-01"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    budgets = []
    for item in data.get("value", []):
        props = item.get("properties", {})
        current_spend = props.get("currentSpend", {})
        budgets.append({
            "name": item.get("name", ""),
            "amount": props.get("amount", 0),
            "time_grain": props.get("timeGrain", ""),
            "time_period": props.get("timePeriod", {}),
            "current_spend": current_spend.get("amount", 0),
            "currency": current_spend.get("unit", ""),
            "category": props.get("category", ""),
            "notifications": list(props.get("notifications", {}).keys()),
        })

    return {"count": len(budgets), "budgets": budgets}


async def get_advisor_recommendations(
    subscription_id: Annotated[str, "Azure subscription ID"],
    category: Annotated[str | None, "Filter by category: Cost, Security, Performance, HighAvailability, OperationalExcellence"] = None,
) -> dict:
    """
    Get Azure Advisor recommendations for the subscription.
    Advisor provides personalized best practice recommendations for cost optimization,
    security, reliability, performance, and operational excellence.
    """
    token = get_current_token()

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Advisor/recommendations?api-version=2023-01-01"
    if category:
        url += f"&$filter=Category eq '{category}'"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    recommendations = []
    for item in data.get("value", []):
        props = item.get("properties", {})
        recommendations.append({
            "id": item.get("id", ""),
            "category": props.get("category", ""),
            "impact": props.get("impact", ""),
            "impacted_field": props.get("impactedField", ""),
            "impacted_value": props.get("impactedValue", ""),
            "short_description": props.get("shortDescription", {}).get("problem", ""),
            "solution": props.get("shortDescription", {}).get("solution", ""),
            "resource_metadata": props.get("resourceMetadata", {}).get("resourceId", ""),
        })

    return {"count": len(recommendations), "recommendations": recommendations}
