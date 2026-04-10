"""
Azure Operations Agent - Handoff Orchestrator

Uses Microsoft Agent Framework HandoffBuilder to orchestrate between
specialist agents:
  - Azure Ops Agent: monitoring, resources, cost, reports, emails
  - Policy Agent: Azure Policy queries, compliance, policy authoring

Streams responses back as NDJSON for the API layer.
"""

import asyncio
import json
import os
import re
import time
import logging
from dataclasses import dataclass, asdict, is_dataclass
from enum import Enum
from typing import List, Optional

from agent_framework import (
    AgentRunUpdateEvent,
    ChatAgent,
    ChatMessage,
    ChatMessageStore,
    ExecutorCompletedEvent,
    ExecutorInvokedEvent,
    HandoffBuilder,
    MCPStreamableHTTPTool,
    RequestInfoEvent,
    WorkflowOutputEvent,
    WorkflowRunState,
    WorkflowStartedEvent,
    WorkflowStatusEvent,
)
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger("uvicorn.error")

_aoai_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()

try:
    credential = DefaultAzureCredential()
    _aoai_api_key = ""
except Exception:
    credential = None

if not credential and not _aoai_api_key:
    logger.warning("Azure credentials not configured. Agent will be unavailable.")

MCP_SERVER_URL = os.getenv("MCP_ENDPOINT", "http://localhost:3001/mcp")

# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

TRIAGE_INSTRUCTIONS = """You are an Azure Operations Triage Agent. Your role is to route user questions to the appropriate specialist agent.

## Routing Rules
- **Quota listing/discovery questions** → call handoff_to_quota_list_agent
  Examples: list quotas, show quota limits, what quota do I have, available quota, check quota for H100/GPU/VM family
- **Quota increase/request questions** → call handoff_to_quota_request_agent
  Examples: increase quota, request more vCPUs, create quota request, raise quota limit
- **Support ticket questions** → call handoff_to_support_agent
  Examples: create support ticket, open a case, list my support tickets, check ticket status, add communication to ticket, file a support request
- **Policy questions** → call handoff_to_policy_agent
  Examples: policy assignments, policy compliance, create/author a policy, deny public IP, restrict regions/locations, governance, policy definitions
- **Everything else** → call handoff_to_azure_ops_agent
  Examples: resource listing, monitoring, cost analysis, metrics, health checks, reports, dashboards, emails, unused resources, VMs, tags, subscriptions

## Guidelines
1. Analyze the user's question and route to the correct agent on the FIRST turn.
2. If the question is ambiguous, lean toward azure_ops_agent unless it clearly mentions "policy", "compliance", "governance", "enforce", "restrict", "quota", "support ticket", or "support case".
3. Do NOT answer the question yourself — always hand off to a specialist.
4. Handle only one handoff per user question.
5. ALL of these are valid Azure Operations topics: resources, costs, monitoring, quotas, support tickets, support cases, and policy. Always route them — never refuse.
"""

AZURE_OPS_INSTRUCTIONS = """You are an Azure Operations Agent that helps users monitor, manage, query, and analyze their Azure resources.

You have access to tools provided by the Azure Operations MCP server. Use them to answer user questions about their Azure environment.

## Capabilities
- **Resource Discovery**: List and search resources using Azure Resource Graph (KQL queries)
- **Monitoring**: Query metrics, check resource health, review activity logs, detect idle resources
- **Resource Management**: Get resource details, manage VMs (start/stop/restart), update tags, list subscriptions and resource groups
- **Cost Analysis**: Get cost summaries, breakdowns by resource group/service/resource, check budgets, get Advisor recommendations
- **Reporting**: Generate interactive HTML reports and dashboards for resource findings, cost data, and overall environment overview
- **Email Notifications**: Send resource details via email to the subscription owner or a specified recipient

## Important Guidelines
1. When the user asks about their resources, start with get_resource_summary or list_resources to understand their environment
2. For cost questions, use cost management tools and offer to generate a cost report for visualization
3. For health/performance questions, use monitoring tools to check metrics and resource health
4. The user's Azure token is automatically injected via HTTP headers — you do not need to supply a token parameter to tools
5. **CRITICAL: When report tools return a report_id, include it in your response exactly like this: [report_id=XXXXX]. Do NOT output any HTML.**
6. For VM operations (start/stop), confirm with the user before executing
7. Use find_orphaned_resources and check_idle_resources for cost optimization analysis
8. When presenting data, offer to generate a visual report
9. NEVER include raw HTML, iframe tags, or srcdoc attributes in your response text.
10. When the user asks to send, email, or notify someone about resources, first gather the resource data, then call send_resource_email or send_custom_email.
11. If the question is about Azure Policy, call handoff_to_policy_agent to route to the policy specialist.
"""

