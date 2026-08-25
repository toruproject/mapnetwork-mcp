"""MCP server for MapNetwork — generates styled map images from a place name or coordinates."""

import asyncio
import os
import re
import sys
import threading
import time
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
# そのため generate_* は内部ポーリングをこの秒数までに打ち切り、まだ終わっていなければ
# dataKeyを返してcheck_map_statusでの確認をモデルに促す。60秒に対して十分な余裕を残す値にする。
INITIAL_WAIT_SEC = 40.0
# check_map_statusをモデルが連打しても機械的に間隔が空くようにする最低待機時間
CHECK_THROTTLE_SEC = 5.0


# extra="forbid": 未知のキー(例えばmarkerの'label'と紛れて'place'を書いてしまう等)を
# Pydanticが黙って無視せずバリデーションエラーとして返す。tools/callがエラーで返れば
# モデルは次の呼び出しで自己修正できるが、黙って無視されるとその機会が失われる
# (実運用でmarkersの各要素に存在しない'place'キーが紛れ込む事故が実際に発生し、
# extra="ignore"がデフォルトのため気づかれずに黙って無視されていたことが判明した)。

class Marker(BaseModel):
    """generate_markers_mapのマーカー。labelは必須、linkは任意で公式サイトURLを付けられる
    (バックエンド側がPlaces APIで自動補完する場合も多いが、モデルが確度高く知っている場合は
    明示的に渡してもよい)。link無しのマーカーが混在してもよい(むしろ通常はこちら)。"""
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Place name to geocode and display.")
    link: str | None = Field(
        default=None,
        description="Optional: the marker's official website URL, if known. See server instructions.",
    )


mcp = FastMCP(
    "MapNetwork",
    instructions=(
        "You have access to MapNetwork, which generates styled map images (PNG) "
        "for any location on Earth.\n\n"
        "## Key capabilities — pick the tool that matches what you need\n"
        "- **Single location**: `generate_map(place=...)` — centers a map on one place.\n"
        "- **Multiple pinned locations**: `generate_markers_map(markers=[{\"label\": ...}, ...])` — "
        "pins several places on one map. Each marker needs a `label`, and may optionally include "
        "`link` (its official website URL, if known) to make that pin a clickable link in the "
        "result's 'Open in editor' link. Use this whenever the user asks to show multiple places "
        "on a single map, or wants a map they (or someone else) can click through to visit the "
        "pinned places' websites.\n"
        "- **Walking/driving route**: `generate_route_map(from=..., to=..., mode=...)` — computes a "
        "route between two places and draws it on the map.\n"
        "- Every generate_* call produces a PNG at the default color theme — see 'Important: "
        "re-download without regenerating' below to change the theme or format without regenerating.\n\n"
        "## If generation is still in progress\n"
        "For busy areas, any generate_* tool may return before the map is ready, with a `dataKey` "
        "and a message asking you to check back. In that case, wait about 10-20 seconds and "
        "call `check_map_status(data_key=...)` — do not call the generate_* tool again for the same "
        "request, that only starts a redundant duplicate job. Repeat `check_map_status` "
        "(with the same wait between calls) until it returns the image.\n\n"
        "## Important: re-download without regenerating\n"
        "After generating a map, the tool returns a `dataKey`. "
        "You can call `redownload_map` with that `dataKey` to get the same map in a different "
        "color theme or format (png/svg) — **no regeneration needed**. "
        "Use this when the user asks to change only the appearance after already generating the map. "
        "MapNetwork supports 11 color themes (white, darkBlue, darkGreen, popArt, lightBlue, "
        "lightGreen, beige, magenta, gray, black, brawn).\n\n"
        "## Sharing the generated map\n"
        "Every successful result includes a Download link and an 'Open in editor' link — "
        "mention both to the user.\n\n"
        "## Parameter discipline\n"
        "- **place/label**: `place`, a marker's `label`, and route's `from`/`to` must each be a "
        "place name specific enough for the server to geocode it to a single, unambiguous "
        "location (e.g. a full venue or station name, not a vague or generic term like "
        "\"the station\" or \"a nearby cafe\")."
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
                    color_set: str | None) -> bytes:
    params: dict = {"dataKey": data_key, "format": format}
    if color_set is not None:
        params["colorSet"] = color_set
    resp = await client.get(f"{BASE_URL}/download", params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed ({resp.status_code}): {resp.text}")
    return resp.content


async def _has_any_link(client: httpx.AsyncClient, data_key: str) -> bool:
    """dataKeyの生成結果(placeDict)にlinkを持つエントリが1つでもあるか判定する。
    Web UIの共有リンク機能が使うAPIを流用する(MCP生成データも同じSTORE_DIR配下に
    保存されるため、dataKeyを指定すればWeb UI側からも同じデータが取得できる)。
    どのツールを呼んだかではなく、実際に生成されたデータで判定する。"""
    try:
        resp = await client.get(f"{BASE_URL}/api/upd/download", params={"dataKey": data_key}, timeout=10)
        if resp.status_code != 200:
            return False
        place_dict = resp.json().get("placeDict", {})
        return any(isinstance(p, dict) and p.get("link") for p in place_dict.values())
    except Exception:
        return False


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


def _build_success_result(image_bytes: bytes, out_path: Path, data_key: str, has_link: bool) -> list:
    out_path.write_bytes(image_bytes)
    # linkを持つマーカーが1つでもあれば、"Open in editor"を閲覧専用(lock)モードのURLにする。
    # どのツールを呼んだかではなく実際に生成されたplaceDictの中身で判定している(_has_any_link)。
    editor_url = f"https://mapnetwork.app/generate?dataKey={data_key}"
    if has_link:
        editor_url += "&lock=true"
    download_url = f"https://mapnetwork.app/download?dataKey={data_key}"
    return [
        Image(data=image_bytes, format="png"),
        (
            f"Map saved: {out_path}\n"
            f"dataKey: {data_key}\n\n"
            f"Download: {download_url}\n"
            f"Open in editor: {editor_url}\n\n"
            f"Tip: call redownload_map(data_key='{data_key}', ...) to get this map "
            f"in a different color theme or format (svg/png) without regenerating."
        ),
    ]


def _still_in_progress_result(data_key: str) -> list:
    return [(
        f"Map generation is still in progress (dataKey={data_key}). This can take a few "
        "minutes for busy areas. Wait about 20-30 seconds, then call "
        f"check_map_status(data_key='{data_key}') to check again. "
        "Do NOT call the generate_* tool again for the same request — that starts a redundant "
        "duplicate job and wastes time."
    )]


async def _generate_map_common(body: dict, ctx: Context | None, label: str | None = None) -> list:
    """3つのgenerate_*ツール共通のenqueue→poll→download→has_link判定→結果構築フロー。
    labelはダウンロードファイル名の元にのみ使う(バックエンド側もplace/markers/routeから
    同じ表示名をconfigDict.nameとして独自に導出するため、bodyには含めない)。
    常にformat="png"・デフォルトcolor_setでダウンロードする(別の見た目が欲しければ
    redownload_mapで取り直す)。"""
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
                "Try a different location."
            )
        if status == "pending":
            return _still_in_progress_result(data_key)

        # 3. Download + linkの有無判定
        image_bytes = await _download(client, data_key, "png", None)
        has_link = await _has_any_link(client, data_key)

    return _build_success_result(image_bytes, _make_filename(label or data_key, "png"), data_key, has_link)


# ---------------------------------------------------------------------------
# Tool: generate_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_map(
    place: Annotated[str, Field(description="Place name to center the map on (geocoded server-side).")],
    ctx: Context = None,
) -> list:
    """Generate a styled map centered on a single place.

    Example: generate_map(place="Tokyo Station")
    """
    body: dict = {"place": place}
    return await _generate_map_common(body, ctx, label=place)


