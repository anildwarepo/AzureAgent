"""
Deep Unused Resource Scanner

Performs a comprehensive multi-signal analysis to identify truly unused
resources.  The pipeline is generic — it discovers resource types present
in the subscription, looks up which metrics/signals apply, then fans out
across Azure Monitor, Cost Management, and Activity Log to build a
per-resource usage profile.

Signal sources
--------------
1. **Resource Graph properties** — structural orphan checks (unattached
   disks, NICs, IPs, empty plans, deallocated VMs …)
2. **Azure Monitor metrics** — zero-traffic / zero-activity detection
   over a configurable lookback window
3. **Cost Management** — per-resource spend in the same window
4. **Activity Log** — last management-plane operation timestamp

Each resource gets a composite classification:
- ORPHANED  — structurally detached (no parent / association)
- IDLE      — metrics show zero or below-threshold activity
- ACTIVE    — has meaningful traffic/activity
- UNKNOWN   — metrics not available or query failed
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

from azure_auth import get_current_credential, get_current_token
from report_store import store_report

logger = logging.getLogger(__name__)

# Concurrency limiter — prevents overwhelming Azure APIs with parallel calls
_AZURE_SEMAPHORE = asyncio.Semaphore(5)

# Cache of discovered metric definitions per resource type
_metric_definitions_cache: dict[str, list[dict]] = {}

# Metric names that indicate "activity" — if all are zero, resource is idle.
# Used as fallback when METRIC_PROFILES doesn't match available metrics.
_ACTIVITY_METRIC_KEYWORDS = [
    "request", "transaction", "call", "connection", "message",
    "query", "hit", "operation", "invocation", "execution",
    "cpu", "pull", "push", "ingress", "egress",
]

# ---------------------------------------------------------------------------
# Generic metric config per resource type
#
# Each entry declares which Azure Monitor metrics to query and what
# threshold distinguishes idle from active.  Add new resource types here
# to extend coverage — no code changes required.
# ---------------------------------------------------------------------------
METRIC_PROFILES = {
    "microsoft.compute/virtualmachines": {
        "label": "Virtual Machine",
        "metrics": [
            {"name": "Percentage CPU", "aggregation": "Average"},
            {"name": "Network In Total", "aggregation": "Total"},
        ],
        "idle_threshold": 1,
        "idle_hint": "CPU < 1% avg and near-zero network over the lookback window",
    },
    "microsoft.storage/storageaccounts": {
        "label": "Storage Account",
        "metrics": [{"name": "Transactions", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero transactions over the lookback window",
    },
    "microsoft.sql/servers/databases": {
        "label": "SQL Database",
        "metrics": [
            {"name": "cpu_percent", "aggregation": "Average"},
            {"name": "dtu_consumption_percent", "aggregation": "Average"},
        ],
        "idle_threshold": 1,
        "idle_hint": "CPU and DTU < 1% average",
    },
    "microsoft.dbforpostgresql/flexibleservers": {
        "label": "PostgreSQL Flexible Server",
        "metrics": [
            {"name": "active_connections", "aggregation": "Average"},
            {"name": "cpu_percent", "aggregation": "Average"},
        ],
        "idle_threshold": 1,
        "idle_hint": "Zero active connections and CPU < 1%",
    },
    "microsoft.documentdb/databaseaccounts": {
        "label": "Cosmos DB Account",
        "metrics": [{"name": "TotalRequests", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero requests over the lookback window",
    },
    "microsoft.eventhub/namespaces": {
        "label": "Event Hub Namespace",
        "metrics": [
            {"name": "IncomingMessages", "aggregation": "Total"},
            {"name": "OutgoingMessages", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
        "idle_hint": "Zero incoming and outgoing messages",
    },
    "microsoft.servicebus/namespaces": {
        "label": "Service Bus Namespace",
        "metrics": [
            {"name": "IncomingMessages", "aggregation": "Total"},
            {"name": "OutgoingMessages", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
        "idle_hint": "Zero incoming and outgoing messages",
    },
    "microsoft.search/searchservices": {
        "label": "Azure AI Search",
        "metrics": [{"name": "SearchQueriesPerSecond", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero search queries — could be DR standby; check indexer activity",
    },
    "microsoft.cognitiveservices/accounts": {
        "label": "Cognitive / AI Services",
        "metrics": [{"name": "TotalCalls", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero API calls — may be Foundry endpoint or standby resource",
    },
    "microsoft.containerregistry/registries": {
        "label": "Container Registry",
        "metrics": [
            {"name": "TotalPullCount", "aggregation": "Total"},
            {"name": "TotalPushCount", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
        "idle_hint": "Zero pulls and pushes — images may still be referenced externally",
    },
    "microsoft.keyvault/vaults": {
        "label": "Key Vault",
        "metrics": [{"name": "ServiceApiHit", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero API hits — secrets/certs may still be referenced by apps",
    },
    "microsoft.app/containerapps": {
        "label": "Container App",
        "metrics": [{"name": "Requests", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero HTTP requests — may run on schedule or respond to events",
    },
    "microsoft.apimanagement/service": {
        "label": "API Management",
        "metrics": [{"name": "TotalRequests", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero requests — may proxy internal/batch services; check analytics",
    },
    "microsoft.web/sites": {
        "label": "App Service / Function App",
        "metrics": [{"name": "Requests", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "Zero HTTP requests — Functions may be timer-triggered (check invocations)",
    },
    "microsoft.machinelearningservices/workspaces": {
        "label": "ML Workspace",
        "metrics": [{"name": "Model Deploy Succeeded", "aggregation": "Total"}],
        "idle_threshold": 0,
        "idle_hint": "No deployments — workspace may still hold datasets/models",
    },
    "microsoft.cache/redis": {
        "label": "Azure Cache for Redis",
        "metrics": [
            {"name": "connectedclients", "aggregation": "Average"},
            {"name": "totalcommandsprocessed", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
        "idle_hint": "Zero connected clients and zero commands processed",
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_orphan_queries():
    """Return Resource Graph KQL queries for structural orphan detection."""
    return [
        # Unattached disks
        """resources
        | where type == "microsoft.compute/disks" and isempty(managedBy)
            and tostring(properties.diskState) == "Unattached"
        | extend signal = "Disk unattached", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Unattached NICs
        """resources
        | where type == "microsoft.network/networkinterfaces"
            and isempty(properties.virtualMachine)
        | extend signal = "NIC unattached", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Unattached Public IPs
        """resources
        | where type == "microsoft.network/publicipaddresses"
            and isempty(properties.ipConfiguration)
        | extend signal = "Public IP unattached", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Deallocated VMs
        """resources
        | where type == "microsoft.compute/virtualmachines"
            and tostring(properties.extended.instanceView.powerState.code) == "PowerState/deallocated"
        | extend signal = "VM deallocated", classification = "UNUSED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Empty App Service Plans
        """resources
        | where type == "microsoft.web/serverfarms"
            and toint(properties.numberOfSites) == 0
        | extend signal = "App Service Plan empty", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Unassociated NSGs
        """resources
        | where type == "microsoft.network/networksecuritygroups"
            and array_length(properties.subnets) == 0
            and array_length(properties.networkInterfaces) == 0
        | extend signal = "NSG unassociated", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Empty Load Balancers
        """resources
        | where type == "microsoft.network/loadbalancers"
            and array_length(properties.backendAddressPools) == 0
        | extend signal = "Load Balancer empty", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
        # Unattached NAT Gateways
        """resources
        | where type == "microsoft.network/natgateways"
            and array_length(properties.subnets) == 0
        | extend signal = "NAT Gateway unattached", classification = "ORPHANED"
        | project id, name, type, resourceGroup, subscriptionId, signal, classification""",
    ]


