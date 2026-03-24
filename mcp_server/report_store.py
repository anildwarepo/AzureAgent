"""
Report Store

Server-side storage for generated HTML reports. Reports are stored in memory
and served via HTTP endpoints, preventing the LLM from echoing large HTML
blobs through the streaming chat channel.
"""

import uuid
from typing import Dict, Optional


_reports: Dict[str, str] = {}


def store_report(html: str) -> str:
    """Store an HTML report and return a unique report_id."""
    report_id = uuid.uuid4().hex[:12]
    _reports[report_id] = html
    return report_id


def get_report(report_id: str) -> Optional[str]:
    """Retrieve a stored HTML report by ID. Returns None if not found."""
    return _reports.get(report_id)
