"""
Azure Operations Agent - Agent Implementation

Uses Microsoft Agent Framework ChatAgent with the Azure Operations MCP server
tools. Streams responses back as NDJSON for the API layer.
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
    ChatAgent,
    ChatMessage,
    ChatMessageStore,
    MCPStreamableHTTPTool,
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

AZURE_OPS_INSTRUCTIONS = """You are an Azure Operations Agent that helps users monitor, manage, query, and analyze their Azure resources.

You have access to tools provided by the Azure Operations MCP server. Use them to answer user questions about their Azure environment.

## Capabilities
- **Resource Discovery**: List and search resources using Azure Resource Graph (KQL queries)
- **Monitoring**: Query metrics, check resource health, review activity logs, detect idle resources
- **Resource Management**: Get resource details, manage VMs (start/stop/restart), update tags, list subscriptions and resource groups
- **Cost Analysis**: Get cost summaries, breakdowns by resource group/service/resource, check budgets, get Advisor recommendations
- **Reporting**: Generate interactive HTML reports and dashboards for resource findings, cost data, and overall environment overview
- **Email Notifications**: Send resource details via email to the subscription owner or a specified recipient using send_resource_email or send_custom_email

## Important Guidelines
1. When the user asks about their resources, start with get_resource_summary or list_resources to understand their environment
2. For cost questions, use cost management tools and offer to generate a cost report for visualization
3. For health/performance questions, use monitoring tools to check metrics and resource health
4. The user's Azure token is automatically injected via HTTP headers — you do not need to supply a token parameter to tools
5. **CRITICAL: When report tools (generate_resource_report, generate_cost_report, generate_dashboard_report) return a report_id, include it in your response exactly like this: [report_id=XXXXX]. Do NOT output any HTML. Just describe the findings in text and include the report_id marker so the UI can render the visual report.**
6. For VM operations (start/stop), confirm with the user before executing
7. Use find_orphaned_resources and check_idle_resources for cost optimization analysis
8. When presenting data, offer to generate a visual report using generate_resource_report, generate_cost_report, or generate_dashboard_report
9. NEVER include raw HTML, iframe tags, or srcdoc attributes in your response text. Reports are rendered separately by the UI.
10. When the user asks to send, email, or notify someone about resources (e.g. "send email about unused resources"), first gather the resource data using the appropriate tools (find_orphaned_resources, check_idle_resources, list_resources, etc.), then call send_resource_email with a JSON-serialized summary as the resource_details parameter. If the user provides a specific recipient, use send_custom_email instead.
"""


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


class AzureOpsAgent:
    """
    Azure Operations Agent using Microsoft Agent Framework.
    Connects to the Azure Operations MCP server for tools.
    """

    def __init__(self):
        self._access_token = None
        self._agent: Optional[ChatAgent] = None

    async def _get_fresh_token(self):
        if _aoai_api_key:
            return None
        now = int(time.time())
        if self._access_token is None or (getattr(self._access_token, "expires_on", 0) - 60) <= now:
            self._access_token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        return self._access_token

    async def _ensure_agent(self, azure_token: str = ""):
        """Create the ChatAgent with MCP tools. Rebuilt per-request to carry the user's Azure token."""
        token = await self._get_fresh_token()

        # Pass the user's Azure Management token as an Authorization header
        # so the MCP server middleware can extract it for Azure API calls.
        mcp_headers = {}
        if azure_token:
            mcp_headers["Authorization"] = f"Bearer {azure_token}"

        azure_ops_mcp = MCPStreamableHTTPTool(
            name="azure_ops_mcp_server",
            url=MCP_SERVER_URL,
            headers=mcp_headers,
        )

        chat_client = (
            AzureOpenAIChatClient(api_key=_aoai_api_key)
            if _aoai_api_key
            else AzureOpenAIChatClient(ad_token=token.token)
        )

        self._agent = ChatAgent(
            name="azure_ops_agent",
            description="Azure Operations Agent that monitors, manages, queries, and analyzes Azure resources.",
            instructions=AZURE_OPS_INSTRUCTIONS,
            chat_client=chat_client,
            tools=azure_ops_mcp,
        )

    async def run_workflow(self, chat_history: List[ChatMessage], azure_token: str = ""):
        """
        Stream agent responses as NDJSON. The azure_token is injected into
        tool calls so the MCP server can authenticate with Azure APIs.
        """
        await self._ensure_agent(azure_token=azure_token)
        logger.info(f"Running Azure Ops workflow: {chat_history[-1].text[:100]}")

        output = ""

        try:
            async for response in self._agent.run_stream(chat_history):
                if hasattr(response, "text") and response.text:
                    output += response.text
                    yield _ndjson({
                        "response_message": asdict(ResponseMessage(
                            type="AgentRunUpdateEvent",
                            delta=response.text,
                        ))
                    })

            # Extract report_id from the agent's text output (pattern: [report_id=XXXXX])
            report_id = None
            report_match = re.search(r'\[report_id=([a-f0-9]+)\]', output)
            if report_match:
                report_id = report_match.group(1)
                # Remove the report_id marker from the text shown to the user
                output = output.replace(report_match.group(0), "").strip()

            chat_history.append(ChatMessage(role="assistant", text=output))

            done_msg = ResponseMessage(type="done", result=output)
            if report_id:
                done_msg.report_id = report_id

            yield _ndjson({"response_message": asdict(done_msg)})

        except Exception as e:
            logger.exception(f"Azure Ops workflow failed: {e}")
            yield _ndjson({"response_message": asdict(
                ResponseMessage(type="error", message=f"Workflow execution failed: {e}")
            )})