async def _run_orphan_queries(client, subscription_id: str) -> list[dict]:
    """Run all orphan detection queries concurrently and merge results."""
    queries = _build_orphan_queries()

    async def _run_one(q):
        async with _AZURE_SEMAPHORE:
            try:
                req = QueryRequest(subscriptions=[subscription_id], query=q)
                resp = await asyncio.to_thread(client.resources, req)
                return resp.data
            except Exception as exc:
                logger.debug("Orphan query failed: %s", exc)
                return []

    batches = await asyncio.gather(*[_run_one(q) for q in queries])
    return [item for batch in batches for item in batch]


async def _discover_resources_by_type(client, subscription_id: str) -> dict[str, list[dict]]:
    """Discover resources grouped by type for all types we can analyse."""
    type_list = "', '".join(METRIC_PROFILES.keys())
    query = f"""
resources
| where type in~ ('{type_list}')
| project id, name, type, resourceGroup, subscriptionId, location,
          sku=tostring(sku.name), kind,
          provisioningState=tostring(properties.provisioningState)
| order by type asc
"""
    req = QueryRequest(subscriptions=[subscription_id], query=query)
    resp = await asyncio.to_thread(client.resources, req)

    grouped: dict[str, list[dict]] = {}
    for r in resp.data:
        rtype = r.get("type", "").lower()
        grouped.setdefault(rtype, []).append(r)
    return grouped


