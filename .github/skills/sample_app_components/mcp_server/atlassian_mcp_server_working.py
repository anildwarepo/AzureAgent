import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider

# The AzureProvider handles Azure's token format and validation
auth_provider = AzureProvider(
    client_id=os.getenv("AZURE_CLIENT_ID", ""),
    client_secret=os.getenv("AZURE_CLIENT_SECRET", ""),
    tenant_id=os.getenv("AZURE_TENANT_ID", ""),
    base_url=os.getenv("AZURE_AUTH_BASE_URL", "http://localhost:8000"),
    required_scopes=["read"],                 # At least one scope REQUIRED - name of scope from your App
    # identifier_uri defaults to api://{client_id}
    # identifier_uri="api://your-api-id",
    # Optional: request additional upstream scopes in the authorize request
    # additional_authorize_scopes=["User.Read", "offline_access", "openid", "email"],
    # redirect_path="/auth/callback"                  # Default value, customize if needed
    # base_authority="login.microsoftonline.us"      # For Azure Government (default: login.microsoftonline.com)
)

mcp = FastMCP(name="Azure Secured App", auth=auth_provider)

# Add a protected tool to test authentication
@mcp.tool
async def get_user_info() -> dict:
    """Returns information about the authenticated Azure user."""
    from fastmcp.server.dependencies import get_access_token
    
    token = get_access_token()
    # The AzureProvider stores user data in token claims
    return {
        "azure_id": token.claims.get("sub"),
        "email": token.claims.get("email"),
        "name": token.claims.get("name"),
        "job_title": token.claims.get("job_title"),
        "office_location": token.claims.get("office_location")
    }


#if __name__ == "__main__":
#    mcp.run(transport="streamable-http", port=8000)