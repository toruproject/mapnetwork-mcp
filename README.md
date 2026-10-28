English | [日本語](https://github.com/toruproject/mapnetwork-mcp/blob/main/README.ja.md)

# mapnetwork-mcp

MCP server for [MapNetwork](https://mapnetwork.app) — turn places in Japan into styled, illustration-like map images (PNG / SVG), directly from Claude or any MCP-compatible AI assistant.

Maps are built from detailed OpenStreetMap data served by MapNetwork's own Overpass API for Japan, so coverage is currently limited to **Japan (country code JP)** — requests for places outside Japan return an error.

Since MapNetwork is built for Japan, a Japanese version of this README is also available: [README.ja.md](https://github.com/toruproject/mapnetwork-mcp/blob/main/README.ja.md).

## Features

- Generate a map from a place name or a list of markers
- Overlay a walking / driving route between two places
- 11 color themes (white, darkBlue, darkGreen, popArt, lightBlue, lightGreen, beige, magenta, gray, black, brown)
- PNG or SVG output, saved to your Downloads folder
- Slow-to-generate maps are handled automatically — `generate_*` tools poll internally, and `download_map` can pick up a job later if needed
- Re-download a previously generated map in a different theme or format without regenerating
- Open any generated map in the MapNetwork web editor for manual customization
- View any generated map in 3D — each result includes a link that draws a 3D rendering of the map in your own desktop browser and shows it as an image (not available on mobile devices)

## Example Usage

**Prompt:** "城崎温泉の外湯を地図にして。" (Make a map of the public bathhouses of Kinosaki Onsen.)

The map it generated, saved to your Downloads folder:

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-download.png" width="600" alt="Map of the public bathhouses of Kinosaki Onsen">

The result also includes three links. Open them in a browser to get more out of the same map — no need to regenerate.

**Download link** — change the color theme by editing the link's `colorSet` parameter (here `&colorSet=black`):

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-black.png" width="600" alt="The same map in the black color theme">

**View 3D link** — a 3D rendering of the map, drawn in your desktop browser and shown as an image:

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-view3d.png" width="600" alt="3D rendering of the map">

**Open in editor link** — customize the map by hand in the MapNetwork web editor (here: buildings hidden, patchwork turned on, and the popArt color theme applied):

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-popArt.png" width="600" alt="The map customized in the web editor">

## Installation

### Claude Desktop

#### Using the .mcpb extension file (recommended)

The easiest way to install is via the `.mcpb` extension file available on the [Releases](https://github.com/toruproject/mapnetwork-mcp/releases) page.

1. Download the latest `.mcpb` file from the Releases page.
2. Open Claude Desktop and go to **Settings** → **Extensions**.
3. Drag the downloaded `.mcpb` file onto the Extensions panel to install.

#### Manual configuration

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mapnetwork": {
      "command": "uvx",
      "args": ["mapnetwork-mcp"],
      "alwaysAllow": ["generate_map", "generate_markers_map", "generate_route_map", "download_map", "redownload_map"]
    }
  }
}
```

If you use `pip` instead of `uvx`:

```bash
pip install mapnetwork-mcp
```

```json
{
  "mcpServers": {
    "mapnetwork": {
      "command": "mapnetwork-mcp"
    }
  }
}
```

## Remote MCP Server

You can also use MapNetwork through MCP without installing anything, via the remote MCP server:

```
https://mapnetwork.app/mcp
```

In Claude, go to **Settings** → **Connectors** → **Add custom connector** and register the URL above.

The key difference from this local server is how you receive the generated map image. Since `mapnetwork-mcp` runs on your PC, it can save the generated image directly to your Downloads folder. The remote server can't do this — most AI assistants are unable to properly display or save tool-result images within the chat — so instead of image data, it returns a text response with three links at [mapnetwork.app](https://mapnetwork.app): a download link for the PNG, a link to edit the map, and a link to view the map in 3D (the 3D rendering is drawn in your own desktop browser and shown as an image after a few seconds; it is not available on mobile devices). Open the links in a browser to view or save the image, to edit the map, or to see it in 3D. The local server returns the same three links after saving the PNG.

## Tools

| Tool | Description |
|---|---|
| `generate_map` | Generate a styled map image centered on a single place and save it to Downloads |
| `generate_markers_map` | Generate a map with multiple pinned locations |
| `generate_route_map` | Compute a walking or driving route between two places and generate a map with it overlaid |
| `download_map` | Check a pending `generate_*` job and download it once ready |
| `redownload_map` | Re-download a previously generated map in a different color theme or format — instant, no regeneration |

## Example prompts

**Japanese**
- "東京タワー周辺の地図を作って"
- "上野駅が最寄りの美術館・博物館を地図にして"
- "赤坂駅から赤坂氷川神社までの徒歩ルートを地図にして"
- "その地図を darkBlue テーマでSVG画像で出し直して"
- "生成した地図の編集用リンクをください"

**English**
- "Generate a map around Tokyo Tower"
- "Show a map of the museums near Ueno Station"
- "Show a walking route from Akasaka Station to Akasaka Hikawa Shrine on a map"
- "Redownload that map as an SVG in the darkBlue theme"
- "Give me a link to edit the generated map"

## Links

- Web UI: [https://mapnetwork.app](https://mapnetwork.app)
- Web UI Manual: [https://mapnetwork.app/manual](https://mapnetwork.app/manual)
- API docs: [https://mapnetwork.app/openapi.json](https://mapnetwork.app/openapi.json)

mcp-name: io.github.toruproject/mapnetwork-mcp
