import os
import time
import secrets
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.dependencies import get_access_token
import requests


load_dotenv()

# -----------------------------
# Azure (protects MCP tools)
# -----------------------------
AZURE_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]          # your Entra app id
AZURE_CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
AZURE_TENANT_ID = os.environ["AZURE_TENANT_ID"]

# -----------------------------
# Atlassian (Jira) OAuth 2.0 (3LO)
# -----------------------------
ATLASSIAN_CLIENT_ID = os.environ["ATLASSIAN_CLIENT_ID"]
ATLASSIAN_CLIENT_SECRET = os.environ["ATLASSIAN_CLIENT_SECRET"]

# IMPORTANT: include offline_access if you want refresh tokens
ATLASSIAN_SCOPES = os.getenv(
    "ATLASSIAN_SCOPES",
    "read:jira-work write:jira-work manage:jira-webhook offline_access"
)

# Optional: help pick the correct Jira site if you have multiple
ATLASSIAN_SITE_URL_CONTAINS = os.getenv("ATLASSIAN_SITE_URL_CONTAINS", "").strip()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
ATLASSIAN_REDIRECT_URI = f"{BASE_URL}/atlassian/callback"

AUTH_BASE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# -----------------------------
# In-memory token store (demo)
# For real use: persist (Redis/DB) because multi-worker breaks memory.
# -----------------------------
@dataclass
class JiraSession:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float
    cloud_id: str
    site_url: Optional[str] = None

pending_state_to_user: Dict[str, str] = {}  # state -> azure_user_key
jira_sessions: Dict[str, JiraSession] = {}  # azure_user_key -> JiraSession


def _azure_user_key() -> str:
    """
    Identify the Azure user from the *MCP request context*.
    Prefer oid (AAD object id) when available; fall back to sub.
    """
    tok = get_access_token()
    claims = tok.claims
    return claims.get("oid") or claims.get("sub") or "unknown-user"


def _build_atlassian_authorize_url(state: str) -> str:
    params = {
        "audience": "api.atlassian.com",
        "client_id": ATLASSIAN_CLIENT_ID,
        "scope": ATLASSIAN_SCOPES,
        "redirect_uri": ATLASSIAN_REDIRECT_URI,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{AUTH_BASE_URL}?{urlencode(params)}"


async def _exchange_code_for_token(code: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": ATLASSIAN_CLIENT_ID,
                "client_secret": ATLASSIAN_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ATLASSIAN_REDIRECT_URI,
            },
        )
        r.raise_for_status()
        return r.json()


async def _refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": ATLASSIAN_CLIENT_ID,
                "client_secret": ATLASSIAN_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
        )
        r.raise_for_status()
        return r.json()


async def _get_accessible_resources(access_token: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            ACCESSIBLE_RESOURCES_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


def _pick_jira_resource(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Keep only resources that look Jira-related by scopes
    jira_candidates = [
        r for r in resources
        if any(s.startswith("read:jira") or s.startswith("write:jira") for s in r.get("scopes", []))
    ]

    if ATLASSIAN_SITE_URL_CONTAINS:
        jira_candidates = [
            r for r in jira_candidates
            if ATLASSIAN_SITE_URL_CONTAINS in (r.get("url") or "")
        ]

    if len(jira_candidates) == 1:
        return jira_candidates[0]

    if len(jira_candidates) == 0:
        raise ValueError(f"No Jira resources matched. Got: {resources}")

    # Multiple possible Jira sites
    sites = [{"name": r.get("name"), "url": r.get("url"), "id": r.get("id")} for r in jira_candidates]
    raise ValueError(
        "Multiple Jira sites available. Set ATLASSIAN_SITE_URL_CONTAINS to pick one. "
        f"Candidates: {sites}"
    )


async def _ensure_jira_session(user_key: str) -> JiraSession:
    sess = jira_sessions.get(user_key)
    if not sess:
        raise RuntimeError("Not connected to Jira. Run tool jira_connect first.")

    # Refresh if token is near expiry
    if time.time() > sess.expires_at - 30:
        if not sess.refresh_token:
            raise RuntimeError("Jira access token expired and no refresh_token. Re-run jira_connect.")
        token_data = await _refresh_access_token(sess.refresh_token)
        access_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 3600))
        new_refresh = token_data.get("refresh_token") or sess.refresh_token

        sess.access_token = access_token
        sess.refresh_token = new_refresh
        sess.expires_at = time.time() + expires_in

    return sess


# -----------------------------
# FastMCP server
# -----------------------------
auth_provider = AzureProvider(
    client_id=AZURE_CLIENT_ID,
    client_secret=AZURE_CLIENT_SECRET,
    tenant_id=AZURE_TENANT_ID,
    base_url=BASE_URL,
    required_scopes=["read"],  # (use your Azure scope name)
)

mcp = FastMCP(name="Azure Secured MCP + Jira", auth=auth_provider)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return PlainTextResponse("OK")


@mcp.custom_route("/atlassian/callback", methods=["GET"])
async def atlassian_callback(request: Request):
    """
    Atlassian redirects here with ?code=...&state=...
    We use state to map back to the Azure user who initiated jira_connect().
    """
    params = request.query_params
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error:
        return HTMLResponse(f"<h3>Atlassian OAuth error</h3><pre>{error}</pre>", status_code=400)

    if not code or not state or state not in pending_state_to_user:
        return HTMLResponse("<h3>Invalid state or missing code</h3>", status_code=400)

    user_key = pending_state_to_user.pop(state)

    try:
        token_data = await _exchange_code_for_token(code)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = int(token_data.get("expires_in", 3600))

        resources = await _get_accessible_resources(access_token)
        jira_resource = _pick_jira_resource(resources)

        jira_sessions[user_key] = JiraSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            cloud_id=jira_resource["id"],
            site_url=jira_resource.get("url"),
        )
    except Exception as e:
        return HTMLResponse(f"<h3>Failed to finish Jira connect</h3><pre>{e}</pre>", status_code=400)

    return RedirectResponse("http://localhost:3000/?jira=connected")


# -----------------------------
# MCP tools
# -----------------------------
@mcp.tool
async def jira_connect() -> dict:
    """
    Returns an Atlassian authorize URL. Open it in your browser once per user.
    """
    user_key = _azure_user_key()
    state = secrets.token_urlsafe(24)
    pending_state_to_user[state] = user_key

    return {
        "redirect_uri_registered_in_atlassian_app": ATLASSIAN_REDIRECT_URI,
        "authorize_url": _build_atlassian_authorize_url(state),
        "note": "Open authorize_url in a browser, approve, then rerun jira_search or other Jira tools."
    }


@mcp.tool
async def jira_search(jql: str = "ORDER BY created DESC", max_results: int = 10) -> dict:
    """
    Search Jira issues via the Atlassian 'ex/jira/{cloud_id}' gateway.
    """
    user_key = _azure_user_key()
    sess = await _ensure_jira_session(user_key)

    url = f"https://api.atlassian.com/ex/jira/{sess.cloud_id}/rest/api/3/search/jql"
    body = {
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "status", "issuetype", "priority", "created"]
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {sess.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        return r.json()



@mcp.tool
async def jira_list_issues(project_key: str = "SCRUM", max_results: int = 10) -> dict:
    """
    List latest issues from a Jira project (via stored Jira session for this Entra user).
    """
    jql = f"project = {project_key} ORDER BY created DESC"
    return await jira_search(jql=jql, max_results=max_results)



if __name__ == "__main__":
    # Custom routes live at /health and /atlassian/callback
    # MCP endpoint lives at /mcp (or /mcp/) depending on your client/path config.
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
