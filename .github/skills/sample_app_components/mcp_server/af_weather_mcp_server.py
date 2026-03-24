from typing import Annotated
from fastmcp import FastMCP
from typing import Literal, TypedDict
import httpx



mcp = FastMCP("Weather MCP Server")
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

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


@mcp.tool
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




if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=3001)