"""
Azure Authentication Helper

Provides a custom TokenCredential that wraps a bearer token passed from
the UI layer through the API layer. This allows the MCP server to
authenticate with Azure APIs using the user's Entra ID token.

The token is extracted from the Authorization header by ASGI middleware
and stored in a contextvars.ContextVar, so tool functions never need
a 'token' parameter — they call get_current_token() instead.
"""

import contextvars

from azure.core.credentials import AccessToken, TokenCredential

# ── Context var: holds the Azure bearer token for the current request ─────
_current_azure_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_azure_token", default=""
)


def get_current_token() -> str:
    """Return the Azure bearer token for the in-flight request."""
    return _current_azure_token.get()


def get_current_credential() -> "BearerTokenCredential":
    """Convenience: return a BearerTokenCredential from the current token."""
    return BearerTokenCredential(get_current_token())


class BearerTokenCredential(TokenCredential):
    """
    A TokenCredential that returns a pre-acquired bearer token.
    Used when the token is passed from the SPA through the API layer.
    """

    def __init__(self, token: str, expires_on: int = 0):
        self._token = token
        self._expires_on = expires_on

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        return AccessToken(self._token, self._expires_on)


class AzureTokenMiddleware:
    """
    ASGI middleware that extracts ``Authorization: Bearer <token>``
    from incoming HTTP requests and stores the token in a ContextVar
    so that downstream MCP tool functions can access it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            auth_value = headers.get(b"authorization", b"").decode()
            if auth_value.lower().startswith("bearer "):
                token = auth_value.split(" ", 1)[1].strip()
                _current_azure_token.set(token)

        await self.app(scope, receive, send)
