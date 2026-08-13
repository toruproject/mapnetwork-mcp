"""MCP server for MapNetwork — generates styled map images from a place name or coordinates."""

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import httpx
from mcp.server.fastmcp import Context, FastMCP, Image
from pydantic import BaseModel, ConfigDict, Field

BASE_URL = os.environ.get("MAPNETWORK_BASE_URL", "https://mapnetwork.app")
POLL_INTERVAL_SEC = 10.0
MAX_WAIT_SEC = 600
# MCPクライアントによっては、report_progressで進捗通知を送ってもtools/call自体の
# クライアント側タイムアウトを延長してくれない(60秒固定のクライアントで実測確認済み)。
# そのため generate_map は内部ポーリングをこの秒数までに打ち切り、まだ終わっていなければ
# dataKeyを返してcheck_map_statusでの確認をモデルに促す。60秒に対して十分な余裕を残す値にする。
INITIAL_WAIT_SEC = 40.0
# check_map_statusをモデルが連打しても機械的に間隔が空くようにする最低待機時間
CHECK_THROTTLE_SEC = 5.0

# layersは自由な多値配列にすると、モデルが「roadはhighway/driving/walkingの上位互換」
# といった内部の組み合わせルールを知らないまま組み合わせを選んでしまう。
# 人間側で意味のある4パターンだけに絞り込み、モデルにはこのプリセット名だけを選ばせる。
_LAYER_PRESETS: dict[str, list[str]] = {
    "default": ["road", "poi"],
    "road_and_railway": ["road", "railway", "poi"],
    "only_driving_road": ["driving"],
    "fully_detailed": ["road", "railway", "poi", "waterline", "greenarea"],
}


class Location(BaseModel):
    lat: float = Field(description="Latitude.")
    lng: float = Field(description="Longitude.")


class Marker(BaseModel):
    label: str | None = Field(default=None, description="Place name for geocoding and/or display.")
    location: Location | None = Field(
        default=None,
        description="Explicit coordinates. If provided, geocoding of 'label' is skipped.",
    )


class RouteEndpoint(BaseModel):
    label: str | None = Field(default=None, description="Place name for geocoding and/or display.")
    location: Location | None = Field(default=None, description="Explicit coordinates.")


