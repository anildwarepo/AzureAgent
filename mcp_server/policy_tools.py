"""
Azure Policy Tools

Provides MCP tools for querying Azure Policy assignments, definitions,
compliance state, and generating policy definitions with CLI commands.
"""

import json
import logging
from typing import Annotated

import httpx

from azure_auth import get_current_token

logger = logging.getLogger(__name__)

POLICY_API_VERSION = "2023-04-01"
POLICY_ASSIGNMENT_API_VERSION = "2024-05-01"
POLICY_STATE_API_VERSION = "2019-10-01"


async def list_policy_assignments(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_group: Annotated[str | None, "Optional resource group name to scope the query"] = None,
    resource_id: Annotated[str | None, "Optional full Azure resource ID to scope the query"] = None,
) -> dict:
    """
    List Azure Policy assignments at the subscription, resource group, or resource level.
    Returns policy assignment names, display names, enforcement mode, and linked policy definition IDs.
    """
    token = get_current_token()

    if resource_id:
        scope = resource_id
    elif resource_group:
        scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    else:
        scope = f"/subscriptions/{subscription_id}"

    url = f"https://management.azure.com{scope}/providers/Microsoft.Authorization/policyAssignments?api-version={POLICY_ASSIGNMENT_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    assignments = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    return {"error": "Not found", "message": f"Could not list policy assignments at scope '{scope}'. The subscription or resource group may not exist."}
                if resp.status_code == 403:
                    return {"error": "Access denied", "message": "You don't have permission to list policy assignments at this scope."}
                resp.raise_for_status()
                data = resp.json()

                for a in data.get("value", []):
                    props = a.get("properties", {})
                    assignments.append({
                        "id": a.get("id", ""),
                        "name": a.get("name", ""),
                        "display_name": props.get("displayName", ""),
                        "description": props.get("description", ""),
                        "enforcement_mode": props.get("enforcementMode", "Default"),
                        "policy_definition_id": props.get("policyDefinitionId", ""),
                        "scope": props.get("scope", ""),
                        "parameters": props.get("parameters", {}),
                        "not_scopes": props.get("notScopes", []),
                    })

                url = data.get("nextLink")
    except httpx.ConnectError:
        return {"error": "Connection failed", "message": "Could not connect to Azure Management API."}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "message": f"Azure API error: {e.response.text[:300]}"}

    return {"count": len(assignments), "assignments": assignments}


async def get_policy_definition(
    policy_definition_id: Annotated[str, "Full policy definition ID starting with /providers/ or /subscriptions/. Example: /providers/Microsoft.Authorization/policyDefinitions/<id>. Do NOT pass a bare name — always pass the full ID from the policy assignment's policy_definition_id field."],
) -> dict:
    """
    Get the details of a specific Azure Policy definition or policy set definition (initiative).
    Works for built-in, custom, and management-group-scoped definitions.
    Handles both policyDefinitions and policySetDefinitions.

    IMPORTANT: The policy_definition_id MUST be a full Azure resource ID starting with
    /providers/ or /subscriptions/. You can get this from list_policy_assignments output
    (the policy_definition_id field). Do NOT pass display names or bare names.
    """
    token = get_current_token()

    # Validate that the ID looks like a proper Azure resource path
    if not policy_definition_id.startswith("/"):
        return {
            "error": "Invalid policy_definition_id format",
            "policy_definition_id": policy_definition_id,
            "message": f"'{policy_definition_id}' is not a valid policy definition ID. "
                       "It must be a full Azure resource ID starting with /providers/ or /subscriptions/. "
                       "Use list_policy_assignments to get the correct policy_definition_id value.",
        }

    # Use the correct API version for policy set definitions (initiatives)
    is_set_definition = "policySetDefinitions" in policy_definition_id
    api_version = "2023-04-01" if is_set_definition else POLICY_API_VERSION

    url = f"https://management.azure.com{policy_definition_id}?api-version={api_version}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                # Management-group-scoped definitions may not be accessible from this token.
                # Try falling back to built-in definitions at the tenant level.
                fallback_attempted = False
                if "/managementGroups/" in policy_definition_id:
                    # Extract the definition name and try the built-in path
                    def_name = policy_definition_id.rsplit("/", 1)[-1]
                    def_type = "policySetDefinitions" if is_set_definition else "policyDefinitions"
                    fallback_url = f"https://management.azure.com/providers/Microsoft.Authorization/{def_type}/{def_name}?api-version={api_version}"
                    fallback_resp = await client.get(fallback_url, headers=headers)
                    if fallback_resp.status_code == 200:
                        resp = fallback_resp
                        fallback_attempted = True

                if not fallback_attempted and resp.status_code == 404:
                    return {
                        "error": "Policy definition not found",
                        "policy_definition_id": policy_definition_id,
                        "message": f"The policy definition was not found. "
                                   "It may be scoped to a management group you don't have access to, "
                                   "or it may have been deleted. Try list_policy_definitions to search by keyword.",
                    }

            if resp.status_code == 403:
                return {
                    "error": "Access denied",
                    "policy_definition_id": policy_definition_id,
                    "message": "You don't have permission to read this policy definition. "
                               "It may be scoped to a management group requiring elevated access.",
                }

            resp.raise_for_status()
            data = resp.json()

    except httpx.ConnectError:
        return {
            "error": "Connection failed",
            "policy_definition_id": policy_definition_id,
            "message": "Could not connect to Azure Management API. Check network connectivity.",
        }
    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP {e.response.status_code}",
            "policy_definition_id": policy_definition_id,
            "message": f"Azure API returned {e.response.status_code}: {e.response.text[:300]}",
        }

    props = data.get("properties", {})
    result = {
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "display_name": props.get("displayName", ""),
        "description": props.get("description", ""),
        "policy_type": props.get("policyType", ""),
        "mode": props.get("mode", ""),
        "metadata": props.get("metadata", {}),
        "parameters": props.get("parameters", {}),
    }

    if is_set_definition:
        result["policy_definitions"] = props.get("policyDefinitions", [])
        result["definition_type"] = "policySetDefinition"
    else:
        result["policy_rule"] = props.get("policyRule", {})
        result["definition_type"] = "policyDefinition"

    return result


