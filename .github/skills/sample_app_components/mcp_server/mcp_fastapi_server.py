"""
Run:  uvicorn mcp_fastapi_server:app --port 3000 --reload
dapr: dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --port 3000 mcp_fastapi_server:app
"""

import asyncio, json, uuid, socket, os, inspect
from typing import Annotated, Optional
from fastapi import FastAPI, Request, BackgroundTasks, Response, Path
from fastapi.responses import StreamingResponse, JSONResponse
from contextlib import asynccontextmanager
import json
from dotenv import load_dotenv
from datetime import timedelta
from tools import REGISTERED_TOOLS, TOOL_FUNCS, tool
from sse_bus import SESSIONS, sse_event, JSONRPC, publish_progress, publish_message
from pydantic import BaseModel, Field
from pg_age_helper import PGAgeHelper
from typing import Literal, TypedDict
import httpx

load_dotenv()

POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "unknown")

DSN = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", "postgres"),
)

GRAPH = os.getenv("GRAPH", "customer_graph")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

@asynccontextmanager
async def lifespan(app: FastAPI):
    
    try:
        #global pg_helper 
        #pg_helper = await PGAgeHelper.create(DSN, GRAPH)
        yield
    finally:
        pass

app = FastAPI(lifespan=lifespan)



# ───────────────── tools (unchanged API) ─────────────────────────────────────

class WeatherResult(TypedDict, total=False):
    city: str
    country: str | None
    latitude: float
    longitude: float
    timezone: str
    observed: str               # ISO timestamp
    units: Literal["metric", "imperial"]
    temperature: float
    apparent_temperature: float | None
    relative_humidity: float | None
    wind_speed: float | None
    precipitation: float | None
    weather_code: int | None
    is_day: int | None          # 1 day, 0 night
    source: str

async def _geocode(city: str, country: str | None) -> tuple[float, float, dict]:
    params = {
        "name": city,
        "count": 1,
        "language": "en",
        "format": "json",
    }
    if country:
        params["country"] = country

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(GEOCODE_URL, params=params)
        r.raise_for_status()
        data = r.json()
        if not data or not data.get("results"):
            raise ValueError(f"Could not geocode: {city!r}{' '+country if country else ''}")
        return data["results"][0]["latitude"], data["results"][0]["longitude"], data["results"][0]


@tool
async def get_current_weather(
    city: str,
    country: str | None = None,
    units: Literal["metric", "imperial"] = "metric",
) -> WeatherResult:
    """
    Get current weather for a city.

    Args:
        city: City name (e.g. "San Francisco").
        country: Optional ISO 2-letter country code (e.g. "US") to disambiguate.
        units: "metric" (°C, km/h, mm) or "imperial" (°F, mph, inch).

    Returns:
        A JSON object with resolved location and current conditions.
    """
    lat, lon, place = await _geocode(city, country)

    # Map unit strings to Open-Meteo query params
    temp_unit = "celsius" if units == "metric" else "fahrenheit"
    wind_unit = "kmh" if units == "metric" else "mph"
    precip_unit = "mm" if units == "metric" else "inch"

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "precipitation",
            "wind_speed_10m",
            "weather_code",
            "is_day",
        ]),
        "temperature_unit": temp_unit,
        "wind_speed_unit": wind_unit,
        "precipitation_unit": precip_unit,
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        data = r.json()

    cur = data.get("current", {})
    tz = data.get("timezone", "GMT")

    return WeatherResult(
        city=place.get("name", city),
        country=place.get("country_code"),
        latitude=lat,
        longitude=lon,
        timezone=tz,
        observed=cur.get("time"),
        units=units,
        temperature=cur.get("temperature_2m"),
        apparent_temperature=cur.get("apparent_temperature"),
        relative_humidity=cur.get("relative_humidity_2m"),
        wind_speed=cur.get("wind_speed_10m"),
        precipitation=cur.get("precipitation"),
        weather_code=cur.get("weather_code"),
        is_day=cur.get("is_day"),
        source="Open-Meteo",
    )





# ─────────────── call_tool wrapper ensures session_id injection ──────────────
async def call_tool(name: str, raw_args: dict, tasks: BackgroundTasks, session_id: str):

    print(f"[call_tool] {name} args={raw_args} session={session_id}", flush=True)

    if name not in TOOL_FUNCS:
        return "Error: Tool not found"

    fn  = TOOL_FUNCS[name]
    sig = inspect.signature(fn)
    args = dict(raw_args)
    if "session_id" in sig.parameters:
        args["session_id"] = session_id
    result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
    return result

def _ensure_calltool_result(obj):
    if isinstance(obj, dict) and "content" in obj:
        return obj
    return {"content": [{"type": "text", "text": str(obj)}]}

def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()

@app.get("/healthz")
async def healthz():
    # super-fast 200 OK for Dapr liveness/readiness
    return Response(status_code=200)


# ───────────────── SSE channel ───────────────────────────────────────────────

@app.get("/mcp")
async def mcp_sse(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    print(f"[@app.get(/mcp)] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        #yield "event: message\ndata: {}\n\n"
        heartbeat_every = 1.0
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = session.q.get_nowait()
                client_msg = f"{msg}\n\n"
                yield client_msg
            except asyncio.QueueEmpty:
                yield "no message"
                await asyncio.sleep(heartbeat_every)

    return StreamingResponse(event_stream(), media_type="text/event-stream")



# ───────────────── health check ─────────────────────────────────────────────
@app.get("/status")
async def status(request: Request):
    return {"status": "ok"}

# ───────────────── JSON-RPC handler ──────────────────────────────────────────
@app.post("/mcp")
async def mcp_post(req: Request, tasks: BackgroundTasks):
    req_json   = await req.json()
    raw        = req.headers.get("Mcp-Session-Id")
    session_id = _normalize_session_id(raw, default=str(uuid.uuid4()))
    # ensure session exists for any tool that will stream
    await SESSIONS.get_or_create(session_id)

    method = req_json.get("method")
    rpc_id = req_json.get("id")
    print(f"[@app.post(/mcp) POST] method={method} session={session_id} pod={POD} rev={REV}", flush=True)

    match method:
        case "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "fastapi-mcp", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "callTool": True}}, #{"listTools": True, "toolCalling": True, "sse": True},
            }

        case "ping" | "$/ping":
            result = {} #{"pong": True}

        case "workspace/listTools" | "$/listTools" | "list_tools" | "tools/list":
            result = {"tools": REGISTERED_TOOLS}

        case "tools/call" | "$/call":
            tool_name = req_json["params"]["name"]
            raw_args  = req_json["params"].get("arguments", {})
            raw_out   = await call_tool(tool_name, raw_args, tasks, session_id)
            result    = _ensure_calltool_result(raw_out)

        case _ if method in TOOL_FUNCS:
            raw_args = req_json.get("params", {})
            raw_out  = await call_tool(method, raw_args, tasks, session_id)
            result   = _ensure_calltool_result(raw_out)

        case _:
            if rpc_id is None:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return JSONResponse(
                content={"jsonrpc": JSONRPC, "id": rpc_id,
                         "error": {"code": -32601, "message": "method not found"}},
                headers={"Mcp-Session-Id": session_id},
                background=tasks,
            )

    return JSONResponse(
        content={"jsonrpc": JSONRPC, "id": rpc_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
        background=tasks,
    )

# ───────────────── session cleanup ───────────────────────────────────────────
@app.delete("/mcp")
async def mcp_delete(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    if session_id:
        deleted = await SESSIONS.delete(session_id)
        return Response(status_code=204 if deleted else 404)
    return Response(status_code=404)

