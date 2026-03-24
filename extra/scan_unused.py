"""
Azure Unused Resource Scanner

Scans an Azure subscription for unused, idle, and orphaned resources using:
- Phase 0: azqr CLI for resource inventory and pre-confirmed findings
- Phase 1: Azure Resource Graph for bulk orphan/stopped detection
- Phase 2: Azure Monitor metrics for idle resource detection (30-day lookback)
- Phase 3: Activity logs for last-touch audit (90-day lookback)

Usage:
    python scan_unused.py --subscription-id <sub-id> [--filters filters.yaml] [--skip-azqr]
"""

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Suppress verbose Azure SDK HTTP logging
for _name in ("azure.core.pipeline.policies.http_logging_policy", "azure.identity", "azure.mgmt"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Resource type → metric mapping for Phase 2
# ---------------------------------------------------------------------------
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
    "microsoft.network/virtualnetworkgateways": {
        "metrics": [
            {"name": "TunnelIngressBytes", "aggregation": "Total"},
            {"name": "TunnelEgressBytes", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
    },
    "microsoft.keyvault/vaults": {
        "metrics": [{"name": "ServiceApiHit", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.insights/components": {
        "metrics": [{"name": "requestsCount", "aggregation": "Total"}],
        "idle_threshold": 0,
    },
    "microsoft.kusto/clusters": {
        "metrics": [
            {"name": "QueryCount", "aggregation": "Total"},
            {"name": "IngestionResult", "aggregation": "Total"},
        ],
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
    "microsoft.datafactory/factories": {
        "metrics": [
            {"name": "PipelineSucceededRuns", "aggregation": "Total"},
            {"name": "PipelineFailedRuns", "aggregation": "Total"},
        ],
        "idle_threshold": 0,
    },
}

# Resource types that can only be assessed via activity log
ACTIVITY_LOG_ONLY_TYPES = {
    "microsoft.machinelearningservices/workspaces",
    "microsoft.databricks/workspaces",
}

# Infrastructure / passive resource types to skip
SKIP_TYPES = {
    "microsoft.network/virtualnetworks",
    "microsoft.network/privatednszones",
    "microsoft.network/privateendpoints",
    "microsoft.network/networkwatchers",
    "microsoft.insights/activitylogalerts",
    "microsoft.sql/servers",
    "microsoft.eventgrid/systemtopics",
    "microsoft.managedidentity/userassignedidentities",
    "microsoft.alertsmanagement/smartdetectoralertrules",
    "microsoft.network/privatednszones/virtualnetworklinks",
    "microsoft.botservice/botservices",
    "microsoft.bing/accounts",
    "microsoft.operationalinsights/querypacks",
    "microsoft.cognitiveservices/accounts/projects",
}


# =========================================================================
# Phase 0 — azqr scan
# =========================================================================
def run_azqr(subscription_id: str, filters_path: str) -> dict | None:
    """Run azqr scan and return parsed JSON output."""
    logger.info("Phase 0: Running azqr scan...")
    try:
        subprocess.run(
            [
                "azqr", "scan",
                "--subscription-id", subscription_id,
                "--filters", filters_path,
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.warning("azqr CLI not found. Skipping Phase 0.")
        return None
    except subprocess.CalledProcessError as exc:
        logger.warning("azqr scan returned non-zero exit code: %s", exc.returncode)
        # azqr may still produce output on non-zero exit

    # Find the most recent azqr output file
    files = sorted(glob("azqr_action_plan_*.json"), key=lambda f: Path(f).stat().st_mtime, reverse=True)
    if not files:
        logger.warning("No azqr output file found.")
        return None

    azqr_file = files[0]
    logger.info("Phase 0: Parsing azqr output: %s", azqr_file)
    with open(azqr_file, encoding="utf-8") as f:
        return json.load(f)


def parse_azqr_inventory(azqr_data: dict) -> list[dict]:
    """Extract resource inventory from azqr output."""
    inventory = []
    for item in azqr_data.get("inventory", []):
        resource_type = (item.get("resourceType") or "").lower()
        inventory.append({
            "resourceId": item.get("resourceId", ""),
            "resourceName": item.get("resourceName", ""),
            "resourceGroup": item.get("resourceGroup", ""),
            "resourceType": resource_type,
            "subscriptionId": item.get("subscriptionId", ""),
            "skuName": item.get("skuName", ""),
            "sla": item.get("sla", ""),
        })
    return inventory


def parse_azqr_findings(azqr_data: dict) -> list[dict]:
    """Extract pre-confirmed Cost advisor and AOR findings from azqr."""
    findings = []

    # Cost advisor items
    for item in azqr_data.get("advisor", []):
        if item.get("category") == "Cost":
            findings.append({
                "resourceId": item.get("resourceId", ""),
                "resourceName": item.get("resourceName", ""),
                "source": "azqr-advisor-cost",
                "category": "Cost",
                "impact": item.get("impact", ""),
                "description": item.get("description", ""),
            })

    # AOR orphan rules that are not implemented (orphans detected)
    for rec in azqr_data.get("recommendations", []):
        if rec.get("recommendationSource") == "AOR" and rec.get("implemented") == "false":
            count = int(rec.get("numberOfImpactedResources", "0"))
            if count > 0:
                findings.append({
                    "resourceId": "",
                    "resourceName": rec.get("recommendation", ""),
                    "source": "azqr-aor",
                    "category": "Orphaned",
                    "impact": rec.get("impact", ""),
                    "description": rec.get("bestPracticesGuidance", ""),
                    "impactedCount": count,
                })

    return findings


# =========================================================================
# Phase 1 — Resource Graph bulk property checks
# =========================================================================
# Split into two queries to stay within the 6-union-leg limit in Resource Graph
RESOURCE_GRAPH_QUERY_1 = """
resources
| where type == "microsoft.compute/virtualmachines"
    and tostring(properties.extended.instanceView.powerState.code) == "PowerState/deallocated"
| extend signal = "VM deallocated", classification = "UNUSED"
| project id, name, type, resourceGroup, subscriptionId, signal, classification,
          created = tostring(properties.timeCreated)
| union (
    resources
    | where type == "microsoft.compute/disks" and isempty(managedBy)
        and tostring(properties.diskState) == "Unattached"
    | extend signal = "Disk unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = tostring(properties.timeCreated)
)
| union (
    resources
    | where type == "microsoft.network/publicipaddresses"
        and isempty(properties.ipConfiguration)
    | extend signal = "Public IP unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = ""
)
| union (
    resources
    | where type == "microsoft.network/networkinterfaces"
        and isempty(properties.virtualMachine)
    | extend signal = "NIC unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = ""
)
"""

RESOURCE_GRAPH_QUERY_2 = """
resources
| where type == "microsoft.network/networksecuritygroups"
    and array_length(properties.subnets) == 0
    and array_length(properties.networkInterfaces) == 0
| extend signal = "NSG unassociated", classification = "ORPHANED"
| project id, name, type, resourceGroup, subscriptionId, signal, classification,
          created = ""
| union (
    resources
    | where type == "microsoft.network/natgateways"
        and array_length(properties.subnets) == 0
    | extend signal = "NAT Gateway unattached", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = ""
)
| union (
    resources
    | where type == "microsoft.web/serverfarms"
        and toint(properties.numberOfSites) == 0
    | extend signal = "App Service Plan empty", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = ""
)
| union (
    resources
    | where type == "microsoft.network/loadbalancers"
        and array_length(properties.backendAddressPools) == 0
    | extend signal = "Load Balancer empty", classification = "ORPHANED"
    | project id, name, type, resourceGroup, subscriptionId, signal, classification,
              created = ""
)
"""


def run_resource_graph(credential, subscription_id: str) -> list[dict]:
    """Phase 1: Run Resource Graph queries for orphaned/stopped resources."""
    logger.info("Phase 1: Running Resource Graph queries for orphaned/stopped resources...")
    client = ResourceGraphClient(credential)

    results = []
    for idx, query in enumerate([RESOURCE_GRAPH_QUERY_1, RESOURCE_GRAPH_QUERY_2], 1):
        request = QueryRequest(
            subscriptions=[subscription_id],
            query=query,
        )
        response = client.resources(request)
        logger.info("  Query %d returned %d results.", idx, len(response.data))
        for row in response.data:
            results.append({
                "resourceId": row.get("id", ""),
                "resourceName": row.get("name", ""),
                "resourceType": row.get("type", ""),
                "resourceGroup": row.get("resourceGroup", ""),
                "classification": row.get("classification", "ORPHANED"),
                "reason": row.get("signal", ""),
                "evidence": {
                    "source": "phase1-property",
                    "phase": "1",
                    "signal": row.get("signal", ""),
                    "value": row.get("signal", ""),
                    "lastActivity": "",
                },
                "createdDate": row.get("created", ""),
                "recommendation": "DELETE",
            })
    logger.info("Phase 1: Found %d orphaned/stopped resources.", len(results))
    return results


# =========================================================================
# Phase 2 — Azure Monitor metrics
# =========================================================================
def query_metric(monitor_client: MonitorManagementClient, resource_id: str,
                 metric_name: str, aggregation: str, start_time: datetime,
                 end_time: datetime) -> float:
    """Query a single metric for a resource and return the aggregate value."""
    try:
        response = monitor_client.metrics.list(
            resource_uri=resource_id,
            metricnames=metric_name,
            timespan=f"{start_time.isoformat()}/{end_time.isoformat()}",
            interval="P1D",
            aggregation=aggregation,
        )
    except Exception as exc:
        logger.debug("Metric query failed for %s/%s: %s", resource_id, metric_name, exc)
        return -1  # signal that metric is unavailable

    total = 0.0
    for metric in response.value:
        for ts in metric.timeseries:
            for dp in ts.data:
                val = getattr(dp, aggregation.lower(), None)
                if val is not None:
                    total += val
    return total


def check_resource_metrics(monitor_client: MonitorManagementClient,
                           resource: dict, start_time: datetime,
                           end_time: datetime) -> dict | None:
    """Check metrics for a single resource. Returns a finding dict or None if active."""
    resource_type = resource["resourceType"]
    config = METRIC_MAP.get(resource_type)
    if config is None:
        return None

    resource_id = resource["resourceId"]
    metric_results = {}
    all_idle = True

    for metric_def in config["metrics"]:
        value = query_metric(
            monitor_client, resource_id,
            metric_def["name"], metric_def["aggregation"],
            start_time, end_time,
        )
        metric_results[metric_def["name"]] = value
        if value < 0:
            # Metric unavailable — cannot confirm idle
            all_idle = False
        elif value > config["idle_threshold"]:
            all_idle = False

    if not all_idle:
        return None

    metric_summary = ", ".join(f"{k}={v}" for k, v in metric_results.items())
    return {
        "resourceId": resource_id,
        "resourceName": resource.get("resourceName", ""),
        "resourceType": resource_type,
        "resourceGroup": resource.get("resourceGroup", ""),
        "classification": "IDLE",
        "reason": f"Zero activity over 30 days: {metric_summary}",
        "evidence": {
            "source": "phase2-metrics",
            "phase": "2",
            "signal": metric_summary,
            "value": str(metric_results),
            "lastActivity": "",
        },
        "createdDate": "",
        "recommendation": "STOP",
    }


def run_metrics_check(credential, subscription_id: str,
                      inventory: list[dict]) -> list[dict]:
    """Phase 2: Check Azure Monitor metrics for idle resources."""
    logger.info("Phase 2: Checking Azure Monitor metrics (30-day lookback)...")
    monitor_client = MonitorManagementClient(credential, subscription_id)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=30)

    # Filter inventory to only resources with metric mappings
    metric_resources = [r for r in inventory if r["resourceType"] in METRIC_MAP]
    logger.info("Phase 2: %d resources to check metrics for.", len(metric_resources))

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_resource = {
            executor.submit(
                check_resource_metrics, monitor_client, resource, start_time, end_time
            ): resource
            for resource in metric_resources
        }
        for future in as_completed(future_to_resource):
            resource = future_to_resource[future]
            try:
                finding = future.result()
                if finding:
                    results.append(finding)
                    logger.info("  IDLE: %s (%s)", resource["resourceName"], resource["resourceType"])
            except Exception as exc:
                logger.debug("Metric check failed for %s: %s", resource["resourceName"], exc)

    logger.info("Phase 2: Found %d idle resources.", len(results))
    return results


# =========================================================================
# Phase 3 — Activity log audit
# =========================================================================
def check_activity_log(monitor_client: MonitorManagementClient,
                       resource_id: str, start_time: datetime) -> dict:
    """Query activity log for a resource and return last activity info."""
    try:
        filter_str = f"resourceUri eq '{resource_id}' and eventTimestamp ge '{start_time.isoformat()}'"
        logs = list(monitor_client.activity_logs.list(filter=filter_str))
    except Exception as exc:
        logger.debug("Activity log query failed for %s: %s", resource_id, exc)
        return {"lastOperation": None, "lastTime": None, "lastCaller": None}

    if not logs:
        return {"lastOperation": None, "lastTime": None, "lastCaller": None}

    # Find the most recent event
    latest = max(logs, key=lambda e: e.event_timestamp or datetime.min.replace(tzinfo=timezone.utc))
    return {
        "lastOperation": latest.operation_name.value if latest.operation_name else None,
        "lastTime": latest.event_timestamp.isoformat() if latest.event_timestamp else None,
        "lastCaller": latest.caller or None,
    }


def run_activity_log_audit(credential, subscription_id: str,
                           flagged: list[dict], inventory: list[dict]) -> list[dict]:
    """Phase 3: Check activity logs for flagged resources and activity-log-only types."""
    logger.info("Phase 3: Auditing activity logs (90-day lookback)...")
    monitor_client = MonitorManagementClient(credential, subscription_id)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=89)  # API max is 90 days

    # Collect resource IDs to check: all flagged + activity-log-only types
    resources_to_check = []

    # Flagged resources from Phase 1 and 2
    for finding in flagged:
        resources_to_check.append({
            "resourceId": finding["resourceId"],
            "resourceName": finding["resourceName"],
            "resourceType": finding.get("resourceType", ""),
        })

    # Activity-log-only resources from inventory
    for resource in inventory:
        if resource["resourceType"] in ACTIVITY_LOG_ONLY_TYPES:
            resources_to_check.append(resource)

    # Deduplicate by resource ID
    seen = set()
    unique = []
    for r in resources_to_check:
        rid = r["resourceId"]
        if rid and rid not in seen:
            seen.add(rid)
            unique.append(r)

    logger.info("Phase 3: %d resources to audit.", len(unique))

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_resource = {
            executor.submit(check_activity_log, monitor_client, r["resourceId"], start_time): r
            for r in unique
        }
        for future in as_completed(future_to_resource):
            resource = future_to_resource[future]
            try:
                activity = future.result()
                # Enrich existing findings with activity data
                for finding in flagged:
                    if finding["resourceId"] == resource["resourceId"]:
                        finding["evidence"]["lastActivity"] = activity["lastTime"] or "no activity in 90+ days"
                        finding["lastCaller"] = activity["lastCaller"] or "unknown"
                        break
                else:
                    # Activity-log-only resource — classify based on activity
                    if activity["lastOperation"] is None:
                        results.append({
                            "resourceId": resource["resourceId"],
                            "resourceName": resource["resourceName"],
                            "resourceType": resource["resourceType"],
                            "resourceGroup": resource.get("resourceGroup", ""),
                            "classification": "REVIEW",
                            "reason": "No management activity in 90+ days",
                            "evidence": {
                                "source": "phase3-activitylog",
                                "phase": "3",
                                "signal": "No activity logs found",
                                "value": "",
                                "lastActivity": "no activity in 90+ days",
                            },
                            "createdDate": "",
                            "recommendation": "REVIEW",
                        })
                        logger.info("  REVIEW: %s (%s) — no activity in 90+ days",
                                    resource["resourceName"], resource["resourceType"])
            except Exception as exc:
                logger.debug("Activity log check failed for %s: %s", resource["resourceName"], exc)

    logger.info("Phase 3: Found %d additional resources needing review.", len(results))
    return results


# =========================================================================
# Phase 5 — Consolidate report
# =========================================================================
def build_report(subscription_id: str, azqr_file: str | None,
                 inventory: list[dict], phase1: list[dict],
                 phase2: list[dict], phase3: list[dict],
                 azqr_findings: list[dict]) -> dict:
    """Consolidate all findings into a single report."""
    # Merge all findings, deduplicate by resource ID
    all_findings = []
    seen_ids = set()

    for finding in phase1 + phase2 + phase3:
        rid = finding.get("resourceId", "")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            finding.setdefault("azqrFindings", {})
            finding.setdefault("estimatedMonthlyCost", "")
            all_findings.append(finding)

    # Merge azqr Cost advisor findings that weren't already detected
    for azqr_f in azqr_findings:
        rid = azqr_f.get("resourceId", "")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            all_findings.append({
                "resourceId": rid,
                "resourceName": azqr_f.get("resourceName", ""),
                "resourceType": "",
                "resourceGroup": "",
                "classification": "REVIEW",
                "reason": f"Azure Advisor {azqr_f.get('category', '')} recommendation",
                "evidence": {
                    "source": "azqr",
                    "phase": "0",
                    "signal": azqr_f.get("description", ""),
                    "value": "",
                    "lastActivity": "",
                },
                "azqrFindings": {
                    "advisorCategory": azqr_f.get("category", ""),
                    "advisorImpact": azqr_f.get("impact", ""),
                    "aorRule": "",
                },
                "createdDate": "",
                "estimatedMonthlyCost": "",
                "recommendation": "REVIEW",
            })

    # Count classifications
    counts = {"unused": 0, "idle": 0, "orphaned": 0, "active": 0, "needsReview": 0}
    for f in all_findings:
        cls = f.get("classification", "").upper()
        if cls == "UNUSED":
            counts["unused"] += 1
        elif cls == "IDLE":
            counts["idle"] += 1
        elif cls == "ORPHANED":
            counts["orphaned"] += 1
        elif cls == "ACTIVE":
            counts["active"] += 1
        else:
            counts["needsReview"] += 1

    skipped = len([r for r in inventory if r["resourceType"] in SKIP_TYPES])

    return {
        "scanDate": datetime.now(timezone.utc).isoformat(),
        "subscriptionId": subscription_id,
        "azqrReportFile": azqr_file or "",
        "summary": {
            "totalInventory": len(inventory),
            "totalScanned": len(inventory) - skipped,
            "unused": counts["unused"],
            "idle": counts["idle"],
            "orphaned": counts["orphaned"],
            "active": counts["active"],
            "needsReview": counts["needsReview"],
            "skipped": skipped,
        },
        "findings": all_findings,
    }


# =========================================================================
# Main
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="Azure Unused Resource Scanner")
    parser.add_argument("--subscription-id", required=True, help="Azure subscription ID")
    parser.add_argument("--filters", default="filters.yaml", help="Path to azqr filters file")
    parser.add_argument("--skip-azqr", action="store_true", help="Skip azqr scan (use existing output)")
    parser.add_argument("--azqr-file", help="Path to existing azqr JSON file (implies --skip-azqr)")
    parser.add_argument("--workers", type=int, default=5, help="Max parallel metric queries")
    parser.add_argument("--output", help="Output file path (default: unused_resources_<date>.json)")
    args = parser.parse_args()

    subscription_id = args.subscription_id
    credential = DefaultAzureCredential()

    # ---- Phase 0: azqr scan ----
    azqr_data = None
    azqr_file = None
    inventory = []
    azqr_findings = []

    if args.azqr_file:
        azqr_file = args.azqr_file
        logger.info("Phase 0: Loading existing azqr file: %s", azqr_file)
        with open(azqr_file, encoding="utf-8") as f:
            azqr_data = json.load(f)
    elif not args.skip_azqr:
        azqr_data = run_azqr(subscription_id, args.filters)
        files = sorted(glob("azqr_action_plan_*.json"), key=lambda f: Path(f).stat().st_mtime, reverse=True)
        if files:
            azqr_file = files[0]

    if azqr_data:
        inventory = parse_azqr_inventory(azqr_data)
        azqr_findings = parse_azqr_findings(azqr_data)
        logger.info("Phase 0: %d resources in inventory, %d pre-confirmed findings.",
                     len(inventory), len(azqr_findings))
    else:
        logger.info("Phase 0: No azqr data. Will use Resource Graph for inventory.")
        # Fallback: use Resource Graph to build inventory
        client = ResourceGraphClient(credential)
        request = QueryRequest(
            subscriptions=[subscription_id],
            query="resources | project id, name, type, resourceGroup, subscriptionId",
        )
        response = client.resources(request)
        for row in response.data:
            inventory.append({
                "resourceId": row.get("id", ""),
                "resourceName": row.get("name", ""),
                "resourceGroup": row.get("resourceGroup", ""),
                "resourceType": (row.get("type") or "").lower(),
                "subscriptionId": row.get("subscriptionId", ""),
                "skuName": "",
                "sla": "",
            })
        logger.info("Phase 0 (fallback): %d resources from Resource Graph.", len(inventory))

    # ---- Phase 1: Resource Graph property checks ----
    phase1_results = run_resource_graph(credential, subscription_id)

    # ---- Phase 2: Azure Monitor metrics ----
    phase2_results = run_metrics_check(credential, subscription_id, inventory)

    # ---- Phase 3: Activity log audit ----
    all_flagged = phase1_results + phase2_results
    phase3_results = run_activity_log_audit(credential, subscription_id, all_flagged, inventory)

    # ---- Phase 5: Consolidate report ----
    report = build_report(
        subscription_id, azqr_file,
        inventory, phase1_results, phase2_results, phase3_results, azqr_findings,
    )

    output_file = args.output or f"unused_resources_{datetime.now().strftime('%Y_%m_%d_T%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    # Print summary
    s = report["summary"]
    logger.info("=" * 60)
    logger.info("SCAN COMPLETE")
    logger.info("=" * 60)
    logger.info("Total inventory:   %d", s["totalInventory"])
    logger.info("Total scanned:     %d", s["totalScanned"])
    logger.info("Skipped:           %d", s["skipped"])
    logger.info("-" * 40)
    logger.info("UNUSED (stopped):  %d", s["unused"])
    logger.info("IDLE (zero metrics): %d", s["idle"])
    logger.info("ORPHANED:          %d", s["orphaned"])
    logger.info("Needs REVIEW:      %d", s["needsReview"])
    logger.info("-" * 40)
    logger.info("Report saved to:   %s", output_file)

    # Print findings table
    if report["findings"]:
        logger.info("")
        logger.info("%-14s %-40s %-45s %s", "CLASSIFICATION", "NAME", "TYPE", "REASON")
        logger.info("-" * 140)
        for f in sorted(report["findings"], key=lambda x: x.get("classification", "")):
            logger.info(
                "%-14s %-40s %-45s %s",
                f.get("classification", ""),
                f.get("resourceName", "")[:40],
                f.get("resourceType", "")[:45],
                f.get("reason", "")[:50],
            )


if __name__ == "__main__":
    main()
