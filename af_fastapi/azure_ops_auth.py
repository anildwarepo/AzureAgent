"""
Azure Operations Agent - JWT Token Validation + OBO Flow

Validates Entra ID bearer tokens from the SPA (scoped to the backend API),
then exchanges them via the On-Behalf-Of (OBO) flow for Azure Management
tokens that can be forwarded to the MCP server.
"""

import os
import time
import logging
from typing import Any, Dict, Optional

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException
import msal

logger = logging.getLogger("uvicorn.error")

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")  # Home tenant (for JWKS fallback)
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")  # Backend app registration client ID
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")

# The SPA now requests tokens scoped to the backend API
# v1 tokens use api://{clientId} as audience, v2 tokens use just {clientId}
BACKEND_API_AUDIENCES = [f"api://{CLIENT_ID}", CLIENT_ID]

# The backend exchanges the SPA token for a management-scoped token via OBO
MANAGEMENT_SCOPE = "https://management.azure.com/.default"

# Lazily initialised JWKS client
_jwk_client: Optional[PyJWKClient] = None

# Per-tenant MSAL confidential clients for OBO (multi-tenant support)
_msal_apps: Dict[str, msal.ConfidentialClientApplication] = {}


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # Use the common endpoint for multi-tenant token validation
        jwks_url = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
        _jwk_client = PyJWKClient(jwks_url)
    return _jwk_client


def _get_msal_app(tenant_id: str) -> msal.ConfidentialClientApplication:
    """Return a per-tenant MSAL confidential client for OBO exchange."""
    if tenant_id not in _msal_apps:
        if not CLIENT_SECRET:
            raise HTTPException(status_code=500, detail="AZURE_CLIENT_SECRET not configured for OBO flow")
        _msal_apps[tenant_id] = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=CLIENT_SECRET,
        )
    return _msal_apps[tenant_id]


def _exchange_obo_token(user_assertion: str, tenant_id: str) -> str:
    """Exchange the SPA's access token for a management-scoped token via OBO."""
    app = _get_msal_app(tenant_id)
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=[MANAGEMENT_SCOPE],
    )
    if "access_token" in result:
        return result["access_token"]
    error = result.get("error_description", result.get("error", "Unknown OBO error"))
    logger.error("OBO token exchange failed: %s", error)
    raise HTTPException(status_code=401, detail=f"OBO token exchange failed: {error}")


def decode_and_validate_bearer(auth_header: Optional[str]) -> Dict[str, Any]:
    """
    Validate a Bearer token from the Authorization header.

    Returns:
        Dict with 'token' (raw JWT), 'claims' (decoded payload), and
        'azure_token' (the token to pass to Azure management APIs).
    """
    if not auth_header or not auth_header.lower().startswith("bearer "):
        logger.warning("Auth: No bearer token in Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer token")

    token = auth_header.split(" ", 1)[1].strip()

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
    except Exception as e:
        logger.error("Auth: Failed to get signing key: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token signing key")

    # Multi-tenant: accept tokens from any Azure AD tenant
    # First decode without issuer verification to extract the tenant ID
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        token_tid = unverified.get("tid", TENANT_ID)
        token_aud = unverified.get("aud", "")
        token_iss = unverified.get("iss", "")
        token_ver = unverified.get("ver", "")
        logger.info("Auth: Token tid=%s aud=%s iss=%s ver=%s (expected aud=%s)",
                     token_tid, token_aud, token_iss, token_ver, BACKEND_API_AUDIENCES)
    except Exception:
        token_tid = TENANT_ID

    # All possible Azure AD issuer formats for the token's tenant
    allowed_issuers = [
        f"https://sts.windows.net/{token_tid}/",
        f"https://login.microsoftonline.com/{token_tid}/v2.0",
        f"https://login.microsoftonline.com/{token_tid}/",
    ]

    last_err = None
    for issuer in allowed_issuers:
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=BACKEND_API_AUDIENCES,
                issuer=issuer,
                options={
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                },
            )

            # Exchange the SPA token for a management-scoped token via OBO
            azure_token = _exchange_obo_token(token, token_tid)

            return {
                "token": token,
                "claims": claims,
                "azure_token": azure_token,
                "user_oid": claims.get("oid", "unknown"),
                "user_name": claims.get("name", claims.get("preferred_username", "")),
                "expires_on": int(claims.get("exp", time.time() + 3600)),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.debug("Auth: issuer %s did not match: %s", issuer, e)
            last_err = e

    logger.error("Auth: All issuer checks failed. last_err=%s", last_err)
    raise HTTPException(
        status_code=401,
        detail=f"Token validation failed: {type(last_err).__name__}: {last_err}",
    )
