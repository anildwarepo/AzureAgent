import sys, asyncio
import os

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from typing import Annotated
from fastmcp import FastMCP
from dotenv import load_dotenv
from pg_age_helper import PGAgeHelper





load_dotenv()

mcp = FastMCP("Graph Age MCP Server")

GRAPH_NAME = os.getenv("GRAPH_NAME", "")
_ONTOLOGY_MEMORY: dict[str, str] = {}


@mcp.tool
async def save_ontology(
    ontology: Annotated[str, "Generated ontology content to store in memory"],
    graph_name: Annotated[str | None, "Graph name for ontology cache key"] = None,
) -> dict:
    """
    Save the generated ontology in process memory.
    Args:
        ontology: The ontology content as text.
    Returns:
        Status indicating ontology was saved.
    """
    graph_key = (graph_name or GRAPH_NAME or "default").strip()
    _ONTOLOGY_MEMORY[graph_key] = ontology

    print("Ontology saved in memory for graph:", graph_key, "length:", len(ontology))
    return {
        "status": "saved",
        "has_ontology": True,
        "graph_name": graph_key,
        "length": len(ontology),
    }


@mcp.tool
async def fetch_ontology(
    graph_name: Annotated[str | None, "Graph name for ontology cache lookup"] = None,
) -> dict:
    """
    Fetch the ontology previously saved in process memory.
    Returns:
        The saved ontology content, if any.
    """
    graph_key = (graph_name or GRAPH_NAME or "default").strip()
    ontology = _ONTOLOGY_MEMORY.get(graph_key)
    print("Fetching ontology from memory for graph:", graph_key, "has ontology:", ontology is not None)
    return {
        "has_ontology": ontology is not None,
        "graph_name": graph_key,
        "ontology": ontology,
    }

@mcp.tool
async def resolve_entity_ids(
    search_term: Annotated[str, "The search term to find entities (e.g., a person name, organization, topic)"],
    node_label: Annotated[str, "The node label to filter results to (e.g., 'Person', 'Product')"],
    id_property: Annotated[str, "The JSON path to the ID property within the node's props JSONB column, using Postgres ->> syntax. Default: payload.id"] = "payload.id",
) -> dict:
    """
    Resolve all entity IDs matching a search term and node label using PostgreSQL full-text search.
    This tool removes ambiguity by returning the COMPLETE, AUTHORITATIVE list of matching entity IDs.
    The agent MUST use every returned ID verbatim in its Cypher query — no filtering or subsetting.

    Args:
        search_term: The search term to look up via search_graph_nodes.
        node_label: The exact node label to filter to (case-sensitive, from ontology).
        id_property: Dot-separated path to the ID field in the props JSONB (default: payload.id).
    Returns:
        A dict with entity_ids (list), id_count (int), node_label, and search_term.
    """
    # Build the JSON extraction chain from the dot-separated path
    # e.g. "payload.id" -> props->'payload'->>'id'
    parts = id_property.split(".")
    json_path = "props"
    for part in parts[:-1]:
        json_path += f"->'{part}'"
    json_path += f"->>'{parts[-1]}'"

    sql = f"""
        SELECT {json_path} AS entity_id
        FROM public.search_graph_nodes('{search_term.replace("'", "''")}')
        WHERE node_label = '{node_label.replace("'", "''")}'
        ORDER BY rank DESC;
    """
    print(f"[resolve_entity_ids] search_term={search_term}, node_label={node_label}, id_property={id_property}")
    print(f"[resolve_entity_ids] SQL: {sql}")

    rows = await pg_helper.query_using_sql_cypher(sql, None)

    # Fallback: if FTS returns nothing and the search term has 4+ words,
    # retry with progressively shorter terms (drop trailing words that may
    # be single letters or fragments causing tsquery to fail).
    if not rows:
        words = search_term.split()
        for trim_count in range(1, min(3, len(words))):
            shorter = " ".join(words[: len(words) - trim_count])
            if len(shorter.split()) < 2:
                break
            sql_retry = f"""
                SELECT {json_path} AS entity_id
                FROM public.search_graph_nodes('{shorter.replace("'", "''")}')
                WHERE node_label = '{node_label.replace("'", "''")}'
                ORDER BY rank DESC;
            """
            print(f"[resolve_entity_ids] Retry with shorter term: {shorter}")
            rows = await pg_helper.query_using_sql_cypher(sql_retry, None)
            if rows:
                print(f"[resolve_entity_ids] Shorter term matched {len(rows)} rows")
                break
    entity_ids = [row["entity_id"] for row in rows if row.get("entity_id") is not None]
    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for eid in entity_ids:
        if eid not in seen:
            seen.add(eid)
            unique_ids.append(eid)

    result = {
        "entity_ids": unique_ids,
        "id_count": len(unique_ids),
        "node_label": node_label,
        "search_term": search_term,
    }
    print(f"[resolve_entity_ids] Result: {result}")
    return result


