# magentic_implementation.py
import asyncio
from agent_framework import (
    ChatAgent,
    ChatMessage,
    HostedCodeInterpreterTool,
    MagenticAgentDeltaEvent,
    MagenticAgentMessageEvent,
    MagenticBuilder,
    MagenticFinalResultEvent,
    MagenticOrchestratorMessageEvent,
    WorkflowOutputEvent,
    MCPStreamableHTTPTool
)
from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from azure.identity.aio import DefaultAzureCredential
import json
import os
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
    logger.warning("Azure credentials not configured. Magentic workflow will be unavailable.")

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



class MagenticWorkflow():
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
            self._weather_agent = ChatAgent(
                name="weather agent",
                description="Weather agent that can answer questions about the weather using a weather tool.",
                instructions="You are a helpful weather agent. Use the weather tool to answer questions about the weather. Answer questions about weather and nothing else",
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,
                tools=weather_mcp_server
            )

            self._search_agent = ChatAgent(
                name="search agent",
                description="Search agent that can answer questions using a search tool.",
                instructions="You are an AI agent. Always use the tools provided to answer the questions. If you cannot " \
            "answer using the tools, respond with 'I don't know.'" \
            "Alway provide citations[part_id, chapter_id, part_title] for your answers from the tools.",
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,
                tools=search_mcp_server
            )

            self._reviewer_agent = ChatAgent(
                name="reviewer agent",
                description="Reviewer agent that reviews the answers provided by other agents.",
                instructions="""You are a helpful reviewer agent. Review the answers provided by other agents for correctness and completeness. " \
                If the tool return incorrect or incomplete information, ask the relevant agent to call the tool with correct parameters and answer the question accurately. 
                Always present the final answer with citations[part_id, chapter_id, part_title] from the tools used by other agents.
                """,
                chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                #chat_message_store_factory=self._create_message_store,

            )

            logger.info("Building workflow with agents")
            self._workflow = (
                MagenticBuilder()
                .participants(weather=self._weather_agent, search=self._search_agent, reviewer=self._reviewer_agent)
                .with_standard_manager(
                    instructions="Manage the workflow between weather, search, and reviewer agents to answer the user's question accurately using the tools provided." \
                    
                    "Provide the final answer with citations[part_id, chapter_id, part_title] only if used from the tools used by other agents.",
                    task_ledger_full_prompt="When using the search tool always send the original user query as the query parameter."
                    "Do not modify the user query in any way.",
                    
                    chat_client=AzureOpenAIChatClient(api_key=_aoai_api_key) if _aoai_api_key else AzureOpenAIChatClient(ad_token=token.token),
                    max_round_count=3,
                    max_stall_count=3,
                    max_reset_count=2,
                )
                .build()
            )
            logger.info("Workflow built successfully")

    async def run_workflow(self, chat_history: List[ChatMessage]):
        await self._ensure_clients()
        logger.info(f"Running workflow with question: {chat_history[-1].text}")
        # local stream state per run
        self._last_stream_agent_id = None
        self._stream_line_open = False
        self._output = None

        
        try:
            async for event in self._workflow.run_stream(chat_history):
                if isinstance(event, MagenticOrchestratorMessageEvent):
                    resp = ResponseMessage(type="MagenticOrchestratorMessageEvent", delta=f"\n[ORCH:{event.kind}]\n\n{getattr(event.message, 'text', '')}\n{'-' * 26}")
                    yield _ndjson({"response_message": resp})
                elif isinstance(event, MagenticAgentDeltaEvent):
                    if self._last_stream_agent_id != event.agent_id or not self._stream_line_open:
                        if self._stream_line_open:
                            resp = ResponseMessage(type="MagenticAgentDeltaEvent", delta=" (incomplete)\n")
                            yield _ndjson({"response_message": resp})
                            #yield _ndjson({"type": "content", "delta": "\n"})
                        self._last_stream_agent_id = event.agent_id
                        self._stream_line_open = True
                        yield _ndjson({"response_message": ResponseMessage(type="MagenticAgentDeltaEvent", delta=f"\n[STREAM:{event.agent_id}]: ")})
                    if event.text:
                        yield _ndjson({"response_message": ResponseMessage(type="MagenticAgentDeltaEvent", delta=event.text)})
                elif isinstance(event, MagenticAgentMessageEvent):
                    if self._stream_line_open:
                        self._stream_line_open = False
                        yield _ndjson({"response_message": ResponseMessage(type="MagenticAgentMessageEvent", delta=" (final)\n")})
                    msg = event.message
                    if msg is not None:
                        response_text = (msg.text or "").replace("\n", " ")
                        yield _ndjson({"response_message": ResponseMessage(type="MagenticAgentMessageEvent", delta=f"\n[AGENT:{event.agent_id}] {msg.role.value}\n\n{response_text}\n{'-' * 26}")})
                elif isinstance(event, MagenticFinalResultEvent):
                    if event.message is not None:
                        yield _ndjson({"response_message": ResponseMessage(type="WorkflowFinalResultEvent", delta=event.message.text)})

                elif isinstance(event, WorkflowOutputEvent):
                    output = str(event.data.text) if event.data is not None else None
                    chat_history.append(ChatMessage(role="assistant", text=output or ""))
                    yield _ndjson({"response_message": ResponseMessage(type="WorkflowOutputEvent", delta=f"Workflow output event: {output}")})
            if self._stream_line_open:
                self._stream_line_open = False


            yield _ndjson({"response_message": ResponseMessage(type="done", result=output)})
        except Exception as e:
            yield _ndjson({"type": "error", "message": f"Workflow execution failed: {e}"})