class RouteInput(BaseModel):
    """generate_mapのrouteパラメータ。compute_routeの戻り値をそのまま渡せる形にする。"""
    model_config = ConfigDict(populate_by_name=True)

    coords: list[list[float]] | None = Field(
        default=None, description="Ordered list of [lat, lng] pairs along the route."
    )
    mode: Literal["walking", "driving"] | None = Field(default=None, description="'walking' or 'driving'.")
    from_: RouteEndpoint | None = Field(default=None, alias="from", description="Route start point.")
    to: RouteEndpoint | None = Field(default=None, description="Route end point.")
    color: str | None = Field(default=None, description="Optional line color as a CSS hex color (e.g. '#FF4500').")


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
        "- **Layers**: choose a preset via `layers` — 'default' (roads + POI), "
        "'only_driving_road' (car-accessible roads only), 'road_and_railway' (roads + railway, no POI), "
        "or 'fully_detailed' (roads + railway + waterline + greenarea + POI; waterline/greenarea only "
        "apply within Japan). Omit for 'default'.\n"
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
        "## If generation is still in progress\n"
        "For busy areas, `generate_map` may return before the map is ready, with a `dataKey` "
        "and a message asking you to check back. In that case, wait about 20-30 seconds and "
        "call `check_map_status(dataKey=...)` — do not call `generate_map` again for the same "
        "location, that only starts a redundant duplicate job. Repeat `check_map_status` "
        "(with the same wait between calls) until it returns the image.\n\n"
        "## Important: re-download without regenerating\n"
        "After generating a map, `generate_map` returns a `dataKey`. "
        "You can call `redownload_map` with that `dataKey` to get the same map in a different "
        "format (png/svg) or color theme — **no regeneration needed**. "
        "Use this when the user asks to change only the appearance after already generating the map.\n\n"
        "## Open in the MapNetwork editor\n"
        "Any generated map can be opened and edited interactively in the MapNetwork web UI at:\n"
        "  https://mapnetwork.app/generate?dataKey=<dataKey>\n"
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
                    color_set: str | None, canvas_width: int | None, canvas_height: int | None) -> bytes:
    params: dict = {"dataKey": data_key, "format": format}
    if color_set is not None:
        params["colorSet"] = color_set
    if canvas_width is not None:
        params["canvasWidth"] = canvas_width
    if canvas_height is not None:
        params["canvasHeight"] = canvas_height
    resp = await client.get(f"{BASE_URL}/download", params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed ({resp.status_code}): {resp.text}")
    return resp.content


async def _poll_with_budget(
    client: httpx.AsyncClient, data_key: str, budget_sec: float, ctx: Context | None,
) -> str:
    """dataKeyの状態をPOLL_INTERVAL_SEC間隔でbudget_sec以内までポーリングする。
    "ready"/"failed"になればその時点で返す。budget_sec内に決着しなければ"pending"を
    返す(例外は投げない、呼び出し側で分岐する)。"""
    if ctx is not None:
        await ctx.report_progress(0, budget_sec, "Map generation queued...")
    elapsed = 0.0
    while elapsed < budget_sec:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC
        status_resp = await client.get(
            f"{BASE_URL}/status", params={"dataKey": data_key}, timeout=10
        )
        status = status_resp.json().get("status")
        if status in ("ready", "failed"):
            return status
        if ctx is not None:
            await ctx.report_progress(
                elapsed, budget_sec, f"Map generation in progress... ({elapsed:.0f}s elapsed)"
            )
    return "pending"


def _build_success_result(image_bytes: bytes, out_path: Path, data_key: str) -> list:
    out_path.write_bytes(image_bytes)
    return [
        Image(data=image_bytes, format="png"),
        (
            f"Map saved: {out_path}\n"
            f"dataKey: {data_key}\n\n"
            f"Open in editor: https://mapnetwork.app/generate?dataKey={data_key}\n\n"
            f"Tip: call redownload_map(data_key='{data_key}', ...) to get this map "
            f"in a different color_set or format (svg/png) without regenerating."
        ),
    ]


def _still_in_progress_result(data_key: str) -> list:
    return [(
        f"Map generation is still in progress (dataKey={data_key}). This can take a few "
        "minutes for busy areas. Wait about 20-30 seconds, then call "
        f"check_map_status(data_key='{data_key}') to check again — pass the same format/"
        "color_set/canvas_width/canvas_height you used with generate_map, if any. "
        "Do NOT call generate_map again for the same location — that starts a redundant "
        "duplicate job and wastes time."
    )]


# ---------------------------------------------------------------------------
# Tool: compute_route
# ---------------------------------------------------------------------------

@mcp.tool()
async def compute_route(
    from_location: Annotated[
        RouteEndpoint, Field(description="Route start. 'location' for coordinates, or 'label' to geocode.")
    ],
    to_location: Annotated[RouteEndpoint, Field(description="Route end. Same format as from_location.")],
    mode: Annotated[Literal["walking", "driving"], Field(description="Default 'walking'.")] = "walking",
) -> dict:
    """Compute a route. Returns {coords, from, to} — pass directly to generate_map(route=...).

    Example: compute_route(from_location={"label": "Tokyo Station"}, to_location={"label": "Tokyo Tower"})
    """
    body = {
        "from": from_location.model_dump(exclude_none=True),
        "to": to_location.model_dump(exclude_none=True),
        "mode": mode,
    }
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
        str | None, Field(description="Place name to center the map on. Use this OR lat/lng, not both.")
    ] = None,
    lat: Annotated[float | None, Field(description="Latitude of the map center. Must be combined with lng.")] = None,
    lng: Annotated[float | None, Field(description="Longitude of the map center. Must be combined with lat.")] = None,
    markers: Annotated[list[Marker] | None, Field(description="Locations to pin on the map.")] = None,
    name: Annotated[str | None, Field(description="Map name stored in the data file.")] = None,
    radius: Annotated[
        int | None,
        Field(description="Circular radius in meters (default 500, max 2500). Exclusive with size_ew/size_ns."),
    ] = None,
    size_ew: Annotated[
        float | None,
        Field(description="Rectangular area width (east-west), meters. Must pair with size_ns; exclusive with radius."),
    ] = None,
    size_ns: Annotated[
        float | None,
        Field(description="Rectangular area height (north-south), meters. Must pair with size_ew."),
    ] = None,
    layers: Annotated[
        Literal["default", "road_and_railway", "only_driving_road", "fully_detailed"] | None,
        Field(description="Layer preset — see server instructions for what each preset includes. Omit for 'default'."),
    ] = None,
    route: Annotated[
        RouteInput | None,
        Field(description="Route to overlay — pass compute_route()'s result directly. Add/override 'color' (hex) to customize."),
    ] = None,
    color_set: Annotated[
        str | None,
        Field(description="Color theme name — see server instructions for the list. Only set when explicitly requested."),
    ] = None,
    format: Annotated[Literal["png", "svg"], Field(description="'png' (default) or 'svg'.")] = "png",
    canvas_width: Annotated[int | None, Field(description="Canvas width in pixels (default 1000).")] = None,
    canvas_height: Annotated[int | None, Field(description="Canvas height in pixels (default 600).")] = None,
    ctx: Context = None,
) -> list:
    """Generate a styled map image and save it to Downloads.

    May return a pending dataKey instead of the image — see server instructions for the
    check_map_status follow-up flow.

    Example: generate_map(place="Tokyo Station")
    """
    has_center = place or (lat is not None and lng is not None)
    has_route_coords = route is not None and route.coords is not None and len(route.coords) >= 2
    if not has_center and not markers and not has_route_coords:
        raise ValueError("Specify 'place', both 'lat'+'lng', 'markers', or a 'route' with coords.")
    if radius is not None and (size_ew is not None or size_ns is not None):
        raise ValueError("Specify either 'radius' or 'size_ew'+'size_ns', not both.")
    if (size_ew is None) != (size_ns is None):
        raise ValueError("'size_ew' and 'size_ns' must be specified together.")

    body: dict = {}
    if layers is not None:
        body["layers"] = _LAYER_PRESETS[layers]
    if place:
        body["place"] = place
    elif lat is not None and lng is not None:
        body["center"] = {"lat": lat, "lng": lng}
    if markers is not None:
        body["markers"] = [m.model_dump(exclude_none=True) for m in markers]
    if name is not None:
        body["name"] = name
    if color_set is not None:
        body["colorSet"] = color_set
    if radius is not None:
        body["radius"] = radius
    elif size_ew is not None:
        body["size"] = {"ew": size_ew, "ns": size_ns}
    if route is not None:
        body["route"] = route.model_dump(by_alias=True, exclude_none=True)

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

        # 2. Poll, but only up to INITIAL_WAIT_SEC (安全のためMCPクライアントの典型的な
        # 60秒タイムアウトより十分短くしてある)。多くの地図生成はこの範囲で終わるため、
        # その場合はこの1回の呼び出しだけで完結する。間に合わなければ、ここで打ち切って
        # dataKeyを返し、check_map_statusでの確認をモデルに促す(生成自体はバックエンドで
        # 継続しているので、ここで諦めても失敗にはならない)。
        status = await _poll_with_budget(client, data_key, INITIAL_WAIT_SEC, ctx)

        if status == "failed":
            raise RuntimeError(
                f"Map generation failed (dataKey={data_key}). "
                "Try a different location or smaller radius."
            )
        if status == "pending":
            return _still_in_progress_result(data_key)

        # 3. Download
        image_bytes = await _download(client, data_key, format, color_set,
                                      canvas_width, canvas_height)

    if place:
        label = place
    elif lat is not None and lng is not None:
        label = f"{lat}_{lng}"
    elif markers:
        label = "_".join(m.label or "" for m in markers)[:40]
    else:
        label = "route"
    return _build_success_result(image_bytes, _make_filename(label, format), data_key)


