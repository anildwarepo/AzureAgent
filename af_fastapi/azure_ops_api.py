"""
Azure Operations Agent - FastAPI Backend API

Streaming API that connects the SPA UI layer to the Azure Operations Agent.
Handles:
- Entra ID token validation
- Chat sessions with conversation history
- NDJSON streaming of agent responses
- SSE event channel for real-time notifications

Run:
    uvicorn azure_ops_api:app --port 8080 --reload
"""

import sys
import asyncio

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import contextlib
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env vars at import time
load_dotenv()

from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from agent_framework import ChatMessage
from azure_ops_orchestrator import AzureOpsOrchestrator, ResponseMessage, _ndjson
from azure_ops_auth import decode_and_validate_bearer
from azure_ops_sse_bus import SESSIONS, associate_user_session

logger = logging.getLogger("uvicorn.error")

POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "v0.1")

# ── SPA origins (configured via env or defaults for local dev) ──
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")

# ── FastAPI app ──
app = FastAPI(title="Azure Operations Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── Request/response models ──

class ChatRequest(BaseModel):
    message: str
    subscription_id: str | None = None


# ── Per-session chat history ──

class ChatSessionManager:
    """Keeps per-user chat history in memory."""

    def __init__(self) -> None:
        self._sessions: Dict[str, List[ChatMessage]] = {}

    def get_history(self, user_id: str) -> List[ChatMessage]:
        return self._sessions.setdefault(user_id, [])

    def append(self, user_id: str, role: str, content: str) -> None:
        self.get_history(user_id).append(ChatMessage(role=role, text=content))

    def clear(self, user_id: str) -> None:
        self._sessions.pop(user_id, None)


session_manager = ChatSessionManager()


# ── Routes ──

@app.get("/health")
async def health():
    return {"ok": True, "pod": POD, "rev": REV}


@app.get("/events")
async def sse_events(request: Request):
    """
    SSE endpoint for real-time notifications (progress, agent messages).
    The SPA connects here on load and receives events during agent execution.
    """
    sid = request.query_params.get("sid")
    if not sid:
        return Response(content="sid query param required", status_code=400)

    session = await SESSIONS.get_or_create(sid)

    async def event_stream():
        yield "event: open\ndata: {}\n\n"

        heartbeat_interval = 15.0
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = session.q.get_nowait()
                yield msg
            except asyncio.QueueEmpty:
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(heartbeat_interval)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """
    Main chat endpoint. Accepts a user message and streams back NDJSON
    responses from the Azure Operations Agent.

    The Authorization header must contain a valid Entra ID bearer token
    with Azure management scope.
    """
    ctx = decode_and_validate_bearer(authorization)
    user_id = ctx["user_oid"]
    azure_token = ctx["azure_token"]

    associate_user_session(user_id, user_id)

    history = session_manager.get_history(user_id)

    # Inject subscription context if provided
    user_msg = req.message
    if req.subscription_id and not history:
        user_msg = f"[Subscription ID: {req.subscription_id}]\n\n{req.message}"

    session_manager.append(user_id, "user", user_msg)

    agent = AzureOpsOrchestrator()

    async def safe_stream():
        try:
            async for chunk in agent.run_workflow(history, azure_token=azure_token):
                yield chunk
        except asyncio.CancelledError:
            logger.info(f"Chat stream cancelled user={user_id}")
            return
        except BaseException as e:
            logger.exception(f"Chat stream failed user={user_id}")
            yield _ndjson({"response_message": {
                "type": "error",
                "message": f"Agent execution failed: {e}",
            }})
            yield _ndjson({"response_message": {"type": "done", "result": None}})

    return StreamingResponse(
        safe_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/clear")
async def clear_chat(
    authorization: Optional[str] = Header(default=None),
):
    """Clear chat history for the current user."""
    ctx = decode_and_validate_bearer(authorization)
    user_id = ctx["user_oid"]
    session_manager.clear(user_id)
    return {"ok": True, "message": "Chat history cleared"}


@app.get("/subscriptions")
async def list_subscriptions(
    authorization: Optional[str] = Header(default=None),
):
    """
    List Azure subscriptions accessible to the authenticated user.
    Used by the SPA to populate a subscription picker.
    """
    import httpx

    ctx = decode_and_validate_bearer(authorization)
    azure_token = ctx["azure_token"]

    headers = {
        "Authorization": f"Bearer {azure_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://management.azure.com/subscriptions?api-version=2022-12-01",
            headers=headers,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Failed to list subscriptions")
        data = resp.json()

    subs = []
    for sub in data.get("value", []):
        subs.append({
            "subscription_id": sub.get("subscriptionId", ""),
            "display_name": sub.get("displayName", ""),
            "state": sub.get("state", ""),
        })

    return {"subscriptions": subs}


@app.get("/reports/{report_id}")
async def get_report(report_id: str):
    """
    Proxy endpoint to fetch a generated HTML report from the MCP server.
    Reports are stored server-side to prevent large HTML blobs from being
    streamed through the chat channel.
    """
    import httpx

    mcp_base = os.getenv("MCP_ENDPOINT", "http://localhost:3001/mcp")
    # MCP endpoint is like http://host:port/mcp, report endpoint is at /reports/
    report_url = mcp_base.rsplit("/", 1)[0] + f"/reports/{report_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(report_url)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Report not found")
        return Response(
            content=resp.content,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )
