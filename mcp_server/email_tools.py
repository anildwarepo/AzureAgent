"""
Azure Email Notification Tools

Provides MCP tools for sending email notifications about Azure resources.
Currently simulates email sending — logs the email payload and returns
a confirmation. In production, this would integrate with Azure Communication
Services, Microsoft Graph Send Mail API, or an SMTP relay.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)


async def _get_subscription_owner(subscription_id: str, token: str) -> dict:
    """
    Look up the subscription owner by fetching role assignments scoped to the
    subscription and finding the Owner role, then resolving the principal via
    Microsoft Graph.  Falls back to subscription display name info if Graph
    calls are not possible.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Fetch subscription info for display name
    sub_url = f"https://management.azure.com/subscriptions/{subscription_id}?api-version=2022-12-01"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(sub_url, headers=headers)
        resp.raise_for_status()
        sub_info = resp.json()

    display_name = sub_info.get("displayName", subscription_id)

    # Try to resolve Owner role assignments
    # Owner built-in role definition ID suffix
    owner_role_def = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
    ra_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Authorization/roleAssignments"
        f"?$filter=atScope()&api-version=2022-04-01"
    )

    owner_principal_id = None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(ra_url, headers=headers)
            resp.raise_for_status()
            ra_data = resp.json()

        for ra in ra_data.get("value", []):
            props = ra.get("properties", {})
            role_def_id = props.get("roleDefinitionId", "")
            if role_def_id.endswith(owner_role_def):
                owner_principal_id = props.get("principalId", "")
                break
    except Exception:
        logger.warning("Could not fetch role assignments for subscription %s", subscription_id)

    return {
        "subscription_display_name": display_name,
        "owner_principal_id": owner_principal_id or "unknown",
        "owner_email": f"owner-of-{display_name.lower().replace(' ', '-')}@contoso.com",
    }


async def send_resource_email(
    subscription_id: Annotated[str, "Azure subscription ID"],
    subject: Annotated[str, "Email subject line"],
    resource_details: Annotated[
        str,
        "JSON string with resource details to include in the email body. "
        "Can be a list of resources or a summary object.",
    ],
    additional_message: Annotated[
        str | None,
        "Optional extra message or context to include in the email body",
    ] = None,
) -> dict:
    """
    Send an email with resource details to the subscription owner.
    Automatically resolves the subscription owner via role assignments.

    Use this tool when the user wants to email, notify, or send resource
    information (unused resources, cost insights, health issues, etc.)
    to the subscription owner or stakeholders.

    **Currently simulated** — the email content is logged and returned
    but not actually delivered.
    """
    token = get_current_token()

    # Resolve subscription owner
    owner_info = await _get_subscription_owner(subscription_id, token)

    # Parse resource details
    try:
        resources = json.loads(resource_details)
    except json.JSONDecodeError:
        resources = resource_details

    # Build the email body
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    body_lines = [
        f"Azure Resource Notification — {owner_info['subscription_display_name']}",
        f"Generated: {timestamp}",
        "",
        f"Subject: {subject}",
        "",
    ]

    if additional_message:
        body_lines.append(additional_message)
        body_lines.append("")

    # Format resource details for the email
    if isinstance(resources, list):
        body_lines.append(f"Total resources: {len(resources)}")
        body_lines.append("")
        for idx, res in enumerate(resources, 1):
            if isinstance(res, dict):
                name = res.get("name", res.get("Name", "N/A"))
                rtype = res.get("type", res.get("Type", "N/A"))
                rg = res.get("resourceGroup", res.get("ResourceGroup", "N/A"))
                signal = res.get("signal", res.get("Signal", ""))
                classification = res.get("classification", res.get("Classification", ""))
                body_lines.append(f"  {idx}. {name}")
                body_lines.append(f"     Type: {rtype}")
                body_lines.append(f"     Resource Group: {rg}")
                if signal:
                    body_lines.append(f"     Signal: {signal}")
                if classification:
                    body_lines.append(f"     Classification: {classification}")
                body_lines.append("")
            else:
                body_lines.append(f"  {idx}. {res}")
    elif isinstance(resources, dict):
        body_lines.append(json.dumps(resources, indent=2))
    else:
        body_lines.append(str(resources))

    email_body = "\n".join(body_lines)

    email_payload = {
        "to": owner_info["owner_email"],
        "subject": subject,
        "body": email_body,
        "subscription_id": subscription_id,
        "subscription_name": owner_info["subscription_display_name"],
        "owner_principal_id": owner_info["owner_principal_id"],
        "timestamp": timestamp,
    }

    # ── SIMULATED SEND ──
    # In production, replace this with an actual email delivery call:
    # - Azure Communication Services Email
    # - Microsoft Graph sendMail API
    # - SMTP relay
    logger.info(
        "📧 SIMULATED EMAIL SENT\n  To: %s\n  Subject: %s\n  Resources: %s\n---\n%s",
        email_payload["to"],
        email_payload["subject"],
        len(resources) if isinstance(resources, list) else "1 payload",
        email_body,
    )

    return {
        "status": "simulated_sent",
        "message": (
            f"Email simulated successfully. In production this would be delivered "
            f"via Azure Communication Services or Microsoft Graph."
        ),
        "email_details": {
            "to": email_payload["to"],
            "subject": email_payload["subject"],
            "subscription": email_payload["subscription_name"],
            "owner_principal_id": email_payload["owner_principal_id"],
            "timestamp": email_payload["timestamp"],
            "resource_count": len(resources) if isinstance(resources, list) else 1,
        },
        "email_body_preview": email_body[:500],
    }


async def send_custom_email(
    subscription_id: Annotated[str, "Azure subscription ID"],
    to_email: Annotated[str, "Recipient email address"],
    subject: Annotated[str, "Email subject line"],
    body: Annotated[str, "Plain-text email body content"],
) -> dict:
    """
    Send a custom email to a specified recipient about Azure resources.
    Use this when the user provides a specific recipient email address
    rather than defaulting to the subscription owner.

    **Currently simulated** — the email content is logged and returned
    but not actually delivered.
    """
    token = get_current_token()

    # Fetch subscription display name for context
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    sub_url = f"https://management.azure.com/subscriptions/{subscription_id}?api-version=2022-12-01"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(sub_url, headers=headers)
        resp.raise_for_status()
        sub_info = resp.json()

    sub_name = sub_info.get("displayName", subscription_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    email_payload = {
        "to": to_email,
        "subject": subject,
        "body": body,
        "subscription_id": subscription_id,
        "subscription_name": sub_name,
        "timestamp": timestamp,
    }

    logger.info(
        "📧 SIMULATED CUSTOM EMAIL SENT\n  To: %s\n  Subject: %s\n---\n%s",
        to_email,
        subject,
        body,
    )

    return {
        "status": "simulated_sent",
        "message": (
            f"Custom email simulated successfully to {to_email}. "
            f"In production this would be delivered via Azure Communication Services "
            f"or Microsoft Graph."
        ),
        "email_details": {
            "to": to_email,
            "subject": subject,
            "subscription": sub_name,
            "timestamp": timestamp,
        },
        "email_body_preview": body[:500],
    }
