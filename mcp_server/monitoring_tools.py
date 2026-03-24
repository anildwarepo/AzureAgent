"""
Azure Monitoring Tools

Provides MCP tools for querying Azure Monitor metrics, resource health,
alerts, and activity logs.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.monitor.models import ResultType

from azure_auth import get_current_credential, get_current_token

logger = logging.getLogger(__name__)

# Metric mapping from scan_unused.py for idle detection
METRIC_MAP = {
    "microsoft.storage/storageaccounts": {
        "metrics": [{"name": "Transactions", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.sql/servers/databases": {
        "metrics": [
            {"name": "cpu_percent", "aggregation": "Average"},
            {"name": "dtu_consumption_percent", "aggregation": "Average"},
        ],
        "idle_threshold": 1,
    },
    "microsoft.dbforpostgresql/flexibleservers": {
        "metrics": [
            {"name": "active_connections", "aggregation": "Average"},
            {"name": "cpu_percent", "aggregation": "Average"},
        ],
        "idle_threshold": 1,
    },
    "microsoft.documentdb/databaseaccounts": {
        "metrics": [{"name": "TotalRequests", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.eventhub/namespaces": {
        "metrics": [
            {"name": "IncomingMessages", "aggregation": "Total"},
            {"name": "OutgoingMessages", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
    },
    "microsoft.servicebus/namespaces": {
        "metrics": [
            {"name": "IncomingMessages", "aggregation": "Total"},
            {"name": "OutgoingMessages", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
    },
    "microsoft.search/searchservices": {
        "metrics": [{"name": "SearchQueriesPerSecond", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.cognitiveservices/accounts": {
        "metrics": [{"name": "TotalCalls", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.containerregistry/registries": {
        "metrics": [
            {"name": "TotalPullCount", "aggregation": "Total"},
            {"name": "TotalPushCount", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
    },
    "microsoft.keyvault/vaults": {
        "metrics": [{"name": "ServiceApiHit", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.app/containerapps": {
        "metrics": [{"name": "Requests", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.apimanagement/service": {
        "metrics": [{"name": "TotalRequests", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.compute/virtualmachines": {
        "metrics": [
            {"name": "Percentage CPU", "aggregation": "Average"},
            {"name": "Network In Total", "aggregation": "Total"},
        ],
        "idle_threshold": 1,
    },
}


async def get_resource_metrics(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_id: Annotated[str, "Full Azure resource ID"],
    metric_names: Annotated[str, "Comma-separated metric names (e.g. 'Percentage CPU,Network In Total')"],
    aggregation: Annotated[str, "Aggregation type: Average, Total, Maximum, Minimum, Count"] = "Average",
    timespan_days: Annotated[int, "Number of days to look back (1-30)"] = 7,
    interval: Annotated[str, "Time grain ISO 8601 duration (e.g. PT1H, P1D)"] = "P1D",
) -> dict:
    """
    Query Azure Monitor metrics for a specific resource.
    Returns time-series data for the requested metrics.
    Use this to check CPU usage, memory, network traffic, request counts, etc.
    """
    credential = get_current_credential()
    client = MonitorManagementClient(credential, subscription_id)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=min(timespan_days, 30))
    timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"

    response = client.metrics.list(
        resource_uri=resource_id,
        metricnames=metric_names,
        timespan=timespan,
        interval=interval,
        aggregation=aggregation,
    )

    metrics_data = []
    for metric in response.value:
        timeseries_list = []
        for ts in metric.timeseries:
            data_points = []
            for dp in ts.data:
                point = {"timestamp": dp.time_stamp.isoformat() if dp.time_stamp else None}
                for agg in ["average", "total", "maximum", "minimum", "count"]:
                    val = getattr(dp, agg, None)
                    if val is not None:
                        point[agg] = val
                data_points.append(point)
            timeseries_list.append({"data": data_points})

        metrics_data.append({
            "name": metric.name.value if metric.name else "",
            "unit": str(metric.unit) if metric.unit else "",
            "timeseries": timeseries_list,
        })

    return {
        "resource_id": resource_id,
        "timespan": timespan,
        "interval": interval,
        "metrics": metrics_data,
    }


async def check_resource_health(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_id: Annotated[str | None, "Full Azure resource ID (omit for subscription-level health)"] = None,
) -> dict:
    """
    Check Azure Resource Health status. Returns the availability status
    of a specific resource or all resources in the subscription.
    """
    import httpx

    token = get_current_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if resource_id:
        url = f"https://management.azure.com{resource_id}/providers/Microsoft.ResourceHealth/availabilityStatuses/current?api-version=2023-07-01-preview"
    else:
        url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.ResourceHealth/availabilityStatuses?api-version=2023-07-01-preview"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if resource_id:
        props = data.get("properties", {})
        return {
            "resource_id": resource_id,
            "availability_state": props.get("availabilityState", "Unknown"),
            "summary": props.get("summary", ""),
            "reason_type": props.get("reasonType", ""),
            "occurred_time": props.get("occuredTime", ""),
        }

    statuses = []
    for item in data.get("value", []):
        props = item.get("properties", {})
        statuses.append({
            "resource_id": item.get("id", "").split("/providers/Microsoft.ResourceHealth")[0],
            "availability_state": props.get("availabilityState", "Unknown"),
            "summary": props.get("summary", ""),
        })
    return {"count": len(statuses), "statuses": statuses}


async def get_activity_log(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_id: Annotated[str | None, "Filter by resource ID"] = None,
    days: Annotated[int, "Number of days to look back (1-89)"] = 7,
    operation: Annotated[str | None, "Filter by operation name"] = None,
) -> dict:
    """
    Query the Azure Activity Log for management operations.
    Returns recent operations on resources including who performed them and when.
    """
    credential = get_current_credential()
    client = MonitorManagementClient(credential, subscription_id)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=min(days, 89))

    filter_parts = [f"eventTimestamp ge '{start_time.isoformat()}'"]
    if resource_id:
        filter_parts.append(f"resourceUri eq '{resource_id}'")
    filter_str = " and ".join(filter_parts)

    logs = list(client.activity_logs.list(filter=filter_str))

    entries = []
    for log in logs[:200]:  # cap results
        if operation and operation.lower() not in (log.operation_name.value or "").lower():
            continue
        entries.append({
            "timestamp": log.event_timestamp.isoformat() if log.event_timestamp else "",
            "operation": log.operation_name.value if log.operation_name else "",
            "status": log.status.value if log.status else "",
            "caller": log.caller or "",
            "resource_id": log.resource_id or "",
            "resource_type": log.resource_type.value if log.resource_type else "",
            "level": str(log.level) if log.level else "",
        })

    return {
        "count": len(entries),
        "timespan_days": min(days, 89),
        "entries": entries,
    }


async def list_metric_alerts(
    subscription_id: Annotated[str, "Azure subscription ID"],
) -> dict:
    """
    List all metric alert rules in the subscription.
    Shows configured monitoring alerts, their conditions, and current state.
    """
    import httpx

    token = get_current_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Insights/metricAlerts?api-version=2018-03-01"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    alerts = []
    for item in data.get("value", []):
        props = item.get("properties", {})
        alerts.append({
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "location": item.get("location", ""),
            "enabled": props.get("enabled", False),
            "severity": props.get("severity", ""),
            "description": props.get("description", ""),
            "scopes": props.get("scopes", []),
            "target_resource_type": props.get("targetResourceType", ""),
            "criteria": str(props.get("criteria", {})),
        })

    return {"count": len(alerts), "alerts": alerts}


async def check_idle_resources(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_ids: Annotated[str, "Comma-separated list of full Azure resource IDs to check"],
    resource_type: Annotated[str, "The Azure resource type (e.g. microsoft.compute/virtualmachines)"],
    days: Annotated[int, "Number of days to look back for idle detection (1-30)"] = 30,
) -> dict:
    """
    Check if specific resources are idle based on their metric activity over a time period.
    Uses the same idle detection logic as the unused resource scanner.
    Returns a list of resources classified as idle or active.
    """
    config = METRIC_MAP.get(resource_type.lower())
    if not config:
        return {
            "error": f"No idle detection metrics configured for resource type: {resource_type}",
            "supported_types": list(METRIC_MAP.keys()),
        }

    credential = get_current_credential()
    client = MonitorManagementClient(credential, subscription_id)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=min(days, 30))
    timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"

    ids = [rid.strip() for rid in resource_ids.split(",") if rid.strip()]
    results = []

    for resource_id in ids:
        metric_results = {}
        all_idle = True

        for metric_def in config["metrics"]:
            try:
                response = client.metrics.list(
                    resource_uri=resource_id,
                    metricnames=metric_def["name"],
                    timespan=timespan,
                    interval="P1D",
                    aggregation=metric_def["aggregation"],
                )
                total = 0.0
                for metric in response.value:
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            val = getattr(dp, metric_def["aggregation"].lower(), None)
                            if val is not None:
                                total += val
                metric_results[metric_def["name"]] = total
                if total > config["idle_threshold"]:
                    all_idle = False
            except Exception as exc:
                logger.debug("Metric query failed for %s/%s: %s", resource_id, metric_def["name"], exc)
                metric_results[metric_def["name"]] = -1
                all_idle = False

        results.append({
            "resource_id": resource_id,
            "classification": "IDLE" if all_idle else "ACTIVE",
            "metrics": metric_results,
            "threshold": config["idle_threshold"],
            "lookback_days": min(days, 30),
        })

    return {
        "count": len(results),
        "idle_count": sum(1 for r in results if r["classification"] == "IDLE"),
        "active_count": sum(1 for r in results if r["classification"] == "ACTIVE"),
        "results": results,
    }
