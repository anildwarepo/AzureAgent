"""
Report Generation Tools

Provides MCP tools to generate self-contained interactive HTML reports
and visualizations for Azure resource data. Uses the same visual style as
the unused resource scanner report (generate_report.py).
"""

import html
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from report_store import store_report

logger = logging.getLogger(__name__)


def _escape(text) -> str:
    return html.escape(str(text)) if text else ""


async def generate_resource_report(
    report_data: Annotated[str, "JSON string of findings data. Each item should have: name (or resourceName), type (or resourceType), resourceGroup, classification, signal (or reason), and optionally recommendation and evidence"],
    title: Annotated[str, "Report title"] = "Azure Resource Report",
    subscription_id: Annotated[str, "Azure subscription ID for the header"] = "",
) -> dict:
    """
    Generate a self-contained interactive HTML report from resource findings data.
    Returns a report_id that the UI uses to fetch and render the report.
    The report includes charts, summary cards, filterable data table, and dark theme styling.
    Use this to create rich visualizations of unused resources, cost data, or resource health.

    Accepts data from find_orphaned_resources, check_idle_resources, or custom findings.
    Field mapping is flexible: accepts both 'name'/'resourceName', 'type'/'resourceType', 'signal'/'reason'.
    """
    try:
        findings = json.loads(report_data) if isinstance(report_data, str) else report_data
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in report_data"}

    # Accept multiple possible dict shapes
    if not isinstance(findings, list):
        if isinstance(findings, dict):
            for key in ("findings", "orphaned_resources", "resources", "data", "idle_resources"):
                if key in findings and isinstance(findings[key], list):
                    findings = findings[key]
                    break
            else:
                findings = []
        else:
            findings = []

    # Normalize field names so both find_orphaned_resources output and
    # explicitly-mapped data work correctly.
    def _norm(f):
        return {
            "resourceName": f.get("resourceName") or f.get("name", ""),
            "resourceType": f.get("resourceType") or f.get("type", ""),
            "resourceGroup": f.get("resourceGroup", ""),
            "classification": f.get("classification", "UNKNOWN"),
            "reason": f.get("reason") or f.get("signal", ""),
            "recommendation": f.get("recommendation", ""),
            "evidence": f.get("evidence", {}),
        }
    findings = [_norm(f) for f in findings]

    # Aggregate data for charts
    classification_counts = {}
    type_counts = {}
    rg_counts = {}
    recommendation_counts = {}

    for f in findings:
        cls = f.get("classification", "UNKNOWN")
        classification_counts[cls] = classification_counts.get(cls, 0) + 1

        rtype = f.get("resourceType", "unknown")
        short_type = rtype.split("/")[-1] if "/" in rtype else rtype
        type_counts[short_type] = type_counts.get(short_type, 0) + 1

        rg = f.get("resourceGroup", "unknown")
        rg_counts[rg] = rg_counts.get(rg, 0) + 1

        rec = f.get("recommendation", "UNKNOWN")
        recommendation_counts[rec] = recommendation_counts.get(rec, 0) + 1

    type_counts = dict(sorted(type_counts.items(), key=lambda x: x[1], reverse=True))
    rg_counts = dict(sorted(rg_counts.items(), key=lambda x: x[1], reverse=True)[:15])

    cls_colors = {
        "UNUSED": "#e74c3c", "IDLE": "#f39c12", "ORPHANED": "#e67e22",
        "REVIEW": "#3498db", "ACTIVE": "#2ecc71", "HIGH": "#e74c3c",
        "MEDIUM": "#f39c12", "LOW": "#2ecc71",
    }

    # Build table rows
    table_rows = []
    for f in sorted(findings, key=lambda x: x.get("classification", "")):
        cls = _escape(f.get("classification", ""))
        name = _escape(f.get("resourceName", ""))
        rtype = _escape(f.get("resourceType", ""))
        rg = _escape(f.get("resourceGroup", ""))
        reason = _escape(f.get("reason", ""))
        rec = _escape(f.get("recommendation", ""))
        badge_color = cls_colors.get(cls, "#95a5a6")
        table_rows.append(f"""<tr>
            <td><span class="badge" style="background:{badge_color}">{cls}</span></td>
            <td class="resource-name">{name}</td>
            <td>{rtype}</td>
            <td>{rg}</td>
            <td>{reason}</td>
            <td><span class="rec-badge">{rec}</span></td>
        </tr>""")

    findings_html = "\n".join(table_rows)
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Count by classification for summary
    unused_count = classification_counts.get("UNUSED", 0)
    idle_count = classification_counts.get("IDLE", 0)
    orphaned_count = classification_counts.get("ORPHANED", 0)
    review_count = classification_counts.get("REVIEW", 0)

    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
    :root {{
        --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a;
        --text: #e4e6eb; --text-muted: #8b8fa3; --accent: #6366f1;
        --unused: #e74c3c; --idle: #f39c12; --orphaned: #e67e22;
        --review: #3498db; --active: #2ecc71;
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: var(--bg); color: var(--text); line-height: 1.6; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
    .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #1e3a5f 100%);
               border-radius: 16px; padding: 32px; margin-bottom: 24px; border: 1px solid var(--border); }}
    .header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px;
                  background: linear-gradient(90deg, #818cf8, #6ee7b7);
                  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    .header-meta {{ display: flex; gap: 24px; color: var(--text-muted); font-size: 14px; flex-wrap: wrap; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                     gap: 16px; margin-bottom: 24px; }}
    .summary-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
                     padding: 20px; text-align: center; }}
    .summary-card .number {{ font-size: 36px; font-weight: 800; line-height: 1.1; }}
    .summary-card .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
                            color: var(--text-muted); margin-top: 4px; }}
    .card-total .number {{ color: var(--accent); }}
    .card-unused .number {{ color: var(--unused); }}
    .card-idle .number {{ color: var(--idle); }}
    .card-orphaned .number {{ color: var(--orphaned); }}
    .card-review .number {{ color: var(--review); }}
    .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                    gap: 20px; margin-bottom: 24px; }}
    .chart-card {{ background: var(--card); border: 1px solid var(--border);
                   border-radius: 12px; padding: 24px; }}
    .chart-card h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; }}
    .chart-container {{ position: relative; width: 100%; max-height: 300px; }}
    .table-card {{ background: var(--card); border: 1px solid var(--border);
                   border-radius: 12px; padding: 24px; overflow-x: auto; }}
    .table-card h3 {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; }}
    .filter-bar {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
    .filter-btn {{ padding: 6px 14px; border-radius: 20px; border: 1px solid var(--border);
                   background: transparent; color: var(--text-muted); font-size: 13px; cursor: pointer; }}
    .filter-btn:hover, .filter-btn.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
    table.dataTable {{ border-collapse: collapse !important; width: 100% !important; }}
    table.dataTable thead th {{ background: #252836 !important; color: var(--text) !important;
        border-bottom: 2px solid var(--accent) !important; padding: 12px 16px !important;
        font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
    table.dataTable tbody td {{ padding: 10px 16px !important; border-bottom: 1px solid var(--border) !important;
        color: var(--text) !important; font-size: 13px; background: transparent !important; }}
    table.dataTable tbody tr:hover td {{ background: rgba(99,102,241,0.08) !important; }}
    .dataTables_wrapper .dataTables_filter input {{ background: var(--bg) !important;
        border: 1px solid var(--border) !important; color: var(--text) !important;
        border-radius: 6px; padding: 6px 12px; }}
    .dataTables_wrapper .dataTables_length select {{ background: var(--bg) !important;
        border: 1px solid var(--border) !important; color: var(--text) !important; border-radius: 6px; }}
    .dataTables_wrapper .dataTables_info, .dataTables_wrapper .dataTables_paginate {{
        color: var(--text-muted) !important; padding-top: 12px !important; }}
    .dataTables_wrapper .dataTables_paginate .paginate_button {{
        color: var(--text-muted) !important; border: 1px solid var(--border) !important;
        border-radius: 4px !important; margin: 0 2px !important; }}
    .dataTables_wrapper .dataTables_paginate .paginate_button.current {{
        background: var(--accent) !important; color: white !important; border-color: var(--accent) !important; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 11px;
              font-weight: 700; color: white; text-transform: uppercase; }}
    .rec-badge {{ display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 600;
                  background: rgba(99,102,241,0.15); color: #818cf8; }}
    .resource-name {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px;
                      max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .footer {{ text-align: center; padding: 24px; color: var(--text-muted); font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{_escape(title)}</h1>
        <div class="header-meta">
            <span>&#128197; {scan_date}</span>
            {'<span>&#128273; Subscription: ' + _escape(subscription_id) + '</span>' if subscription_id else ''}
            <span>&#128196; Total findings: {len(findings)}</span>
        </div>
    </div>
    <div class="summary-grid">
        <div class="summary-card card-total">
            <div class="number">{len(findings)}</div><div class="label">Total Findings</div>
        </div>
        <div class="summary-card card-unused">
            <div class="number">{unused_count}</div><div class="label">Unused</div>
        </div>
        <div class="summary-card card-idle">
            <div class="number">{idle_count}</div><div class="label">Idle</div>
        </div>
        <div class="summary-card card-orphaned">
            <div class="number">{orphaned_count}</div><div class="label">Orphaned</div>
        </div>
        <div class="summary-card card-review">
            <div class="number">{review_count}</div><div class="label">Review</div>
        </div>
    </div>
    <div class="charts-grid">
        <div class="chart-card">
            <h3>By Classification</h3>
            <div class="chart-container"><canvas id="clsChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>By Resource Type</h3>
            <div class="chart-container"><canvas id="typeChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>By Resource Group</h3>
            <div class="chart-container"><canvas id="rgChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>By Recommendation</h3>
            <div class="chart-container"><canvas id="recChart"></canvas></div>
        </div>
    </div>
    <div class="table-card">
        <h3>Findings ({len(findings)})</h3>
        <div class="filter-bar">
            <button class="filter-btn active" onclick="filterTbl('')">All</button>
            <button class="filter-btn" onclick="filterTbl('UNUSED')">Unused</button>
            <button class="filter-btn" onclick="filterTbl('IDLE')">Idle</button>
            <button class="filter-btn" onclick="filterTbl('ORPHANED')">Orphaned</button>
            <button class="filter-btn" onclick="filterTbl('REVIEW')">Review</button>
        </div>
        <table id="findingsTable" class="display" style="width:100%">
            <thead><tr><th>Status</th><th>Resource</th><th>Type</th><th>Resource Group</th><th>Reason</th><th>Action</th></tr></thead>
            <tbody>{findings_html}</tbody>
        </table>
    </div>
    <div class="footer">Generated by Azure Operations Agent &middot; {scan_date}</div>
</div>
<script>
Chart.defaults.color='#8b8fa3';Chart.defaults.borderColor='#2a2d3a';
new Chart(document.getElementById('clsChart'),{{type:'doughnut',data:{{labels:{json.dumps(list(classification_counts.keys()))},datasets:[{{data:{json.dumps(list(classification_counts.values()))},backgroundColor:{json.dumps([cls_colors.get(k,'#95a5a6') for k in classification_counts.keys()])},borderWidth:0}}]}},options:{{responsive:true,cutout:'60%',plugins:{{legend:{{position:'right'}}}}}}}});
new Chart(document.getElementById('typeChart'),{{type:'bar',data:{{labels:{json.dumps(list(type_counts.keys()))},datasets:[{{data:{json.dumps(list(type_counts.values()))},backgroundColor:'#6366f1',borderRadius:6,barThickness:20}}]}},options:{{responsive:true,indexAxis:'y',plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('rgChart'),{{type:'bar',data:{{labels:{json.dumps(list(rg_counts.keys()))},datasets:[{{data:{json.dumps(list(rg_counts.values()))},backgroundColor:'#a78bfa',borderRadius:6,barThickness:20}}]}},options:{{responsive:true,indexAxis:'y',plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('recChart'),{{type:'doughnut',data:{{labels:{json.dumps(list(recommendation_counts.keys()))},datasets:[{{data:{json.dumps(list(recommendation_counts.values()))},backgroundColor:['#e74c3c','#f39c12','#3498db','#2ecc71','#9b59b6','#95a5a6'],borderWidth:0}}]}},options:{{responsive:true,cutout:'60%',plugins:{{legend:{{position:'right'}}}}}}}});
let tbl=$('#findingsTable').DataTable({{pageLength:25,order:[[0,'asc']]}});
function filterTbl(c){{tbl.column(0).search(c).draw();document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');}}
</script>
</body>
</html>"""

    report_id = store_report(report_html)

    return {
        "report_id": report_id,
        "summary": {
            "total": len(findings),
            "unused": unused_count,
            "idle": idle_count,
            "orphaned": orphaned_count,
            "review": review_count,
        },
        "message": f"Report generated with {len(findings)} findings. The interactive report is available at report_id={report_id}",
    }


async def generate_cost_report(
    cost_data: Annotated[str, "JSON string of cost data. Can be the direct output from get_cost_by_resource_group, get_cost_by_service, get_cost_by_resource, or get_cost_summary. Also accepts a plain list of objects with cost and label fields."],
    title: Annotated[str, "Report title"] = "Azure Cost Report",
    group_by: Annotated[str, "What the cost is grouped by: resource_group, service, resource, daily"] = "resource_group",
    subscription_id: Annotated[str, "Azure subscription ID for the header"] = "",
) -> dict:
    """
    Generate a self-contained interactive HTML cost report with charts and tables.
    Returns a report_id that the UI uses to fetch and render the report.
    Use this to visualize cost breakdowns by resource group, service, resource, or over time.

    Accepts data directly from cost tools (get_cost_by_resource_group, etc.) or
    custom data. Auto-detects field names for cost values and labels.
    """
    try:
        data = json.loads(cost_data) if isinstance(cost_data, str) else cost_data
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in cost_data"}

    if not isinstance(data, list):
        if isinstance(data, dict):
            data = data.get("data", data.get("data_points", []))
            if not isinstance(data, list):
                data = []
        else:
            data = []

    if not data:
        return {"error": "No cost data provided"}

    # Auto-detect the cost field and label field from the actual data keys
    sample = data[0] if data else {}
    sample_keys_lower = {k.lower(): k for k in sample.keys()}

    # Find cost field: try Cost, cost, totalCost, Amount, amount
    cost_field = None
    for candidate in ["Cost", "cost", "totalCost", "Amount", "amount", "PreTaxCost"]:
        if candidate in sample:
            cost_field = candidate
            break
    if not cost_field:
        for k, orig in sample_keys_lower.items():
            if "cost" in k or "amount" in k or "price" in k:
                cost_field = orig
                break
    if not cost_field:
        cost_field = "Cost"

    # Find currency field
    currency_field = None
    for candidate in ["Currency", "currency", "CurrencyCode"]:
        if candidate in sample:
            currency_field = candidate
            break
    currency = str(sample.get(currency_field, "USD")) if currency_field else "USD"

    # Find label field based on group_by, with fallback auto-detection
    preferred_label_keys = {
        "resource_group": ["ResourceGroupName", "resourceGroup", "ResourceGroup"],
        "service": ["ServiceName", "serviceName", "MeterCategory"],
        "resource": ["ResourceId", "resourceId", "ResourceName", "resourceName", "name"],
        "daily": ["UsageDate", "Date", "date", "BillingPeriod"],
    }
    label_field = None
    for candidate in preferred_label_keys.get(group_by, []):
        if candidate in sample:
            label_field = candidate
            break
    # Fallback: use the first non-cost, non-currency string field
    if not label_field:
        for k, v in sample.items():
            if k != cost_field and k != currency_field and isinstance(v, str):
                label_field = k
                break
    if not label_field:
        label_field = "Unknown"

    total_cost = sum(float(entry.get(cost_field, 0) or 0) for entry in data)

    def _label(entry):
        val = entry.get(label_field, "Unknown")
        s = str(val)
        # For ResourceId, extract just the resource name from the full path
        if "/" in s and len(s) > 60:
            s = s.rsplit("/", 1)[-1]
        return s[:50]

    labels = [_label(entry) for entry in data]
    costs = [round(float(entry.get(cost_field, 0) or 0), 2) for entry in data]

    # Table rows
    table_rows = []
    for i, entry in enumerate(data):
        label = _escape(_label(entry))
        cost = float(entry.get(cost_field, 0) or 0)
        curr = _escape(str(entry.get(currency_field, currency)) if currency_field else currency)
        pct = round((cost / total_cost * 100) if total_cost > 0 else 0, 1)
        table_rows.append(f"<tr><td>{label}</td><td>${cost:,.2f}</td><td>{curr}</td><td>{pct}%</td></tr>")

    table_html = "\n".join(table_rows)
    chart_type = "line" if group_by == "daily" else "bar"
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
    :root {{ --bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e4e6eb;--text-muted:#8b8fa3;--accent:#6366f1; }}
    * {{ margin:0;padding:0;box-sizing:border-box; }}
    body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6; }}
    .container {{ max-width:1400px;margin:0 auto;padding:20px; }}
    .header {{ background:linear-gradient(135deg,#1e1b4b 0%,#312e81 50%,#1e3a5f 100%);border-radius:16px;padding:32px;margin-bottom:24px;border:1px solid var(--border); }}
    .header h1 {{ font-size:28px;font-weight:700;margin-bottom:8px;background:linear-gradient(90deg,#818cf8,#6ee7b7);-webkit-background-clip:text;-webkit-text-fill-color:transparent; }}
    .header-meta {{ display:flex;gap:24px;color:var(--text-muted);font-size:14px;flex-wrap:wrap; }}
    .summary-grid {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px; }}
    .summary-card {{ background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center; }}
    .summary-card .number {{ font-size:36px;font-weight:800;line-height:1.1;color:var(--accent); }}
    .summary-card .label {{ font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-top:4px; }}
    .chart-card {{ background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:24px; }}
    .chart-card h3 {{ font-size:16px;font-weight:600;margin-bottom:16px; }}
    .table-card {{ background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;overflow-x:auto; }}
    .table-card h3 {{ font-size:18px;font-weight:600;margin-bottom:16px; }}
    table.dataTable {{ border-collapse:collapse!important;width:100%!important; }}
    table.dataTable thead th {{ background:#252836!important;color:var(--text)!important;border-bottom:2px solid var(--accent)!important;padding:12px 16px!important;font-size:12px;text-transform:uppercase; }}
    table.dataTable tbody td {{ padding:10px 16px!important;border-bottom:1px solid var(--border)!important;color:var(--text)!important;font-size:13px;background:transparent!important; }}
    table.dataTable tbody tr:hover td {{ background:rgba(99,102,241,0.08)!important; }}
    .dataTables_wrapper .dataTables_filter input {{ background:var(--bg)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:6px;padding:6px 12px; }}
    .dataTables_wrapper .dataTables_length select {{ background:var(--bg)!important;border:1px solid var(--border)!important;color:var(--text)!important; }}
    .dataTables_wrapper .dataTables_info,.dataTables_wrapper .dataTables_paginate {{ color:var(--text-muted)!important; }}
    .dataTables_wrapper .dataTables_paginate .paginate_button {{ color:var(--text-muted)!important;border:1px solid var(--border)!important;border-radius:4px!important; }}
    .dataTables_wrapper .dataTables_paginate .paginate_button.current {{ background:var(--accent)!important;color:white!important; }}
    .footer {{ text-align:center;padding:24px;color:var(--text-muted);font-size:12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{_escape(title)}</h1>
        <div class="header-meta">
            <span>&#128197; {scan_date}</span>
            {'<span>&#128273; ' + _escape(subscription_id) + '</span>' if subscription_id else ''}
        </div>
    </div>
    <div class="summary-grid">
        <div class="summary-card">
            <div class="number">${total_cost:,.2f}</div><div class="label">Total Cost ({currency})</div>
        </div>
        <div class="summary-card">
            <div class="number">{len(data)}</div><div class="label">Categories</div>
        </div>
    </div>
    <div class="chart-card">
        <h3>Cost Breakdown</h3>
        <canvas id="costChart" style="max-height:400px"></canvas>
    </div>
    <div class="table-card">
        <h3>Cost Details</h3>
        <table id="costTable" class="display" style="width:100%">
            <thead><tr><th>{_escape(group_by.replace('_',' ').title())}</th><th>Cost</th><th>Currency</th><th>% of Total</th></tr></thead>
            <tbody>{table_html}</tbody>
        </table>
    </div>
    <div class="footer">Generated by Azure Operations Agent &middot; {scan_date}</div>
</div>
<script>
Chart.defaults.color='#8b8fa3';Chart.defaults.borderColor='#2a2d3a';
new Chart(document.getElementById('costChart'),{{type:'{chart_type}',data:{{labels:{json.dumps(labels)},datasets:[{{label:'Cost ({currency})',data:{json.dumps(costs)},backgroundColor:'#6366f1',borderColor:'#818cf8',borderRadius:6,barThickness:20,fill:{'true' if chart_type == 'line' else 'false'}}}]}},options:{{responsive:true,{("indexAxis:'y'," if chart_type == "bar" and len(data) > 5 else "")}plugins:{{legend:{{display:false}}}}}}}});
$('#costTable').DataTable({{pageLength:25,order:[[1,'desc']]}});
</script>
</body>
</html>"""

    report_id = store_report(report_html)

    return {
        "report_id": report_id,
        "summary": {
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "categories": len(data),
        },
        "message": f"Cost report generated. Total cost: ${total_cost:,.2f} {currency} across {len(data)} categories. The interactive report is available at report_id={report_id}",
    }


async def generate_dashboard_report(
    resource_summary: Annotated[str, "JSON string with resource summary data (by_type, by_location, by_resource_group, total_resources)"],
    cost_summary: Annotated[str | None, "JSON string with cost summary (total_cost, currency, data points)"] = None,
    health_data: Annotated[str | None, "JSON string with resource health statuses"] = None,
    title: Annotated[str, "Dashboard title"] = "Azure Operations Dashboard",
    subscription_id: Annotated[str, "Azure subscription ID"] = "",
) -> dict:
    """
    Generate a comprehensive dashboard HTML report combining resource inventory,
    cost overview, and health status into a single interactive view.
    This is the main dashboard visualization for the Azure Operations Agent.
    """
    try:
        resources = json.loads(resource_summary) if isinstance(resource_summary, str) else resource_summary
    except json.JSONDecodeError:
        resources = {}

    cost = None
    if cost_summary:
        try:
            cost = json.loads(cost_summary) if isinstance(cost_summary, str) else cost_summary
        except json.JSONDecodeError:
            pass

    health = None
    if health_data:
        try:
            health = json.loads(health_data) if isinstance(health_data, str) else health_data
        except json.JSONDecodeError:
            pass

    total = resources.get("total_resources", 0)
    by_type = resources.get("by_type", [])
    by_location = resources.get("by_location", [])
    by_rg = resources.get("by_resource_group", [])

    type_labels = [t.get("type", "").split("/")[-1] for t in by_type[:15]]
    type_values = [t.get("count_", 0) for t in by_type[:15]]
    loc_labels = [l.get("location", "") for l in by_location[:10]]
    loc_values = [l.get("count_", 0) for l in by_location[:10]]

    cost_total = cost.get("total_cost", 0) if cost else 0
    cost_currency = cost.get("currency", "USD") if cost else "USD"

    # Health summary
    health_available = 0
    health_unavailable = 0
    health_degraded = 0
    if health and "statuses" in health:
        for s in health["statuses"]:
            state = s.get("availability_state", "").lower()
            if state == "available":
                health_available += 1
            elif state == "unavailable":
                health_unavailable += 1
            else:
                health_degraded += 1

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    dashboard_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
    :root {{ --bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e4e6eb;--text-muted:#8b8fa3;--accent:#6366f1; }}
    * {{ margin:0;padding:0;box-sizing:border-box; }}
    body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6; }}
    .container {{ max-width:1400px;margin:0 auto;padding:20px; }}
    .header {{ background:linear-gradient(135deg,#1e1b4b 0%,#312e81 50%,#1e3a5f 100%);border-radius:16px;padding:32px;margin-bottom:24px;border:1px solid var(--border); }}
    .header h1 {{ font-size:28px;font-weight:700;margin-bottom:8px;background:linear-gradient(90deg,#818cf8,#6ee7b7);-webkit-background-clip:text;-webkit-text-fill-color:transparent; }}
    .header-meta {{ display:flex;gap:24px;color:var(--text-muted);font-size:14px;flex-wrap:wrap; }}
    .kpi-grid {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px; }}
    .kpi {{ background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center; }}
    .kpi .number {{ font-size:32px;font-weight:800;line-height:1.1; }}
    .kpi .label {{ font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-top:4px; }}
    .kpi-accent .number {{ color:var(--accent); }}
    .kpi-green .number {{ color:#2ecc71; }}
    .kpi-red .number {{ color:#e74c3c; }}
    .kpi-yellow .number {{ color:#f39c12; }}
    .kpi-blue .number {{ color:#3498db; }}
    .charts-grid {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-bottom:24px; }}
    .chart-card {{ background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px; }}
    .chart-card h3 {{ font-size:16px;font-weight:600;margin-bottom:16px; }}
    .footer {{ text-align:center;padding:24px;color:var(--text-muted);font-size:12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{_escape(title)}</h1>
        <div class="header-meta">
            <span>&#128197; {scan_date}</span>
            {'<span>&#128273; ' + _escape(subscription_id) + '</span>' if subscription_id else ''}
        </div>
    </div>
    <div class="kpi-grid">
        <div class="kpi kpi-accent"><div class="number">{total}</div><div class="label">Total Resources</div></div>
        <div class="kpi kpi-blue"><div class="number">{len(by_rg)}</div><div class="label">Resource Groups</div></div>
        <div class="kpi kpi-green"><div class="number">{len(by_location)}</div><div class="label">Regions</div></div>
        {'<div class="kpi kpi-yellow"><div class="number">$' + f'{cost_total:,.0f}</div><div class="label">Cost ({cost_currency})</div></div>' if cost else ''}
        {'<div class="kpi kpi-green"><div class="number">' + str(health_available) + '</div><div class="label">Healthy</div></div>' if health else ''}
        {'<div class="kpi kpi-red"><div class="number">' + str(health_unavailable) + '</div><div class="label">Unhealthy</div></div>' if health and health_unavailable else ''}
    </div>
    <div class="charts-grid">
        <div class="chart-card">
            <h3>Resources by Type</h3>
            <canvas id="typeChart" style="max-height:350px"></canvas>
        </div>
        <div class="chart-card">
            <h3>Resources by Region</h3>
            <canvas id="locChart" style="max-height:350px"></canvas>
        </div>
    </div>
    <div class="footer">Generated by Azure Operations Agent &middot; {scan_date}</div>
</div>
<script>
Chart.defaults.color='#8b8fa3';Chart.defaults.borderColor='#2a2d3a';
new Chart(document.getElementById('typeChart'),{{type:'bar',data:{{labels:{json.dumps(type_labels)},datasets:[{{data:{json.dumps(type_values)},backgroundColor:'#6366f1',borderRadius:6,barThickness:20}}]}},options:{{responsive:true,indexAxis:'y',plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('locChart'),{{type:'doughnut',data:{{labels:{json.dumps(loc_labels)},datasets:[{{data:{json.dumps(loc_values)},backgroundColor:['#6366f1','#818cf8','#a78bfa','#c4b5fd','#3498db','#2ecc71','#f39c12','#e74c3c','#9b59b6','#1abc9c'],borderWidth:0}}]}},options:{{responsive:true,cutout:'55%',plugins:{{legend:{{position:'right'}}}}}}}});
</script>
</body>
</html>"""

    report_id = store_report(dashboard_html)

    return {
        "report_id": report_id,
        "summary": {
            "total_resources": total,
            "resource_groups": len(by_rg),
            "regions": len(by_location),
            "total_cost": cost_total if cost else None,
        },
        "message": f"Dashboard generated with {total} resources across {len(by_rg)} resource groups and {len(by_location)} regions. The interactive dashboard is available at report_id={report_id}",
    }
