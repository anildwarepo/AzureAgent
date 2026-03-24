# mcp_server.py
import os
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
import httpx
from fastmcp.server.dependencies import get_access_token
from dotenv import load_dotenv

load_dotenv() 

TENANT_ID = os.environ["AZURE_TENANT_ID"]
MCP_SERVER_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]  # 6bae24d4-f6b0-41f0-af76-25828f6bbb76

# IMPORTANT:
# Set this to whatever your OBO token's `aud` claim actually is.
# Your backend log shows aud == the GUID, so use the GUID.
AUDIENCE = MCP_SERVER_CLIENT_ID
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


azure_jwt_verifier = JWTVerifier(
    jwks_uri=f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys",
    issuer=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    audience=AUDIENCE,
    required_scopes=["read"],   # Entra puts unprefixed scope names in `scp`
)


atlassian_jwt_verifier_1 = JWTVerifier(
    jwks_uri=f"https://auth.atlassian.com/.well-known/jwks.json",
    issuer=f"https://auth.atlassian.com",
    audience="y3FFAJeGF3IgqudO1pXPYzNBqOh8zQQ0",
    required_scopes=["read"],   # Entra puts unprefixed scope names in `scp`
)


atlassian_jwt_verifier = JWTVerifier(
    jwks_uri="https://auth.atlassian.com/.well-known/jwks.json",
    issuer="https://auth.atlassian.com",
    audience="y3FFAJeGF3IgqudO1pXPYzNBqOh8zQQ0",
    required_scopes=None,  # <-- disable scope enforcement
)

#{"issuer":"https://auth.atlassian.com","authorization_endpoint":"https://auth.atlassian.com/authorize","token_endpoint":"https://auth.atlassian.com/oauth/token","device_authorization_endpoint":"https://auth.atlassian.com/oauth/device/code","userinfo_endpoint":"https://auth.atlassian.com/userinfo","mfa_challenge_endpoint":"https://auth.atlassian.com/mfa/challenge","jwks_uri":"https://auth.atlassian.com/.well-known/jwks.json","registration_endpoint":"https://auth.atlassian.com/oidc/register","revocation_endpoint":"https://auth.atlassian.com/oauth/revoke","scopes_supported":["openid","profile","offline_access","name","given_name","family_name","nickname","email","email_verified","picture","created_at","identities","phone","address"],"response_types_supported":["code","token","id_token","code token","code id_token","token id_token","code token id_token"],"code_challenge_methods_supported":["S256"],"response_modes_supported":["query","fragment","form_post"],"subject_types_supported":["public"],"id_token_signing_alg_values_supported":["HS256","RS256"],"token_endpoint_auth_methods_supported":["client_secret_basic","client_secret_post"],"claims_supported":["aud","auth_time","created_at","email","email_verified","exp","family_name","given_name","iat","identities","iss","name","nickname","phone_number","picture","sub"],"request_uri_parameter_supported":false,"request_parameter_supported":false}

mcp = FastMCP(name="Azure Secured App", auth=atlassian_jwt_verifier)

@mcp.tool
async def get_user_info() -> dict:
    from fastmcp.server.dependencies import get_access_token
    token = get_access_token()
    c = token.claims
    return {
        "name": c.get("name"),
        "preferred_username": c.get("preferred_username") or c.get("upn"),
        "oid": c.get("oid"),
        "tid": c.get("tid"),
        "scp": c.get("scp"),
        "aud": c.get("aud"),
        "iss": c.get("iss"),
    }

import httpx

ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# optional cache: Atlassian "sub" -> chosen cloud_id
_cloud_cache: dict[str, str] = {}

async def _fetch_accessible_resources(atlassian_access_token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            ACCESSIBLE_RESOURCES_URL,
            headers={
                "Authorization": f"Bearer {atlassian_access_token}",
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

def _filter_jira_resources(resources: list[dict]) -> list[dict]:
    # Jira resources have jira-related scopes like read:jira-work, write:jira-work, etc.
    jira = []
    for r in resources:
        scopes = r.get("scopes", []) or []
        if any("jira" in s for s in scopes):
            jira.append(r)
    return jira

async def _probe_jira_cloud_id(atlassian_access_token: str, cloud_id: str) -> bool:
    # lightweight Jira endpoint; 200 means this cloud_id is a Jira tenant accessible with this token
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/serverInfo"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {atlassian_access_token}",
                "Accept": "application/json",
            },
        )
        if r.status_code == 200:
            return True
        if r.status_code == 401:
            # token invalid/expired -> don't keep trying others
            r.raise_for_status()
        return False

async def _resolve_jira_cloud_id(atlassian_access_token: str, sub: str | None = None) -> str:
    if sub and sub in _cloud_cache:
        return _cloud_cache[sub]

    resources = await _fetch_accessible_resources(atlassian_access_token)
    jira_resources = _filter_jira_resources(resources)
    if not jira_resources:
        raise RuntimeError(f"No Jira resources found. Accessible resources: {resources}")

    # If only one, use it
    if len(jira_resources) == 1:
        cloud_id = jira_resources[0]["id"]
        if sub:
            _cloud_cache[sub] = cloud_id
        return cloud_id

    # Multiple Jira sites: probe each and pick the first that responds like Jira
    for r in jira_resources:
        cid = r.get("id")
        if not cid:
            continue
        if await _probe_jira_cloud_id(atlassian_access_token, cid):
            if sub:
                _cloud_cache[sub] = cid
            return cid

    # Fallback: choose first Jira resource deterministically
    cloud_id = jira_resources[0]["id"]
    if sub:
        _cloud_cache[sub] = cloud_id
    return cloud_id



async def _jira_search_with_token(
    *,
    atlassian_access_token: str,
    cloud_id: str,
    jql: str,
    max_results: int = 10,
) -> dict:
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql"
    body = {
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "status", "issuetype", "priority", "created"],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {atlassian_access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        return r.json()

@mcp.tool
async def jira_list_issues(project_key: str = "SCRUM", max_results: int = 10) -> dict:
    token = get_access_token()  # Atlassian bearer token (in your Atlassian-auth MCP mode)
    claims = token.claims
    print("JIRA TOKEN CLAIMS:", claims)

    cloud_id = await _resolve_jira_cloud_id(token.token, sub=claims.get("sub"))

    jql = f"project = {project_key} ORDER BY created DESC"
    return await _jira_search_with_token(
        atlassian_access_token=token.token,
        cloud_id=cloud_id,
        jql=jql,
        max_results=max_results,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8000)
