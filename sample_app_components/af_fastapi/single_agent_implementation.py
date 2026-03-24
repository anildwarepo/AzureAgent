# single_agent_implementation.py
import asyncio
import os
from agent_framework import (
    AgentRunUpdateEvent,
    ChatAgent,
    ChatMessage,
    HostedCodeInterpreterTool,
    WorkflowOutputEvent,
    MCPStreamableHTTPTool
)
from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from azure.identity.aio import DefaultAzureCredential
import json
from enum import Enum
from dataclasses import dataclass, asdict, is_dataclass
from agent_framework import ChatMessageStore
from typing import List, Optional
import time
import logging

logger = logging.getLogger("uvicorn.error")
_aoai_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
# Priority: Entra ID service principal first, API key fallback
try:
    credential = DefaultAzureCredential()
    _aoai_api_key = ""  # SP available, ignore API key
except Exception:
    credential = None
if not credential and not _aoai_api_key:
    logger.warning("Azure credentials not configured. Single agent will be unavailable.")

def create_message_store():
    return ChatMessageStore()

def _json_default(o):
    # Make dataclasses, Enums, and bytes JSON-serializable
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    # Fallback: string representation
    return str(o)

def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8")


@dataclass
class ResponseMessage:
    type: str
    delta: str | None = None
    message: str | None = None
    result: str | None = None



class SingleAgent():
    def __init__(self):
        # stream state
        self._last_executor_id: str | None = None
        self._output: Optional[str] = None

        # lazily populated runtime state
        self._access_token = None          # azure.core.credentials.AccessToken | None
        self._jira_agent = None             # ChatAgent | None
        self._workflow = None              # Built workflow
        self._create_message_store = create_message_store
        #self._chat_history: List[ChatMessage] = []

    
    async def _get_fresh_token(self):
        """Fetch or refresh an access token (buffers 60s before expiry). Skipped when using API key."""
        if _aoai_api_key:
            return None
        now = int(time.time())
        logger.info(f"Fetching fresh token at time {now}")
        if self._access_token is None or (getattr(self._access_token, "expires_on", 0) - 60) <= now:
            self._access_token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        return self._access_token
    
    async def _ensure_clients(self):
        """Create agents and the workflow exactly once (or after token refresh if you choose)."""
        logger.info("Ensuring clients are created or refreshed")
        token = await self._get_fresh_token()

        atlassian_api_token = os.getenv("ATLASSIAN_API_TOKEN", "")
        atlassian_auth_header = f"Token {atlassian_api_token}" if atlassian_api_token else ""

        jira_mcp_server = MCPStreamableHTTPTool(
            name="jira_mcp_server",
            url="https://mcp.atlassian.com/v1/mcp",
            headers={"Authorization": atlassian_auth_header} if atlassian_auth_header else {},
        )

        microsoftdocs_mcp_server = MCPStreamableHTTPTool(
            name="microsoftdocs_mcp_server",
            url="https://learn.microsoft.com/api/mcp",
        )
        #self._jira_agent = ChatAgent(
        #    name="jira_agent",
        #    description="Jira agent that can answer questions about Jira using a jira tool.",
        #    instructions="You are a helpful jira agent. Use the jira tool to answer questions about jira. Answer questions about jira and nothing else",
        #    chat_client=AzureOpenAIChatClient(ad_token=token.token),
        #    tools=jira_mcp_server
        #    #chat_message_store_factory=self._create_message_store,
        #    )

        self._microsoftdocs_agent = ChatAgent(
            name="microsoftdocs_agent",
            description="Microsoft Docs agent that can answer questions about Microsoft documentation using a docs tool.",
            instructions="You are a helpful Microsoft Docs agent. Use the docs tool to answer questions about Microsoft documentation. Answer questions about Microsoft documentation and nothing else only using the microsoft docs mcp server tool.",
            chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
            tools=microsoftdocs_mcp_server
            #chat_message_store_factory=self._create_message_store,

        )

            

    async def run_workflow(self, chat_history: List[ChatMessage]):
        await self._ensure_clients()
        logger.info(f"Running workflow with question: {chat_history[-1].text}")
        # local stream state per run
        self._last_executor_id: str | None = None
        self._output = ''

        try:
            async for response in self._microsoftdocs_agent.run_stream(chat_history):
                # Handle streaming response from the agent
                if hasattr(response, 'text') and response.text:
                    self._output += response.text
                    yield _ndjson({"response_message": ResponseMessage(type="AgentRunUpdateEvent", delta=response.text)})
            
            # Yield final done event
            chat_history.append(ChatMessage(role="assistant", text=self._output or ""))
            yield _ndjson({"response_message": ResponseMessage(type="done", result=self._output)})
        except Exception as e:
            yield _ndjson({"type": "error", "message": f"Workflow execution failed: {e}"})




