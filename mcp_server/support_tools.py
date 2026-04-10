"""
Azure Support Request Tools

Provides MCP tools for creating, listing, getting, and updating Azure support
tickets, as well as managing communications on tickets, using the Azure Support
REST API (Microsoft.Support resource provider, api-version 2024-04-01).
"""

import logging
import uuid
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)

SUPPORT_API_VERSION = "2024-04-01"
ARM_BASE = "https://management.azure.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# List support services & problem classifications (discovery helpers)
# ---------------------------------------------------------------------------

async def list_support_services() -> dict:
    """
    List all Azure support services and their IDs.
    Use the returned serviceId when creating a support ticket.
    """
    token = get_current_token()
    url = f"{ARM_BASE}/providers/Microsoft.Support/services?api-version={SUPPORT_API_VERSION}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def list_problem_classifications(
    service_id: Annotated[str, "Full service resource ID, e.g. /providers/Microsoft.Support/services/<guid>"],
) -> dict:
    """
    List problem classifications for a given support service.
    Use the returned problemClassificationId when creating a support ticket.
    """
    token = get_current_token()
    url = f"{ARM_BASE}{service_id}/problemClassifications?api-version={SUPPORT_API_VERSION}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# List support tickets
# ---------------------------------------------------------------------------

async def list_support_tickets(
    subscription_id: Annotated[str, "Azure subscription ID"],
    filter_expr: Annotated[str | None, "OData filter, e.g. \"status eq 'Open'\", \"createdDate ge 2024-01-01T00:00:00Z\""] = None,
    top: Annotated[int | None, "Max number of tickets to return (max 100)"] = 25,
) -> dict:
    """
    List support tickets for a subscription. Supports filtering by status,
    createdDate, serviceId, and problemClassificationId.
    """
    token = get_current_token()
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets?api-version={SUPPORT_API_VERSION}"

    if top is not None:
        url += f"&$top={min(top, 100)}"
    if filter_expr:
        url += f"&$filter={filter_expr}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Get support ticket details
# ---------------------------------------------------------------------------

async def get_support_ticket(
    subscription_id: Annotated[str, "Azure subscription ID"],
    ticket_name: Annotated[str, "Support ticket name"],
) -> dict:
    """
    Get detailed information about a specific support ticket.
    """
    token = get_current_token()
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets/{ticket_name}?api-version={SUPPORT_API_VERSION}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Create support ticket
# ---------------------------------------------------------------------------

async def create_support_ticket(
    subscription_id: Annotated[str, "Azure subscription ID"],
    title: Annotated[str, "Title of the support ticket"],
    description: Annotated[str, "Detailed description of the issue"],
    service_id: Annotated[str, "Service resource ID from list_support_services, e.g. /providers/Microsoft.Support/services/<guid>"],
    problem_classification_id: Annotated[str, "Problem classification ID from list_problem_classifications"],
    severity: Annotated[str, "Severity: minimal, moderate, critical, or highestcriticalimpact"] = "moderate",
    contact_first_name: Annotated[str, "Contact first name"] = "",
    contact_last_name: Annotated[str, "Contact last name"] = "",
    contact_email: Annotated[str, "Contact email address"] = "",
    contact_country: Annotated[str, "Contact country (ISO 3166-1 alpha-3)"] = "usa",
    contact_language: Annotated[str, "Preferred support language"] = "en-us",
    contact_timezone: Annotated[str, "Preferred timezone"] = "Pacific Standard Time",
    contact_method: Annotated[str, "Preferred contact method: email or phone"] = "email",
    resource_id: Annotated[str | None, "Azure resource ID for technical tickets"] = None,
    require_24x7: Annotated[bool, "Require 24x7 response"] = False,
    advanced_diagnostic_consent: Annotated[str, "Consent for diagnostics: Yes or No"] = "No",
) -> dict:
    """
    Create a new Azure support ticket. Requires service_id and problem_classification_id
    which can be discovered using list_support_services and list_problem_classifications.

    Supports technical, billing, subscription management, and quota tickets.
    """
    token = get_current_token()
    ticket_name = f"ticket-{uuid.uuid4().hex[:12]}"
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets/{ticket_name}?api-version={SUPPORT_API_VERSION}"

    body: dict = {
        "properties": {
            "title": title,
            "description": description,
            "serviceId": service_id,
            "problemClassificationId": problem_classification_id,
            "severity": severity,
            "advancedDiagnosticConsent": advanced_diagnostic_consent,
            "require24X7Response": require_24x7,
            "contactDetails": {
                "firstName": contact_first_name,
                "lastName": contact_last_name,
                "primaryEmailAddress": contact_email,
                "preferredContactMethod": contact_method,
                "preferredSupportLanguage": contact_language,
                "preferredTimeZone": contact_timezone,
                "country": contact_country,
            },
        }
    }

    if resource_id:
        body["properties"]["technicalTicketDetails"] = {"resourceId": resource_id}

    logger.info("create_support_ticket: PUT %s", url)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(url, headers=_headers(token), json=body)

        if resp.status_code in (401, 403):
            error_body = {}
            try:
                error_body = resp.json()
            except Exception:
                error_body = {"raw": resp.text}
            return {
                "status_code": resp.status_code,
                "error": f"HTTP {resp.status_code} — check permissions for Microsoft.Support on the subscription.",
                "details": error_body,
            }

        resp.raise_for_status()

        result: dict = {"status_code": resp.status_code}
        if resp.status_code == 202:
            result["message"] = "Support ticket creation accepted and is being processed."
            result["location"] = resp.headers.get("location", "")
            result["ticket_name"] = ticket_name
        else:
            result["data"] = resp.json()
            result["message"] = "Support ticket created successfully."
            result["ticket_name"] = ticket_name
        return result