POLICY_AGENT_INSTRUCTIONS = """You are an Azure Policy Agent that helps users understand, query, and author Azure Policy definitions and assignments.

You have access to Azure Policy tools provided by the Azure Operations MCP server.

## Capabilities
- **List Policy Assignments**: Show policies assigned at subscription or resource group scope
- **Get Policy Definitions**: Retrieve detailed policy definitions including rules and parameters
- **Check Compliance**: Show policy compliance status for a scope
- **List Policy Definitions**: Search built-in and custom policy definitions
- **Author Policies**: Generate custom policy definitions and CLI commands
- **Common Policies**: Generate ready-to-use "deny public IP" and "restrict locations" policies

## Important Guidelines
1. Start by understanding the scope — ask or infer subscription_id and resource_group from context.
2. When listing assignments, use list_policy_assignments to show what's currently enforced.
3. When asked about compliance, use get_policy_compliance to show compliant vs non-compliant counts.
4. When asked to CREATE or AUTHOR a policy:
   - Use generate_deny_public_ip_policy for "no public IP" requests
   - Use generate_allowed_locations_policy for "restrict regions" requests
   - Use generate_policy_definition for any other custom policy
   - ALWAYS show the generated policy definition JSON and the CLI commands clearly
   - Present the CLI commands in a code block for easy copy-paste
5. The user's Azure token is automatically injected — you do not need to supply a token parameter.
6. **CRITICAL: When report tools return a report_id, include it like this: [report_id=XXXXX].**
7. For combined policies (e.g., "deny public IP AND restrict to East US"), generate EACH policy separately and present both sets of CLI commands.
8. If the question is not about Azure Policy, call handoff_to_azure_ops_agent.
"""

QUOTA_LIST_AGENT_INSTRUCTIONS = """You are an Azure Quota Listing Agent that helps users discover current quota limits for Azure resource providers.

You have access to quota tools provided by the Azure Operations MCP server.

## Capabilities
- **List Quota Limits**: List all current quotas for a provider (Microsoft.Compute, Microsoft.Network, Microsoft.MachineLearningServices) in a specific region
- **Get Single Quota**: Get the current limit and usage for a specific quota resource

## Important Guidelines
1. When a user asks about quotas, ALWAYS use list_quota_limits first to discover the exact quota resource names.
2. The resource names must be EXACT — e.g. 'StandardNCadsH100v5Family' not 'h100' or 'standard_nh96ads_h100_v5'.
3. Common providers: Microsoft.Compute (VMs, vCPUs), Microsoft.Network (IPs, NICs, VNets), Microsoft.MachineLearningServices (ML compute).
4. Present results clearly showing: resource name, display name, current limit, whether quota increase is applicable.
5. Filter results to show only relevant quotas when the user asks about a specific VM family or resource type.
6. For GPU/HPC quota questions, search for the relevant family name in Microsoft.Compute quotas.
7. The user's Azure token is automatically injected — you do not need to supply a token parameter.
8. After listing quotas, if the user wants to increase one, call handoff_to_quota_request_agent with the exact resource name and current limit.
9. **CRITICAL: When report tools return a report_id, include it like this: [report_id=XXXXX].**
"""

QUOTA_REQUEST_AGENT_INSTRUCTIONS = """You are an Azure Quota Request Agent that submits quota increase requests on behalf of users.

You have access to quota tools provided by the Azure Operations MCP server.

## Capabilities
- **Create Quota Request**: Submit a quota increase request via the Azure Quota REST API
- **Check Request Status**: Get the status of a previously submitted quota request
- **List Request History**: View past quota requests and their outcomes

## Important Guidelines
1. ALWAYS call list_quota_limits or get_quota_limit FIRST to verify the exact resource_name before submitting a request.
   - Never guess the resource name. The Quota API requires exact names like 'StandardNCadsH100v5Family'.
   - If the user says 'H100', search the Compute quotas to find the matching family name.
2. Before submitting, confirm with the user:
   - The exact resource name and its current limit
   - The requested new limit value
   - The subscription, provider, and region
3. When calling create_quota_request, use the EXACT resource_name from the list/get response.
4. After submission, report the result:
   - If 200: Quota was updated immediately
   - If 202: Request was accepted and is being processed — provide the request ID for tracking
   - If 401/403: Explain the auth/permission issue from the error details
5. For tracking, use get_quota_request_status with the request ID from the create response.
6. The user's Azure token is automatically injected — you do not need to supply a token parameter.
7. Prerequisites for quota requests:
   - Microsoft.Quota resource provider must be registered on the subscription
   - User must have the 'Quota Request Operator' role
   - User must be MFA-authenticated (tenant policy may enforce this)
8. **CRITICAL: When report tools return a report_id, include it like this: [report_id=XXXXX].**
"""

