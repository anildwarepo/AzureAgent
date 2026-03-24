"""
Run:  uvicorn af_fastapi:app --port 8080 --reload

"""


import socket
from agent_framework import ChatMessage
from fastapi import FastAPI, HTTPException
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv
from sse_bus import SESSIONS
from starlette.responses import StreamingResponse
import asyncio
from pydantic import BaseModel
import json
from typing import Any, Dict, List
from mcp_client import MCPClient
from openai import AsyncAzureOpenAI
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider



from magentic_implementation import MagenticWorkflow
from handoff_implementation import  HandoffWorkflow
from graph_implementation_generic_ontology import GraphWorkflow
from single_agent_implementation import SingleAgent
import logging
import sys, asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from pg_age_helper import PGAgeHelper


logger = logging.getLogger("uvicorn.error")

load_dotenv()
POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "v0.1")
GRAPH = "customer_graph"
DSN = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", "postgres"),
)
MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "http://localhost:3000/mcp") # Dapr endpoint

aoai_endpoint    = os.getenv("AZURE_OPENAI_ENDPOINT", os.getenv("ENDPOINT_URL", ""))
aoai_deployment  = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", os.getenv("DEPLOYMENT_NAME", "gpt-4o"))
aoai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
aoai_api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()

# Priority: Entra ID service principal first, API key fallback
try:
    aoai_credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        aoai_credential,
        "https://cognitiveservices.azure.com/.default",
    )
    aoai_client = AsyncAzureOpenAI(
        azure_endpoint=aoai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=aoai_api_version,
    )
    logger.info("Azure Foundry auth mode: entra_id (service principal)")
except Exception as e:
    if aoai_api_key:
        aoai_client = AsyncAzureOpenAI(
            azure_endpoint=aoai_endpoint,
            api_key=aoai_api_key,
            api_version=aoai_api_version,
        )
        logger.info("Azure Foundry auth mode: api_key (service principal not configured)")
    else:
        aoai_client = None
        logger.warning(f"Azure Foundry credentials not configured: {e}. AI endpoints will be unavailable.")
        logger.warning(f"Azure Foundry credentials not configured: {e}. AI endpoints will be unavailable.")

print("Using DSN:", DSN["host"], DSN["port"], DSN["dbname"], DSN["user"])

# ---- FastAPI app ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        #global pg_helper
        #pg_helper = await PGAgeHelper.create(DSN, GRAPH)
        yield
    finally:
        pass


app = FastAPI(title="AGE Node Creator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in prod
    allow_credentials=True,
    allow_methods=["GET","POST","OPTIONS"],
    allow_headers=["*"],
)


def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()


@app.get("/events")
async def sse_events(request: Request):
    sid = request.query_params.get("sid")  
    session_id = _normalize_session_id(sid)
    print(f"[@app.get(/events)] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        # flush headers immediately (APIM/ACA friendly)
        yield "event: open\ndata: {}\n\n"

        heartbeat_every = 1.0  # seconds
        while True:
            if await request.is_disconnected():
                break
            try:
                # wait up to heartbeat interval for next message
                #msg = await asyncio.wait_for(session.q.get(), timeout=heartbeat_every)
                try:
                    msg = session.q.get_nowait()
                    print(f"[@app.get(/events)] MCP CLIENT SSE YIELD session={session_id} msg={msg}...", flush=True)
                except asyncio.QueueEmpty:
                    yield "event: noevent\ndata: {}\n\n"
                    await asyncio.sleep(heartbeat_every)
                    continue
                #msg = sse_event(payload, event="assistant")
                print(f"[@app.get(/events)] SSE YIELD {msg}")
                yield msg
                #await asyncio.sleep(5)
                #session.q.task_done()
            except asyncio.TimeoutError:
                # heartbeat (SSE comment doesn't disturb clients)
                yield "server version 1.0: ping\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )

# ---- Routes ----
@app.get("/health")
async def health():
    try:
        status = await pg_helper.health_check()
        if not status:
            raise HTTPException(status_code=500, detail="Database connection failed")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_faqs")
async def get_faqs(graph_name: str) -> dict:
    faq_files = {
        "customer_graph": "customer_graph_faqs.txt",
        "meetings_graph": "meetings_graph_faqs.txt",
        "meetings_graph_v2": "meetings_graph_v2_faqs.txt",
    }
    selected_file = faq_files.get(graph_name)
    if not selected_file:
        raise HTTPException(status_code=400, detail="Unsupported graph_name")

    faqs_path = Path(__file__).with_name(selected_file)
    try:
        with faqs_path.open("r", encoding="utf-8") as file:
            faqs = [line.strip() for line in file if line.strip()]
        return {"faqs": faqs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"{selected_file} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ConversationIn(BaseModel):
    user_query: str
    graph_name: str
    model_name: str = ""
    #client_id: str


def _normalize_graph_name(graph_name: str) -> str:
    if graph_name in {"meeting_graph", "meetings_graph"}:
        return "meetings_graph"
    return graph_name




class SessionManager:
    """Keeps per-session, per-user chat histories."""
    def __init__(self) -> None:
        # sessions[session_id][user_id] -> list of messages (dicts or strings)
        self.sessions: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def get_history(self, session_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self.sessions.setdefault(session_id, {}).setdefault(user_id, [])

    def append(self, session_id: str, user_id: str, role: str, content: str) -> None:
        self.get_history(session_id, user_id).append({"role": role, "content": content})



# single, long-lived manager you reuse (e.g., module-level or injected)
session_manager = SessionManager()




@app.post("/conversation/{user_id}")
async def start_conversation(user_id: str, convo: ConversationIn, request: Request):
    if not user_id:
        return Response(content="user_id is required", status_code=400)

    orchestration_mode = request.query_params.get("mode", "handoff")
    print(f"orchestration_mode={orchestration_mode}")
    normalized_graph_name = _normalize_graph_name(convo.graph_name)


    session_id = user_id  # keep your existing per-user session key
    logger.info(f"received [@app.post(/conversation/{user_id})] session={session_id} pod={POD} rev={REV} user_query={convo.user_query}")
    
    history = session_manager.get_history(session_id, user_id)
    user_message = ChatMessage(role="user", text=convo.user_query)
    history.append(user_message)

    if orchestration_mode == "magentic":
        workflow = MagenticWorkflow()
    elif orchestration_mode == "graph":
        workflow = GraphWorkflow(normalized_graph_name, model_name=convo.model_name or None)
    elif orchestration_mode == "singleagent":
        workflow = SingleAgent()
    else:
        workflow = HandoffWorkflow()
    

    #return StreamingResponse(
    #    magentic_workflow.run_workflow(history),
    #    media_type="application/x-ndjson",
    #    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    #)

    async def safe_workflow_stream():
        stream = workflow.run_workflow(history)
        try:
            async for chunk in stream:
                yield chunk
        except asyncio.CancelledError:
            logger.info(f"conversation stream cancelled session={session_id} user_id={user_id}")
            return
        except BaseException as e:
            logger.exception(f"conversation stream failed session={session_id} user_id={user_id}")
            error_payload = {
                "response_message": {
                    "type": "error",
                    "message": f"Workflow execution failed: {e}",
                }
            }
            done_payload = {
                "response_message": {
                    "type": "done",
                    "result": None,
                }
            }
            yield (json.dumps(error_payload, ensure_ascii=False) + "\n").encode("utf-8")
            yield (json.dumps(done_payload, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    return StreamingResponse(
        safe_workflow_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
   