async def get_policy_compliance(
    subscription_id: Annotated[str, "Azure subscription ID"],
    resource_group: Annotated[str | None, "Optional resource group name to scope the query"] = None,
    policy_assignment_name: Annotated[str | None, "Optional specific policy assignment name to filter"] = None,
) -> dict:
    """
    Get Azure Policy compliance summary showing compliant vs non-compliant resource counts.
    Can be scoped to a subscription or resource group and optionally filtered to a specific assignment.
    """
    token = get_current_token()

    if resource_group:
        scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    else:
        scope = f"/subscriptions/{subscription_id}"

    url = f"https://management.azure.com{scope}/providers/Microsoft.PolicyInsights/policyStates/latest/summarize?api-version={POLICY_STATE_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code == 404:
                return {"error": "Not found", "scope": scope, "message": "Could not get compliance data for this scope."}
            if resp.status_code == 403:
                return {"error": "Access denied", "scope": scope, "message": "You don't have permission to view compliance data."}
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        return {"error": "Connection failed", "message": "Could not connect to Azure Management API."}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "message": f"Azure API error: {e.response.text[:300]}"}

    summaries = data.get("value", [])
    results = []
    for summary in summaries:
        policy_assignments = summary.get("policyAssignments", [])
        for pa in policy_assignments:
            if policy_assignment_name and pa.get("policyAssignmentId", "").split("/")[-1] != policy_assignment_name:
                continue
            compliance = pa.get("results", {})
            results.append({
                "policy_assignment_id": pa.get("policyAssignmentId", ""),
                "policy_definition_id": pa.get("policyDefinitions", [{}])[0].get("policyDefinitionId", "") if pa.get("policyDefinitions") else "",
                "compliant_resources": compliance.get("resourceDetails", [{}])[0].get("count", 0) if compliance.get("resourceDetails") else 0,
                "non_compliant_resources": compliance.get("nonCompliantResources", 0),
                "non_compliant_policies": compliance.get("nonCompliantPolicies", 0),
            })

    overall = summaries[0].get("results", {}) if summaries else {}
    return {
        "scope": scope,
        "overall_non_compliant_resources": overall.get("nonCompliantResources", 0),
        "overall_non_compliant_policies": overall.get("nonCompliantPolicies", 0),
        "assignment_details": results,
    }