# ---------------------------------------------------------------------------
# Tool: generate_markers_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_markers_map(
    markers: Annotated[
        list[Marker], Field(description="Locations to pin on the map. At least one required.")
    ],
    place: Annotated[
        str | None,
        Field(description="Optional: place to center the map on. If omitted, the center is computed automatically from the markers."),
    ] = None,
    ctx: Context = None,
) -> list:
    """Generate a map with multiple pinned locations.

    Example: generate_markers_map(markers=[{"label": "Tokyo Tower", "link": "https://www.tokyotower.co.jp/"}, {"label": "Zojoji Temple"}])
    """
    if not markers:
        raise ValueError("'markers' must contain at least one marker.")
    label = place or "_".join(m.label for m in markers)[:40]
    body: dict = {"markers": [m.model_dump(exclude_none=True) for m in markers]}
    if place:
        body["place"] = place
    return await _generate_map_common(body, ctx, label=label)


# ---------------------------------------------------------------------------
# Tool: generate_route_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_route_map(
    # Pythonの予約語のため実引数名は'from_'。JSON上のキー名はvalidation_aliasで'from'にする
    # (Field(alias=...)だとFastMCPが関数呼び出し時もalias名でキーワード引数を組み立てて
    # TypeErrorになるため、入力側だけをマッピングするvalidation_aliasを使う)。
    from_: Annotated[str, Field(validation_alias="from", description="Route start. Place name to geocode.")],
    to: Annotated[str, Field(description="Route end. Place name to geocode.")],
    mode: Annotated[Literal["walking", "driving"], Field(description="Default 'walking'.")] = "walking",
    place: Annotated[
        str | None,
        Field(description="Optional: place to center the map on. If omitted, the center is computed automatically from the route."),
    ] = None,
    ctx: Context = None,
) -> list:
    """Compute a walking/driving route between two places and generate a map with it overlaid.
    Both endpoints are pinned on the map automatically — do not also call generate_markers_map for them.

    Example: generate_route_map(from="Tokyo Station", to="Tokyo Tower", mode="walking")
    """
    # from/toはそのままバックエンドの/requestへ渡す(サーバー側がジオコード+ルート探索+
    # 地図生成を1回のリクエスト内で行う。以前はここで先に/routeを呼んでルートを計算して
    # いたが、それだと地図生成用の道路データ取得と合わせて同じ範囲の道路網を2回取得する
    # ことになっていたため、サーバー側で1回にまとめた)。
    label = place or "route"
    body: dict = {"route": {"from": {"label": from_}, "to": {"label": to}, "mode": mode}}
    if place:
        body["place"] = place
    return await _generate_map_common(body, ctx, label=label)


