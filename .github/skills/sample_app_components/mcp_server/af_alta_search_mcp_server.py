import asyncio
from typing import Annotated
from fastmcp import FastMCP


from search_helper import retrieve_search_results
import logging

logger = logging.getLogger("uvicorn.error")

mcp = FastMCP("Search MCP Server")

@mcp.tool
async def search_knowledge_base(search_query: Annotated[str, "The query to search in the knowledge base"], top_k: Annotated[int, "The number of top results to return"] = 10) -> str:
    """Search the knowledge base for relevant information."""
    logger.info(f"Searching knowledge base with query: {search_query} and top_k: {top_k}")
    results = await retrieve_search_results(search_query, top_k)
    data = [m.model_dump() for m in results]
    return data


if __name__ == "__main__":
    #asyncio.run(search_knowledge_base("how to configure backup policy?", 5))
    mcp.run(transport="streamable-http", port=3000)