async def list_policy_definitions(
    subscription_id: Annotated[str, "Azure subscription ID"],
    filter_type: Annotated[str, "Filter: 'builtin' for built-in only, 'custom' for custom only, 'all' for both"] = "custom",
    search_keyword: Annotated[str | None, "Optional keyword to search in display names and descriptions"] = None,
) -> dict:
    """
    List Azure Policy definitions in the subscription. Can filter by type (builtin/custom)
    and search by keyword. Returns definition names, types, descriptions, and modes.
    """
    token = get_current_token()

    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Authorization/policyDefinitions?api-version={POLICY_API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    definitions = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    return {"error": "Not found", "message": f"Could not list policy definitions for subscription '{subscription_id}'. The subscription may not exist or the API version may not be supported."}
                if resp.status_code == 403:
                    return {"error": "Access denied", "message": "You don't have permission to list policy definitions."}
                resp.raise_for_status()
                data = resp.json()

                for d in data.get("value", []):
                    props = d.get("properties", {})
                    policy_type = props.get("policyType", "")

                    if filter_type == "builtin" and policy_type != "BuiltIn":
                        continue
                    if filter_type == "custom" and policy_type != "Custom":
                        continue

                    display_name = props.get("displayName", "")
                    description = props.get("description", "")

                    if search_keyword:
                        kw = search_keyword.lower()
                        if kw not in display_name.lower() and kw not in description.lower():
                            continue

                    definitions.append({
                        "id": d.get("id", ""),
                        "name": d.get("name", ""),
                        "display_name": display_name,
                        "description": description,
                        "policy_type": policy_type,
                        "mode": props.get("mode", ""),
                        "metadata_category": props.get("metadata", {}).get("category", ""),
                    })

                url = data.get("nextLink")
    except httpx.ConnectError:
        return {"error": "Connection failed", "message": "Could not connect to Azure Management API."}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "message": f"Azure API error: {e.response.text[:300]}"}

    return {"count": len(definitions), "definitions": definitions}


async def generate_policy_definition(
    policy_name: Annotated[str, "Short name for the policy (lowercase, hyphens allowed, e.g. 'deny-public-ip')"],
    display_name: Annotated[str, "Human-readable display name for the policy"],
    description: Annotated[str, "Description of what the policy enforces"],
    policy_rule: Annotated[str, "JSON string of the policy rule with 'if' and 'then' blocks"],
    parameters: Annotated[str, "JSON string of policy parameters (can be '{}' for no parameters)"] = "{}",
    mode: Annotated[str, "Policy mode: 'All' (evaluates all resource types) or 'Indexed' (only indexed types)"] = "All",
) -> dict:
    """
    Generate an Azure Policy definition JSON and the Azure CLI commands to create and assign it.
    Does NOT create the policy — it produces the definition and CLI commands for the user to review and execute.
    Use this when the user wants to create or author a new policy.
    """
    try:
        rule = json.loads(policy_rule) if isinstance(policy_rule, str) else policy_rule
        params = json.loads(parameters) if isinstance(parameters, str) else parameters
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}

    # Build the full policy definition
    policy_definition = {
        "properties": {
            "displayName": display_name,
            "description": description,
            "mode": mode,
            "policyType": "Custom",
            "parameters": params,
            "policyRule": rule,
        }
    }

    # Generate Azure CLI commands
    rule_json = json.dumps(rule, indent=2)
    params_json = json.dumps(params, indent=2)

    cli_create_definition = (
        f'az policy definition create \\\n'
        f'  --name "{policy_name}" \\\n'
        f'  --display-name "{display_name}" \\\n'
        f'  --description "{description}" \\\n'
        f'  --mode "{mode}" \\\n'
        f"  --rules '{rule_json}' \\\n"
        f"  --params '{params_json}'"
    )

    cli_assign_subscription = (
        f'az policy assignment create \\\n'
        f'  --name "{policy_name}-assignment" \\\n'
        f'  --display-name "{display_name} Assignment" \\\n'
        f'  --policy "{policy_name}" \\\n'
        f'  --enforcement-mode Default'
    )

    cli_assign_rg = (
        f'az policy assignment create \\\n'
        f'  --name "{policy_name}-assignment" \\\n'
        f'  --display-name "{display_name} Assignment" \\\n'
        f'  --policy "{policy_name}" \\\n'
        f'  --scope "/subscriptions/{{subscription_id}}/resourceGroups/{{resource_group_name}}" \\\n'
        f'  --enforcement-mode Default'
    )

    return {
        "policy_name": policy_name,
        "policy_definition": policy_definition,
        "cli_commands": {
            "create_definition": cli_create_definition,
            "assign_to_subscription": cli_assign_subscription,
            "assign_to_resource_group": cli_assign_rg,
        },
        "notes": [
            "Review the policy definition and CLI commands before executing.",
            "Replace {subscription_id} and {resource_group_name} placeholders in the resource group assignment command.",
            "Use --enforcement-mode DoNotEnforce for audit-only mode.",
            "After creating, use 'az policy compliance scan' to trigger an evaluation.",
        ],
    }