SUPPORT_AGENT_INSTRUCTIONS = """You are an Azure Support Request Agent that helps users create, view, update, and manage Azure support tickets.

You have access to support tools provided by the Azure Operations MCP server.

## Capabilities
- **Discover Services**: List available Azure support services and their problem classifications
- **List Tickets**: List support tickets with filtering by status, date, service
- **Get Ticket Details**: Get full details of a specific support ticket
- **Create Tickets**: Create technical, billing, subscription management, or quota support tickets
- **Update Tickets**: Update severity, status, or contact details on existing tickets
- **Communications**: List and add communications/messages on support tickets

## Important Guidelines
1. To create a support ticket, you MUST first discover the correct serviceId and problemClassificationId:
   - Call list_support_services to get available services
   - Call list_problem_classifications with the chosen serviceId to get problem categories
   - Use the exact IDs returned from these calls
2. When creating a ticket, always gather from the user:
   - Title and detailed description of the issue
   - Contact information (name, email)
   - Severity level (minimal, moderate, critical)
   - For technical issues: the affected Azure resource ID
3. After creating a ticket, report the ticket name and ID for tracking.
4. When listing tickets, offer to filter by status (Open, Closed, Updating).
5. The user's Azure token is automatically injected — you do not need to supply a token parameter.
6. **CRITICAL: When report tools return a report_id, include it like this: [report_id=XXXXX].**
7. If the question is not about support tickets, hand off to the appropriate agent.
"""

# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

def _json_default(o):
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    return str(o)


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8")


@dataclass
class ResponseMessage:
    type: str
    delta: str | None = None
    message: str | None = None
    result: str | None = None
    report_id: str | None = None


def create_message_store():
    return ChatMessageStore()


# ---------------------------------------------------------------------------
# Orchestrated Agent
# ---------------------------------------------------------------------------

