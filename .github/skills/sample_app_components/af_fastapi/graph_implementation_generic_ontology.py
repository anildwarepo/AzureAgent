# magentic_implementation.py
import asyncio
from agent_framework import (
    ChatAgent,
    ChatContext,
    ChatMessage,
    ChatMiddleware,
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
from enum import Enum
from dataclasses import dataclass, asdict, is_dataclass
from agent_framework import ChatMessageStore
from typing import Awaitable, Callable, List, Optional
import time
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")
_aoai_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
# Priority: Entra ID service principal first, API key fallback
try:
    credential = DefaultAzureCredential()
    _aoai_api_key = ""  # SP available, ignore API key
except Exception:
    credential = None
if not credential and not _aoai_api_key:
    logger.warning("Azure credentials not configured. Graph workflow (generic) will be unavailable.")
MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
GRAPH_NAME = os.environ.get("GRAPH_NAME", "")


print("Using MCP_ENDPOINT:", MCP_ENDPOINT)
print("Using AZURE_OPENAI_ENDPOINT:", AZURE_OPENAI_ENDPOINT)
print("Using AZURE_DEPLOYMENT_NAME:", AZURE_DEPLOYMENT_NAME)
print("Using GRAPH_NAME:", GRAPH_NAME)

if not MCP_ENDPOINT:
    raise ValueError("MCP_ENDPOINT environment variable must be set")
if not GRAPH_NAME:
    raise ValueError("GRAPH_NAME environment variable must be set")

def create_message_store():
    return ChatMessageStore()


def _read_instruction_file(file_name: str, graph_name: str) -> str:
    instructions_path = Path(__file__).resolve().parent / "agent_instructions" / file_name
    instructions = instructions_path.read_text(encoding="utf-8-sig")
    return instructions.replace("{{GRAPH_NAME}}", graph_name).replace("{GRAPH_NAME}", graph_name)

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


class LoggingChatMiddleware(ChatMiddleware):
    """Chat middleware that logs AI interactions."""

    async def process(
        self,
        context: ChatContext,
        next: Callable[[ChatContext], Awaitable[None]],
    ) -> None:
        # Pre-processing: Log before AI call
        print(f"[Chat Class] Sending {len(context.messages)} messages to AI")

        # Continue to next middleware or AI service
        await next(context)

        for i, message in enumerate(context.messages):
            content = message.text if message.text else str(message.contents)
            print(f"  Message {i + 1} ({message.role.value}): {content}")
        # Post-processing: Log after AI response
        print("[Chat Class] AI response received")

class GraphWorkflow():
    def __init__(self, graph_name: str | None = None, model_name: str | None = None):
        # stream state
        self._last_stream_agent_id: Optional[str] = None
        self._stream_line_open: bool = False
        self._output: Optional[str] = None

        # lazily populated runtime state
        self._access_token = None          
        self._graph_query_generator_agent = None      
        self._graph_query_validator_agent = None 
        self._graph_query_executor_agent = None 
        self._response_generator_agent = None           
        self._create_message_store = create_message_store
        self._graph_name = graph_name or GRAPH_NAME
        self._deployment_name = model_name or AZURE_DEPLOYMENT_NAME
        #self._chat_history: List[ChatMessage] = []


    async def logging_chat_middleware(
            context: ChatContext,
            next: Callable[[ChatContext], Awaitable[None]],
        ) -> None:
            """Chat middleware that logs AI interactions."""
            # Pre-processing: Log before AI call
            print(f"[Chat] Sending {len(context.messages)} messages to AI")

            # Continue to next middleware or AI service
            await next(context)

            # Post-processing: Log after AI response
            print("[Chat] AI response received")
    
    async def _get_fresh_token(self):
        """Fetch or refresh an access token (buffers 60s before expiry). Skipped when using API key."""
        if _aoai_api_key:
            return None
        now = int(time.time())
        logger.info(f"Fetching fresh token at time {now}")
        if self._access_token is None or (getattr(self._access_token, "expires_on", 0) - 60) <= now:
            self._access_token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        return self._access_token

    def _chat_client_kwargs(self, token, **extra):
        """Build kwargs for AzureOpenAIChatClient depending on auth mode."""
        if _aoai_api_key:
            return dict(api_key=_aoai_api_key, endpoint=AZURE_OPENAI_ENDPOINT, deployment_name=self._deployment_name, **extra)
        return dict(ad_token=token.token, endpoint=AZURE_OPENAI_ENDPOINT, deployment_name=self._deployment_name, **extra)
    
    async def _ensure_clients(self):
        """Create agents and the workflow exactly once (or after token refresh if you choose)."""
        logger.info("Ensuring clients are created or refreshed")
        token = await self._get_fresh_token()
        graph_age_mcp_server = MCPStreamableHTTPTool(
            name="graph age mcp server",
            url=MCP_ENDPOINT,
            #headers={"Authorization": "Bearer your-token"},
        ) 
        

        if self._graph_query_generator_agent is None or self._graph_query_validator_agent is None or self._graph_query_executor_agent is None or self._response_generator_agent is None:
            
            # read instructions from files
            
            graph_query_generator_instructions = _read_instruction_file("CYPHER_QUERY_GENERATION_AGENT_GENERIC_v1.md", self._graph_name)

            self._graph_query_generator_agent = ChatAgent(
                name="graph query generator agent",
                description="Graph query generator agent that can answer questions about the graph using a graph query tool.",
                instructions=graph_query_generator_instructions,
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token)),
                #chat_message_store_factory=self._create_message_store,
                tools=graph_age_mcp_server
            )


            graph_query_validator_instructions = _read_instruction_file("CYPHER_QUERY_VALIDATION_AGENT_GENERIC_v1.md", self._graph_name)

            self._graph_query_validator_agent = ChatAgent(
                name="graph_query_validator",
                description="Graph query validator agent that can validate and refine graph queries using a graph query tool.",
                instructions=graph_query_validator_instructions,
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token)),
                #chat_message_store_factory=self._create_message_store,
                tools=graph_age_mcp_server
            )

            self._graph_query_executor_agent = ChatAgent(
                name="graph_query_executor_agent",
                description="Graph query executor agent that can execute graph queries using a graph query tool.",
                instructions="""
                You are a graph query executor agent. Your job is to execute the SQL queries generated by the graph query generator agent using the provided tool 'query_using_sql_cypher' and return the results.
                State the query that you received from the graph query generator agent before executing it.
                Do not modify the generated queries. Send them as-is to the tool.
                """,
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token, temperature=0.0)),
                middleware=[LoggingChatMiddleware()],
                #chat_message_store_factory=self._create_message_store,
                tools=graph_age_mcp_server
            )


            self._response_generator_agent = ChatAgent(
                name="response_generator_agent",
                description="Final response agent that states the final response based on the results from the graph query executor agent. ",
                instructions="""
                You are a final responder to the user question based on the results obtained from the graph query executor agent.
                Respond only if there are results. Otherwise, state that no results were found.
                Be accurate and concise in your responses. 
                The response should use the results obtained from the graph query executor agent to answer the user's question.
                You only need to respond when the query results are available from the _graph_query_validator_agent.
                """,
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token, temperature=0.0)),
                #chat_message_store_factory=self._create_message_store,

            )

            orchestration_manager_instructions = _read_instruction_file("ORCHESTRATION_MANAGER_INSTRUCTIONS_v1.md", self._graph_name)
            task_ledger_full_prompt = _read_instruction_file("TASK_LEDGER_FULL_PROMPT_v1.md", self._graph_name)

            logger.info("Building workflow with agents")
            self._workflow = (
                MagenticBuilder()
                .participants(
                    graph_query_generator_agent=self._graph_query_generator_agent,
                    graph_query_validator=self._graph_query_validator_agent, 
                    #graph_query_executor=self._graph_query_executor_agent, 
                    #response_generator=self._response_generator_agent
                    )
                .with_standard_manager(
                    instructions=orchestration_manager_instructions,
                    task_ledger_full_prompt=task_ledger_full_prompt,
                    final_answer_prompt=_read_instruction_file("FINAL_ANSWER_PROMPT_v1.md", self._graph_name),
                    chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token, temperature=0.0)),
                    max_round_count=6,
                    max_stall_count=2,
                    max_reset_count=2,
                )
                .build()
            )
            logger.info("Workflow built successfully")

    async def run_workflow(self, chat_history: List[ChatMessage]):
        output = None
        try:
            await self._ensure_clients()
            logger.info(f"Running workflow with question: {chat_history[-1].text}")
            # local stream state per run
            self._last_stream_agent_id = None
            self._stream_line_open = False
            self._output = None

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
                    if event.data is None:
                        output = None
                    else:
                        output = str(getattr(event.data, "text", event.data))
                    self._output = output
                    chat_history.append(ChatMessage(role="assistant", text=output or ""))
                    yield _ndjson({"response_message": ResponseMessage(type="WorkflowOutputEvent", delta=f"Workflow output event: {output}")})
            if self._stream_line_open:
                self._stream_line_open = False

            final_output = self._output if self._output is not None else output
            yield _ndjson({"response_message": ResponseMessage(type="done", result=final_output)})
        except asyncio.CancelledError:
            logger.warning("Workflow stream cancelled (client disconnected).")
            return
        except BaseException as e:
            logger.exception("Workflow execution failed")
            error_message = f"Workflow execution failed: {e}"
            yield _ndjson({"response_message": ResponseMessage(type="error", message=error_message)})
            yield _ndjson({"response_message": ResponseMessage(type="done", result=self._output if self._output is not None else output)})



