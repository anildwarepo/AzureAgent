"""
Azure Operations Agent - JWT Token Validation

Validates Entra ID bearer tokens from the SPA. Extracts the Azure
management token for pass-through to MCP server tools.
"""

import os
import time
from typing import Any, Dict, Optional

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")  # SPA app registration client ID

# Azure Management audience — the SPA acquires tokens with this scope
MANAGEMENT_AUDIENCE = "https://management.azure.com"

# Lazily initialised JWKS client
_jwk_client: Optional[PyJWKClient] = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        if not TENANT_ID:
            raise HTTPException(status_code=500, detail="AZURE_TENANT_ID not configured")
        jwks_url = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
        _jwk_client = PyJWKClient(jwks_url)
    return _jwk_client


def decode_and_validate_bearer(auth_header: Optional[str]) -> Dict[str, Any]:
    """
    Validate a Bearer token from the Authorization header.

    Returns:
        Dict with 'token' (raw JWT), 'claims' (decoded payload), and
        'azure_token' (the token to pass to Azure management APIs).
    """
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer token")

    token = auth_header.split(" ", 1)[1].strip()

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token signing key")

    # All possible Azure AD issuer formats
    allowed_issuers = [
        f"https://sts.windows.net/{TENANT_ID}/",
        f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        f"https://login.microsoftonline.com/{TENANT_ID}/",
    ]

    last_err = None
    for issuer in allowed_issuers:
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=MANAGEMENT_AUDIENCE,
                issuer=issuer,
                options={
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                },
            )
            return {
                "token": token,
                "claims": claims,
                "azure_token": token,
                "user_oid": claims.get("oid", "unknown"),
                "user_name": claims.get("name", claims.get("preferred_username", "")),
                "expires_on": int(claims.get("exp", time.time() + 3600)),
            }
        except HTTPException:
            raise
        except Exception as e:
            last_err = e

    raise HTTPException(
        status_code=401,
        detail=f"Token validation failed: {type(last_err).__name__}: {last_err}",
    )