async def _check_metrics_for_resource(
    monitor_client,
    resource_id: str,
    profile: dict,
    timespan: str,
) -> dict:
    """Query metrics for one resource with auto-discovery fallback."""
    metric_results = {}
    all_idle = True
    all_failed = True

    async def _query_one(mdef):
        async with _AZURE_SEMAPHORE:
            try:
                resp = await asyncio.to_thread(
                    monitor_client.metrics.list,
                    resource_uri=resource_id,
                    metricnames=mdef["name"],
                    timespan=timespan,
                    interval="P1D",
                    aggregation=mdef["aggregation"],
                )
                total = 0.0
                for metric in resp.value:
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            val = getattr(dp, mdef["aggregation"].lower(), None)
                            if val is not None:
                                total += val
                return mdef["name"], round(total, 4)
            except Exception as exc:
                logger.debug("Metric %s failed for %s: %s", mdef["name"], resource_id, exc)
                return mdef["name"], None

    # Try configured metrics first
    results = await asyncio.gather(*[_query_one(m) for m in profile["metrics"]])
    for name, val in results:
        metric_results[name] = val
        if val is not None:
            all_failed = False
            if val > profile["idle_threshold"]:
                all_idle = False
        else:
            all_idle = False  # can't confirm idle if unknown

    # If ALL configured metrics failed, try auto-discovering available metrics
    if all_failed:
        discovered = await _discover_activity_metrics(monitor_client, resource_id)
        if discovered:
            logger.info("Auto-discovered %d activity metrics for %s", len(discovered), resource_id.split("/")[-1])
            fallback_results = await asyncio.gather(*[_query_one(m) for m in discovered[:3]])
            metric_results = {}
            all_idle = True
            for name, val in fallback_results:
                metric_results[name] = val
                if val is not None:
                    all_failed = False
                    if val > 0:
                        all_idle = False
                else:
                    all_idle = False

    return {"metrics": metric_results, "idle": all_idle, "all_failed": all_failed}


async def _discover_activity_metrics(monitor_client, resource_id: str) -> list[dict]:
    """Auto-discover available metrics for a resource and pick activity-related ones."""
    rtype = "/".join(resource_id.split("/providers/")[-1].split("/")[:2]).lower()

    if rtype in _metric_definitions_cache:
        return _metric_definitions_cache[rtype]

    try:
        async with _AZURE_SEMAPHORE:
            definitions = await asyncio.to_thread(
                monitor_client.metric_definitions.list,
                resource_uri=resource_id,
            )
            all_metrics = [
                {"name": d.name.value, "aggregation": _pick_aggregation(d)}
                for d in definitions
                if d.name and d.name.value
            ]
    except Exception as exc:
        logger.debug("Metric definitions failed for %s: %s", resource_id, exc)
        _metric_definitions_cache[rtype] = []
        return []

    # Filter to activity-indicating metrics
    activity_metrics = [
        m for m in all_metrics
        if any(kw in m["name"].lower() for kw in _ACTIVITY_METRIC_KEYWORDS)
    ]

    # If no keyword matches, take the first few metrics as-is
    if not activity_metrics and all_metrics:
        activity_metrics = all_metrics[:3]

    _metric_definitions_cache[rtype] = activity_metrics
    return activity_metrics


def _pick_aggregation(metric_def) -> str:
    """Pick the best aggregation type for a metric definition."""
    supported = set()
    if metric_def.supported_aggregation_types:
        for a in metric_def.supported_aggregation_types:
            supported.add(str(a).split(".")[-1])
    # Prefer Total for count-like metrics, Average for percentage-like
    name_lower = (metric_def.name.value or "").lower()
    if "percent" in name_lower or "cpu" in name_lower or "memory" in name_lower:
        if "Average" in supported:
            return "Average"
    if "Total" in supported:
        return "Total"
    if "Average" in supported:
        return "Average"
    if "Count" in supported:
        return "Count"
    if supported:
        return next(iter(supported))
    return "Total"