# ---------------------------------------------------------------------------
# Update support ticket
# ---------------------------------------------------------------------------

async def update_support_ticket(
    subscription_id: Annotated[str, "Azure subscription ID"],
    ticket_name: Annotated[str, "Support ticket name"],
    severity: Annotated[str | None, "New severity: minimal, moderate, critical"] = None,
    status: Annotated[str | None, "New status: open or closed"] = None,
    contact_first_name: Annotated[str | None, "Updated first name"] = None,
    contact_last_name: Annotated[str | None, "Updated last name"] = None,
    contact_email: Annotated[str | None, "Updated email"] = None,
) -> dict:
    """
    Update severity, status, or contact details on an existing support ticket.
    Note: severity cannot be changed if an engineer is actively working on the ticket.
    """
    token = get_current_token()
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets/{ticket_name}?api-version={SUPPORT_API_VERSION}"

    body: dict = {}
    if severity:
        body["severity"] = severity
    if status:
        body["status"] = status

    contact_updates: dict = {}
    if contact_first_name:
        contact_updates["firstName"] = contact_first_name
    if contact_last_name:
        contact_updates["lastName"] = contact_last_name
    if contact_email:
        contact_updates["primaryEmailAddress"] = contact_email
    if contact_updates:
        body["contactDetails"] = contact_updates

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(url, headers=_headers(token), json=body)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# List communications on a ticket
# ---------------------------------------------------------------------------

async def list_ticket_communications(
    subscription_id: Annotated[str, "Azure subscription ID"],
    ticket_name: Annotated[str, "Support ticket name"],
    filter_expr: Annotated[str | None, "OData filter, e.g. \"communicationType eq 'web'\""] = None,
    top: Annotated[int | None, "Max number of communications to return (max 10)"] = 10,
) -> dict:
    """
    List all communications for a support ticket.
    Supports filtering by communicationType and createdDate.
    """
    token = get_current_token()
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets/{ticket_name}/communications?api-version={SUPPORT_API_VERSION}"

    if top is not None:
        url += f"&$top={min(top, 10)}"
    if filter_expr:
        url += f"&$filter={filter_expr}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Add communication to a ticket
# ---------------------------------------------------------------------------

async def add_ticket_communication(
    subscription_id: Annotated[str, "Azure subscription ID"],
    ticket_name: Annotated[str, "Support ticket name"],
    subject: Annotated[str, "Subject of the communication"],
    body_text: Annotated[str, "Body of the communication message"],
    sender: Annotated[str | None, "Sender email address (required for service principals)"] = None,
) -> dict:
    """
    Add a new communication (message) to an existing support ticket.
    """
    token = get_current_token()
    comm_name = f"comm-{uuid.uuid4().hex[:12]}"
    url = f"{ARM_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Support/supportTickets/{ticket_name}/communications/{comm_name}?api-version={SUPPORT_API_VERSION}"

    comm_body: dict = {
        "properties": {
            "subject": subject,
            "body": body_text,
        }
    }
    if sender:
        comm_body["properties"]["sender"] = sender

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.put(url, headers=_headers(token), json=comm_body)
        resp.raise_for_status()

        result: dict = {"status_code": resp.status_code}
        if resp.status_code == 202:
            result["message"] = "Communication creation accepted and is being processed."
            result["location"] = resp.headers.get("location", "")
        else:
            result["data"] = resp.json()
            result["message"] = "Communication added successfully."
        return result