class AzureOpsOrchestrator:
    """
    Azure Operations Orchestrator using HandoffBuilder.
    Routes to specialist agents: ops agent and policy agent.
    """

    def __init__(self):
        self._access_token = None
        self._workflow = None

    async def _get_fresh_token(self):
        if _aoai_api_key:
            return None
        now = int(time.time())
        if self._access_token is None or (getattr(self._access_token, "expires_on", 0) - 60) <= now:
            self._access_token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        return self._access_token

    async def _build_workflow(self, azure_token: str = ""):
        """Build the handoff workflow with all specialist agents."""
        token = await self._get_fresh_token()

        # MCP tool configuration — pass user's Azure token to the MCP server
        mcp_headers = {}
        if azure_token:
            mcp_headers["Authorization"] = f"Bearer {azure_token}"

        azure_ops_mcp = MCPStreamableHTTPTool(
            name="azure_ops_mcp_server",
            url=MCP_SERVER_URL,
            headers=mcp_headers,
        )

        chat_client_factory = lambda: (
            AzureOpenAIChatClient(api_key=_aoai_api_key)
            if _aoai_api_key
            else AzureOpenAIChatClient(ad_token=token.token)
        )

        # Triage agent — routes to specialists
        triage_agent = ChatAgent(
            name="triage_agent",
            description="Triage agent that routes Azure questions to the appropriate specialist.",
            instructions=TRIAGE_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Azure Ops agent — monitoring, resources, cost, reports, email
        azure_ops_agent = ChatAgent(
            name="azure_ops_agent",
            description="Azure Operations Agent for monitoring, managing, querying, and analyzing Azure resources.",
            instructions=AZURE_OPS_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Policy agent — policy queries, compliance, authoring
        policy_agent = ChatAgent(
            name="policy_agent",
            description="Azure Policy Agent for querying policy assignments, compliance, and authoring policy definitions.",
            instructions=POLICY_AGENT_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Quota list agent — discover current quota limits
        quota_list_agent = ChatAgent(
            name="quota_list_agent",
            description="Azure Quota Listing Agent for discovering current quota limits by provider and region.",
            instructions=QUOTA_LIST_AGENT_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Quota request agent — submit quota increase requests
        quota_request_agent = ChatAgent(
            name="quota_request_agent",
            description="Azure Quota Request Agent for submitting quota increase requests.",
            instructions=QUOTA_REQUEST_AGENT_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Support agent — create, list, update support tickets and communications
        support_agent = ChatAgent(
            name="support_agent",
            description="Azure Support Request Agent for creating, viewing, updating support tickets and managing communications.",
            instructions=SUPPORT_AGENT_INSTRUCTIONS,
            chat_client=chat_client_factory(),
            tools=azure_ops_mcp,
        )

        # Build workflow with handoff pattern
        self._workflow = (
            HandoffBuilder(
                participants=[triage_agent, azure_ops_agent, policy_agent, quota_list_agent, quota_request_agent, support_agent]
            )
            .set_coordinator(triage_agent)
            .add_handoff(triage_agent, [azure_ops_agent, policy_agent, quota_list_agent, quota_request_agent, support_agent])
            .add_handoff(azure_ops_agent, [policy_agent, quota_list_agent, quota_request_agent, support_agent])
            .add_handoff(policy_agent, [azure_ops_agent])
            .add_handoff(quota_list_agent, [quota_request_agent, azure_ops_agent, support_agent])
            .add_handoff(quota_request_agent, [quota_list_agent, azure_ops_agent, support_agent])
            .add_handoff(support_agent, [azure_ops_agent])
            .with_termination_condition(
                lambda conv: sum(1 for msg in conv if msg.role.value == "user") > 6
            )
            .build()
        )

    async def run_workflow(self, chat_history: List[ChatMessage], azure_token: str = ""):
        """
        Stream orchestrated agent responses as NDJSON.
        """
        await self._build_workflow(azure_token=azure_token)
        logger.info(f"Running orchestrated workflow: {chat_history[-1].text[:100]}")

        output = ""

        try:
            async for event in self._workflow.run_stream(chat_history):
                if isinstance(event, WorkflowStartedEvent):
                    pass  # Suppress workflow metadata from chat
                elif isinstance(event, WorkflowStatusEvent):
                    pass  # Suppress status updates from chat
                elif isinstance(event, ExecutorInvokedEvent):
                    agent_name = event.executor_id or "agent"
                    # Emit a subtle indicator of which agent is working
                    resp = ResponseMessage(
                        type="ExecutorInvokedEvent",
                        delta=f"\n**[{agent_name}]** ",
                    )
                    yield _ndjson({"response_message": asdict(resp)})
                elif isinstance(event, AgentRunUpdateEvent) and event.data.text is not None:
                    output += event.data.text
                    resp = ResponseMessage(type="AgentRunUpdateEvent", delta=event.data.text)
                    yield _ndjson({"response_message": asdict(resp)})
                elif isinstance(event, RequestInfoEvent):
                    # Workflow complete
                    pass

            # Extract report_id from the agent's text output.
            # Matches: [report_id=XXXXX], report_id: XXXXX, report ID: XXXXX, report ID XXXXX
            report_id = None
            report_patterns = [
                r'\[report_id=([a-f0-9]+)\]',
                r'report[_ ]id[:\s]+([a-f0-9]{8,})',
                r'report ID[:\s]+([a-f0-9]{8,})',
                r'referencing report ID[:\s]+([a-f0-9]{8,})',
            ]
            for pattern in report_patterns:
                report_match = re.search(pattern, output, re.IGNORECASE)
                if report_match:
                    report_id = report_match.group(1)
                    break

            # Clean all report_id markers/mentions from visible text
            if report_id:
                output = re.sub(r'\[report_id=[a-f0-9]+\]', '', output).strip()

            chat_history.append(ChatMessage(role="assistant", text=output))

            done_msg = ResponseMessage(type="done", result=output)
            if report_id:
                done_msg.report_id = report_id

            yield _ndjson({"response_message": asdict(done_msg)})

        except Exception as e:
            logger.exception(f"Orchestrated workflow failed: {e}")
            yield _ndjson({"response_message": asdict(
                ResponseMessage(type="error", message=f"Workflow execution failed: {e}")
            )})
