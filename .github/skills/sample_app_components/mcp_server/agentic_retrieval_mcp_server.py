import asyncio
from typing import Annotated
from fastmcp import FastMCP
from agentic_retrieval_run_pipeline import search_knowledge_base

from search_helper import retrieve_search_results
import logging

logger = logging.getLogger("uvicorn.error")

mcp = FastMCP("Agentic Retrieval MCP Server")


@mcp.tool
async def search_knowledge_base_tool(search_query: Annotated[str, "The query to search in the knowledge base"]) -> dict:
    """Search the knowledge base for relevant information."""

    logger.info(f"Searching knowledge base with query: {search_query}")
    results = await search_knowledge_base(search_query)
    return results



if __name__ == "__main__":
    #asyncio.run(search_knowledge_base("how to configure backup policy?", 5))
    mcp.run(transport="streamable-http", port=3003)