# ---------------------------------------------------------------------------
# Tool: check_map_status
# ---------------------------------------------------------------------------

@mcp.tool()
async def check_map_status(
    data_key: Annotated[
        str,
        Field(description="The dataKey returned by a generate_map call that reported it was still in progress."),
    ],
    color_set: Annotated[
        str | None,
        Field(description="Color theme, matching what generate_map was called with, if any."),
    ] = None,
    format: Annotated[Literal["png", "svg"], Field(description="Matching what generate_map was called with.")] = "png",
    canvas_width: Annotated[
        int | None, Field(description="Canvas width, matching what generate_map was called with, if any.")
    ] = None,
    canvas_height: Annotated[
        int | None, Field(description="Canvas height, matching what generate_map was called with, if any.")
    ] = None,
    ctx: Context = None,
) -> list:
    """Poll a pending generate_map job and download it once ready. Wait ~20-30s between calls.

    Example: check_map_status(data_key="20260618aBcDeFgHiJ")
    """
    async with httpx.AsyncClient() as client:
        # 連打されても機械的に間隔が空くようにする最低限のスロットル
        await asyncio.sleep(CHECK_THROTTLE_SEC)
        status_resp = await client.get(f"{BASE_URL}/status", params={"dataKey": data_key}, timeout=10)
        status = status_resp.json().get("status")
        if status == "failed":
            raise RuntimeError(
                f"Map generation failed (dataKey={data_key}). "
                "Try a different location or smaller radius."
            )
        if status != "ready":
            if ctx is not None:
                await ctx.report_progress(0, 1, "Map generation still in progress...")
            return _still_in_progress_result(data_key)

        image_bytes = await _download(client, data_key, format, color_set,
                                      canvas_width, canvas_height)

    return _build_success_result(image_bytes, _make_filename(data_key, format), data_key)


# ---------------------------------------------------------------------------
# Tool: redownload_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def redownload_map(
    data_key: Annotated[str, Field(description="The dataKey returned by a previous generate_map call.")],
    color_set: Annotated[
        str | None,
        Field(description="Color theme — see server instructions for the list. Only set when explicitly requested."),
    ] = None,
    format: Annotated[Literal["png", "svg"], Field(description="'png' (raster) or 'svg' (vector).")] = "png",
    canvas_width: Annotated[int | None, Field(description="Canvas width in pixels (default 1000).")] = None,
    canvas_height: Annotated[int | None, Field(description="Canvas height in pixels (default 600).")] = None,
) -> list:
    """Re-download a prior map in a different color theme, format, or size — instant, no regeneration.

    Example: redownload_map(data_key="20260618aBcDeFgHiJ", color_set="black")
    """
    async with httpx.AsyncClient() as client:
        image_bytes = await _download(client, data_key, format, color_set,
                                      canvas_width, canvas_height)

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