async def generate_deny_public_ip_policy(
    resource_group: Annotated[str | None, "Optional resource group name to scope the assignment"] = None,
    subscription_id: Annotated[str | None, "Optional subscription ID for the assignment scope"] = None,
) -> dict:
    """
    Generate a ready-to-use Azure Policy that denies public IP address creation.
    Returns the policy definition JSON and Azure CLI commands to create and assign it.
    """
    policy_rule = {
        "if": {
            "field": "type",
            "equals": "Microsoft.Network/publicIPAddresses"
        },
        "then": {
            "effect": "deny"
        }
    }

    result = await generate_policy_definition(
        policy_name="deny-public-ip",
        display_name="Deny Public IP Addresses",
        description="This policy denies creation of public IP address resources to prevent public exposure of resources.",
        policy_rule=json.dumps(policy_rule),
        mode="All",
    )

    # Add resource-group-scoped command if RG is provided
    if resource_group and subscription_id:
        result["cli_commands"]["assign_to_specific_rg"] = (
            f'az policy assignment create \\\n'
            f'  --name "deny-public-ip-assignment" \\\n'
            f'  --display-name "Deny Public IP Addresses Assignment" \\\n'
            f'  --policy "deny-public-ip" \\\n'
            f'  --scope "/subscriptions/{subscription_id}/resourceGroups/{resource_group}" \\\n'
            f'  --enforcement-mode Default'
        )

    return result


async def generate_allowed_locations_policy(
    allowed_locations: Annotated[str, "JSON array of allowed Azure region names, e.g. '[\"eastus\", \"eastus2\"]'"],
    resource_group: Annotated[str | None, "Optional resource group name to scope the assignment"] = None,
    subscription_id: Annotated[str | None, "Optional subscription ID for the assignment scope"] = None,
) -> dict:
    """
    Generate a ready-to-use Azure Policy that restricts resource creation to specific Azure regions.
    Returns the policy definition JSON and Azure CLI commands to create and assign it.
    """
    try:
        locations = json.loads(allowed_locations) if isinstance(allowed_locations, str) else allowed_locations
    except json.JSONDecodeError:
        return {"error": "Invalid JSON for allowed_locations. Provide a JSON array like '[\"eastus\"]'"}

    policy_rule = {
        "if": {
            "allOf": [
                {
                    "field": "location",
                    "notIn": "[parameters('allowedLocations')]"
                },
                {
                    "field": "location",
                    "notEquals": "global"
                },
                {
                    "field": "type",
                    "notEquals": "Microsoft.AzureActiveDirectory/b2cDirectories"
                }
            ]
        },
        "then": {
            "effect": "deny"
        }
    }

    parameters = {
        "allowedLocations": {
            "type": "Array",
            "metadata": {
                "displayName": "Allowed Locations",
                "description": "The list of Azure regions where resources can be deployed.",
                "strongType": "location"
            },
            "defaultValue": locations
        }
    }

    result = await generate_policy_definition(
        policy_name="restrict-locations",
        display_name="Restrict Resource Locations",
        description=f"Restricts resource deployment to the following Azure regions: {', '.join(locations)}.",
        policy_rule=json.dumps(policy_rule),
        parameters=json.dumps(parameters),
        mode="Indexed",
    )

    # Add parameterized assignment command
    params_value_json = json.dumps({"allowedLocations": {"value": locations}})
    result["cli_commands"]["assign_with_locations"] = (
        f'az policy assignment create \\\n'
        f'  --name "restrict-locations-assignment" \\\n'
        f'  --display-name "Restrict Resource Locations Assignment" \\\n'
        f'  --policy "restrict-locations" \\\n'
        f"  --params '{params_value_json}' \\\n"
        f'  --enforcement-mode Default'
    )

    if resource_group and subscription_id:
        result["cli_commands"]["assign_to_specific_rg"] = (
            f'az policy assignment create \\\n'
            f'  --name "restrict-locations-assignment" \\\n'
            f'  --display-name "Restrict Resource Locations Assignment" \\\n'
            f'  --policy "restrict-locations" \\\n'
            f'  --scope "/subscriptions/{subscription_id}/resourceGroups/{resource_group}" \\\n'
            f"  --params '{params_value_json}' \\\n"
            f'  --enforcement-mode Default'
        )

    return result