async def _get_cost_per_resource(token: str, subscription_id: str, days: int) -> dict[str, float]:
    """Return {resource_id_lower: cost} from Cost Management with retry for 429."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    )
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceId"}],
        },
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    cost_map: dict[str, float] = {}
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                    logger.info("Cost API 429 — retrying in %ds (attempt %d/%d)", retry_after, attempt + 1, max_retries)
                    await asyncio.sleep(min(retry_after, 30))
                    continue
                resp.raise_for_status()
                data = resp.json()
            columns = [c["name"] for c in data.get("properties", {}).get("columns", [])]
            for row in data.get("properties", {}).get("rows", []):
                entry = dict(zip(columns, row))
                rid = entry.get("ResourceId", "").lower()
                cost_map[rid] = round(entry.get("Cost", 0), 2)
            break
        except Exception as exc:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** (attempt + 1))
            else:
                logger.warning("Cost query failed after %d attempts: %s", max_retries, exc)
    return cost_map


async def _get_last_activity(token: str, subscription_id: str, resource_id: str) -> str | None:
    """Return ISO timestamp of the most recent activity log entry for a resource."""
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Insights/eventtypes/management/values"
        f"?api-version=2015-04-01"
        f"&$filter=eventTimestamp ge '{since}'"
        f" and resourceUri eq '{resource_id}'"
        f"&$top=1&$orderby=eventTimestamp desc"
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _AZURE_SEMAPHORE:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            entries = data.get("value", [])
            if entries:
                return entries[0].get("eventTimestamp", None)
    except Exception as exc:
        logger.debug("Activity log query failed for %s: %s", resource_id, exc)
    return None


# ---------------------------------------------------------------------------
# Public MCP tool
# ---------------------------------------------------------------------------

async def scan_unused_resources(
    subscription_id: Annotated[str, "Azure subscription ID"],
    days: Annotated[int, "Lookback window in days for metrics and cost analysis (1-30)"] = 14,
    include_cost: Annotated[bool, "Include per-resource cost data from Cost Management"] = True,
    include_activity: Annotated[bool, "Include last activity log timestamp (slower — samples up to 10 idle resources)"] = True,
) -> dict:
    """
    Comprehensive unused resource scan across the entire subscription.

    Performs a multi-signal analysis:
    1. **Resource Graph** — finds structural orphans (unattached disks, NICs,
       IPs, empty plans, deallocated VMs, unassociated NSGs, empty LBs)
    2. **Auto-discovers** all resources whose types have metric profiles
       (VMs, Storage, SQL, Cosmos, Event Hub, Service Bus, AI Search,
       Cognitive Services, ACR, Key Vault, Container Apps, APIM, App Service,
       Redis, ML Workspaces …)
    3. **Azure Monitor metrics** — queries each resource's usage metrics over
       the lookback window to classify as IDLE or ACTIVE
    4. **Cost Management** — attaches per-resource spend so you can see
       cost impact of idle resources
    5. **Activity Log** — samples last management-plane operation for idle
       resources to help distinguish standby from truly abandoned

    Each resource gets a classification, metric values, cost, and contextual
    hints explaining why it may appear idle even if it's intentional (e.g.
    DR standby, batch-triggered, event-driven).

    Returns a structured summary with orphaned, idle, and active counts.
    """
    days = max(1, min(days, 30))
    credential = get_current_credential()
    token = get_current_token()
    rg_client = ResourceGraphClient(credential)

    # ── Phase 1 & 2 & cost run in parallel ─────────────────────────────
    async def _noop_cost():
        return {}

    orphans, grouped, cost_map = await asyncio.gather(
        _run_orphan_queries(rg_client, subscription_id),
        _discover_resources_by_type(rg_client, subscription_id),
        _get_cost_per_resource(token, subscription_id, days) if include_cost else _noop_cost(),
    )

    orphan_ids = {r.get("id", "").lower() for r in orphans}

    # ── Phase 3: Metric-based idle detection (all resources in parallel) ─
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    timespan = f"{start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    # Build flat list of (resource, profile) pairs, skipping orphans
    check_items = []
    for rtype, resources in grouped.items():
        profile = METRIC_PROFILES.get(rtype)
        if not profile:
            continue
        for res in resources:
            if res.get("id", "").lower() not in orphan_ids:
                check_items.append((res, rtype, profile))

    # Run metric checks concurrently (batched, semaphore-throttled)
    BATCH_SIZE = 10
    idle_resources = []
    active_resources = []
    unknown_resources = []

    for i in range(0, len(check_items), BATCH_SIZE):
        batch = check_items[i:i + BATCH_SIZE]
        monitor = MonitorManagementClient(credential, subscription_id)

        async def _check_one(res, rtype, profile):
            rid = res.get("id", "")
            result = await _check_metrics_for_resource(monitor, rid, profile, timespan)
            entry = {
                "resource_id": rid,
                "name": res.get("name", ""),
                "type": rtype,
                "type_label": profile["label"],
                "resource_group": res.get("resourceGroup", ""),
                "location": res.get("location", ""),
                "sku": res.get("sku", ""),
                "metrics": result["metrics"],
                "lookback_days": days,
                "cost": cost_map.get(rid.lower(), 0.0),
            }
            if result.get("all_failed"):
                entry["classification"] = "UNKNOWN"
                entry["signal"] = "No metrics available for this resource type"
            elif any(v is None for v in result["metrics"].values()):
                # Some metrics worked, some didn't — partial data
                if result["idle"]:
                    entry["classification"] = "IDLE"
                    entry["signal"] = profile["idle_hint"]
                else:
                    entry["classification"] = "ACTIVE"
                    entry["signal"] = "Has activity above threshold (some metrics unavailable)"
            elif result["idle"]:
                entry["classification"] = "IDLE"
                entry["signal"] = profile["idle_hint"]
            else:
                entry["classification"] = "ACTIVE"
                entry["signal"] = "Has activity above threshold"
            return entry

        results = await asyncio.gather(
            *[_check_one(res, rtype, prof) for res, rtype, prof in batch],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.debug("Metric check failed: %s", r)
                continue
            if r["classification"] == "IDLE":
                idle_resources.append(r)
            elif r["classification"] == "UNKNOWN":
                unknown_resources.append(r)
            else:
                active_resources.append(r)

    # ── Phase 4: Cost enrichment for orphans ─────────────────────────────
    for r in orphans:
        rid = r.get("id", "").lower()
        r["cost"] = cost_map.get(rid, 0.0)

    # ── Phase 5: Activity log sampling in parallel ───────────────────────
    if include_activity and idle_resources:
        sample = idle_resources[:10]
        activity_results = await asyncio.gather(
            *[_get_last_activity(token, subscription_id, r["resource_id"]) for r in sample],
            return_exceptions=True,
        )
        for r, ts in zip(sample, activity_results):
            r["last_activity"] = ts if not isinstance(ts, Exception) else None

    # ── Phase 6: Auto-generate report ──────────────────────────────────
    # Combine all non-active findings into a report-compatible format
    all_findings = []
    for r in orphans:
        all_findings.append({
            "name": r.get("name", ""),
            "type": r.get("type", ""),
            "resourceGroup": r.get("resourceGroup", ""),
            "classification": r.get("classification", "ORPHANED"),
            "signal": r.get("signal", ""),
            "cost": r.get("cost", 0.0),
        })
    for r in idle_resources:
        all_findings.append({
            "name": r.get("name", ""),
            "type": r.get("type", ""),
            "resourceGroup": r.get("resource_group", ""),
            "classification": "IDLE",
            "signal": r.get("signal", ""),
            "cost": r.get("cost", 0.0),
            "metrics": r.get("metrics", {}),
            "last_activity": r.get("last_activity"),
        })
    for r in unknown_resources:
        all_findings.append({
            "name": r.get("name", ""),
            "type": r.get("type", ""),
            "resourceGroup": r.get("resource_group", ""),
            "classification": "REVIEW",
            "signal": r.get("signal", ""),
            "cost": r.get("cost", 0.0),
        })

    report_id = None
    if all_findings:
        from report_tools import generate_resource_report
        import json as _json
        report_result = await generate_resource_report(
            report_data=_json.dumps(all_findings),
            title="Orphaned & Unused Azure Resources Report",
            subscription_id=subscription_id,
        )
        report_id = report_result.get("report_id")

    # ── Build summary ────────────────────────────────────────────────────
    idle_cost = sum(r.get("cost", 0) for r in idle_resources)
    orphan_cost = sum(r.get("cost", 0) for r in orphans)

    result = {
        "subscription_id": subscription_id,
        "lookback_days": days,
        "summary": {
            "orphaned_count": len(orphans),
            "idle_count": len(idle_resources),
            "active_count": len(active_resources),
            "unknown_count": len(unknown_resources),
            "total_scanned": len(orphans) + len(idle_resources) + len(active_resources) + len(unknown_resources),
            "idle_cost": round(idle_cost, 2),
            "orphan_cost": round(orphan_cost, 2),
            "potential_savings": round(idle_cost + orphan_cost, 2),
            "currency": "USD",
        },
        "resource_types_scanned": list(grouped.keys()),
        "resource_types_supported": list(METRIC_PROFILES.keys()),
        "orphaned": orphans,
        "idle": idle_resources,
        "unknown": unknown_resources,
        "active_count_by_type": {
            rtype: sum(1 for r in active_resources if r["type"] == rtype)
            for rtype in set(r["type"] for r in active_resources)
        },
    }
    if report_id:
        result["report_id"] = report_id
    return result
