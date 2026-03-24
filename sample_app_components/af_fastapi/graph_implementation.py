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
    logger.warning("Azure credentials not configured. Graph workflow will be unavailable.")
MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")


print("Using MCP_ENDPOINT:", MCP_ENDPOINT)
print("Using AZURE_OPENAI_ENDPOINT:", AZURE_OPENAI_ENDPOINT)
print("Using AZURE_DEPLOYMENT_NAME:", AZURE_DEPLOYMENT_NAME)

if not MCP_ENDPOINT:
    raise ValueError("MCP_ENDPOINT environment variable must be set")

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
    def __init__(self):
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
            return dict(api_key=_aoai_api_key, endpoint=AZURE_OPENAI_ENDPOINT, deployment_name=AZURE_DEPLOYMENT_NAME, **extra)
        return dict(ad_token=token.token, endpoint=AZURE_OPENAI_ENDPOINT, deployment_name=AZURE_DEPLOYMENT_NAME, **extra)
    
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
            self._graph_query_generator_agent = ChatAgent(
                name="graph query generator agent",
                description="Graph query generator agent that can answer questions about the graph using a graph query tool.",
                instructions=""" 
                You are a PostgreSQL AGE query generator. Your job is to produce correct, executable SQL that embeds Cypher for a CRM knowledge graph named customer_graph 
                and call the tool query_using_sql_cypher to get results. 
                Only output code (one SQL statement per answer) unless asked otherwise.

                Graph Database name should always be 'customer_graph'

                Graph schema (labels, relationships, properties)

                Node labels

                Customer — properties are stored under payload:
                payload.id, payload.name, payload.segment, payload.owner, payload.satisfaction_score, payload.health, payload.growth_potential, payload.current_arr, payload.current_mrr, payload.timezone, payload.notes

                Contract — payload.id, payload.customer_id, payload.start_date, payload.end_date, payload.amount, payload.status, payload.auto_renew, payload.renewal_term_months, payload.last_renewal_date, payload.next_renewal_date

                SupportCase — payload.id, payload.customer_id, payload.opened_at, payload.last_updated_at, payload.status, payload.priority, payload.escalation_level, payload.sla_breached, payload.product_area, payload.subject, payload.tags

                Communication — payload.id, payload.customer_id, payload.timestamp, payload.channel, payload.counterpart, payload.direction, payload.sentiment, payload.summary

                Opportunity — payload.id, payload.customer_id, payload.opp_type, payload.product, payload.stage, payload.amount, payload.opened_at, payload.expected_close

                TelemetryMonth — payload.customer_id, payload.month, payload.dau, payload.mau, payload.feature_adoption, payload.usage_hours, payload.incidents

                QBRArtifact — payload.customer_id, payload.report_period, payload.highlights, payload.risks, payload.asks, payload.attachments

                Product(name), Feature(name) (catalog nodes)

                Relationships

                (:Customer)-[:ADOPTED_PRODUCT]->(:Product)

                (:Customer)-[:HAS_CONTRACT]->(:Contract)

                (:Customer)-[:RAISED_CASE]->(:SupportCase)

                (:SupportCase)-[:ABOUT_AREA]->(:Feature)

                (:Customer)-[:HAD_COMM]->(:Communication)

                (:Customer)-[:HAS_OPPORTUNITY]->(:Opportunity)

                (:Opportunity)-[:FOR_PRODUCT]->(:Product)

                (:Customer)-[:HAS_TELEMETRY]->(:TelemetryMonth)

                (:TelemetryMonth)-[:ADOPTED_FEATURE {percent, month}]->(:Feature)

                (:Customer)-[:HAS_QBR]->(:QBRArtifact)

                All business properties live under .payload. Access them as alias.payload.<field>.

                Output format (SQL wrapper)

                Always wrap Cypher in this shape and ensure the number of RETURN items equals the column list:
                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                // Cypher goes here

                $$) AS (
                col1 ag_catalog.agtype,
                col2 ag_catalog.agtype,
                -- etc.
                );

                
                """,
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token)),
                #chat_message_store_factory=self._create_message_store,
                #tools=graph_age_mcp_server
            )

            self._graph_query_validator_agent = ChatAgent(
                name="graph_query_validator",
                description="Graph query validator agent that can validate and refine graph queries using a graph query tool.",
                instructions="""You are a graph query validator agent. Your job is to validate and refine the SQL/Cypher queries generated by the 
                graph query generator agent and execute the sql query using the 'query_using_sql_cypher' tool to get results. 
                
                Make sure that Graph Database name should always be 'customer_graph'. for example:
                SELECT * FROM cypher('customer_graph', $$
                MATCH (v:Customer)-[:ADOPTED_PRODUCT]-(m)
                RETURN m
                LIMIT 3
                $$) as (m agtype);

                Example working cypher queries:

                User question: simple query for a given customer name

                Working valid Cypher Query:

                SELECT * FROM cypher('customer_graph', $$
                MATCH (n:Customer)
                WHERE n.payload.name = 'Customer 080'
                RETURN id(n) AS id, labels(n) AS label, n.payload AS properties,
                    'node' AS kind, NULL AS src, NULL AS dst

                $$) AS (id ag_catalog.agtype,
                                label ag_catalog.agtype,
                                properties ag_catalog.agtype,
                                kind ag_catalog.agtype,
                                src ag_catalog.agtype,
                                dst ag_catalog.agtype);

                
                User question:  simple query to see top 3 connected nodes

                Working valid Cypher Query:

                SELECT * FROM cypher('customer_graph', $$
                MATCH (v:Customer)-[:ADOPTED_PRODUCT]-(m)
                RETURN m
                LIMIT 3
                $$) as (m agtype);
                
                User question: what are the top 3 products used by 'Customer 080'

                Working valid Cypher Query:

                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 001'
                OPTIONAL MATCH (c)-[:ADOPTED_PRODUCT]->(p:Product)

                WITH c, p
                ORDER BY coalesce(p.payload.name, '') ASC

                WITH c,
                    collect(
                        CASE WHEN p IS NOT NULL
                            THEN p.payload.name
                            ELSE NULL
                        END
                    ) AS raw_names

                WITH c, [x IN raw_names WHERE x IS NOT NULL][0..3] AS top_products

                RETURN coalesce(c.payload.name, '') AS customer_name,
                        top_products

                $$) AS (
                customer_name ag_catalog.agtype,
                top_products  ag_catalog.agtype
                );

                User question:  what are the  3 products used by the same customers
                
                Working valid Cypher Query:

                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 080'

                OPTIONAL MATCH (c)-[:ADOPTED_PRODUCT]->(p_c:Product)
                WITH c, collect(DISTINCT p_c) AS adopted_products

                // pick up to 3 seed products
                WITH c, [x IN adopted_products WHERE x IS NOT NULL][0..3] AS seed_products
                UNWIND seed_products AS seed

                OPTIONAL MATCH (cust:Customer)-[:ADOPTED_PRODUCT]->(seed)
                WITH seed_products, collect(DISTINCT cust) AS cohort

                UNWIND cohort AS cust
                OPTIONAL MATCH (cust)-[:ADOPTED_PRODUCT]->(p2:Product)
                WHERE p2 IS NOT NULL AND NOT p2 IN seed_products

                // aggregate
                WITH p2, count(DISTINCT cust) AS adopter_count

                // extra WITH to avoid the bug
                WITH p2, adopter_count
                ORDER BY adopter_count DESC, p2.payload.name ASC
                LIMIT 3

                RETURN p2.payload.name AS product_name, adopter_count

                $$) AS (
                product_name ag_catalog.agtype,
                adopter_count ag_catalog.agtype
                );


                User question:
                I am going on a sales call with customer 'Customer 080'. Provide a consolidated customer insight including:

                - Opportunities for upsell or cross-sell

                Working valid Cypher Query:
                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 080'

                OPTIONAL MATCH (c)-[:RAISED_CASE]->(sc:SupportCase)
                WITH c,
                    collect(CASE WHEN sc IS NOT NULL AND (sc.payload.status = 'Open' OR sc.payload.status = 'Pending' OR sc.payload.status = 'In Progress' OR sc.payload.status = 'Escalated') THEN {
                        case_id: sc.payload.id,
                        status: sc.payload.status,
                        priority: sc.payload.priority,
                        opened_at: sc.payload.opened_at,
                        last_updated_at: sc.payload.last_updated_at,
                        escalation_level: sc.payload.escalation_level,
                        sla_breached: coalesce(sc.payload.sla_breached, false),
                        product_area: sc.payload.product_area,
                        subject: sc.payload.subject,
                        tags: sc.payload.tags
                    } ELSE NULL END) AS open_cases_tmp,
                    sum(CASE WHEN sc IS NOT NULL AND (sc.payload.status = 'Open' OR sc.payload.status = 'Pending' OR sc.payload.status = 'In Progress' OR sc.payload.status = 'Escalated') THEN 1 ELSE 0 END) AS open_case_count

                WITH c,
                    coalesce(open_case_count, 0) AS open_case_count,
                    [x IN open_cases_tmp WHERE x IS NOT NULL] AS open_cases

                OPTIONAL MATCH (c)-[:HAS_OPPORTUNITY]->(o:Opportunity)
                WITH c, open_case_count, open_cases,
                    collect(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN {
                        opp_id: o.payload.id,
                        opp_type: o.payload.opp_type,
                        product: o.payload.product,
                        stage: o.payload.stage,
                        amount: coalesce(o.payload.amount, 0),
                        opened_at: o.payload.opened_at,
                        expected_close: o.payload.expected_close
                    } ELSE NULL END) AS upsell_xsell_opps_tmp,
                    sum(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN coalesce(o.payload.amount, 0) ELSE 0 END) AS opp_total_amount,
                    sum(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN 1 ELSE 0 END) AS opp_count

                WITH c, open_case_count, open_cases,
                    coalesce(opp_count, 0) AS opp_count,
                    coalesce(opp_total_amount, 0) AS opp_total_amount,
                    [x IN upsell_xsell_opps_tmp WHERE x IS NOT NULL] AS upsell_xsell_opps

                RETURN
                    c.payload.id AS customer_id,
                    c.payload.name AS customer_name,
                    c.payload.segment AS segment,
                    c.payload.owner AS owner,
                    c.payload.health AS health,
                    c.payload.satisfaction_score AS satisfaction_score,
                    c.payload.current_arr AS current_arr,
                    c.payload.current_mrr AS current_mrr,
                    open_case_count AS open_case_count,
                    open_cases AS open_cases,
                    opp_count AS opp_count,
                    opp_total_amount AS opp_total_amount,
                    upsell_xsell_opps AS upsell_xsell_opps

                $$) AS (
                customer_id ag_catalog.agtype,
                customer_name ag_catalog.agtype,
                segment ag_catalog.agtype,
                owner ag_catalog.agtype,
                health ag_catalog.agtype,
                satisfaction_score ag_catalog.agtype,
                current_arr ag_catalog.agtype,
                current_mrr ag_catalog.agtype,
                open_case_count ag_catalog.agtype,
                open_cases ag_catalog.agtype,
                opp_count ag_catalog.agtype,
                opp_total_amount ag_catalog.agtype,
                upsell_xsell_opps ag_catalog.agtype
                );
                
                Required conventions & gotchas

                Use .payload for all business fields
                Access properties as alias.payload.<field> (e.g., c.payload.name, ctr.payload.amount).

                Keep rows with OPTIONAL MATCH
                Use OPTIONAL MATCH for edges that might be missing to avoid dropping the base node.

                Never filter an OPTIONAL MATCH with WHERE on the optional variable
                A WHERE clause attached to an OPTIONAL MATCH that references the optional variable (e.g., WHERE sc.payload.status = 'open') will drop nulls and effectively turn it into an inner match.
                Do this instead: compute flags in a WITH, then aggregate:

                OPTIONAL MATCH (c)-[:RAISED_CASE]->(sc:SupportCase)
                WITH c, sc, coalesce(sc.payload.status,'') AS sc_status
                WITH c, sc, (sc IS NOT NULL AND sc_status IN ['open','Open','OPEN']) AS is_pending
                WITH c,
                    sum(CASE WHEN is_pending THEN 1 ELSE 0 END) AS open_cnt,
                    collect(CASE WHEN is_pending THEN { id: sc.payload.id } ELSE NULL END) AS tmp
                WITH c, open_cnt, [x IN tmp WHERE x IS NOT NULL] AS open_cases
                RETURN ...


                RETURN is terminal
                Once you RETURN, the query ends. If you need further processing, use WITH (not RETURN) and keep piping until your single final RETURN.


                Example working cypher queries:

                User question: simple query for a given customer name

                Working valid Cypher Query:

                SELECT * FROM cypher('customer_graph', $$
                MATCH (n:Customer)
                WHERE n.payload.name = 'Customer 080'
                RETURN id(n) AS id, labels(n) AS label, n.payload AS properties,
                    'node' AS kind, NULL AS src, NULL AS dst

                $$) AS (id ag_catalog.agtype,
                                label ag_catalog.agtype,
                                properties ag_catalog.agtype,
                                kind ag_catalog.agtype,
                                src ag_catalog.agtype,
                                dst ag_catalog.agtype);

                
                User question:  simple query to see top 3 connected nodes

                Working valid Cypher Query:

                SELECT * FROM cypher('customer_graph', $$
                MATCH (v:Customer)-[:ADOPTED_PRODUCT]-(m)
                RETURN m
                LIMIT 3
                $$) as (m agtype);
                
                User question: what are the top 3 products used by 'Customer 001'

                Working valid Cypher Query:

                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 001'
                OPTIONAL MATCH (c)-[:ADOPTED_PRODUCT]->(p:Product)

                WITH c, p
                ORDER BY coalesce(p.payload.name, '') ASC

                WITH c,
                    collect(
                        CASE WHEN p IS NOT NULL
                            THEN p.payload.name
                            ELSE NULL
                        END
                    ) AS raw_names

                WITH c, [x IN raw_names WHERE x IS NOT NULL][0..3] AS top_products

                RETURN coalesce(c.payload.name, '') AS customer_name,
                        top_products

                $$) AS (
                customer_name ag_catalog.agtype,
                top_products  ag_catalog.agtype
                );

                User question:  what are the  3 products used by the same customers
                
                Working valid Cypher Query:

                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 080'

                OPTIONAL MATCH (c)-[:ADOPTED_PRODUCT]->(p_c:Product)
                WITH c, collect(DISTINCT p_c) AS adopted_products

                // pick up to 3 seed products
                WITH c, [x IN adopted_products WHERE x IS NOT NULL][0..3] AS seed_products
                UNWIND seed_products AS seed

                OPTIONAL MATCH (cust:Customer)-[:ADOPTED_PRODUCT]->(seed)
                WITH seed_products, collect(DISTINCT cust) AS cohort

                UNWIND cohort AS cust
                OPTIONAL MATCH (cust)-[:ADOPTED_PRODUCT]->(p2:Product)
                WHERE p2 IS NOT NULL AND NOT p2 IN seed_products

                // aggregate
                WITH p2, count(DISTINCT cust) AS adopter_count

                // extra WITH to avoid the bug
                WITH p2, adopter_count
                ORDER BY adopter_count DESC, p2.payload.name ASC
                LIMIT 3

                RETURN p2.payload.name AS product_name, adopter_count

                $$) AS (
                product_name ag_catalog.agtype,
                adopter_count ag_catalog.agtype
                );


                User question:
                I am going on a sales call with customer 'Customer 080'. Provide a consolidated customer insight including:

                - Opportunities for upsell or cross-sell

                Working valid Cypher Query:
                SELECT *
                FROM ag_catalog.cypher('customer_graph', $$

                MATCH (c:Customer)
                WHERE c.payload.name = 'Customer 080'

                OPTIONAL MATCH (c)-[:RAISED_CASE]->(sc:SupportCase)
                WITH c,
                    collect(CASE WHEN sc IS NOT NULL AND (sc.payload.status = 'Open' OR sc.payload.status = 'Pending' OR sc.payload.status = 'In Progress' OR sc.payload.status = 'Escalated') THEN {
                        case_id: sc.payload.id,
                        status: sc.payload.status,
                        priority: sc.payload.priority,
                        opened_at: sc.payload.opened_at,
                        last_updated_at: sc.payload.last_updated_at,
                        escalation_level: sc.payload.escalation_level,
                        sla_breached: coalesce(sc.payload.sla_breached, false),
                        product_area: sc.payload.product_area,
                        subject: sc.payload.subject,
                        tags: sc.payload.tags
                    } ELSE NULL END) AS open_cases_tmp,
                    sum(CASE WHEN sc IS NOT NULL AND (sc.payload.status = 'Open' OR sc.payload.status = 'Pending' OR sc.payload.status = 'In Progress' OR sc.payload.status = 'Escalated') THEN 1 ELSE 0 END) AS open_case_count

                WITH c,
                    coalesce(open_case_count, 0) AS open_case_count,
                    [x IN open_cases_tmp WHERE x IS NOT NULL] AS open_cases

                OPTIONAL MATCH (c)-[:HAS_OPPORTUNITY]->(o:Opportunity)
                WITH c, open_case_count, open_cases,
                    collect(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN {
                        opp_id: o.payload.id,
                        opp_type: o.payload.opp_type,
                        product: o.payload.product,
                        stage: o.payload.stage,
                        amount: coalesce(o.payload.amount, 0),
                        opened_at: o.payload.opened_at,
                        expected_close: o.payload.expected_close
                    } ELSE NULL END) AS upsell_xsell_opps_tmp,
                    sum(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN coalesce(o.payload.amount, 0) ELSE 0 END) AS opp_total_amount,
                    sum(CASE WHEN o IS NOT NULL AND (o.payload.opp_type = 'Upsell' OR o.payload.opp_type = 'Cross-sell') AND NOT (o.payload.stage = 'Closed Won' OR o.payload.stage = 'Closed Lost') THEN 1 ELSE 0 END) AS opp_count

                WITH c, open_case_count, open_cases,
                    coalesce(opp_count, 0) AS opp_count,
                    coalesce(opp_total_amount, 0) AS opp_total_amount,
                    [x IN upsell_xsell_opps_tmp WHERE x IS NOT NULL] AS upsell_xsell_opps

                RETURN
                    c.payload.id AS customer_id,
                    c.payload.name AS customer_name,
                    c.payload.segment AS segment,
                    c.payload.owner AS owner,
                    c.payload.health AS health,
                    c.payload.satisfaction_score AS satisfaction_score,
                    c.payload.current_arr AS current_arr,
                    c.payload.current_mrr AS current_mrr,
                    open_case_count AS open_case_count,
                    open_cases AS open_cases,
                    opp_count AS opp_count,
                    opp_total_amount AS opp_total_amount,
                    upsell_xsell_opps AS upsell_xsell_opps

                $$) AS (
                customer_id ag_catalog.agtype,
                customer_name ag_catalog.agtype,
                segment ag_catalog.agtype,
                owner ag_catalog.agtype,
                health ag_catalog.agtype,
                satisfaction_score ag_catalog.agtype,
                current_arr ag_catalog.agtype,
                current_mrr ag_catalog.agtype,
                open_case_count ag_catalog.agtype,
                open_cases ag_catalog.agtype,
                opp_count ag_catalog.agtype,
                opp_total_amount ag_catalog.agtype,
                upsell_xsell_opps ag_catalog.agtype
                );

                Aggregation pattern (AGE-safe)



                Syntax reminders:

                Do NOT use reduce(...).

                Do NOT use list/pattern comprehensions that filter by property access (e.g., [x IN list WHERE x.payload.foo]).

                Instead, compute booleans/derived scalars in a WITH, aggregate with SUM(CASE ...), and build lists with:

                collect(CASE WHEN cond THEN { ... } ELSE NULL END) AS tmp
                WITH [x IN tmp WHERE x IS NOT NULL] AS clean


                Null safety

                Numeric: coalesce(sum(...), 0)

                Scalars: coalesce(field, default)

                Booleans: coalesce(flag, false)

                Case folding
                AGE doesn’t support SQL lower(). Prefer exact string matches when you control casing. If normalization is needed, compute it in a WITH using toLower(field) and compare there (don’t attach it to OPTIONAL MATCH as a WHERE):

                WITH c, sc, toLower(coalesce(sc.payload.status,'')) AS st
                WITH c, sc, (st IN ['open','pending','escalated']) AS is_pending


                Column list must match RETURN
                The number and order of RETURN items must exactly match the AS ( ... ) column list in the SQL wrapper.

                IDs

                Internal node id: id(n)

                Business id: n.payload.id

                Close all blocks

                Close map literals }

                Close the $$ block before AS (...)

                One SQL statement per answer

                If asked for multiple sections (e.g., revenue + cases + opportunities), compute them in one Cypher with proper WITH pipelines and return all requested fields in one row per customer unless a list is explicitly requested.

                Hard “don'ts”

                Do NOT use reduce(...), list/pattern comprehensions with property access in filters, APOC procedures, or SQL functions like lower() inside Cypher.

                Do NOT return a single map while declaring multiple columns (or vice versa).

                Do NOT omit closing braces or ``` or the  $$/AS (...) wrapper. 
                Do NOT add \n or other escape characters.
                Graph Database name should always be 'customer_graph'
                execute the sql query using the 'query_using_sql_cypher' tool to get results.
                Share the results with other agents for further processing.

                """,
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
                chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token)),
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
                    instructions="""Manage the workflow between graph query executor, and 
                    final response agents to answer the user's question accurately using the tools provided.
                    Do not modify the generated queries. 
                    
                    """,
                    task_ledger_full_prompt="""
                    Look at the user question guide the agents to answer the question step by step. Be precise and concise in your instructions. Do not add unnecessary information.
                    1. The graph query generator agent generates a SQL/Cypher query based on the user's question.
                    2. The graph query validator agent validates the generated query and executes it using the 'query_using_sql_cypher' tool to get results.
                    3. State the final response to the user.
                    4. If no results are found, the final response should state that no results were found.

                    Do ask any agent to generate response. Ask the agents to use facts only from the graph query executor agent to provide the final response.

                    """,
                    final_answer_prompt=""" 
                    Based on the results obtained from the graph query executor agent, provide a concise and accurate answer to the user's question.
                    If no results were found, state that no results were found.
                    """,
                    chat_client=AzureOpenAIChatClient(**self._chat_client_kwargs(token, temperature=0.0)),
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
            print(f"Workflow execution failed: {e}")
            yield _ndjson({"type": "error", "message": f"Workflow execution failed: {e}"})