@mcp.tool
async def find_related_nodes(
    entity_ids: Annotated[list[str], "List of anchor entity IDs to find related nodes for"],
    anchor_label: Annotated[str, "The node label of the anchor entities (e.g., 'Product', 'Event')"],
    target_label: Annotated[str, "The node label to search for related nodes (e.g., 'Person', 'Organization')"],
    graph_name: Annotated[str, "Graph name to query"],
    id_property: Annotated[str, "Dot-separated path to the ID property (default: payload.id)"] = "payload.id",
) -> dict:
    """
    Find nodes of a target label that share source documents with the given anchor entities.
    Uses source-based co-occurrence: two nodes sharing a payload.sources entry means they
    participated in the same document/event context.

    Use this AFTER resolve_entity_ids when the user's question implies a related entity
    (e.g., "Who is the contact person for X?", "Who presented Y?", "What staff worked on Z?").

    Args:
        entity_ids: The anchor entity IDs (from resolve_entity_ids).
        anchor_label: Node label of the anchor (e.g., Product, Event).
        target_label: Node label to search for related nodes (e.g., Person, Organization).
        graph_name: The graph to query.
        id_property: Dot-separated path to the entity ID field.
    Returns:
        A dict with related_nodes (list of {id, name, properties}), count, and the query used.
    """
    # Build the Cypher property access from dot-separated path
    cypher_id_path = ".".join(f"{p}" for p in id_property.split("."))

    # Sanitize entity IDs to prevent injection
    safe_ids = ", ".join(f"'{eid.replace(chr(39), chr(39)+chr(39))}'" for eid in entity_ids)

    sql = f"""SELECT * FROM ag_catalog.cypher('{graph_name}', $$
  MATCH (anchor:{anchor_label}) WHERE anchor.{cypher_id_path} IN [{safe_ids}]
  UNWIND coalesce(anchor.payload.sources, []) AS src
  WITH DISTINCT src
  MATCH (related:{target_label}) WHERE related.payload.sources IS NOT NULL
  UNWIND coalesce(related.payload.sources, []) AS rsrc
  WITH related, rsrc, src WHERE rsrc = src
  RETURN DISTINCT related.{cypher_id_path} AS id, related.payload.name AS name, related.payload.attributes AS properties
$$) AS (id ag_catalog.agtype, name ag_catalog.agtype, properties ag_catalog.agtype);"""

    print(f"[find_related_nodes] anchor_label={anchor_label}, target_label={target_label}, entity_ids_count={len(entity_ids)}")
    print(f"[find_related_nodes] SQL: {sql}")

    try:
        rows = await pg_helper.query_using_sql_cypher(sql, graph_name)
    except Exception as e:
        return {
            "related_nodes": [],
            "count": 0,
            "error": str(e),
            "query": sql,
        }

    print(f"[find_related_nodes] Found {len(rows)} related nodes")
    return {
        "related_nodes": rows,
        "count": len(rows),
        "anchor_label": anchor_label,
        "target_label": target_label,
        "query": sql,
    }


@mcp.tool
async def query_using_sql_cypher(
    sql_query: Annotated[str, "SQL Query"],
    graph_name: Annotated[str, "Graph name to use for ag_catalog.cypher(...)"]
) -> list[dict]:
    """
    Execute the sql statement against a PostgreSQL database with the AGE extension.
    The sql query is generated by another agent. This tool simply executes the query and returns the result.
    Args:
        sql_query: The SQL query to execute.
        graph_name: Graph name to use when executing AGE cypher calls.
    Returns:
        The query result as a list of dictionaries.
    """
    rows = await pg_helper.query_using_sql_cypher(sql_query, graph_name)
    print("Query executed, result:\n", rows)
    return rows





if __name__ == "__main__":
    global pg_helper
    pg_helper = asyncio.run(PGAgeHelper.create())

    #test_query = """SELECT *
    #    FROM ag_catalog.cypher('customer_graph', $$
#
    #    MATCH (c:Customer)
    #    WHERE c.payload.name = 'Customer 002'
    #    OPTIONAL MATCH (c)-[:ADOPTED_PRODUCT]->(p:Product)
    #    RETURN c.payload.name AS customer_name, collect(DISTINCT p.payload.name) AS product_names
#
    #    $$) AS (
    #    customer_name ag_catalog.agtype,
    #    product_names ag_catalog.agtype
    #    );"""
    #
    #result = asyncio.run(query_using_sql_cypher(test_query))
    #print("Test query result:\n", result)

    mcp.run(transport="streamable-http", host="0.0.0.0", port=3002)