// Entra ID configuration for Azure Operations Agent SPA
//
// Replace clientId and authority with your app registration values.
// The SPA requests Azure Management tokens to pass through to the backend.

export const msalConfig = {
  auth: {
    clientId: "5fba2d1c-c368-41b2-8035-1a1ab06ce1b8",
    authority: "https://login.microsoftonline.com/150305b3-cc4b-46dd-9912-425678db1498",
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
};

// Azure Management scope — token is forwarded to backend → MCP → Azure APIs
export const azureManagementLoginRequest = {
  scopes: ["https://management.azure.com/user_impersonation"],
};