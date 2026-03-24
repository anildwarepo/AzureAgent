"""
Azure Operations Agent - MCP Client

Connects to the Azure Operations MCP server over streamable-http transport.
Discovers tools, invokes them with the user's Azure token, and relays
notifications (progress, messages) to the SSE bus.
"""

import asyncio
import json
import sys
import os
from contextlib import AsyncExitStack
from typing import Optional

from mcp import ClientSession, ListToolsResult
from mcp.client.streamable_http import streamablehttp_client
import mcp.types as types
from mcp.shared.session import RequestResponder

from azure_ops_sse_bus import (
    SESSIONS,
    sse_event,
    publish_progress,
    publish_message,
    session_for_user,
)

MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "http://localhost:3001/mcp")


class AzureOpsMCPClient:
    """
    MCP client that connects to the Azure Operations MCP server.
    Manages a persistent session, discovers tools, and calls them.
    """

    def __init__(self, mcp_endpoint: str = MCP_ENDPOINT):
        self.mcp_endpoint = mcp_endpoint
        self.exit_stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.session_id: Optional[str] = None
        self.mcp_tools: Optional[ListToolsResult] = None
        self._broadcast_session_id: str | None = None

    async def _on_incoming(
        self,
        msg: RequestResponder[types.ServerRequest, types.ClientResult]
        | types.ServerNotification
        | Exception,
    ) -> None:
        if isinstance(msg, Exception):
            print(f"[mcp] incoming exception: {msg!r}", file=sys.stderr)
            return

        if isinstance(msg, RequestResponder):
            return

        if isinstance(msg, types.ServerNotification):
            root = msg.root
            method = getattr(root, "method", None)
            params = getattr(root, "params", None)

            if hasattr(params, "model_dump"):
                params_json = params.model_dump(mode="json")
            else:
                params_json = params

            # Relay progress notifications
            if method == "notifications/progress":
                pct = float(params_json.get("progress", 0))
                token = params_json.get("progressToken", "")
                target = self._broadcast_session_id
                if target:
                    await publish_progress(target, token, pct)
                return

            # Relay message notifications
            if method == "notifications/message":
                data_items = params_json.get("data", [])
                texts = [d.get("text", "") for d in data_items if d.get("type") == "text"]
                level = params_json.get("level", "info")
                target = self._broadcast_session_id
                if target:
                    await publish_message(target, " ".join(texts), level)
                return

    def set_broadcast_session(self, session_id: str) -> None:
        self._broadcast_session_id = session_id

    async def connect(self, session_id: str) -> None:
        """Open the streamable-http channel and discover tools."""
        self.exit_stack = AsyncExitStack()
        await self.exit_stack.__aenter__()
        self.session_id = session_id
        headers = {"Mcp-Session-Id": self.session_id}

        streamable_http = streamablehttp_client(url=self.mcp_endpoint, headers=headers)
        read, write, _ = await self.exit_stack.enter_async_context(streamable_http)

        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write, message_handler=self._on_incoming)
        )

        await self.session.initialize()
        await self.session.send_ping()
        self.mcp_tools = await self.session.list_tools()

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Invoke a tool on the MCP server and return the text result."""
        if not self.session:
            raise RuntimeError("MCP client not connected")

        result = await self.session.call_tool(tool_name, arguments)

        # Extract text content from result
        texts = []
        for item in result.content:
            if hasattr(item, "text"):
                texts.append(item.text)
        return "\n".join(texts) if texts else str(result.content)

    def get_tools_for_openai(self) -> list[dict]:
        """Return MCP tools in OpenAI function-calling format."""
        if not self.mcp_tools:
            return []

        tools = []
        for t in self.mcp_tools.tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                },
            })
        return tools

    async def aclose(self) -> None:
        """Close the MCP session and exit stack."""
        if self.exit_stack is not None:
            await self.exit_stack.aclose()
            self.exit_stack = None
