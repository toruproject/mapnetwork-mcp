"""MCP server for MapNetwork — generates styled map images from a place name or coordinates."""

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
from mcp.server.fastmcp import FastMCP, Image

BASE_URL = os.environ.get("MAPNETWORK_BASE_URL", "https://mapnetwork.app")
POLL_INTERVAL_SEC = 5.0
MAX_WAIT_SEC = 120

mcp = FastMCP(
    "MapNetwork",
    instructions=(
        "You have access to MapNetwork, which generates styled map images (PNG or SVG) "
        "for any location on Earth.\n\n"
        "## Key capabilities\n"
        "- **Single location**: pass `place` (geocoded server-side) or `lat`/`lng` as the map center\n"
        "- **Multiple locations**: pass `markers` — a list of places to pin on the map. "
        "Each marker needs only a `label` (geocoded automatically); explicit `lat`/`lng` is optional. "
        "When `markers` are given without a `place`/`lat`/`lng` center, the server derives the center "
        "from the centroid of the markers and sets the radius automatically (1.2× farthest marker distance). "
        "Use this whenever the user asks to show multiple places on a single map.\n"
        "- **Circular area**: specify `radius` in meters (default 500, max 2500) around the center\n"
        "- **Rectangular area**: specify `size_ew` (east-west) and `size_ns` (north-south) in meters "
        "instead of radius — useful when the area of interest is not square\n"
        "- **Layers**: choose which features appear. "
        "Use EXACTLY these values (singular, no trailing 's'): "
        "'road', 'highway', 'driving', 'walking', 'railway', 'waterline', 'poi'. "
        "Wrong: 'roads', 'railways', 'waterlines' — these will be rejected by the server.\n"
        "- **Color themes** via `color_set`: "
        "white (clean, default), darkBlue (navy bg), darkGreen (dark teal bg), "
        "popArt (blue bg, bold contrast), lightBlue (pale blue bg), lightGreen (pale green bg), "
        "beige (warm peach bg), magenta (hot-pink bg), gray (monochrome), "
        "black (dark mode), brawn (dark brown/earthy bg)\n"
        "- **SVG output**: request `format='svg'` for a vector file instead of PNG\n\n"
        "## Route overlay\n"
        "To show a walking or driving route on a map:\n"
        "1. Call `compute_route` with `from_location` and `to_location` (place name or coords)\n"
        "2. Call `generate_map` with `route=<result>` and `markers=[result['from'], result['to']]`\n"
        "The route is drawn on top of all other layers. "
        "Add `'color': '#RRGGBB'` to the route dict to customise the line color.\n\n"
        "## Important: re-download without regenerating\n"
        "After generating a map, `generate_map` returns a `dataKey`. "
        "You can call `redownload_map` with that `dataKey` to get the same map in a different "
        "format (png/svg) or color theme — **no regeneration needed**. "
        "Use this when the user asks to change only the appearance after already generating the map.\n\n"
        "## Open in the MapNetwork editor\n"
        "Any generated map can be opened and edited interactively in the MapNetwork web UI at:\n"
        "  https://mapnetwork.app/edit?dataKey=<dataKey>\n"
        "Mention this URL when the user might want to customize markers, colors, or layout manually. "
        "The map data format is identical to what the UI produces when uploading data.\n\n"
        "## Color themes\n"
        "MapNetwork supports 11 color themes via color_set (white, darkBlue, darkGreen, popArt, lightBlue, "
        "lightGreen, beige, magenta, gray, black, brawn). "
        "Mention this when relevant, but do not ask the user unprompted. "
        "Only set color_set when the user explicitly requests a theme.\n\n"
        "## Parameter discipline\n"
        "- **lat/lng**: Never guess or estimate coordinates from training data. "
        "If the location is known only by name, use `place` and let the server geocode it.\n"
        "- **radius / size_ew / size_ns**: Do not set these unless the user has explicitly asked for "
        "a specific map range or shape. Omit them to let the server apply its default (500 m radius)."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_filename(label: str, format: str) -> Path:
    slug = re.sub(r"_+", "_", re.sub(r"[^\w぀-鿿]", "_", label)).strip("_")[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.home() / "Downloads"
    out_dir.mkdir(exist_ok=True)
    return out_dir / f"map_{slug}_{timestamp}.{format}"


async def _download(client: httpx.AsyncClient, data_key: str, format: str,
                    color_set: str | None, canvas_width: int | None, canvas_height: int | None,
                    edge_weight: int | None = None) -> bytes:
    params: dict = {"dataKey": data_key, "format": format}
    if color_set is not None:
        params["colorSet"] = color_set
    if canvas_width is not None:
        params["canvasWidth"] = canvas_width
    if canvas_height is not None:
        params["canvasHeight"] = canvas_height
    if edge_weight is not None:
        params["edgeWeight"] = edge_weight
    resp = await client.get(f"{BASE_URL}/download", params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed ({resp.status_code}): {resp.text}")
    return resp.content


# ---------------------------------------------------------------------------
# Tool: compute_route
# ---------------------------------------------------------------------------

@mcp.tool()
async def compute_route(
    from_location: Annotated[
        dict,
        "Start point of the route. "
        "Use {\"label\": \"place name\"} to geocode server-side, "
        "or {\"label\": \"...\", \"location\": {\"lat\": ..., \"lng\": ...}} to supply explicit coordinates. "
        "Example: {\"label\": \"赤坂駅\"}",
    ],
    to_location: Annotated[
        dict,
        "End point of the route. Same format as from_location. "
        "Example: {\"label\": \"赤坂氷川神社\"}",
    ],
    mode: Annotated[
        str,
        "Routing mode: 'walking' (default, footpaths and streets) or 'driving' (car-accessible roads).",
    ] = "walking",
) -> dict:
    """Compute a walking or driving route between two locations.

    Returns the route as an ordered list of coordinates, plus the resolved from/to locations.
    Pass the result directly to generate_map's `route` parameter to overlay it on a map image.
    Use from/to as `markers` in generate_map to place pins at the start and end points.

    Typical flow:
    1. route = compute_route(from_location={"label": "A"}, to_location={"label": "B"})
    2. generate_map(route=route, markers=[route["from"], route["to"]], ...)
    """
    if mode not in ("walking", "driving"):
        raise ValueError("mode must be 'walking' or 'driving'")

    body = {"from": from_location, "to": to_location, "mode": mode}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/route", json=body, timeout=180)
        if resp.status_code == 404:
            raise RuntimeError("No route found between the two locations.")
        if resp.status_code == 504:
            raise RuntimeError("Route computation timed out. Try a shorter distance or different locations.")
        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Route request failed ({resp.status_code}): {detail}")
        return resp.json()


# ---------------------------------------------------------------------------
# Tool: generate_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_map(
    place: Annotated[
        str | None,
        "Place name to center the map on (e.g. 'Shibuya Station', '東京駅', 'Eiffel Tower'). "
        "A red pin is placed at the resolved location. Use this OR lat/lng, not both. "
        "Omit when using markers-only mode.",
    ] = None,
    lat: Annotated[
        float | None,
        "Latitude of the map center. Must be combined with lng. "
        "ONLY set this when you have a precise, verified coordinate value — never guess or estimate. "
        "If the location is known only by name, use `place` instead and let the server geocode it. "
        "Omit when using markers-only mode.",
    ] = None,
    lng: Annotated[
        float | None,
        "Longitude of the map center. Must be combined with lat. "
        "ONLY set this when you have a precise, verified coordinate value — never guess or estimate. "
        "If the location is known only by name, use `place` instead and let the server geocode it. "
        "Omit when using markers-only mode.",
    ] = None,
    markers: Annotated[
        list[dict] | None,
        "List of locations to pin on the map as blue markers. "
        "Each item: {\"label\": \"place name\"} — the server geocodes it automatically. "
        "Optionally include explicit coordinates: {\"label\": \"...\", \"location\": {\"lat\": ..., \"lng\": ...}}. "
        "Each marker may also include an optional \"icon\" field to override its appearance: "
        "{\"label\": \"...\", \"icon\": {\"color\": \"#ff0000\"}}. "
        "All icon sub-fields (code/size/color) are optional — omit any to keep the default. "
        "When markers are provided without place/lat/lng, the server derives the map center from their centroid "
        "and sets radius automatically. "
        "Example for two stations: [{\"label\": \"東京駅\"}, {\"label\": \"新宿駅\"}]",
    ] = None,
    place_icon: Annotated[
        dict | None,
        "Custom icon for the `place` pin (or center pin if no `place`). "
        "All fields are optional — specify only what you want to override. "
        "Fields: code (FontAwesome \\uXXXX), size (pixels), color (CSS hex). "
        "Example (red icon, larger): {\"color\": \"#ff0000\", \"size\": 50}. "
        "Omit entirely to use the default icon.",
    ] = None,
    name: Annotated[
        str | None,
        "Map name stored in the data file. Used as the default download filename. "
        "Omit to leave unnamed.",
    ] = None,
    title: Annotated[
        str | None,
        "Optional title text rendered in the bottom-left corner of the map image. "
        "Use when the user wants a label on the map (e.g. the place name or a description). "
        "Omit to generate an untitled map.",
    ] = None,
    radius: Annotated[
        int | None,
        "Circular coverage radius in meters (max 2500). "
        "Mutually exclusive with size_ew/size_ns. "
        "ONLY set this when the user has explicitly requested a specific coverage range. "
        "If the user has not specified a range, omit it — the server applies a sensible default (500 m).",
    ] = None,
    size_ew: Annotated[
        float | None,
        "East-west width of a rectangular coverage area in meters. "
        "Must be combined with size_ns. Mutually exclusive with radius. "
        "ONLY set this when the user has explicitly requested a rectangular area of specific dimensions. "
        "Do not infer dimensions — omit and let the server use its default if the user has not specified.",
    ] = None,
    size_ns: Annotated[
        float | None,
        "North-south height of a rectangular coverage area in meters. "
        "Must be combined with size_ew. Mutually exclusive with radius. "
        "ONLY set this when the user has explicitly requested a rectangular area of specific dimensions.",
    ] = None,
    layers: Annotated[
        list[str] | None,
        "Map layers to render. Default ['road', 'poi']. "
        "IMPORTANT: use EXACTLY these singular spellings — never add 's' or change the spelling:\n"
        "'road'      — all roads (supersedes highway/driving/walking; no need to add them when road is present)\n"
        "'highway'   — major roads only\n"
        "'driving'   — car-accessible roads\n"
        "'walking'   — footpaths and pedestrian walkways\n"
        "'railway'   — train and subway lines\n"
        "'waterline' — rivers, lakes, and coastlines\n"
        "'poi'       — point-of-interest icons (filterable with poi_types)\n"
        "Example for road + railway + POI: ['road', 'railway', 'poi']",
    ] = None,
    poi_types: Annotated[
        list[str] | None,
        "Filter POI categories when 'poi' is in layers. Omit to include all. "
        "Valid values: museum, library, theatre, convenience, supermarket, "
        "school, religion, station, park, hospital, cityhall, cafe, restaurant.",
    ] = None,
    route: Annotated[
        dict | None,
        "Route to overlay on the map. Pass the full response from compute_route() directly — "
        "the server uses coords and (optionally) color from it. "
        "To customise the line color, add a 'color' key: {**route_result, 'color': '#FF4500'}. "
        "Omit to generate a map without a route overlay.",
    ] = None,
    color_set: Annotated[
        str | None,
        "Color theme for the map. Only set when the user explicitly requests one. "
        "Baked into the stored map data so redownload_map uses the same theme by default. "
        "Can be overridden per-download by passing color_set to redownload_map. "
        "Available themes: white, darkBlue, darkGreen, popArt, lightBlue, lightGreen, beige, magenta, gray, black, brawn.",
    ] = None,
    format: Annotated[
        str,
        "Output file format: 'png' (default, raster image) or 'svg' (vector, scalable to any size).",
    ] = "png",
    canvas_width: Annotated[int | None, "Canvas width in pixels. Omit to use server default (1000)."] = None,
    canvas_height: Annotated[int | None, "Canvas height in pixels. Omit to use server default (700)."] = None,
    edge_weight: Annotated[
        int | None,
        "Road and line width adjustment. Positive values thicken lines, negative values thin them. "
        "Default 0 (no adjustment). Example: 2 to make roads slightly thicker, -1 to make them thinner.",
    ] = None,
) -> list:
    """Generate a styled map image for a given location and save it to Downloads.

    Coverage shape:
    - Default (no radius, no size): circular 500 m radius
    - radius=N: circular, N meters
    - size_ew + size_ns: rectangular bounding box

    After generation the dataKey is returned in the text result.
    Use redownload_map(dataKey=...) to get the same map in a different color or format
    without waiting for regeneration.
    """
    has_center = place or (lat is not None and lng is not None)
    if not has_center and not markers:
        raise ValueError("Specify 'place', both 'lat'+'lng', or 'markers'.")
    if radius is not None and (size_ew is not None or size_ns is not None):
        raise ValueError("Specify either 'radius' or 'size_ew'+'size_ns', not both.")
    if (size_ew is None) != (size_ns is None):
        raise ValueError("'size_ew' and 'size_ns' must be specified together.")

    body: dict = {}
    if layers is not None:
        body["layers"] = layers
    if place:
        body["place"] = place
    elif lat is not None and lng is not None:
        body["center"] = {"lat": lat, "lng": lng}
    if markers is not None:
        body["markers"] = markers
    if place_icon is not None:
        body["icon"] = place_icon
    if name is not None:
        body["name"] = name
    if title is not None:
        body["title"] = title
    if color_set is not None:
        body["colorSet"] = color_set
    if radius is not None:
        body["radius"] = radius
    elif size_ew is not None:
        body["size"] = {"ew": size_ew, "ns": size_ns}
    if poi_types:
        body["poiTypes"] = poi_types
    if route is not None:
        body["route"] = route

    async with httpx.AsyncClient() as client:
        # 1. Enqueue
        resp = await client.post(f"{BASE_URL}/request", json=body, timeout=30)
        if resp.status_code != 202:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Request rejected ({resp.status_code}): {detail}")
        data_key = resp.json()["dataKey"]

        # 2. Poll until ready
        elapsed = 0.0
        while elapsed < MAX_WAIT_SEC:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            elapsed += POLL_INTERVAL_SEC
            status_resp = await client.get(
                f"{BASE_URL}/status", params={"dataKey": data_key}, timeout=10
            )
            status = status_resp.json().get("status")
            if status == "ready":
                break
            if status == "failed":
                raise RuntimeError(
                    f"Map generation failed (dataKey={data_key}). "
                    "Try a different location or smaller radius."
                )
        else:
            raise TimeoutError(f"Map generation timed out after {MAX_WAIT_SEC}s.")

        # 3. Download
        image_bytes = await _download(client, data_key, format, color_set,
                                      canvas_width, canvas_height, edge_weight)

    if place:
        label = place
    elif lat is not None and lng is not None:
        label = f"{lat}_{lng}"
    else:
        label = "_".join(m.get("label", "") for m in (markers or []))[:40]
    out_path = _make_filename(label, format)
    out_path.write_bytes(image_bytes)

    return [
        Image(data=image_bytes, format="png" if format == "png" else "png"),
        (
            f"Map saved: {out_path}\n"
            f"dataKey: {data_key}\n\n"
            f"Open in editor: https://mapnetwork.app/edit?dataKey={data_key}\n\n"
            f"Tip: call redownload_map(data_key='{data_key}', ...) to get this map "
            f"in a different color_set or format (svg/png) without regenerating."
        ),
    ]


# ---------------------------------------------------------------------------
# Tool: redownload_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def redownload_map(
    data_key: Annotated[
        str,
        "The dataKey returned by a previous generate_map call (e.g. '20260618aBcDeFgHiJ'). "
        "The map data is reused — no regeneration occurs.",
    ],
    color_set: Annotated[
        str | None,
        "Color theme for the map. Only set when the user explicitly requests one. "
        "Available: white, darkBlue, darkGreen, popArt, lightBlue, lightGreen, beige, magenta, gray, black, brawn.",
    ] = None,
    format: Annotated[
        str,
        "'png' (raster) or 'svg' (vector, infinitely scalable).",
    ] = "png",
    canvas_width: Annotated[int | None, "Canvas width in pixels. Omit to use server default (1000)."] = None,
    canvas_height: Annotated[int | None, "Canvas height in pixels. Omit to use server default (700)."] = None,
    edge_weight: Annotated[
        int | None,
        "Road and line width adjustment. Positive values thicken lines, negative values thin them. "
        "Default 0 (no adjustment).",
    ] = None,
) -> list:
    """Re-download a previously generated map with a different color theme or format.

    Uses the dataKey from a prior generate_map call. The map data is NOT regenerated,
    so this is instant. Use this when the user wants to:
    - Try a different color theme (e.g. 'black' for dark mode, 'lightBlue', 'popArt')
    - Get an SVG version of a map already generated as PNG
    - Get a larger or smaller canvas size
    """
    async with httpx.AsyncClient() as client:
        image_bytes = await _download(client, data_key, format, color_set,
                                      canvas_width, canvas_height, edge_weight)

    out_path = _make_filename(data_key, format)
    out_path.write_bytes(image_bytes)

    return [
        Image(data=image_bytes, format="png"),
        f"Map saved: {out_path}  (dataKey={data_key}, colorSet={color_set}, format={format})",
    ]


# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