# ---------------------------------------------------------------------------
# Tool: check_map_status
# ---------------------------------------------------------------------------

@mcp.tool()
async def check_map_status(
    data_key: Annotated[
        str,
        Field(description="The dataKey returned by a generate_* call that reported it was still in progress."),
    ],
    ctx: Context = None,
) -> list:
    """Poll a pending generate_* job and download it once ready. Wait ~20-30s between calls.

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
                "Try a different location."
            )
        if status != "ready":
            if ctx is not None:
                await ctx.report_progress(0, 1, "Map generation still in progress...")
            return _still_in_progress_result(data_key)

        image_bytes = await _download(client, data_key, "png", None)
        has_link = await _has_any_link(client, data_key)

    return _build_success_result(image_bytes, _make_filename(data_key, "png"), data_key, has_link)


# ---------------------------------------------------------------------------
# Tool: redownload_map
# ---------------------------------------------------------------------------

@mcp.tool()
async def redownload_map(
    data_key: Annotated[str, Field(description="The dataKey returned by a previous generate_* call.")],
    color_set: Annotated[
        str | None,
        Field(description="Color theme — see server instructions for the list. Only set when explicitly requested."),
    ] = None,
    format: Annotated[Literal["png", "svg"], Field(description="'png' (raster) or 'svg' (vector).")] = "png",
) -> list:
    """Re-download a prior map in a different color theme or format — instant, no regeneration.

    Example: redownload_map(data_key="20260618aBcDeFgHiJ", color_set="black")
    """
    async with httpx.AsyncClient() as client:
        image_bytes = await _download(client, data_key, format, color_set)

    out_path = _make_filename(data_key, format)
    out_path.write_bytes(image_bytes)

    # Image(format=...)はmimeTypeを"image/{format}"にそのまま埋め込む。svgの正式なmimeTypeは
    # "image/svg+xml"なので、ここだけ変換する(バックエンドへのリクエストやファイル名生成には
    # 影響しない、format変数自体は"svg"のまま)。
    image_format = "svg+xml" if format == "svg" else format
    return [
        Image(data=image_bytes, format=image_format),
        f"Map saved: {out_path}  (dataKey={data_key}, colorSet={color_set}, format={format})",
    ]


# ---------------------------------------------------------------------------
# Orphan watchdog
# ---------------------------------------------------------------------------
#
# MCPホスト(Claude Desktop)は"uv run ... mapnetwork_mcp/server.py"でこのプロセスを起動する。
# Windows上ではuvが自身のプロセス置換(execve相当)を行えないため、"uv.exe -> python.exe"という
# 子プロセスになり、さらに観測上はその下にもう一段python.exeが生まれる(mcp SDK/anyio側の
# 挙動と見られるが詳細未特定)。標準入力のEOFで自発終了するstdio_serverのロジックは正しく
# 機能する(到達不能URLでの単体テストで確認済み)一方、実運用では拡張機能の無効化/更新/
# アンインストール時にホストが子孫プロセスツリー全体を確実にkillできず、末端のpython.exeが
# 孤児として残留し、.venv配下の実行ファイルをロックし続けてアンインストール自体が失敗する
# 事故が確認された(Windows特有: プロセスをkillしても既定では子孫まで自動的には終了しない)。
# stdinパイプのEOF検知に頼らず、起動時の親プロセス(uv.exe)の生死を直接ポーリングし、
# 親が消えていたら自分も終了することで、この孤児化を防ぐ。
_ORPHAN_CHECK_INTERVAL_SEC = 5.0


def _is_process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _start_orphan_watchdog() -> None:
    parent_pid = os.getppid()

    def _watch() -> None:
        while True:
            time.sleep(_ORPHAN_CHECK_INTERVAL_SEC)
            if not _is_process_alive(parent_pid):
                os._exit(1)

    threading.Thread(target=_watch, daemon=True, name="orphan-watchdog").start()


def main() -> None:
    _start_orphan_watchdog()
    mcp.run()


if __name__ == "__main__":
    main()
