"""
Azure Unused Resource Report Generator

Reads the unused_resources JSON report from scan_unused.py and generates
a self-contained interactive HTML dashboard with charts and filterable tables.

Usage:
    python generate_report.py unused_resources_2026_03_16_T131926.json
    python generate_report.py unused_resources_2026_03_16_T131926.json --output report.html
"""

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path


def load_report(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def escape(text: str) -> str:
    return html.escape(str(text)) if text else ""


def generate_html(report: dict, source_file: str) -> str:
    summary = report.get("summary", {})
    findings = report.get("findings", [])
    scan_date = report.get("scanDate", "")

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
        rec_label = "EMAIL OWNER" if rec == "DELETE" else rec
        recommendation_counts[rec_label] = recommendation_counts.get(rec_label, 0) + 1

    # Sort type_counts and rg_counts by value descending
    type_counts = dict(sorted(type_counts.items(), key=lambda x: x[1], reverse=True))
    rg_counts = dict(sorted(rg_counts.items(), key=lambda x: x[1], reverse=True)[:15])

    # Color scheme
    cls_colors = {
        "UNUSED": "#e74c3c",
        "IDLE": "#f39c12",
        "ORPHANED": "#e67e22",
        "REVIEW": "#3498db",
        "ACTIVE": "#2ecc71",
    }

    # Build findings table rows
    table_rows = []
    for f in sorted(findings, key=lambda x: x.get("classification", "")):
        cls = escape(f.get("classification", ""))
        name = escape(f.get("resourceName", ""))
        rtype = escape(f.get("resourceType", ""))
        rg = escape(f.get("resourceGroup", ""))
        reason = escape(f.get("reason", ""))
        rec = escape(f.get("recommendation", ""))
        created = escape(f.get("createdDate", ""))
        evidence = f.get("evidence", {})
        source = escape(evidence.get("source", ""))
        last_activity = escape(evidence.get("lastActivity", ""))
        resource_id = escape(f.get("resourceId", ""))

        rec_display = rec if rec != "DELETE" else "EMAIL OWNER"
        rec_css = "email" if rec == "DELETE" else rec.lower()

        badge_color = cls_colors.get(cls, "#95a5a6")
        table_rows.append(f"""<tr>
            <td><span class="badge" style="background:{badge_color}">{cls}</span></td>
            <td class="resource-name" title="{resource_id}">{name}</td>
            <td>{rtype}</td>
            <td>{rg}</td>
            <td>{reason}</td>
            <td><span class="rec-badge rec-{rec_css}"{' onclick="sendEmail(this)"' if rec_css == 'email' else ''}>{rec_display}</span></td>
            <td>{source}</td>
            <td>{last_activity}</td>
        </tr>""")

    findings_html = "\n".join(table_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Azure Unused Resource Report</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
    :root {{
        --bg: #0f1117;
        --card: #1a1d27;
        --border: #2a2d3a;
        --text: #e4e6eb;
        --text-muted: #8b8fa3;
        --accent: #6366f1;
        --unused: #e74c3c;
        --idle: #f39c12;
        --orphaned: #e67e22;
        --review: #3498db;
        --active: #2ecc71;
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: var(--bg);
        color: var(--text);
        line-height: 1.6;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

    /* Header */
    .header {{
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #1e3a5f 100%);
        border-radius: 16px;
        padding: 32px;
        margin-bottom: 24px;
        border: 1px solid var(--border);
    }}
    .header h1 {{
        font-size: 28px;
        font-weight: 700;
        margin-bottom: 8px;
        background: linear-gradient(90deg, #818cf8, #6ee7b7);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }}
    .header-meta {{
        display: flex;
        gap: 24px;
        color: var(--text-muted);
        font-size: 14px;
        flex-wrap: wrap;
    }}
    .header-meta span {{ display: flex; align-items: center; gap: 6px; }}

    /* Summary Cards */
    .summary-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
    }}
    .summary-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        transition: transform 0.2s, box-shadow 0.2s;
    }}
    .summary-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }}
    .summary-card .number {{
        font-size: 36px;
        font-weight: 800;
        line-height: 1.1;
    }}
    .summary-card .label {{
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: var(--text-muted);
        margin-top: 4px;
    }}
    .card-unused .number {{ color: var(--unused); }}
    .card-idle .number {{ color: var(--idle); }}
    .card-orphaned .number {{ color: var(--orphaned); }}
    .card-review .number {{ color: var(--review); }}
    .card-total .number {{ color: var(--accent); }}
    .card-scanned .number {{ color: #a78bfa; }}
    .card-skipped .number {{ color: var(--text-muted); }}

    /* Charts Section */
    .charts-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
        gap: 20px;
        margin-bottom: 24px;
    }}
    .chart-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 24px;
    }}
    .chart-card h3 {{
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 16px;
        color: var(--text);
    }}
    .chart-container {{
        position: relative;
        width: 100%;
        max-height: 300px;
    }}

    /* Findings Table */
    .table-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 24px;
        overflow-x: auto;
    }}
    .table-card h3 {{
        font-size: 18px;
        font-weight: 600;
        margin-bottom: 16px;
    }}
    .filter-bar {{
        display: flex;
        gap: 8px;
        margin-bottom: 16px;
        flex-wrap: wrap;
    }}
    .filter-btn {{
        padding: 6px 14px;
        border-radius: 20px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text-muted);
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
    }}
    .filter-btn:hover, .filter-btn.active {{
        background: var(--accent);
        color: white;
        border-color: var(--accent);
    }}

    /* DataTables styling override */
    table.dataTable {{ border-collapse: collapse !important; width: 100% !important; }}
    table.dataTable thead th {{
        background: #252836 !important;
        color: var(--text) !important;
        border-bottom: 2px solid var(--accent) !important;
        padding: 12px 16px !important;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    table.dataTable tbody td {{
        padding: 10px 16px !important;
        border-bottom: 1px solid var(--border) !important;
        color: var(--text) !important;
        font-size: 13px;
        background: transparent !important;
    }}
    table.dataTable tbody tr:hover td {{
        background: rgba(99, 102, 241, 0.08) !important;
    }}
    .dataTables_wrapper .dataTables_filter input {{
        background: var(--bg) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
        border-radius: 6px;
        padding: 6px 12px;
    }}
    .dataTables_wrapper .dataTables_length select {{
        background: var(--bg) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
        border-radius: 6px;
    }}
    .dataTables_wrapper .dataTables_info,
    .dataTables_wrapper .dataTables_paginate {{
        color: var(--text-muted) !important;
        padding-top: 12px !important;
    }}
    .dataTables_wrapper .dataTables_paginate .paginate_button {{
        color: var(--text-muted) !important;
        border: 1px solid var(--border) !important;
        border-radius: 4px !important;
        margin: 0 2px !important;
    }}
    .dataTables_wrapper .dataTables_paginate .paginate_button.current {{
        background: var(--accent) !important;
        color: white !important;
        border-color: var(--accent) !important;
    }}
    .dataTables_wrapper .dataTables_paginate .paginate_button:hover {{
        background: var(--accent) !important;
        color: white !important;
        border-color: var(--accent) !important;
    }}
    .dataTables_wrapper .dataTables_filter label,
    .dataTables_wrapper .dataTables_length label {{
        color: var(--text-muted) !important;
    }}

    /* Badges */
    .badge {{
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 11px;
        font-weight: 700;
        color: white;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .rec-badge {{
        display: inline-block;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 600;
    }}
    .rec-email {{ background: rgba(155,89,182,0.15); color: #9b59b6; cursor: pointer; }}
    .rec-email:hover {{ background: rgba(155,89,182,0.35); }}
    .rec-stop {{ background: rgba(243,156,18,0.15); color: #f39c12; }}
    .rec-resize {{ background: rgba(52,152,219,0.15); color: #3498db; }}
    .rec-review {{ background: rgba(52,152,219,0.15); color: #3498db; }}
    .rec-keep {{ background: rgba(46,204,113,0.15); color: #2ecc71; }}

    /* Email bar */
    .email-bar {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 16px;
        flex-wrap: wrap;
    }}
    .email-bar label {{
        font-size: 13px;
        color: var(--text-muted);
        white-space: nowrap;
    }}
    .email-bar input {{
        background: var(--bg);
        border: 1px solid var(--border);
        color: var(--text);
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 13px;
        width: 300px;
        outline: none;
        transition: border-color 0.2s;
    }}
    .email-bar input:focus {{
        border-color: var(--accent);
    }}
    .email-bar .email-hint {{
        font-size: 11px;
        color: var(--text-muted);
        font-style: italic;
    }}

    .resource-name {{
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 12px;
        max-width: 280px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }}

    /* Footer */
    .footer {{
        text-align: center;
        padding: 24px;
        color: var(--text-muted);
        font-size: 12px;
    }}
</style>
</head>
<body>
<div class="container">

    <!-- Header -->
    <div class="header">
        <h1>Azure Unused Resource Report</h1>
        <div class="header-meta">
            <span>&#128197; Scan Date: {escape(scan_date[:19] if scan_date else 'N/A')}</span>
            <span>&#128273; Subscription: {escape(report.get('subscriptionId', 'N/A'))}</span>
            <span>&#128196; Source: {escape(source_file)}</span>
            {f'<span>&#128736; azqr: {escape(report.get("azqrReportFile", ""))}</span>' if report.get("azqrReportFile") else ""}
        </div>
    </div>

    <!-- Summary Cards -->
    <div class="summary-grid">
        <div class="summary-card card-total">
            <div class="number">{summary.get('totalInventory', 0)}</div>
            <div class="label">Total Inventory</div>
        </div>
        <div class="summary-card card-scanned">
            <div class="number">{summary.get('totalScanned', 0)}</div>
            <div class="label">Scanned</div>
        </div>
        <div class="summary-card card-unused">
            <div class="number">{summary.get('unused', 0)}</div>
            <div class="label">Unused</div>
        </div>
        <div class="summary-card card-idle">
            <div class="number">{summary.get('idle', 0)}</div>
            <div class="label">Idle</div>
        </div>
        <div class="summary-card card-orphaned">
            <div class="number">{summary.get('orphaned', 0)}</div>
            <div class="label">Orphaned</div>
        </div>
        <div class="summary-card card-review">
            <div class="number">{summary.get('needsReview', 0)}</div>
            <div class="label">Needs Review</div>
        </div>
        <div class="summary-card card-skipped">
            <div class="number">{summary.get('skipped', 0)}</div>
            <div class="label">Skipped</div>
        </div>
    </div>

    <!-- Charts -->
    <div class="charts-grid">
        <div class="chart-card">
            <h3>Findings by Classification</h3>
            <div class="chart-container">
                <canvas id="classificationChart"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>Findings by Resource Type</h3>
            <div class="chart-container">
                <canvas id="typeChart"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>Findings by Resource Group</h3>
            <div class="chart-container">
                <canvas id="rgChart"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <h3>Recommended Actions</h3>
            <div class="chart-container">
                <canvas id="recChart"></canvas>
            </div>
        </div>
    </div>

    <!-- Findings Table -->
    <div class="table-card">
        <h3>All Findings ({len(findings)})</h3>
        <div class="email-bar">
            <label>&#9993; Owner Email:</label>
            <input type="email" id="ownerEmail" placeholder="owner@company.com" />
            <span class="email-hint">Click any EMAIL OWNER badge to compose an email to this address</span>
        </div>
        <div class="filter-bar">
            <button class="filter-btn active" onclick="filterTable('')">All</button>
            <button class="filter-btn" onclick="filterTable('UNUSED')" style="border-color:var(--unused)">Unused</button>
            <button class="filter-btn" onclick="filterTable('IDLE')" style="border-color:var(--idle)">Idle</button>
            <button class="filter-btn" onclick="filterTable('ORPHANED')" style="border-color:var(--orphaned)">Orphaned</button>
            <button class="filter-btn" onclick="filterTable('REVIEW')" style="border-color:var(--review)">Review</button>
        </div>
        <table id="findingsTable" class="display" style="width:100%">
            <thead>
                <tr>
                    <th>Status</th>
                    <th>Resource Name</th>
                    <th>Type</th>
                    <th>Resource Group</th>
                    <th>Reason</th>
                    <th>Action</th>
                    <th>Source</th>
                    <th>Last Activity</th>
                </tr>
            </thead>
            <tbody>
                {findings_html}
            </tbody>
        </table>
    </div>

    <div class="footer">
        Generated by Azure Unused Resource Scanner &middot; {datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    </div>
</div>

<script>
    // Chart.js defaults for dark theme
    Chart.defaults.color = '#8b8fa3';
    Chart.defaults.borderColor = '#2a2d3a';

    // Classification Donut
    new Chart(document.getElementById('classificationChart'), {{
        type: 'doughnut',
        data: {{
            labels: {json.dumps(list(classification_counts.keys()))},
            datasets: [{{
                data: {json.dumps(list(classification_counts.values()))},
                backgroundColor: {json.dumps([cls_colors.get(k, '#95a5a6') for k in classification_counts.keys()])},
                borderWidth: 0,
                hoverOffset: 8,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: true,
            cutout: '60%',
            plugins: {{
                legend: {{ position: 'right', labels: {{ padding: 16, usePointStyle: true, pointStyle: 'circle' }} }}
            }}
        }}
    }});

    // Resource Type Bar
    new Chart(document.getElementById('typeChart'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps(list(type_counts.keys()))},
            datasets: [{{
                label: 'Count',
                data: {json.dumps(list(type_counts.values()))},
                backgroundColor: '#6366f1',
                borderRadius: 6,
                barThickness: 20,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: true,
            indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ grid: {{ color: '#2a2d3a' }} }},
                y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }}
            }}
        }}
    }});

    // Resource Group Bar
    new Chart(document.getElementById('rgChart'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps(list(rg_counts.keys()))},
            datasets: [{{
                label: 'Count',
                data: {json.dumps(list(rg_counts.values()))},
                backgroundColor: '#a78bfa',
                borderRadius: 6,
                barThickness: 20,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: true,
            indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ grid: {{ color: '#2a2d3a' }} }},
                y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }}
            }}
        }}
    }});

    // Recommendations Donut
    const recColors = {{ 'EMAIL OWNER': '#9b59b6', STOP: '#f39c12', RESIZE: '#3498db', REVIEW: '#3498db', KEEP: '#2ecc71' }};
    const recChart = new Chart(document.getElementById('recChart'), {{
        type: 'doughnut',
        data: {{
            labels: {json.dumps(list(recommendation_counts.keys()))},
            datasets: [{{
                data: {json.dumps(list(recommendation_counts.values()))},
                backgroundColor: {json.dumps([{"DELETE": "#9b59b6", "STOP": "#f39c12", "RESIZE": "#3498db", "REVIEW": "#3498db", "KEEP": "#2ecc71"}.get(k, "#95a5a6") for k in recommendation_counts.keys()])},
                borderWidth: 0,
                hoverOffset: 8,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: true,
            cutout: '60%',
            onHover: function(evt, elements) {{
                evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            }},
            plugins: {{
                legend: {{ position: 'right', labels: {{ padding: 16, usePointStyle: true, pointStyle: 'circle' }} }}
            }}
        }}
    }});

    // Click handler on Recommended Actions donut
    document.getElementById('recChart').addEventListener('click', function(evt) {{
        const points = recChart.getElementsAtEventForMode(evt, 'nearest', {{ intersect: true }}, false);
        if (!points.length) return;
        const label = recChart.data.labels[points[0].index];
        if (label !== 'EMAIL OWNER') return;
        const email = document.getElementById('ownerEmail').value.trim();
        if (!email) {{ alert('Please enter an owner email address in the input box above the table.'); return; }}
        const rows = document.querySelectorAll('#findingsTable tbody tr');
        let items = [];
        rows.forEach(r => {{
            const cells = r.querySelectorAll('td');
            const action = cells[5]?.innerText || '';
            if (action.indexOf('EMAIL OWNER') === -1) return;
            items.push({{ name: cells[1]?.innerText, type: cells[2]?.innerText, rg: cells[3]?.innerText, status: cells[0]?.innerText, reason: cells[4]?.innerText }});
        }});
        if (!items.length) {{ alert('No EMAIL OWNER items found.'); return; }}
        let body = 'Hi,\\n\\nThe following Azure resources have been identified as unused and may need your attention:\\n\\n';
        items.forEach((it, i) => {{ body += (i+1) + '. ' + it.name + '\\n   Type: ' + it.type + '\\n   Resource Group: ' + it.rg + '\\n   Status: ' + it.status + '\\n   Reason: ' + it.reason + '\\n\\n'; }});
        body += 'Please review these resources and take appropriate action (delete, stop, or confirm they are still needed).\\n\\nThank you.';
        const subject = encodeURIComponent('Action Required: ' + items.length + ' Unused Azure Resources');
        window.open('mailto:' + email + '?subject=' + subject + '&body=' + encodeURIComponent(body), '_self');
    }});

    // DataTable
    let table = $('#findingsTable').DataTable({{
        pageLength: 25,
        order: [[0, 'asc']],
        language: {{
            search: "Search findings:",
            lengthMenu: "Show _MENU_ per page",
            info: "Showing _START_ to _END_ of _TOTAL_ findings",
        }}
    }});

    // Filter buttons
    function filterTable(classification) {{
        table.column(0).search(classification).draw();
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        event.target.classList.add('active');
    }}

    // Email owner
    function sendEmail(badge) {{
        const email = document.getElementById('ownerEmail').value.trim();
        if (!email) {{
            alert('Please enter an owner email address in the input box above the table.');
            return;
        }}
        const row = badge.closest('tr');
        const cells = row.querySelectorAll('td');
        const status = cells[0]?.innerText || '';
        const name = cells[1]?.innerText || '';
        const type = cells[2]?.innerText || '';
        const rg = cells[3]?.innerText || '';
        const reason = cells[4]?.innerText || '';

        const subject = encodeURIComponent(`Action Required: Unused Azure Resource - ${{name}}`);
        const body = encodeURIComponent(
            `Hi,\n\n` +
            `The following Azure resource has been identified as unused and may need your attention:\n\n` +
            `Resource Name: ${{name}}\n` +
            `Resource Type: ${{type}}\n` +
            `Resource Group: ${{rg}}\n` +
            `Status: ${{status}}\n` +
            `Reason: ${{reason}}\n\n` +
            `Please review this resource and take appropriate action (delete, stop, or confirm it is still needed).\n\n` +
            `This finding was generated by the Azure Unused Resource Scanner on {escape(scan_date[:10] if scan_date else 'N/A')}.\n\n` +
            `Thank you.`
        );
        window.open(`mailto:${{email}}?subject=${{subject}}&body=${{body}}`, '_self');
    }}
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate interactive HTML report from unused resource scan")
    parser.add_argument("input", help="Path to unused_resources JSON file")
    parser.add_argument("--output", help="Output HTML file path (default: <input>_report.html)")
    args = parser.parse_args()

    report = load_report(args.input)
    source_file = Path(args.input).name
    html_content = generate_html(report, source_file)

    output_path = args.output or str(Path(args.input).with_suffix(".html"))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Report generated: {output_path}")


if __name__ == "__main__":
    main()
