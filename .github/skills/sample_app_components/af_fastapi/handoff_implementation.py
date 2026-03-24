# magentic_implementation.py
import asyncio
from agent_framework import (
    AgentRunUpdateEvent,
    ChatAgent,
    ChatMessage,
    ExecutorCompletedEvent,
    ExecutorInvokedEvent,
    HostedCodeInterpreterTool,
    MagenticAgentDeltaEvent,
    MagenticAgentMessageEvent,
    HandoffBuilder,
    MagenticFinalResultEvent,
    MagenticOrchestratorMessageEvent,
    RequestInfoEvent,
    WorkflowOutputEvent,
    MCPStreamableHTTPTool,
    WorkflowRunState,
    WorkflowStartedEvent,
    WorkflowStatusEvent
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
import os

logger = logging.getLogger("uvicorn.error")
_aoai_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
# Priority: Entra ID service principal first, API key fallback
try:
    credential = DefaultAzureCredential()
    _aoai_api_key = ""  # SP available, ignore API key
except Exception:
    credential = None
if not credential and not _aoai_api_key:
    logger.warning("Azure credentials not configured. Handoff workflow will be unavailable.")

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



class HandoffWorkflow():
    def __init__(self):
        # stream state
        self._last_stream_agent_id: Optional[str] = None
        self._stream_line_open: bool = False
        self._output: Optional[str] = None

        # lazily populated runtime state
        self._access_token = None          # azure.core.credentials.AccessToken | None
        self._weather_agent = None      # ChatAgent | None
        self._search_agent = None 
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
        
        weather_mcp_server = MCPStreamableHTTPTool(
            name="weather mcp server",
            url="http://localhost:3001/mcp",
            #headers={"Authorization": "Bearer your-token"},
        ) 
        search_mcp_server = MCPStreamableHTTPTool(
            name="search mcp server",
            url="http://localhost:3000/mcp",
            #headers={"Authorization": "Bearer your-token"},
        ) 

        if self._weather_agent is None or self._search_agent is None or self._workflow is None:
            
            self._triage_agent = ChatAgent(
                name="triage agent",
                description="Triage agent that can answer questions route appropriately to weather or search agents.",
                instructions="You are a helpful Triage agent that can answer questions route appropriately to weather or search agents."
                "- If the question is about weather, call handoff_to_weather_agent" 
                "- If the question is about backup related, call handoff_to_search_agent."
                "- Handle only one handoff based on the user's previous questions."
                "Be consise and friendly in your responses.",
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,
                tools=weather_mcp_server
            )
            
            self._weather_agent = ChatAgent(
                name="weather agent",
                description="Weather agent that can answer questions about the weather using a weather tool.",
                instructions="You are a helpful weather agent. Use the weather tool to answer questions about the weather. Answer questions about weather and nothing else."
                "If the question is about backup related call handoff_to_search_agent."
                "- Handle only one handoff based on the user's previous questions."
                "Be consise and friendly in your responses.",
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,
                tools=weather_mcp_server
            )

            self._search_agent = ChatAgent(
                name="search agent",
                description="Search agent that can answer questions using a search tool.",
                instructions="You are an AI agent. Always use the tools provided to answer the questions. If you cannot " \
                            "answer using the tools, respond with 'I don't know.'" \
                            "Alway provide citations[part_id, chapter_id, part_title] for your answers from the tools." \
                            "If the question is about weather call handoff_to_weather_agent"
                            "- Handle only one handoff based on the user's previous questions."
                            "Be consise and friendly in your responses.",
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,
                tools=search_mcp_server
            )

            logger.info("Building workflow with agents")
            self._workflow = (
                HandoffBuilder(
                    participants=[self._triage_agent, self._weather_agent, self._search_agent]
                )
                .set_coordinator(self._triage_agent)
                .add_handoff(self._triage_agent, [self._weather_agent, self._search_agent])
                .add_handoff(self._weather_agent, [self._search_agent])
                .add_handoff(self._search_agent, [self._weather_agent])
                .with_termination_condition(lambda conv: sum(1 for msg in conv if msg.role.value == "user") > 4)
                .build()
            )
            logger.info("Workflow built successfully")

    async def run_workflow(self, chat_history: List[ChatMessage]):
        await self._ensure_clients()
        logger.info(f"Running workflow with question: {chat_history[-1].text}")
        # local stream state per run
        self._last_stream_agent_id = None
        self._stream_line_open = False
        self._output = ''

        
        try:
            async for event in self._workflow.run_stream(chat_history):
                print(f"Received event: {event}")
                if isinstance(event, WorkflowStartedEvent):
                    resp = ResponseMessage(type="WorkflowStartedEvent", delta=f"\n[Workflow:{event.origin}]\n\n{getattr(event.data, 'text', '')}\n{'-' * 26}")
                    #yield _ndjson({"response_message": resp})
                elif isinstance(event, WorkflowStatusEvent):
                    resp = ResponseMessage(type="WorkflowStatusEvent", delta=f"\n[Workflow:{event.state}]\n\n{getattr(event.data, 'text', '')}\n{'-' * 26}\n")
                    #yield _ndjson({"response_message": resp})
                elif isinstance(event, ExecutorInvokedEvent):
                    resp = ResponseMessage(type="ExecutorInvokedEvent", delta=f"\n[Workflow:{event.executor_id}]\n\n{getattr(event.data, 'text', '')}\n{'-' * 26}\n")
                    yield _ndjson({"response_message": resp})
                elif isinstance(event, AgentRunUpdateEvent) and event.data.text is not None:
                    resp = ResponseMessage(type="AgentRunUpdateEvent", delta=event.data.text)
                    self._output += event.data.text
                    #yield _ndjson({"response_message": ResponseMessage(type="WorkflowFinalResultEvent", delta=event.data.text)})
                    yield _ndjson({"response_message": resp})
                elif isinstance(event, RequestInfoEvent):
                    print(f"Final output: {self._output}")
                    yield _ndjson({"response_message": ResponseMessage(type="WorkflowFinalResultEvent", delta=self._output)})
            
        except Exception as e:
            yield _ndjson({"type": "error", "message": f"Workflow execution failed: {e}"})



